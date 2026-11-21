"""IPv6: addressing (global + EUI-64 link-local), NDP, ICMPv6 echo, SLAAC, static routing."""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv6Address, IPv6Interface, IPv6Network
from typing import TYPE_CHECKING, Any

from ..addresses import MacAddress
from .icmp import PingSession, TracerouteSession

if TYPE_CHECKING:
    from ..port import Port

ETH_IPV6 = "IPv6"
ALL_NODES = IPv6Address("ff02::1")
ALL_ROUTERS = IPv6Address("ff02::2")
NDP_TIMEOUT_MS = 2000.0
RA_INTERVAL_MS = 200_000.0

ECHO_REQUEST, ECHO_REPLY = 128, 129
RS, RA, NS, NA = 133, 134, 135, 136
DEST_UNREACH, TIME_EXCEEDED = 1, 3
ICMP6_NAMES = {ECHO_REQUEST: "Echo Request", ECHO_REPLY: "Echo Reply", RS: "Router Solicitation",
               RA: "Router Advertisement", NS: "Neighbor Solicitation", NA: "Neighbor Advertisement",
               DEST_UNREACH: "Destination Unreachable", TIME_EXCEEDED: "Time Exceeded"}


def eui64_link_local(mac: MacAddress) -> IPv6Interface:
    v = mac.value
    b = v.to_bytes(6, "big")
    iid = bytes([b[0] ^ 0x02, b[1], b[2], 0xFF, 0xFE, b[3], b[4], b[5]])
    return IPv6Interface(IPv6Address(b"\xfe\x80" + b"\x00" * 6 + iid).compressed + "/64")


def eui64_address(prefix: IPv6Network, mac: MacAddress) -> IPv6Interface:
    ll = eui64_link_local(mac)
    iid = int(ll.ip) & ((1 << 64) - 1)
    return IPv6Interface(f"{IPv6Address(int(prefix.network_address) | iid)}/{prefix.prefixlen}")


def solicited_node(ip: IPv6Address) -> IPv6Address:
    return IPv6Address(int(IPv6Address("ff02::1:ff00:0")) | (int(ip) & 0xFFFFFF))


def mcast_mac(ip: IPv6Address) -> MacAddress:
    return MacAddress(0x333300000000 | (int(ip) & 0xFFFFFFFF))


@dataclass(frozen=True)
class Icmp6Packet:
    type: int
    code: int = 0
    ident: int = 0
    seq: int = 0
    size: int = 32
    target: IPv6Address | None = None          # NS/NA target
    lladdr: Any = None                         # source/target link-layer address option
    prefixes: tuple = ()                       # RA prefix information (str prefixes)
    router_lifetime: int = 1800
    original: Any = None

    @property
    def name(self) -> str:
        return ICMP6_NAMES.get(self.type, f"type {self.type}")

    @property
    def is_ndp(self) -> bool:
        return self.type in (RS, RA, NS, NA)

    def layer_lines(self) -> list[str]:
        lines = [f"Type: {self.type} ({self.name})", f"Code: {self.code}"]
        if self.type in (ECHO_REQUEST, ECHO_REPLY):
            lines += [f"Identifier: 0x{self.ident:04x}", f"Sequence: {self.seq}"]
        if self.target is not None:
            lines.append(f"Target address: {self.target}")
        if self.lladdr is not None:
            lines.append(f"Link-layer address: {self.lladdr}")
        for p in self.prefixes:
            lines.append(f"Prefix information: {p} (autonomous)")
        return lines


@dataclass(frozen=True)
class IPv6Packet:
    src: IPv6Address
    dst: IPv6Address
    hlim: int
    payload: Any
    next_header: int = 58

    @property
    def protocol(self) -> int:
        return self.next_header

    @property
    def ttl(self) -> int:
        return self.hlim

    def layer_lines(self) -> list[str]:
        return [f"Source: {self.src}", f"Destination: {self.dst}", f"Hop limit: {self.hlim}",
                f"Next header: {self.next_header} ({ {58: 'ICMPv6', 89: 'OSPFv3', 17: 'UDP'}.get(self.next_header, '?')})"]


