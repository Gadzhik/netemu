"""QoS: classification, DSCP marking and policing (Cisco MQC / Huawei traffic policy).

A policy is an ordered list of classes; the first class whose match fits the packet applies
its actions: set the DSCP value and/or police to a rate (bits per second, token bucket with a
quarter-second burst) — packets above the rate are dropped.  The default class matches
everything else.  Per-class counters back ``show policy-map interface``."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

DSCP_NAMES = {0: "default", 8: "cs1", 10: "af11", 12: "af12", 14: "af13", 16: "cs2", 18: "af21", 20: "af22",
              22: "af23", 24: "cs3", 26: "af31", 28: "af32", 30: "af33", 32: "cs4", 34: "af41", 36: "af42",
              38: "af43", 40: "cs5", 46: "ef", 48: "cs6", 56: "cs7"}
DSCP_VALUES = {v: k for k, v in DSCP_NAMES.items()}
PROTO = {"icmp": 1, "tcp": 6, "udp": 17, "ospf": 89, "gre": 47}


def parse_dscp(text: str) -> int:
    t = text.lower()
    if t in DSCP_VALUES:
        return DSCP_VALUES[t]
    v = int(t)
    if not 0 <= v <= 63:
        raise ValueError("dscp out of range")
    return v


@dataclass
class QosClass:
    name: str
    match: dict = field(default_factory=dict)       # {"acl": name} | {"protocol": "icmp"} | {"dscp": 46} | {"any": True}
    set_dscp: int | None = None
    police_bps: int | None = None
    matched: int = 0
    dropped: int = 0
    bytes: int = 0

    def fits(self, dev, pkt) -> bool:
        m = self.match
        if "class" in m:
            m = getattr(dev, "qos_classes", {}).get(m["class"], {})
        if not m or m.get("any") or self.name == "class-default":
            return True
        if "protocol" in m and pkt.protocol != PROTO.get(m["protocol"], -1):
            return False
        if "dscp" in m and getattr(pkt, "dscp", 0) != m["dscp"]:
            return False
        if "acl" in m:
            acl = getattr(dev, "acls", {}).get(m["acl"])
            if acl is None or not acl.decide(pkt)[0]:
                return False
        return True


@dataclass
class QosPolicy:
    name: str
    classes: list[QosClass] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "classes": [{"name": c.name, "match": dict(c.match), "set_dscp": c.set_dscp,
                                                "police_bps": c.police_bps} for c in self.classes]}

    @classmethod
    def from_dict(cls, d: dict) -> "QosPolicy":
        return cls(d["name"], [QosClass(c["name"], dict(c.get("match", {})), c.get("set_dscp"), c.get("police_bps"))
                               for c in d.get("classes", [])])


def packet_size(pkt) -> int:
    inner = pkt.payload
    size = getattr(inner, "size", None)
    return 20 + 8 + (size if isinstance(size, int) else 40)


def apply(dev, policy: QosPolicy, pkt, port, direction: str):
    """Run *pkt* through *policy*; returns the (possibly re-marked) packet or None when policed."""
    buckets = dev.__dict__.setdefault("_qos_buckets", {})
    now = dev.engine.now
    for c in policy.classes:
        if not c.fits(dev, pkt):
            continue
        c.matched += 1
        size = packet_size(pkt)
        c.bytes += size
        if c.police_bps:
            key = (port.name, direction, policy.name, c.name)
            burst = max(1500.0, c.police_bps / 8 / 4)
            tokens, last = buckets.get(key, (burst, now))
            tokens = min(burst, tokens + (now - last) / 1000.0 * c.police_bps / 8)
            if tokens < size:
                buckets[key] = (tokens, now)
                c.dropped += 1
                return None
            buckets[key] = (tokens - size, now)
        if c.set_dscp is not None:
            pkt = replace(pkt, dscp=c.set_dscp)
        return pkt
    return pkt
