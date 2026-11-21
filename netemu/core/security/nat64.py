"""Stateful NAT64 (RFC 6146) for ICMP echo: IPv6-only hosts reach IPv4 hosts.

An IPv6 packet to ``<prefix>::a.b.c.d`` (well-known prefix 64:ff9b::/96) arriving on a
NAT64-enabled interface is translated to IPv4: the source becomes the router's IPv4 address
on the exit interface and the ICMP identifier a fresh one from the session table (PAT).  The
IPv4 echo reply is translated back to ICMPv6 and sent to the original IPv6 host."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv6Address, IPv6Network

WELL_KNOWN = IPv6Network("64:ff9b::/96")
_idents = itertools.count(0x6400)


@dataclass
class Nat64Session:
    v6_src: IPv6Address
    v6_dst: IPv6Address
    ident6: int
    v4_src: IPv4Address
    v4_dst: IPv4Address
    ident4: int
    hits: int = 0


@dataclass
class Nat64:
    prefix: IPv6Network = WELL_KNOWN
    interfaces: set = field(default_factory=set)        # ports where IPv6 traffic is translated
    sessions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"prefix": str(self.prefix), "interfaces": sorted(self.interfaces)}

    @classmethod
    def from_dict(cls, d: dict) -> "Nat64":
        return cls(IPv6Network(d.get("prefix", str(WELL_KNOWN))), set(d.get("interfaces", [])))

    @staticmethod
    def embedded_v4(addr: IPv6Address) -> IPv4Address:
        return IPv4Address(int(addr) & 0xFFFFFFFF)

    def translate_out(self, dev, pkt, port) -> bool:
        """IPv6 -> IPv4.  Returns True when the packet was handled (translated or dropped)."""
        from ..packets import ICMP_ECHO_REQUEST, IcmpPacket
        from ..stack.ipv6 import ECHO_REQUEST, Icmp6Packet
        if port.name not in self.interfaces or pkt.dst not in self.prefix:
            return False
        m = pkt.payload
        if not isinstance(m, Icmp6Packet) or m.type != ECHO_REQUEST:
            return True                                          # only ICMP echo is translated here
        dst4 = self.embedded_v4(pkt.dst)
        r = dev.route_lookup(dst4)
        if r is None or r[0].ip is None:
            return True
        s = next((x for x in self.sessions if x.v6_src == pkt.src and x.ident6 == m.ident and x.v4_dst == dst4), None)
        if s is None:
            s = Nat64Session(pkt.src, pkt.dst, m.ident, r[0].ip.ip, dst4, next(_idents) & 0xFFFF)
            self.sessions.append(s)
            dev.register_icmp(s.ident4, lambda p4, s=s: self.translate_in(dev, p4, s))
        s.hits += 1
        icmp = IcmpPacket(ICMP_ECHO_REQUEST, 0, s.ident4, m.seq, m.size)
        from ..packets import IPv4Packet
        dev.send_ip(IPv4Packet(s.v4_src, dst4, max(1, pkt.hlim - 1), icmp, ident=next(dev._ip_ident)))
        return True

    def translate_in(self, dev, p4, s: Nat64Session) -> None:
        from ..packets import ICMP_ECHO_REPLY
        from ..stack.ipv6 import DEST_UNREACH, ECHO_REPLY, TIME_EXCEEDED, Icmp6Packet, IPv6Packet
        icmp = p4.payload
        src6 = IPv6Address(int(self.prefix.network_address) | int(p4.src))
        if icmp.type == ICMP_ECHO_REPLY:
            m6 = Icmp6Packet(ECHO_REPLY, ident=s.ident6, seq=icmp.seq, size=icmp.size)
        else:
            orig = IPv6Packet(s.v6_src, s.v6_dst, 64, Icmp6Packet(128, ident=s.ident6, seq=getattr(
                getattr(icmp.original, "payload", None), "seq", 0)))
            m6 = Icmp6Packet(TIME_EXCEEDED if icmp.type == 11 else DEST_UNREACH, original=orig)
        dev.send_ip6(IPv6Packet(src6, s.v6_src, max(1, p4.ttl - 1), m6))
