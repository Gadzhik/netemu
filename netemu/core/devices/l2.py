"""Layer-2 devices: hub and learning switch."""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..addresses import MacAddress
from ..packets import EthernetFrame
from ..port import Port
from .base import Device
from .router import Router

MAC_AGING_MS = 300_000.0  # 300 s, the common default on Cisco/Huawei/Juniper


class Hub(Device):
    category = "hub"
    model = "Hub-PT"
    default_hostname = "Hub"

    def __init__(self, engine, name, device_id=None, ports: int = 8):
        super().__init__(engine, name, device_id)
        for i in range(ports):
            self.add_port(f"Port{i}", f"Port{i}")

    def receive(self, frame: EthernetFrame, port: Port) -> None:
        for p in self.ports.values():
            if p is not port and p.is_up:
                p.send(frame)


@dataclass(frozen=True)
class LacpPdu:
    protocol: str            # LACP | PAgP
    mode: str
    group: int

    @property
    def label(self) -> str:
        return self.protocol

    def layer_lines(self) -> list[str]:
        return [f"Protocol: {self.protocol}", f"Mode: {self.mode}", f"Channel group: {self.group}",
                "Actor state: Activity, Aggregation, Synchronization" if self.mode in ("active", "desirable", "on")
                else "Actor state: Aggregation (passive)"]


@dataclass
class MacEntry:
    mac: MacAddress
    port: Port
    learned_at: float
    vlan: int = 1


