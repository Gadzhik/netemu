"""Protocol data units: Ethernet frames, ARP, IPv4 and ICMP."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .addresses import IPv4Address, MacAddress

ETH_ARP = "ARP"
ETH_IPV4 = "IPv4"

PROTO_ICMP = 1
PROTO_OSPF = 89

ICMP_ECHO_REPLY = 0
ICMP_DEST_UNREACHABLE = 3
ICMP_ECHO_REQUEST = 8
ICMP_TIME_EXCEEDED = 11

ICMP_NAMES = {
    ICMP_ECHO_REPLY: "Echo Reply",
    ICMP_DEST_UNREACHABLE: "Destination Unreachable",
    ICMP_ECHO_REQUEST: "Echo Request",
    ICMP_TIME_EXCEEDED: "Time Exceeded",
}


@dataclass(frozen=True)
class IcmpPacket:
    type: int
    code: int = 0
    ident: int = 0
    seq: int = 0
    size: int = 32
    original: "IPv4Packet | None" = None  # embedded datagram for error messages

    @property
    def name(self) -> str:
        return ICMP_NAMES.get(self.type, f"type {self.type}")

    def layer_lines(self) -> list[str]:
        lines = [f"Type: {self.type} ({self.name})", f"Code: {self.code}"]
        if self.type in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY):
            lines += [f"Identifier: 0x{self.ident:04x}", f"Sequence: {self.seq}",
                      f"Data: {self.size} bytes"]
        elif self.original is not None:
            lines.append(f"Original datagram: {self.original.src} -> {self.original.dst}")
        return lines


@dataclass(frozen=True)
class OspfPacket:
    kind: str                     # "hello" | "lsu"
    router_id: Any
    area: str
    hello: tuple | None = None    # (hello, dead, prefixlen, priority, dr, bdr, seen neighbours)
    lsas: tuple = ()

    @property
    def name(self) -> str:
        return "Hello" if self.kind == "hello" else "LS Update"

    def layer_lines(self) -> list[str]:
        lines = ["Version: 2", f"Message type: {self.name}", f"Router ID: {self.router_id}",
                 f"Area ID: {self.area}"]
        if self.hello:
            h, d, plen, prio, dr, bdr, seen = self.hello
            lines += [f"Hello interval: {h} s, dead interval: {d} s", f"Network mask: /{plen}",
                      f"Priority: {prio}", f"Designated router: {dr or '0.0.0.0'}",
                      f"Backup designated router: {bdr or '0.0.0.0'}",
                      "Active neighbors: " + (", ".join(map(str, seen)) or "none")]
        for l in self.lsas:
            kind = {1: "Router", 3: "Summary", 4: "ASBR-summary", 5: "AS-external"}.get(l.type, str(l.type))
            lines.append(f"LSA {kind}: id {l.ls_id}, adv {l.adv}, seq 0x{l.seq:08x}")
        return lines


PROTO_TCP = 6
PROTO_UDP = 17


@dataclass(frozen=True)
class TcpSegment:
    """Minimal TCP: ports and flags; *data* is an application message (BGP, HTTP, DNS ...).

    Application messages may define ``label`` (protocol name), ``summary()`` and ``layer_lines()``."""
    sport: int
    dport: int
    data: Any = None
    flags: str = "PSH,ACK"
    seq: int = 0
    ack: int = 0

    def layer_lines(self) -> list[str]:
        return [f"Source port: {self.sport}", f"Destination port: {self.dport}", f"Flags: {self.flags}",
                f"Sequence number: {self.seq}", f"Acknowledgment number: {self.ack}"]


@dataclass(frozen=True)
class UdpDatagram:
    sport: int
    dport: int
    data: Any = None

    def layer_lines(self) -> list[str]:
        return [f"Source port: {self.sport}", f"Destination port: {self.dport}"]


def app_label(l4) -> str:
    data = getattr(l4, "data", None)
    return getattr(data, "label", "TCP" if isinstance(l4, TcpSegment) else "UDP")


PROTO_NAMES = {PROTO_ICMP: "ICMP", PROTO_OSPF: "OSPF", PROTO_TCP: "TCP", PROTO_UDP: "UDP"}


@dataclass(frozen=True)
class IPv4Packet:
    src: IPv4Address
    dst: IPv4Address
    ttl: int
    payload: Any
    protocol: int = PROTO_ICMP
    ident: int = 0
    dscp: int = 0

    def layer_lines(self) -> list[str]:
        from .security.qos import DSCP_NAMES
        return [f"Source IP: {self.src}", f"Destination IP: {self.dst}",
                f"TTL: {self.ttl}", f"Protocol: {self.protocol} ({PROTO_NAMES.get(self.protocol, 'data')})",
                f"DSCP: {self.dscp} ({DSCP_NAMES.get(self.dscp, '-')})",
                f"Identification: 0x{self.ident:04x}"]


@dataclass(frozen=True)
class ArpPacket:
    op: str  # "request" | "reply"
    sender_mac: MacAddress
    sender_ip: IPv4Address
    target_mac: MacAddress
    target_ip: IPv4Address

    def layer_lines(self) -> list[str]:
        return [f"Operation: {self.op.upper()}",
                f"Sender MAC: {self.sender_mac}", f"Sender IP: {self.sender_ip}",
                f"Target MAC: {self.target_mac}", f"Target IP: {self.target_ip}"]


@dataclass(frozen=True)
class EthernetFrame:
    src: MacAddress
    dst: MacAddress
    ethertype: str
    payload: Any
    uid: int = field(default=0, compare=False)
    vlan: int | None = None   # 802.1Q tag (None = untagged)

    @property
    def protocol(self) -> str:
        """Short top-level protocol label used in the simulation panel."""
        if self.ethertype == ETH_ARP:
            return "ARP"
        if isinstance(self.payload, IPv4Packet):
            inner = self.payload.payload
            if isinstance(inner, IcmpPacket):
                return "ICMP"
            if isinstance(inner, OspfPacket):
                return "OSPF"
            if isinstance(inner, (TcpSegment, UdpDatagram)):
                return app_label(inner)
            if hasattr(inner, "label"):
                return inner.label
        if self.ethertype == "IPv6":
            inner = getattr(self.payload, "payload", None)
            return "NDP" if getattr(inner, "is_ndp", False) else "ICMPv6"
        if self.ethertype == "LACP":
            return getattr(self.payload, "label", "LACP")
        return self.ethertype

    def summary(self) -> str:
        text = self._summary()
        return f"{text} [VLAN {self.vlan}]" if self.vlan is not None else text

    def _summary(self) -> str:
        p = self.payload
        if isinstance(p, ArpPacket):
            if p.op == "request":
                return f"ARP Who has {p.target_ip}? Tell {p.sender_ip}"
            return f"ARP {p.sender_ip} is at {p.sender_mac}"
        if isinstance(p, IPv4Packet):
            inner = p.payload
            if isinstance(inner, OspfPacket):
                extra = f" ({len(inner.lsas)} LSA)" if inner.kind == "lsu" else ""
                return f"OSPF {inner.name} {inner.router_id} {p.src} -> {p.dst}{extra}"
            if isinstance(inner, (TcpSegment, UdpDatagram)):
                data = inner.data
                text = data.summary() if hasattr(data, "summary") else ""
                return f"{app_label(inner)} {p.src}:{inner.sport} -> {p.dst}:{inner.dport} {text}".rstrip()
            name = inner.name if isinstance(inner, IcmpPacket) else "data"
            return f"ICMP {name} {p.src} -> {p.dst} (TTL {p.ttl})"
        if self.ethertype == "IPv6" and hasattr(p, "hlim"):
            inner = p.payload
            tgt = f" target {inner.target}" if getattr(inner, "target", None) is not None else ""
            return f"{self.protocol} {inner.name}{tgt} {p.src} -> {p.dst} (HL {p.hlim})"
        return f"{self.ethertype} {self.src} -> {self.dst}"

    def layers(self) -> list[tuple[str, list[str]]]:
        """OSI-like breakdown of the PDU: [(layer title, [lines])]."""
        out = [("Layer 2: Ethernet II", [f"Source MAC: {self.src}",
                                         f"Destination MAC: {self.dst}",
                                         f"EtherType: {self.ethertype}"])]
        if self.vlan is not None:
            out.append(("802.1Q", [f"VLAN ID: {self.vlan}", "Priority: 0"]))
        p = self.payload
        if isinstance(p, ArpPacket):
            out.append(("ARP", p.layer_lines()))
        elif isinstance(p, IPv4Packet):
            out.append(("Layer 3: IPv4", p.layer_lines()))
            if isinstance(p.payload, IcmpPacket):
                out.append(("ICMP", p.payload.layer_lines()))
            elif isinstance(p.payload, OspfPacket):
                out.append(("OSPF", p.payload.layer_lines()))
            elif isinstance(p.payload, (TcpSegment, UdpDatagram)):
                l4 = p.payload
                out.append(("Layer 4: " + ("TCP" if isinstance(l4, TcpSegment) else "UDP"), l4.layer_lines()))
                if hasattr(l4.data, "layer_lines"):
                    out.append((app_label(l4), l4.data.layer_lines()))
        elif self.ethertype == "IPv6" and hasattr(p, "hlim"):
            out.append(("Layer 3: IPv6", p.layer_lines()))
            if hasattr(p.payload, "layer_lines"):
                out.append(("ICMPv6", p.payload.layer_lines()))
        return out