@dataclass
class Route6:
    network: IPv6Network
    code: str
    port: "Port | None" = None
    next_hop: IPv6Address | None = None
    distance: int = 0
    metric: int = 0


@dataclass
class StaticRoute6:
    network: IPv6Network
    next_hop: IPv6Address | None = None
    interface: str | None = None
    distance: int = 1

    def to_dict(self) -> dict:
        return {"network": str(self.network), "next_hop": str(self.next_hop) if self.next_hop else None,
                "interface": self.interface, "distance": self.distance}

    @classmethod
    def from_dict(cls, d: dict) -> "StaticRoute6":
        return cls(IPv6Network(d["network"]), IPv6Address(d["next_hop"]) if d.get("next_hop") else None,
                   d.get("interface"), int(d.get("distance", 1)))


@dataclass
class Neighbor6:
    ip: IPv6Address
    mac: MacAddress
    port: "Port"
    learned: float
    router: bool = False


class Ip6Stack:
    """Mixin for hosts and routers. Ports carry ``ipv6`` (list of IPv6Interface) and ``ipv6_enabled``."""

    ip6_forwarding = False
    sends_ra = False

    def _init_ip6(self) -> None:
        self.nd_cache: dict[tuple[str, IPv6Address], Neighbor6] = {}
        self._nd_pending: dict[tuple[str, IPv6Address], list] = {}
        self._icmp6_handlers: dict[int, Any] = {}
        self.static_routes6: list[StaticRoute6] = []
        self.ra_routers: dict[str, tuple[IPv6Address, float]] = {}     # port -> (router LL, expires)

    # -- addressing ------------------------------------------------------------------------------
    @staticmethod
    def link_local(port) -> IPv6Interface | None:
        if not getattr(port, "ipv6_enabled", False) and not getattr(port, "ipv6", None):
            return None
        return eui64_link_local(port.mac)

    def addrs6(self, port) -> list[IPv6Interface]:
        out = list(getattr(port, "ipv6", []) or [])
        ll = self.link_local(port)
        if ll is not None:
            out.append(ll)
        return out

    def owns_ip6(self, ip: IPv6Address) -> bool:
        return any(a.ip == ip for p in self.ports.values() for a in self.addrs6(p))

    def port_for_ip6(self, ip: IPv6Address):
        for p in self.ports.values():
            if any(a.ip == ip for a in self.addrs6(p)):
                return p
        return None

    def _groups(self, port) -> set[IPv6Address]:
        g = {ALL_NODES}
        if self.ip6_forwarding:
            g.add(ALL_ROUTERS)
        for a in self.addrs6(port):
            g.add(solicited_node(a.ip))
        g |= self.extra_groups6(port)
        return g

    def extra_groups6(self, port) -> set:
        return set()

    def dynamic_routes6(self) -> list:
        return []

    def deliver6_protocol(self, pkt, port) -> None:
        """Non-ICMPv6 payloads (OSPFv3, RIPng) — routers override."""

    # -- routing ------------------------------------------------------------------------------------
    def connected_routes6(self) -> list[Route6]:
        out = []
        for p in self.ports.values():
            if not p.is_up:
                continue
            for a in getattr(p, "ipv6", []) or []:
                out.append(Route6(a.network, "C", p))
                out.append(Route6(IPv6Network(f"{a.ip}/128"), "L", p))
        return out

    def route_lookup6(self, dst: IPv6Address):
        """(exit port, next hop) for *dst*; link-local destinations need an explicit port."""
        cands = []
        for r in self.routing_table6():
            if dst in r.network:
                cands.append((r.network.prefixlen, -r.distance, r))
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
        r = cands[0][2]
        if r.code in ("C", "L"):
            return r.port, dst
        return r.port, r.next_hop

    def routing_table6(self) -> list[Route6]:
        routes = self.connected_routes6()
        for s in self.static_routes6:
            port = self.get_port(s.interface) if s.interface else None
            nh = s.next_hop
            if port is None and nh is not None:
                res = None
                for r in self.connected_routes6():
                    if nh in r.network and r.code == "C":
                        res = r.port
                if res is None and nh.is_link_local:
                    continue
                port = res
            if port is None or not port.is_up:
                continue
            routes.append(Route6(s.network, "S", port, nh, s.distance))
        routes += self.dynamic_routes6()
        now = self.engine.now
        for pname, (rtr, exp) in self.ra_routers.items():
            p = self.ports.get(pname)
            if p is not None and p.is_up and exp > now:
                routes.append(Route6(IPv6Network("::/0"), "ND", p, rtr, 2))
        best: dict = {}
        for r in routes:
            cur = best.get(r.network)
            if cur is None or r.distance < cur.distance:
                best[r.network] = r
        return sorted(best.values(), key=lambda r: (int(r.network.network_address), r.network.prefixlen,
                                                     r.code != "C"))

    # -- frames ---------------------------------------------------------------------------------------
    def handle_ip6_frame(self, frame, port) -> None:
        pkt: IPv6Packet = frame.payload
        if not isinstance(pkt, IPv6Packet):
            return
        if frame.dst != port.mac and not (frame.dst.value >> 32 == 0x3333):
            return
        if not (getattr(port, "ipv6_enabled", False) or getattr(port, "ipv6", None)):
            return
        icmp = pkt.payload
        if isinstance(icmp, Icmp6Packet) and icmp.lladdr is not None and icmp.type in (NS, RS, RA):
            if not pkt.src.is_unspecified:
                self._nd_learn(pkt.src, icmp.lladdr, port, router=icmp.type == RA)
        if pkt.dst.is_multicast:
            if pkt.dst in self._groups(port):
                self._deliver6(pkt, port)
            return
        if self.owns_ip6(pkt.dst):
            self._deliver6(pkt, port)
            return
        if not self.ip6_forwarding or pkt.dst.is_link_local:
            return
        nat64 = getattr(self, "nat64", None)
        if nat64 is not None and nat64.translate_out(self, pkt, port):
            return
        if pkt.hlim <= 1:
            self._icmp6_error(pkt, TIME_EXCEEDED, port)
            return
        fwd = IPv6Packet(pkt.src, pkt.dst, pkt.hlim - 1, pkt.payload, pkt.next_header)
        if not self.send_ip6(fwd):
            self._icmp6_error(pkt, DEST_UNREACH, port)

    def _deliver6(self, pkt: IPv6Packet, port) -> None:
        m = pkt.payload
        from ..packets import TcpSegment, UdpDatagram
        if isinstance(m, (TcpSegment, UdpDatagram)) and hasattr(self, "deliver_protocol"):
            self.deliver6_protocol(pkt, port)
            if not getattr(pkt, "_consumed", False):
                self.deliver_protocol(pkt, port)          # same sockets and services as IPv4
            return
        if not isinstance(m, Icmp6Packet):
            self.deliver6_protocol(pkt, port)
            return
        if m.type == NS and m.target is not None and any(a.ip == m.target for a in self.addrs6(port)):
            na = Icmp6Packet(NA, target=m.target, lladdr=port.mac)
            dst = pkt.src if not pkt.src.is_unspecified else ALL_NODES
            self._send6_on(port, IPv6Packet(m.target, dst, 255, na), mac=self._mac_for_reply(pkt, m))
        elif m.type == NA and m.target is not None:
            self._nd_learn(m.target, m.lladdr or MacAddress(0), port)
        elif m.type == RS and self.sends_ra and self.ra_enabled(port):
            self.send_ra(port, IPv6Address(str(pkt.src)) if not pkt.src.is_unspecified else ALL_NODES)
        elif m.type == RA:
            self.on_ra(pkt, m, port)
        elif m.type == ECHO_REQUEST:
            src = pkt.dst if not pkt.dst.is_multicast else (self.addrs6(port)[0].ip if self.addrs6(port) else None)
            if src is not None:
                rep = Icmp6Packet(ECHO_REPLY, ident=m.ident, seq=m.seq, size=m.size)
                self.send_ip6(IPv6Packet(src, pkt.src, self.default_ttl, rep), out_port=port if pkt.src.is_link_local else None)
        else:
            ident = m.ident
            if m.type in (DEST_UNREACH, TIME_EXCEEDED) and m.original is not None:
                ident = getattr(m.original.payload, "ident", None)
            h = self._icmp6_handlers.get(ident)
            if h is not None:
                h(pkt)

    def _mac_for_reply(self, pkt, m):
        return m.lladdr

    def on_ra(self, pkt, m, port) -> None:
        """Hosts override (SLAAC)."""

    def ra_enabled(self, port) -> bool:
        return False

    def send_ra(self, port, dst=ALL_NODES) -> None:
        ll = self.link_local(port)
        if ll is None:
            return
        prefixes = tuple(str(a.network) for a in (port.ipv6 or []) if a.network.prefixlen == 64)
        ra = Icmp6Packet(RA, lladdr=port.mac, prefixes=prefixes)
        self._send6_on(port, IPv6Packet(ll.ip, dst, 255, ra), mac=mcast_mac(ALL_NODES))

    def _nd_learn(self, ip, mac, port, router=False) -> None:
        key = (port.name, ip)
        self.nd_cache[key] = Neighbor6(ip, mac, port, self.engine.now, router)
        for pkt in self._nd_pending.pop(key, []):
            self._send6_on(port, pkt, mac=mac)

    def _icmp6_error(self, orig: IPv6Packet, kind: int, port) -> None:
        if isinstance(orig.payload, Icmp6Packet) and orig.payload.type not in (ECHO_REQUEST, ECHO_REPLY):
            return
        src = next((a.ip for a in (port.ipv6 or [])), None) or (self.link_local(port).ip if self.link_local(port) else None)
        if src is None:
            return
        self.send_ip6(IPv6Packet(src, orig.src, 64, Icmp6Packet(kind, original=orig)))

    # -- send --------------------------------------------------------------------------------------------
    def _send6_on(self, port, pkt: IPv6Packet, mac=None) -> None:
        from ..packets import EthernetFrame
        if mac is None:
            mac = mcast_mac(pkt.dst)
        port.send(EthernetFrame(port.mac, mac, ETH_IPV6, pkt))

    def l4_send6(self, dst, payload, proto: int, src=None) -> bool:
        """TCP/UDP over IPv6 (used by send_tcp / send_udp when the destination is an IPv6 address)."""
        dst = IPv6Address(str(dst))
        if src is None:
            _port, src = self.source_for6(dst)
            if src is None:
                return False
        return self.send_ip6(IPv6Packet(IPv6Address(str(src)), dst, getattr(self, "default_ttl", 64), payload, proto))

    def send_ip6(self, pkt: IPv6Packet, out_port=None) -> bool:
        if self.owns_ip6(pkt.dst):
            self.engine.schedule(0.0, self._deliver6, pkt, self.port_for_ip6(pkt.dst))
            return True
        if out_port is not None:
            port, nh = out_port, pkt.dst
        else:
            r = self.route_lookup6(pkt.dst)
            if r is None:
                return False
            port, nh = r
        if port is None or not port.is_up:
            return False
        if pkt.dst.is_multicast:
            self._send6_on(port, pkt)
            return True
        n = self.nd_cache.get((port.name, nh))
        if n is not None:
            self._send6_on(port, pkt, mac=n.mac)
            return True
        key = (port.name, nh)
        if key in self._nd_pending:
            self._nd_pending[key].append(pkt)
            return True
        self._nd_pending[key] = [pkt]
        src = next((a.ip for a in self.addrs6(port) if (not nh.is_link_local) and a.network.prefixlen < 128
                    and nh in a.network), None) or (self.link_local(port).ip if self.link_local(port) else None)
        if src is None:
            return False
        ns = Icmp6Packet(NS, target=nh, lladdr=port.mac)
        sn = solicited_node(nh)
        self._send6_on(port, IPv6Packet(src, sn, 255, ns), mac=mcast_mac(sn))
        self.engine.schedule(NDP_TIMEOUT_MS, self._nd_pending.pop, key, None)
        return True

    def source_for6(self, dst: IPv6Address):
        r = self.route_lookup6(dst)
        if r is None:
            return None, None
        port = r[0]
        if dst.is_link_local:
            ll = self.link_local(port)
            return port, ll.ip if ll else None
        glob = [a.ip for a in (port.ipv6 or [])]
        if glob:
            return port, glob[0]
        ll = self.link_local(port)
        return port, ll.ip if ll else None