class Switch(Router):
    """Learning 802.1Q switch; with VLAN interfaces (SVIs) it routes like a router (L3 switch)."""

    category = "switch"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        # None: vendor default (Cisco "ip routing" and EXOS "enable ipforwarding" are off by default)
        self.dhcp_snooping: dict = {"enabled": False, "vlans": []}
        self.snoop_bindings: dict = {}           # mac -> {ip, vlan, port, lease}
        self.ip_routing: bool | None = None
        self.default_gateway = None              # Cisco "ip default-gateway" (used while not routing)
        self.mac_table: dict[tuple[int, MacAddress], MacEntry] = {}
        self.aging = MAC_AGING_MS
        self.vlans: dict[int, str] = {1: "default"}   # VLAN database: id -> name
        # RouterOS bridge: VLAN tags are ignored until vlan-filtering is enabled
        self.vlan_filtering = True
        self.bridge_vlans: list[dict] = []   # RouterOS /interface bridge vlan table
        from .stp import Stp
        self.stp = Stp(self)

    # -- VLAN database ---------------------------------------------------------
    def add_vlan(self, vid: int, name: str | None = None) -> None:
        if vid not in self.vlans or name:
            self.vlans[vid] = name or f"VLAN{vid:04d}"

    def remove_vlan(self, vid: int) -> None:
        if vid != 1:
            self.vlans.pop(vid, None)
            for key in [k for k in self.mac_table if k[0] == vid]:
                del self.mac_table[key]

    def vlan_ports(self, vid: int) -> list[Port]:
        return [p for p in self.l2_ports() if p.vlan_out(vid)[0]]

    def l2_ports(self) -> list[Port]:
        """Ports switching sees: physical ports outside bundles, plus the bundles themselves."""
        out = []
        for p in self.physical_ports():
            if p.channel_group is not None and self.channel_for_group(p.channel_group) is not None:
                continue
            out.append(p)
        return out + self.channels()

    # -- layer 3 -----------------------------------------------------------------------
    @property
    def ip_forwarding(self) -> bool:
        if self.ip_routing is not None:
            return self.ip_routing
        return self.vendor in ("huawei", "juniper", "mikrotik")      # EXOS: "enable ipforwarding"

    def svi_line_up(self, svi) -> bool:
        if not self.vlan_filtering:
            return any(p.is_up for p in self.physical_ports())
        if svi.vlan not in self.vlans:
            return False
        return any(p.is_up and self.forwarding(p) and p.vlan_out(svi.vlan)[0] for p in self.l2_ports())

    def svi_send(self, svi, frame: EthernetFrame) -> bool:
        vlan = svi.vlan if self.vlan_filtering else (frame.vlan or 1)
        entry = None if frame.dst.is_broadcast or frame.dst.is_multicast else self.lookup(frame.dst, vlan)
        targets = [entry.port] if entry is not None else self.l2_ports()
        sent = False
        for p in targets:
            if not p.is_up or not self.forwarding(p):
                continue
            if not self.vlan_filtering:
                sent = p.send(replace(frame, vlan=None if svi.vlan == 1 else svi.vlan)) or sent
                continue
            member, tag = p.vlan_out(vlan)
            if member:
                sent = p.send(replace(frame, vlan=tag)) or sent
        return sent

    def _to_l3(self, frame: EthernetFrame, vlan: int) -> bool:
        """Hand a frame to the routing engine when it is for one of our VLAN interfaces.

        Returns True when the frame was addressed to us (unicast) and must not be switched further."""
        svi = self.svi_for_vlan(vlan)
        if svi is None or not svi.is_up:
            return False
        if frame.dst == svi.mac or (self.fhrp.groups and not frame.dst.is_multicast and frame.dst in self.fhrp.macs()):
            self.handle_l3_frame(replace(frame, vlan=None), svi)
            return True
        if frame.dst.is_broadcast or frame.dst.is_multicast:
            self.handle_l3_frame(replace(frame, dst=frame.dst, vlan=None), svi)
        return False

    def route_lookup(self, dst):
        res = super().route_lookup(dst)
        if res is None and not self.ip_forwarding and self.default_gateway is not None:
            gw = super().route_lookup(self.default_gateway)
            if gw is not None:
                return gw[0], self.default_gateway
        return res

    def forwarding(self, port: Port) -> bool:
        """False while STP keeps the port blocking/listening/learning."""
        return self.stp.forwarding(port)

    # -- forwarding ------------------------------------------------------------
    def receive(self, frame: EthernetFrame, port: Port) -> None:
        from .stp import BPDU_MAC
        if frame.dst == BPDU_MAC and self.stp.enabled:
            return                                     # BPDUs are consumed by the STP process
        if frame.ethertype == "LACP":
            return                                     # LACP/PAgP negotiation frames stop here
        if port.dot1x and not port.dot1x_ok:              # 802.1X: only EAPOL until authorized
            if frame.ethertype == "EAPOL":
                from ..services.aaa import dot1x_receive
                dot1x_receive(self, frame, port)
            else:
                port.drops += 1
            return
        if port.channel_group is not None:
            po = self.channel_for_group(port.channel_group)
            if po is not None:
                if not po.member_bundled(port):
                    port.drops += 1                    # suspended member: no forwarding
                    return
                port = po
        if not self.forwarding(port):
            return
        if not self.vlan_filtering:
            self._transparent(frame, port)
            return
        vlan = port.vlan_in(frame)
        if vlan is None or vlan not in self.vlans:
            port.drops += 1
            return
        if port.psec is not None and not self._port_security(frame, port):
            return
        if self.dhcp_snooping["enabled"] and not self._dhcp_snoop(frame, port, vlan):
            return
        if self.dhcp_snooping["enabled"] and self.dhcp_snooping.get("option82", True):
            frame = self._insert_option82(frame, port, vlan)
        now = self.engine.now
        if not frame.src.is_broadcast:
            self.mac_table[(vlan, frame.src)] = MacEntry(frame.src, port, now, vlan)
        if self.svis() and self._to_l3(frame, vlan):
            return
        entry = None if frame.dst.is_broadcast else self.lookup(frame.dst, vlan)
        targets = [entry.port] if entry is not None else self.l2_ports()
        for p in targets:
            if p is port or not p.is_up or not self.forwarding(p):
                continue
            member, tag = p.vlan_out(vlan)
            if member:
                out = frame
                if p.qos_out and p.qos_out in self.qos:
                    out = self._switch_qos(frame, p)
                    if out is None:
                        continue
                p.send(out if out.vlan == tag else replace(out, vlan=tag))

    def _switch_qos(self, frame, port):
        from ..packets import IPv4Packet
        from ..security.qos import apply
        if not isinstance(frame.payload, IPv4Packet):
            return frame
        pkt = apply(self, self.qos[port.qos_out], frame.payload, port, "out")
        return None if pkt is None else (frame if pkt is frame.payload else replace(frame, payload=pkt))

    # -- port security / DHCP snooping ------------------------------------------------------------
    def _port_security(self, frame: EthernetFrame, port: Port) -> bool:
        ps = port.psec
        macs = ps.setdefault("macs", [])
        src = frame.src.cisco()
        if src in macs:
            return True
        if len(macs) < int(ps.get("max", 1)):
            macs.append(src)
            if not ps.get("sticky"):
                ps.setdefault("dynamic", []).append(src)
            return True
        ps["violations"] = ps.get("violations", 0) + 1
        ps["last"] = src
        mode = ps.get("violation", "shutdown")
        if mode == "shutdown":
            port.err_disabled = True
            port.set_admin(False)
            self.console(f"%PM-4-ERR_DISABLE: psecure-violation error detected on {port.short}, putting {port.short} "
                         "in err-disable state")
            self.console(f"%PORT_SECURITY-2-PSECURE_VIOLATION: Security violation occurred, caused by MAC address "
                         f"{src} on port {port.name}.")
        elif mode == "restrict":
            self.console(f"%PORT_SECURITY-2-PSECURE_VIOLATION: Security violation occurred, caused by MAC address "
                         f"{src} on port {port.name}.")
        return False

    def _insert_option82(self, frame, port, vlan):
        """Snooping switches add relay agent information to client requests (IOS default)."""
        from ..packets import IPv4Packet, UdpDatagram
        from ..services.dhcp import DhcpMessage
        pkt = frame.payload
        if not isinstance(pkt, IPv4Packet) or not isinstance(pkt.payload, UdpDatagram) or pkt.payload.dport != 67:
            return frame
        m = pkt.payload.data
        if not isinstance(m, DhcpMessage) or m.option82 or m.kind not in ("DISCOVER", "REQUEST"):
            return frame
        m2 = replace(m, option82=(f"vlan{vlan}-{port.short}", self.hostname))
        return replace(frame, payload=replace(pkt, payload=replace(pkt.payload, data=m2)))

    def _dhcp_snoop(self, frame: EthernetFrame, port: Port, vlan: int) -> bool:
        from ..packets import IPv4Packet, UdpDatagram
        vlans = self.dhcp_snooping.get("vlans") or []
        if vlans and vlan not in vlans:
            return True
        pkt = frame.payload
        if not isinstance(pkt, IPv4Packet) or not isinstance(pkt.payload, UdpDatagram):
            return True
        udp = pkt.payload
        if udp.sport == 67 and udp.dport == 68:                     # server -> client
            if not port.dhcp_trust:
                port.drops += 1
                self.dhcp_snooping["dropped"] = self.dhcp_snooping.get("dropped", 0) + 1
                self.console(f"%DHCP_SNOOPING-5-DHCP_SNOOPING_UNTRUSTED_PORT: DHCP_SNOOPING drop message on "
                             f"untrusted port, message type: DHCPOFFER, MAC sa: {frame.src.cisco()}")
                return False
            m = udp.data
            if getattr(m, "kind", None) == "ACK":
                mac = m.chaddr.cisco() if hasattr(m, "chaddr") else str(getattr(m, "mac", ""))
                entry = self.lookup(getattr(m, "chaddr", getattr(m, "mac", None)), vlan) if hasattr(m, "chaddr") else None
                self.snoop_bindings[mac] = {"ip": str(m.yiaddr), "vlan": vlan,
                                            "port": entry.port.name if entry else "", "lease": getattr(m, "lease", 86400)}
        return True

    def _transparent(self, frame: EthernetFrame, port: Port) -> None:
        """Forward without looking at VLANs (frames keep whatever tag they have)."""
        key = frame.vlan or 1
        if not frame.src.is_broadcast:
            self.mac_table[(key, frame.src)] = MacEntry(frame.src, port, self.engine.now, key)
        if self.svis() and self._to_l3(frame, key):
            return
        entry = None if frame.dst.is_broadcast else self.lookup(frame.dst, key)
        targets = [entry.port] if entry is not None else self.l2_ports()
        for p in targets:
            if p is not port and p.is_up and self.forwarding(p):
                p.send(frame)

    def lookup(self, mac: MacAddress, vlan: int = 1) -> MacEntry | None:
        e = self.mac_table.get((vlan, mac))
        if e and self.engine.now - e.learned_at > self.aging:
            del self.mac_table[(vlan, mac)]
            return None
        return e

    def mac_entries(self) -> list[MacEntry]:
        for vlan, mac in list(self.mac_table):
            self.lookup(mac, vlan)
        names = list(self.ports)
        return sorted(self.mac_table.values(),
                      key=lambda e: (e.vlan, names.index(e.port.name), e.mac.value))

    def to_config(self) -> dict:
        cfg = super().to_config()
        if not cfg.get("static_routes"):
            cfg.pop("static_routes", None)
        if self.ip_routing is not None:
            cfg["ip_routing"] = self.ip_routing
        if self.dhcp_snooping["enabled"] or self.dhcp_snooping.get("vlans"):
            cfg["dhcp_snooping"] = {"enabled": self.dhcp_snooping["enabled"],
                                    "vlans": sorted(self.dhcp_snooping.get("vlans") or [])}
            if not self.dhcp_snooping.get("option82", True):
                cfg["dhcp_snooping"]["option82"] = False
        if self.default_gateway is not None:
            cfg["default_gateway"] = str(self.default_gateway)
        if self.vlans != {1: "default"}:
            cfg["vlans"] = {str(k): v for k, v in sorted(self.vlans.items())}
        from .stp import default_enabled, default_mode
        stp = self.stp.to_config()
        if stp != {"enabled": default_enabled(self.vendor), "mode": default_mode(self.vendor), "priority": 32768,
                   "costs": {}, "edge": []}:
            cfg["stp"] = stp
        if self.vendor == "mikrotik":
            cfg["vlan_filtering"] = self.vlan_filtering
            if self.bridge_vlans:
                cfg["bridge_vlans"] = [dict(e) for e in self.bridge_vlans]
        return cfg

    def apply_config(self, cfg: dict) -> None:
        self.vlans = {1: "Default" if self.vendor == "extreme" else "default"}
        self.vlans.update({int(k): v for k, v in cfg.get("vlans", {}).items()})
        if self.vendor == "mikrotik":
            self.vlan_filtering = bool(cfg.get("vlan_filtering", False))
            self.bridge_vlans = [dict(e) for e in cfg.get("bridge_vlans", [])]
        self.stp.apply_config(cfg.get("stp", {}))
        self.ip_routing = cfg.get("ip_routing")
        ds = cfg.get("dhcp_snooping") or {}
        self.dhcp_snooping = {"enabled": bool(ds.get("enabled")), "vlans": list(ds.get("vlans", [])),
                              "option82": ds.get("option82", True)}
        from ..addresses import IPv4Address
        self.default_gateway = IPv4Address(cfg["default_gateway"]) if cfg.get("default_gateway") else None
        super().apply_config(cfg)

    def clear_mac_table(self) -> None:
        self.mac_table.clear()

    def _send_lacpdu(self, port: Port) -> None:
        """One LACP/PAgP frame per negotiation, so the simulation shows the bundle forming."""
        from ..addresses import MacAddress
        from ..port import channel_protocol
        from ..packets import EthernetFrame
        dst = MacAddress(0x0180C2000002) if channel_protocol(port.channel_mode) == "LACP" else MacAddress(0x01000CCCCCCC)
        port.send(EthernetFrame(port.mac, dst, "LACP", LacpPdu(channel_protocol(port.channel_mode),
                                                               port.channel_mode, port.channel_group)))

    def reset_protocols(self) -> None:
        super().reset_protocols()
        self.ip_routing = None
        self.default_gateway = None

    def link_state_changed(self, port: Port) -> None:
        if port.is_svi:
            Router.link_state_changed(self, port)
            return
        if getattr(self, "mgmt", None) is not None:
            self.mgmt.link_event(port)
        if port.channel_group is not None or port.is_channel:
            po = port if port.is_channel else self.channel_for_group(port.channel_group)
            if po is not None:
                for key in [k for k, e in self.mac_table.items() if e.port is po or e.port is port]:
                    del self.mac_table[key]
                if port.is_up and port.channel_mode not in (None, "on"):
                    self._send_lacpdu(port)
        if not port.is_up:
            port.dot1x_ok = False                          # 802.1X: re-authenticate after a link flap
            for key in [k for k, e in self.mac_table.items() if e.port is port]:
                del self.mac_table[key]
        for svi in self.svis():
            Router.link_state_changed(self, svi)
        self.stp.schedule()
        peer = port.peer
        if peer is not None and peer.device.category == "switch" and hasattr(peer.device, "stp"):
            peer.device.stp.schedule()
