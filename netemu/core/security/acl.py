"""Packet filters: Cisco ACLs, Huawei ACLs, Junos firewall filters, RouterOS firewall rules.

All four are expressed as an ordered list of :class:`AclRule` with a default action.
Rules match on protocol, source/destination prefix, TCP/UDP ports and ICMP type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from ..packets import PROTO_ICMP, PROTO_OSPF, IcmpPacket

PROTO_TCP = 6
PROTO_UDP = 17
PROTOCOLS = {"ip": None, "icmp": PROTO_ICMP, "tcp": PROTO_TCP, "udp": PROTO_UDP, "ospf": PROTO_OSPF}
ICMP_TYPES = {"echo": 8, "echo-reply": 0, "unreachable": 3, "time-exceeded": 11,
              "echo-request": 8, "ttl-exceeded": 11}
ANY = IPv4Network("0.0.0.0/0")


@dataclass
class AclRule:
    action: str                          # "permit" | "deny"
    proto: str = "ip"                    # ip | icmp | tcp | udp | ospf
    src: IPv4Network = ANY
    dst: IPv4Network = ANY
    sport: tuple | None = None           # ("eq", 80) | ("range", 1000, 2000) | ("gt", 1023) | ("lt", 1024)
    dport: tuple | None = None
    icmp_type: int | None = None
    seq: int = 0
    hits: int = 0
    text: str = ""                       # how the vendor CLI wrote it (for show/running-config)
    extra: dict = field(default_factory=dict)   # vendor specifics (RouterOS chain, interfaces, ...)

    @staticmethod
    def _port_ok(cond, port) -> bool:
        if cond is None:
            return True
        if port is None:
            return False
        op = cond[0]
        if op == "eq":
            return port == cond[1]
        if op == "neq":
            return port != cond[1]
        if op == "gt":
            return port > cond[1]
        if op == "lt":
            return port < cond[1]
        if op == "range":
            return cond[1] <= port <= cond[2]
        return False

    def matches(self, pkt) -> bool:
        want = PROTOCOLS.get(self.proto, self.proto)
        if want is not None and pkt.protocol != want:
            return False
        if pkt.src not in self.src or pkt.dst not in self.dst:
            return False
        l4 = pkt.payload
        if self.sport is not None or self.dport is not None:
            if not self._port_ok(self.sport, getattr(l4, "sport", None)):
                return False
            if not self._port_ok(self.dport, getattr(l4, "dport", None)):
                return False
        if self.icmp_type is not None:
            if not isinstance(l4, IcmpPacket) or l4.type != self.icmp_type:
                return False
        return True

    def to_dict(self) -> dict:
        return {"action": self.action, "proto": self.proto, "src": str(self.src), "dst": str(self.dst),
                "sport": list(self.sport) if self.sport else None,
                "dport": list(self.dport) if self.dport else None,
                "icmp_type": self.icmp_type, "seq": self.seq, "text": self.text, "extra": dict(self.extra)}

    @classmethod
    def from_dict(cls, d: dict) -> "AclRule":
        return cls(d["action"], d.get("proto", "ip"), IPv4Network(d.get("src", "0.0.0.0/0")),
                   IPv4Network(d.get("dst", "0.0.0.0/0")),
                   tuple(d["sport"]) if d.get("sport") else None,
                   tuple(d["dport"]) if d.get("dport") else None,
                   d.get("icmp_type"), int(d.get("seq", 0)), 0, d.get("text", ""), dict(d.get("extra", {})))


@dataclass
class Acl:
    name: str
    kind: str = "standard"               # standard | extended | basic | advanced | filter
    rules: list[AclRule] = field(default_factory=list)
    default: str = "deny"                # action when nothing matches (implicit deny on Cisco/Junos)
    silent_default: bool = False         # Junos: the implicit discard sends no ICMP

    def decide(self, pkt) -> tuple[bool, bool]:
        """(permitted, drop silently) for *pkt*; counts rule hits."""
        for r in self.rules:
            if r.matches(pkt):
                r.hits += 1
                return r.action == "permit", bool(r.extra.get("silent"))
        return self.default == "permit", self.silent_default

    def check(self, pkt) -> bool:
        """True when the packet is permitted (counts hits)."""
        return self.decide(pkt)[0]

    def matches_source(self, ip: IPv4Address) -> bool:
        """Standard-ACL use (NAT 'list N'): does a permit rule match this source?"""
        for r in self.rules:
            if ip in r.src:
                return r.action == "permit"
        return False

    def add(self, rule: AclRule, step: int = 10) -> AclRule:
        if not rule.seq:
            rule.seq = (max((r.seq for r in self.rules), default=0) // step + 1) * step
        self.rules = sorted([r for r in self.rules if r.seq != rule.seq] + [rule], key=lambda r: r.seq)
        return rule

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "default": self.default,
                "silent_default": self.silent_default, "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, d: dict) -> "Acl":
        return cls(d["name"], d.get("kind", "standard"), [AclRule.from_dict(r) for r in d.get("rules", [])],
                   d.get("default", "deny"), bool(d.get("silent_default", False)))


def wildcard_net(addr: str, wildcard: str) -> IPv4Network:
    mask = int(IPv4Address(wildcard)) ^ 0xFFFFFFFF
    return IPv4Network(f"{addr}/{IPv4Address(mask)}", strict=False)


def net_text_cisco(net: IPv4Network) -> str:
    if net.prefixlen == 0:
        return "any"
    if net.prefixlen == 32:
        return f"host {net.network_address}"
    return f"{net.network_address}, wildcard bits {IPv4Address(int(net.hostmask))}"