class Ping6Session(PingSession):
    """ICMPv6 echo: same behaviour and listener interface as the IPv4 PingSession."""

    def start(self) -> "Ping6Session":
        self.device._icmp6_handlers[self.ident] = self._on_icmp
        self.listener.on_start(self)
        self._send_next()
        return self

    def _send_next(self) -> None:
        if self.done:
            return
        if self._seq >= self.count:
            self._finish()
            return
        seq = self._seq
        self._seq += 1
        self.stats.sent += 1
        port, src = self.device.source_for6(self.dst)
        pkt = None if src is None else IPv6Packet(
            src, self.dst, self.ttl or self.device.default_ttl,
            Icmp6Packet(ECHO_REQUEST, ident=self.ident, seq=seq, size=self.size))
        if pkt is None or not self.device.send_ip6(pkt):
            self.listener.on_send_fail(seq)
            self.engine.schedule(max(self.interval, 1.0), self._send_next)
            return
        self.source = self.source or src
        timer = self.engine.schedule(self.timeout, self._on_timeout, seq)
        self._outstanding[seq] = (self.engine.now, timer)

    def _on_icmp(self, pkt) -> None:
        if self.done:
            return
        m = pkt.payload
        seq = m.seq if m.type == ECHO_REPLY else getattr(getattr(m.original, "payload", None), "seq", None)
        if seq not in self._outstanding:
            return
        sent_at, timer = self._outstanding.pop(seq)
        timer.cancel()
        rtt = self.engine.now - sent_at
        if m.type == ECHO_REPLY:
            self.stats.received += 1
            self.stats.rtts.append(rtt)
            self.listener.on_reply(seq, pkt.src, rtt, pkt.hlim, m.size)
        else:
            self.listener.on_error(seq, "unreachable" if m.type == DEST_UNREACH else "ttl", pkt.src)
        self.engine.schedule(self.interval, self._send_next)

    def _finish(self) -> None:
        self.device._icmp6_handlers.pop(self.ident, None)
        self.listener.on_finish(self.stats)
        self.finish()


