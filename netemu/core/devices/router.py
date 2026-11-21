"""Layer-3 router with connected + static routing."""
from __future__ import annotations

from dataclasses import dataclass

from ..addresses import IPv4Address, IPv4Network
from ..packets import PROTO_OSPF, PROTO_TCP, EthernetFrame, TcpSegment, UdpDatagram
from ..port import Port
from ..stack.ipv4 import IpStack, Route
from ..stack.ipv6 import Ip6Stack, StaticRoute6
from .base import Device


@dataclass
class StaticRoute:
    network: IPv4Network
    next_hop: IPv4Address | None = None
    interface: str | None = None     # exit interface name
    distance: int = 1

    def to_dict(self) -> dict:
        return {"network": str(self.network),
                "next_hop": str(self.next_hop) if self.next_hop else None,
                "interface": self.interface, "distance": self.distance}

    @classmethod
    def from_dict(cls, d: dict) -> "StaticRoute":
        return cls(IPv4Network(d["network"]),
                   IPv4Address(d["next_hop"]) if d.get("next_hop") else None,
                   d.get("interface"), int(d.get("distance", 1)))


class Router(IpStack, Ip6Stack, Device):
    category = "router"
    ip_forwarding = True
    proxy_arp = True

    def __init__(self, engine, name, device_id=None):
        Device.__init__(self, engine, name, device_id)
        self._init_ip()
        self._init_ip6()
        self.static_routes: list[StaticRoute] = []
        self.ipv6_routing: bool | None = None   # None: vendor default (Cisco/Huawei need it switched on)
        self._ra_timer = None
        self.ospf = None            # OspfProcess when "router ospf" is configured
        self.bgp = None             # BgpProcess when "router bgp" is configured
        self.dhcp_server = None     # services.dhcp.DhcpServer (ip dhcp pool ...)
        self.rip = None             # RipProcess when "router rip" is configured
        self.global_router_id: str | None = None   # Junos "routing-options router-id"
        self.acls: dict = {}                        # name -> security.acl.Acl
        self.firewall: list = []                    # RouterOS /ip firewall filter rules (AclRule)
        self.nat = None                             # security.nat.NatEngine
        self.extra: dict = {}                       # vendor raw config (Junos filters/NAT text form)
        self.policies: dict[str, dict] = {}       # Junos policy-options (export to OSPF)
        from ..routing.fhrp import Fhrp
        self.fhrp = Fhrp(self)                      # HSRP / VRRP groups
        self.v6igp: dict = {}                       # "ospf6" / "ripng" -> V6Igp
        from ..services.dhcp6 import Dhcp6Server
        self.dhcp6 = Dhcp6Server(self)
        self.nat64 = None                           # security.nat64.Nat64 when configured
        from ..services.mgmt import Mgmt
        self.mgmt = Mgmt(self)                      # syslog / SNMP agent / NTP client
        self.mgmt.ensure()
        from .phone import CallManager
        self.cme = CallManager(self)                # Cisco CME (telephony-service)
        self.qos: dict = {}                         # policy name -> security.qos.QosPolicy
        self.qos_classes: dict = {}                 # class-map name -> match dict (Cisco) / classifier (Huawei)
        self.qos_global: list = []                  # policies applied to every forwarded packet (RouterOS queues)

    # -- dynamic routing ------------------------------------------------------------
    def dynamic_routes(self) -> list[Route]:
        out: list[Route] = []
        if self.ospf is not None:
            out += [r for r in self.ospf.routes if r.port is not None and r.port.is_up]
        if self.rip is not None:
            out += [r for r in self.rip.routes if r.port is not None and r.port.is_up]
        if self.bgp is not None:
            out += self.bgp.routes          # recursive: resolved through the IGP at lookup time
        return out

    def rip_distance(self) -> int:
        return 100 if self.vendor in ("huawei", "juniper") else 120

    def enable_rip(self, cfg=None):
        from ..routing.rip import RipConfig, RipProcess
        if self.rip is None:
            self.rip = RipProcess(self, cfg or RipConfig())
        elif cfg is not None:
            self.rip.cfg = cfg
        if self.rip.cfg.enabled:
            self.rip.start()
            self.rip.trigger()
        return self.rip

    def disable_rip(self) -> None:
        if self.rip is not None:
            self.rip.stop()
            self.rip = None

    def bgp_distance(self, ebgp: bool) -> int:
        if self.vendor == "juniper":
            return 170
        if self.vendor == "huawei":
            return 255
        return 20 if ebgp else 200

    def enable_bgp(self, cfg=None):
        from ..routing.bgp import BgpConfig, BgpProcess
        if self.bgp is None:
            self.bgp = BgpProcess(self, cfg or BgpConfig())
        elif cfg is not None:
            self.bgp.cfg = cfg
        if self.bgp.cfg.enabled:
            self.bgp.start()
        return self.bgp

    def disable_bgp(self) -> None:
        if self.bgp is not None:
            self.bgp.stop()
            self.bgp = None

    def enable_ospf(self, cfg=None):
        from ..routing.ospf import OspfConfig, OspfProcess
        if self.ospf is None:
            self.ospf = OspfProcess(self, cfg or OspfConfig())
        elif cfg is not None:
            self.ospf.cfg = cfg
        if self.ospf.cfg.enabled:
            if self.ospf.running:
                self.ospf.reconfigure()
            else:
                self.ospf.start()
        elif self.ospf.running:
            self.ospf.stop()
        return self.ospf

    def disable_ospf(self) -> None:
        if self.ospf is not None:
            self.ospf.stop()
            self.ospf = None

    def ospf_distance(self, code: str) -> int:
        """Administrative distance / preference of OSPF routes, per vendor."""
        if self.vendor in ("huawei", "juniper"):
            return 150 if code.startswith("O E") else 10
        return 110

    # -- IPv6 -------------------------------------------------------------------------------
    sends_ra = True

    @property
    def ip6_forwarding(self) -> bool:
        if self.ipv6_routing is not None:
            return self.ipv6_routing
        return self.vendor in ("juniper", "mikrotik")

    def ra_enabled(self, port) -> bool:
        if not self.ip6_forwarding or not any(a.network.prefixlen == 64 for a in port.ipv6):
            return False
        if port.ra is not None:
            return port.ra
        return self.vendor in ("cisco", "mikrotik")      # Huawei: "ipv6 nd ra halt", Junos: needs RA config

    def ipv6_changed(self) -> None:
        """Address/RA configuration changed: advertise now and keep advertising periodically."""
        from ..stack.ipv6 import RA_INTERVAL_MS
        for p in self.ports.values():
            if p.is_up and self.ra_enabled(p):
                self.send_ra(p)
        if self._ra_timer is None:
            def tick():
                self._ra_timer = None
                if any(self.ra_enabled(p) for p in self.ports.values()):
                    self.ipv6_changed()
            self._ra_timer = self.engine.schedule(RA_INTERVAL_MS, tick)

    # -- OSPFv3 / RIPng -------------------------------------------------------------------------
    def extra_groups6(self, port) -> set:
        from ..routing.v6igp import OSPF6_GROUP, RIPNG_GROUP
        out = set()
        for kind, proc in self.v6igp.items():
            if port.name in proc.cfg.interfaces:
                out.add(OSPF6_GROUP if kind == "ospf6" else RIPNG_GROUP)
        if port.name in self.dhcp6.ifaces:
            from ..services.dhcp6 import ALL_DHCP
            out.add(ALL_DHCP)
        return out

    def dynamic_routes6(self) -> list:
        from ..routing.v6igp import distance
        from ..stack.ipv6 import Route6
        out = []
        for kind, proc in self.v6igp.items():
            for r in proc.routes:
                if r.port.is_up:
                    out.append(Route6(r.network, r.code, r.port, r.next_hop, distance(self, kind), r.metric))
        return out

    def deliver6_protocol(self, pkt, port) -> None:
        from ..routing.v6igp import V6IgpMsg
        m = pkt.payload
        if isinstance(m, V6IgpMsg) and m.kind in self.v6igp:
            self.v6igp[m.kind].receive(pkt, port)
            return
        from ..services.dhcp6 import Dhcp6Msg
        if isinstance(m, Dhcp6Msg):
            self.dhcp6.handle(pkt, port)

    def reset_protocols(self) -> None:
        for proc in self.v6igp.values():
            proc.stop()
        self.v6igp = {}
        self.dhcp6.apply_config(None)
        self.nat64 = None
        self.qos, self.qos_classes, self.qos_global = {}, {}, []
        self.mgmt.apply(None)
        self.cme.apply(None)
        self.fhrp.groups.clear()
        self.static_routes6 = []
        self.ipv6_routing = None
        self.disable_ospf()
        self.disable_bgp()
        self.disable_rip()
        self.dhcp_server = None
        self.global_router_id = None
        self.policies = {}
        self.acls = {}
        self.firewall = []
        self.nat = None
        self.extra = {}
        if self.vendor == "juniper" and self.category == "router":
            self.add_loopback("lo0")

    def auto_router_id(self) -> IPv4Address | None:
        """Cisco rule: highest loopback address, else highest active interface address."""
        los = [p.ip.ip for p in self.ports.values() if p.is_loopback and p.ip and p.is_up]
        if los:
            return max(los)
        ips = [p.ip.ip for p in self.ports.values() if p.ip and p.is_up]
        return max(ips) if ips else None

    def routes_changed(self) -> None:
        self.engine.emit("routes_changed", self)

    def console(self, message: str) -> None:
        """Asynchronous console message (%OSPF-5-ADJCHG ...)."""
        self.engine.emit("console", self, message)

    # -- ACLs / firewall / NAT --------------------------------------------------------------
    def nat_engine(self):
        from ..security.nat import NatEngine
        if self.nat is None:
            self.nat = NatEngine(self)
        return self.nat

    def answers_arp_for(self, ip, port) -> bool:
        if self.fhrp.vmac_for(ip, port) is not None:
            return True
        if self.nat is None or port.ip is None or ip not in port.ip.network:
            return False
        return ip in self.nat.global_addresses()

    def arp_mac_for(self, ip, port):
        return self.fhrp.vmac_for(ip, port) or port.mac

    def owns_ip(self, ip) -> bool:
        return super().owns_ip(ip) or (bool(self.fhrp.groups) and self.fhrp.owns(ip))

    def port_for_own_ip(self, ip):
        for g in self.fhrp.groups.values():
            if g.vip == ip and g.state in ("active", "master"):
                return self.ports.get(g.port)
        return super().port_for_own_ip(ip)

    def _acl_reject(self, pkt, port) -> None:
        """Cisco answers a denied packet with ICMP unreachable 'administratively prohibited'."""
        from ..packets import ICMP_DEST_UNREACHABLE
        self.send_icmp_error(pkt, ICMP_DEST_UNREACHABLE, 13, port)

    def _fw(self, chain, pkt, in_port, out_port) -> bool:
        for r in self.firewall:
            x = r.extra
            if x.get("chain") != chain:
                continue
            if x.get("in") and (in_port is None or in_port.name != x["in"]):
                continue
            if x.get("out") and (out_port is None or out_port.name != x["out"]):
                continue
            if r.matches(pkt):
                r.hits += 1
                if r.action == "deny":
                    if x.get("reject") and in_port is not None:
                        self._acl_reject(pkt, in_port)
                    return False
                return True
        return True

    def ingress_filter(self, pkt, port):
        if port.qos_in and port.qos_in in self.qos:
            from ..security.qos import apply
            pkt = apply(self, self.qos[port.qos_in], pkt, port, "in")
            if pkt is None:
                return None
        acl = self.acls.get(port.acl_in) if port.acl_in else None
        if acl is not None:
            allowed, silent = acl.decide(pkt)
            if not allowed:
                if not silent:
                    self._acl_reject(pkt, port)
                return None
        if self.nat is not None:
            pkt = self.nat.inbound(pkt, port)
        if self.firewall and (self.owns_ip(pkt.dst) or pkt.dst.is_multicast):
            if not self._fw("input", pkt, port, None):
                return None
        return pkt

    def egress_filter(self, pkt, in_port, out_port):
        if self.firewall and not self._fw("forward", pkt, in_port, out_port):
            return None
        if self.nat is not None:
            pkt = self.nat.outbound(pkt, in_port, out_port)
            if pkt is None:
                return None
        acl = self.acls.get(out_port.acl_out) if out_port.acl_out else None
        if acl is not None:
            allowed, silent = acl.decide(pkt)
            if not allowed:
                if not silent:
                    self._acl_reject(pkt, in_port)
                return None
        if out_port.qos_out and out_port.qos_out in self.qos:
            from ..security.qos import apply
            pkt = apply(self, self.qos[out_port.qos_out], pkt, out_port, "out")
            if pkt is None:
                return None
        for name in self.qos_global:
            if name in self.qos and pkt is not None:
                from ..security.qos import apply
                pkt = apply(self, self.qos[name], pkt, out_port, "fwd")
        return pkt

    def accepts_mac(self, mac) -> bool:
        from ..routing.ospf import OSPF_MAC
        from ..routing.rip import RIP_MAC
        if self.fhrp.groups and mac in self.fhrp.macs():
            return True
        if self.rip is not None and self.rip.running and mac == RIP_MAC:
            return True
        return self.ospf is not None and self.ospf.running and mac == OSPF_MAC

    def joined_group(self, ip) -> bool:
        from ..routing.ospf import ALL_SPF_ROUTERS
        from ..routing.rip import RIP_GROUP
        from ..routing.fhrp import GROUPS
        if self.fhrp.groups and ip in GROUPS.values():
            return True
        if self.rip is not None and self.rip.running and ip == RIP_GROUP:
            return True
        return self.ospf is not None and self.ospf.running and ip == ALL_SPF_ROUTERS

    def deliver_protocol(self, pkt, port) -> None:
        from ..routing.fhrp import HSRP_PORT, PROTO_VRRP
        from ..tunnel import tunnel_receive
        if tunnel_receive(self, pkt):
            return
        if pkt.protocol == PROTO_VRRP or (isinstance(pkt.payload, UdpDatagram) and pkt.payload.dport == HSRP_PORT):
            self.fhrp.receive(pkt, port)
            return
        if pkt.protocol == PROTO_OSPF and self.ospf is not None:
            self.ospf.receive(pkt, port)
        elif pkt.protocol == PROTO_TCP and isinstance(pkt.payload, TcpSegment) and self.bgp is not None \
                and 179 in (pkt.payload.sport, pkt.payload.dport):
            self.bgp.receive(pkt, port)
        elif isinstance(pkt.payload, UdpDatagram) and pkt.payload.dport == 520:
            if self.rip is not None:
                self.rip.receive(pkt, port)
        elif isinstance(pkt.payload, UdpDatagram) and pkt.payload.dport == 67:
            self._dhcp_in(pkt, port)
        else:
            super().deliver_protocol(pkt, port)

    def dhcp(self, create: bool = True):
        from ..services.dhcp import DhcpServer
        if self.dhcp_server is None and create:
            self.dhcp_server = DhcpServer(self)
        return self.dhcp_server

    def _dhcp_in(self, pkt, port) -> None:
        """DHCP server and relay agent (RFC 1542)."""
        from dataclasses import replace
        from ..services.dhcp import SERVER_PORT, ZERO, DhcpMessage
        m = pkt.payload.data
        if not isinstance(m, DhcpMessage):
            return
        if m.kind in ("OFFER", "ACK", "NAK"):
            down = next((p for p in self.ports.values() if p.ip is not None and p.ip.ip == m.giaddr), None)
            if down is not None:
                self.broadcast_udp(down, SERVER_PORT, 68, m)       # relay: back to the client segment
            return
        if port.helpers and m.giaddr == ZERO and port.ip is not None:
            relayed = replace(m, giaddr=port.ip.ip)
            for helper in port.helpers:
                self.send_udp(helper, SERVER_PORT, SERVER_PORT, relayed, src=port.ip.ip)
            return
        if self.dhcp_server is not None:
            self.dhcp_server.handle(pkt, port)

    def receive(self, frame: EthernetFrame, port: Port) -> None:
        subs = port.subinterfaces
        if frame.vlan is not None:
            port = next((s for s in subs if s.vlan == frame.vlan and not s.native), None)
            if port is None:
                return  # no subinterface for this VLAN
        else:
            port = next((s for s in subs if s.native), port)
        self.handle_l3_frame(frame, port)

    def link_state_changed(self, port: Port) -> None:
        super().link_state_changed(port)
        if getattr(self, "mgmt", None) is not None:
            self.mgmt.link_event(port)
        for sub in port.subinterfaces:
            super().link_state_changed(sub)
        if getattr(self, "fhrp", None) is not None and self.fhrp.groups:
            self.engine.schedule(0.0, self.fhrp.tick)
        for proc in getattr(self, "v6igp", {}).values():
            proc.kick()
            if not port.is_up:
                proc.neighbors = {k: v for k, v in proc.neighbors.items() if k[0] != port.name}
                proc._recompute_domain()
        if self.ospf is not None:
            self.ospf.link_changed(port)

    # -- routing table ------------------------------------------------------
    def connected_routes(self) -> list[Route]:
        out = []
        for p in self.ports.values():
            if p.ip is not None and p.is_up:
                out.append(Route(p.ip.network, "C", p))
                if p.ip.network.prefixlen < 32:
                    out.append(Route(IPv4Network(f"{p.ip.ip}/32"), "L", p))
        return out

    def _resolve(self, dst: IPv4Address, depth: int = 0, exclude=None, no_bgp: bool = False):
        """Resolve *dst* to (exit port, next hop IP) using longest-prefix match."""
        if depth > 6:
            return None
        candidates: list[tuple[int, int, Route]] = []
        for r in self.connected_routes():
            if dst in r.network:
                candidates.append((r.network.prefixlen, 0, r))
        for s in self.static_routes:
            if s is exclude or dst not in s.network:
                continue
            candidates.append((s.network.prefixlen, s.distance,
                               Route(s.network, "S", None, s.next_hop, s.distance)))
            candidates[-1][2].static = s  # type: ignore[attr-defined]
        for r in self.dynamic_routes():
            if dst in r.network and not (no_bgp and r.code == "B"):
                candidates.append((r.network.prefixlen, r.distance, r))
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for _, _, r in candidates:
            if r.code in ("C", "L"):
                return r.port, dst
            if r.code == "B":
                res = self._resolve(r.next_hop, depth + 1, no_bgp=True)
                if res is not None:
                    return res
                continue
            if r.code not in ("S",):
                return r.port, r.next_hop
            s: StaticRoute = r.static  # type: ignore[attr-defined]
            if s.interface:
                p = self.get_port(s.interface)
                if p is None or not p.is_up:
                    continue
                return p, (s.next_hop or dst)
            res = self._resolve(s.next_hop, depth + 1, exclude=s)
            if res is not None:
                return res[0], (s.next_hop if res[1] == s.next_hop else res[1])
        return None

    def route_lookup(self, dst: IPv4Address):
        return self._resolve(dst)

    def routing_table(self) -> list[Route]:
        """Active routes (what `show ip route` prints), sorted by network."""
        routes = self.connected_routes()
        for s in self.static_routes:
            if s.interface:
                p = self.get_port(s.interface)
                if p is None or not p.is_up:
                    continue
                routes.append(Route(s.network, "S", p, s.next_hop, s.distance))
            else:
                res = self._resolve(s.next_hop, 1, exclude=s)
                if res is None:
                    continue
                routes.append(Route(s.network, "S", res[0], s.next_hop, s.distance))
        for r in self.dynamic_routes():
            if r.code == "B":
                res = self._resolve(r.next_hop, 1, no_bgp=True)
                if res is None:
                    continue
                r = Route(r.network, r.code, res[0], r.next_hop, r.distance, r.metric)
            routes.append(r)
        # Keep only the best distance per prefix.
        best: dict[IPv4Network, Route] = {}
        extra: list[Route] = []
        for r in routes:
            cur = best.get(r.network)
            if cur is None or r.distance < cur.distance:
                best[r.network] = r
            elif r.distance == cur.distance and r.code == cur.code and r.code not in ("C", "L"):
                extra.append(r)
        out = list(best.values()) + [r for r in extra if best[r.network].distance == r.distance]
        return sorted(out, key=lambda r: (int(r.network.network_address), r.network.prefixlen,
                                          r.code != "C"))

    def gateway_of_last_resort(self) -> Route | None:
        for r in self.routing_table():
            if r.network.prefixlen == 0:
                return r
        return None

    def add_static_route(self, network: IPv4Network, next_hop: IPv4Address | None,
                         interface: str | None = None, distance: int = 1) -> StaticRoute:
        for s in self.static_routes:
            if s.network == network and s.next_hop == next_hop and s.interface == interface:
                s.distance = distance
                return s
        s = StaticRoute(network, next_hop, interface, distance)
        self.static_routes.append(s)
        if self.ospf is not None:
            self.ospf.originate()
        return s

    def remove_static_route(self, network: IPv4Network, next_hop: IPv4Address | None = None,
                            interface: str | None = None) -> int:
        before = len(self.static_routes)
        self.static_routes = [
            s for s in self.static_routes
            if not (s.network == network
                    and (next_hop is None or s.next_hop == next_hop)
                    and (interface is None or s.interface == interface))]
        return before - len(self.static_routes)

    # -- persistence ---------------------------------------------------------
    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["static_routes"] = [s.to_dict() for s in self.static_routes]
        if self.ospf is not None:
            cfg["ospf"] = self.ospf.cfg.to_dict()
        if self.bgp is not None:
            cfg["bgp"] = self.bgp.cfg.to_dict()
        if self.rip is not None:
            cfg["rip"] = self.rip.cfg.to_dict()
        if self.static_routes6:
            cfg["static_routes6"] = [s.to_dict() for s in self.static_routes6]
        if self.ipv6_routing is not None:
            cfg["ipv6_routing"] = self.ipv6_routing
        if self.dhcp_server is not None:
            cfg["dhcp_server"] = self.dhcp_server.cfg.to_dict()
        if self.global_router_id:
            cfg["router_id"] = self.global_router_id
        if self.policies:
            cfg["policies"] = {k: dict(v) for k, v in self.policies.items()}
        if self.acls:
            cfg["acls"] = {k: a.to_dict() for k, a in self.acls.items()}
        if self.firewall:
            cfg["firewall"] = [r.to_dict() for r in self.firewall]
        if self.nat is not None and not self.nat.cfg.empty:
            cfg["nat"] = self.nat.cfg.to_dict()
        if self.extra:
            import copy
            cfg["extra"] = copy.deepcopy(self.extra)
        if self.fhrp.groups:
            cfg["fhrp"] = [g.to_dict() for g in self.fhrp.groups.values()]
        if self.v6igp:
            cfg["v6igp"] = [p.cfg.to_dict() for p in self.v6igp.values()]
        d6 = self.dhcp6.to_config()
        if d6:
            cfg["dhcp6"] = d6
        if self.nat64 is not None:
            cfg["nat64"] = self.nat64.to_dict()
        mg = self.mgmt.to_dict()
        if mg:
            cfg["mgmt"] = mg
        cm = self.cme.to_dict()
        if cm:
            cfg["cme"] = cm
        if self.qos or self.qos_classes:
            cfg["qos"] = {"policies": [p.to_dict() for p in self.qos.values()],
                          "classes": {k: dict(v) for k, v in self.qos_classes.items()}}
            if self.qos_global:
                cfg["qos"]["global"] = list(self.qos_global)
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.static_routes = [StaticRoute.from_dict(d) for d in cfg.get("static_routes", [])]
        self.global_router_id = cfg.get("router_id")
        self.policies = {k: dict(v) for k, v in cfg.get("policies", {}).items()}
        from ..security.acl import Acl, AclRule
        from ..security.nat import NatConfig
        self.acls = {k: Acl.from_dict(v) for k, v in cfg.get("acls", {}).items()}
        self.firewall = [AclRule.from_dict(r) for r in cfg.get("firewall", [])]
        import copy
        self.extra = copy.deepcopy(cfg.get("extra", {}))
        if cfg.get("nat"):
            self.nat_engine().cfg = NatConfig.from_dict(cfg["nat"])
            self.nat.table = []
        else:
            self.nat = None
        if self.ospf is not None:
            self.ospf.reset_router_id()
        if cfg.get("dhcp_server") is not None:
            from ..services.dhcp import DhcpServerConfig
            self.dhcp().cfg = DhcpServerConfig.from_dict(cfg["dhcp_server"])
            self.dhcp_server.bindings.clear()
        else:
            self.dhcp_server = None
        from ..routing.v6igp import V6IgpConfig, make_process
        for proc in self.v6igp.values():
            proc.stop()
        self.v6igp = {}
        for d in cfg.get("v6igp", []):
            c = V6IgpConfig.from_dict(d)
            self.v6igp[c.kind] = make_process(self, c)
            self.v6igp[c.kind].kick()
        self.dhcp6.apply_config(cfg.get("dhcp6"))
        from ..security.nat64 import Nat64
        self.nat64 = Nat64.from_dict(cfg["nat64"]) if cfg.get("nat64") else None
        from ..security.qos import QosPolicy
        q = cfg.get("qos") or {}
        self.qos = {d["name"]: QosPolicy.from_dict(d) for d in q.get("policies", [])}
        self.qos_classes = {k: dict(v) for k, v in q.get("classes", {}).items()}
        self.qos_global = list(q.get("global", []))
        self.mgmt.apply(cfg.get("mgmt"))
        self.cme.apply(cfg.get("cme"))
        from ..routing.fhrp import FhrpGroup
        self.fhrp.groups.clear()
        for d in cfg.get("fhrp", []):
            g = FhrpGroup.from_dict(d)
            g.since = self.engine.now
            self.fhrp.groups[(g.port, g.proto, g.group)] = g
        self.fhrp._arm()
        self.static_routes6 = [StaticRoute6.from_dict(d) for d in cfg.get("static_routes6", [])]
        self.ipv6_routing = cfg.get("ipv6_routing")
        if any(p.ipv6 for p in self.ports.values()):
            self.engine.schedule(200.0, self.ipv6_changed)
        if cfg.get("rip") is not None:
            from ..routing.rip import RipConfig
            self.disable_rip()
            self.enable_rip(RipConfig.from_dict(cfg["rip"]))
        else:
            self.disable_rip()
        if cfg.get("bgp") is not None:
            from ..routing.bgp import BgpConfig
            self.disable_bgp()
            self.enable_bgp(BgpConfig.from_dict(cfg["bgp"]))
        else:
            self.disable_bgp()
        if cfg.get("ospf") is not None:
            from ..routing.ospf import OspfConfig
            self.enable_ospf(OspfConfig.from_dict(cfg["ospf"]))
        else:
            self.disable_ospf()
