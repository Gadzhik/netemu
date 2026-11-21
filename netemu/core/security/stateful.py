"""Stateful filtering: connection tracking and zone-based firewalls.

* ``ConnTrack`` remembers flows (protocol, addresses, ports / ICMP identifier).  A packet whose
  reverse flow is known is *established*; anything else is *new*.  Used by RouterOS
  ``connection-state=`` rules and by the zone firewalls below.
* ``ZoneFirewall`` is the common model of Cisco Zone-Based Policy Firewall and Juniper SRX security
  zones/policies: interfaces belong to zones; traffic inside one zone passes; traffic between
  zones needs a zone-pair policy (Cisco) / security policy (Junos) whose first matching rule says
  inspect / permit (stateful: replies come back automatically), pass (one way, no state) or
  drop.  No matching rule, or a zoned and an unzoned interface: dropped.  Traffic to and from
  the router itself (the "self zone") is not filtered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Network

from ..packets import ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, IcmpPacket, TcpSegment, UdpDatagram

FLOW_TIMEOUT_MS = 300_000
APPS = {"http": ("tcp", 80), "https": ("tcp", 443), "ssh": ("tcp", 22), "telnet": ("tcp", 23),
        "dns": ("udp", 53), "ntp": ("udp", 123), "snmp": ("udp", 161), "ftp": ("tcp", 21),
        "junos-http": ("tcp", 80), "junos-https": ("tcp", 443), "junos-ssh": ("tcp", 22),
        "junos-telnet": ("tcp", 23), "junos-dns-udp": ("udp", 53), "junos-ping": ("icmp", None),
        "junos-icmp-all": ("icmp", None)}
PROTO_NUM = {"icmp": 1, "tcp": 6, "udp": 17}


def flow_key(pkt):
    l4 = pkt.payload
    if isinstance(l4, IcmpPacket):
        if l4.type in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY):
            return (1, pkt.src, pkt.dst, l4.ident, 0)
        return None
    if isinstance(l4, (TcpSegment, UdpDatagram)):
        return (pkt.protocol, pkt.src, pkt.dst, l4.sport, l4.dport)
    return (pkt.protocol, pkt.src, pkt.dst, 0, 0)


def reverse(key):
    proto, s, d, sp, dp = key
    return (proto, d, s, sp, dp) if proto == 1 else (proto, d, s, dp, sp)


@dataclass
class Flow:
    key: tuple
    created: float
    last: float
    packets: int = 1
    zone_pair: str = ""


class ConnTrack:
    def __init__(self, engine):
        self.engine = engine
        self.flows: dict[tuple, Flow] = {}

    def state(self, pkt) -> str:
        k = flow_key(pkt)
        if k is None:
            return "related"
        now = self.engine.now
        f = self.flows.get(k) or self.flows.get(reverse(k))
        if f is not None and now - f.last < FLOW_TIMEOUT_MS:
            f.last, f.packets = now, f.packets + 1
            return "established"
        return "new"

    def is_reply(self, pkt) -> bool:
        k = flow_key(pkt)
        if k is None:
            return True                                   # ICMP errors relate to some flow
        f = self.flows.get(reverse(k))
        if f is not None and self.engine.now - f.last < FLOW_TIMEOUT_MS:
            f.last, f.packets = self.engine.now, f.packets + 1
            return True
        return False

    def add(self, pkt, label: str = "") -> None:
        k = flow_key(pkt)
        if k is None:
            return
        now = self.engine.now
        f = self.flows.get(k)
        if f is None:
            self.flows[k] = Flow(k, now, now, 1, label)
        else:
            f.last, f.packets = now, f.packets + 1

    def active(self) -> list[Flow]:
        now = self.engine.now
        self.flows = {k: f for k, f in self.flows.items() if now - f.last < FLOW_TIMEOUT_MS}
        return list(self.flows.values())


@dataclass
class ZoneRule:
    name: str
    action: str                                  # inspect | permit | pass | drop
    protocol: str = "any"                        # any | icmp | tcp | udp | <application>
    src: str = "0.0.0.0/0"
    dst: str = "0.0.0.0/0"
    hits: int = 0

    def matches(self, pkt) -> bool:
        proto, port = APPS.get(self.protocol, (self.protocol, None))
        if proto != "any" and pkt.protocol != PROTO_NUM.get(proto, -1):
            return False
        if port is not None and getattr(pkt.payload, "dport", None) != port:
            return False
        return pkt.src in IPv4Network(self.src, strict=False) and pkt.dst in IPv4Network(self.dst, strict=False)

    def to_dict(self) -> dict:
        return {"name": self.name, "action": self.action, "protocol": self.protocol, "src": self.src, "dst": self.dst}


@dataclass
class ZoneFirewall:
    zones: dict = field(default_factory=dict)            # zone -> [interface names]
    pairs: dict = field(default_factory=dict)            # "SRC>DST" -> [ZoneRule]
    dropped: int = 0

    @property
    def active(self) -> bool:
        return any(self.zones.values())

    def zone_of(self, port) -> str | None:
        name = getattr(port, "name", None)
        parent = getattr(getattr(port, "parent", None), "name", None)
        for z, ifs in self.zones.items():
            if name in ifs or (parent is not None and parent in ifs and False):
                return z
        return None

    def check(self, ct: ConnTrack, pkt, in_port, out_port) -> bool:
        zi, zo = self.zone_of(in_port), self.zone_of(out_port)
        if zi is None and zo is None:
            return True
        if zi == zo:
            return True
        if ct.is_reply(pkt):
            return True
        if zi is None or zo is None:
            self.dropped += 1
            return False
        for r in self.pairs.get(f"{zi}>{zo}", []):
            if r.matches(pkt):
                r.hits += 1
                if r.action in ("inspect", "permit"):
                    ct.add(pkt, f"{zi}>{zo}")
                    return True
                if r.action == "pass":
                    return True
                self.dropped += 1
                return False
        self.dropped += 1
        return False

    def to_dict(self) -> dict | None:
        if not self.zones and not self.pairs:
            return None
        return {"zones": {k: list(v) for k, v in self.zones.items()},
                "pairs": {k: [r.to_dict() for r in v] for k, v in self.pairs.items()}}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ZoneFirewall":
        d = d or {}
        return cls({k: list(v) for k, v in d.get("zones", {}).items()},
                   {k: [ZoneRule(**r) for r in v] for k, v in d.get("pairs", {}).items()})