class Trace6Session(TracerouteSession):
    """IPv6 traceroute: ICMPv6 echo probes with increasing hop limit, answered by Time Exceeded."""

    def start(self) -> "Trace6Session":
        self.device._icmp6_handlers[self.ident] = self._on_icmp
        self.listener.on_start(self)
        self._next_hop()
        return self

    def _send_probe(self) -> None:
        if self.done:
            return
        seq = next(self._seq)
        port, src = self.device.source_for6(self.dst)
        pkt = None if src is None else IPv6Packet(src, self.dst, self._hop.ttl,
                                                    Icmp6Packet(ECHO_REQUEST, ident=self.ident, seq=seq, size=32))
        if pkt is None or not self.device.send_ip6(pkt):
            self._hop.probes.append(None)
            self._hop.unreachable = True
            self.listener.on_probe(self._hop, self._probe_index)
            self.listener.on_hop(self._hop)
            self._finish(False)
            return
        timer = self.engine.schedule(self.timeout, self._on_timeout, seq)
        self._pending = (seq, self.engine.now, timer)

    def _on_icmp(self, pkt) -> None:
        if self.done or self._pending is None:
            return
        m = pkt.payload
        seq = m.seq if m.type == ECHO_REPLY else getattr(getattr(m.original, "payload", None), "seq", None)
        pseq, sent_at, timer = self._pending
        if seq != pseq:
            return
        timer.cancel()
        self._pending = None
        if m.type == ECHO_REPLY:
            self._hop.reached = True
        elif m.type == DEST_UNREACH:
            self._hop.unreachable = True
        self._record(self.engine.now - sent_at, pkt.src)

    def _finish(self, reached: bool) -> None:
        self.device._icmp6_handlers.pop(self.ident, None)
        self.listener.on_finish(self.hops, reached)
        self.finish()


__all__ = ["Ip6Stack", "Ping6Session", "Trace6Session", "IPv6Packet", "Icmp6Packet", "eui64_link_local", "StaticRoute6"]
