"""Network address translation: static NAT, dynamic pools, PAT (overload) and port forwarding.

The model follows Cisco IOS semantics (inside/outside interfaces, inside local/global
addresses); Huawei, Junos and RouterOS rules are expressed with the same rule types.
ICMP echo is translated by rewriting the query identifier (like a port), and ICMP error
messages (traceroute replies) are translated back using the embedded original datagram.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from ..packets import ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST, IcmpPacket, IPv4Packet

if TYPE_CHECKING:
    from ..devices.router import Router
    from ..port import Port

PROTO_NAMES = {1: "icmp", 6: "tcp", 17: "udp"}
TIMEOUTS_MS = {1: 60_000, 6: 86_400_000, 17: 300_000}


@dataclass
class NatRule:
    kind: str                              # "static" | "dynamic" | "dnat"
    # static: one-to-one (optionally a single port);  dnat: destination rewrite (port forwarding)
    local: str | None = None               # inside local address (static/dnat target)
    glob: str | None = None                # inside global address (static) / matched destination (dnat)
    proto: str | None = None               # tcp/udp for port statics, or None
    local_port: int | None = None
    global_port: int | None = None
    # dynamic: which sources, translated to what
    acl: str | None = None                 # ACL name that selects inside sources
    prefix: str | None = None              # or a prefix
    out_iface: str | None = None           # only when leaving through this interface
    in_iface: str | None = None            # dnat: only when arriving on this interface
    interface: str | None = None           # use this interface's address (PAT / masquerade)
    pool: tuple[str, str] | None = None    # (first, last) address range
    overload: bool = False
    text: str = ""                         # vendor text for running-config

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["pool"] = list(self.pool) if self.pool else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NatRule":
        r = cls(d["kind"])
        for k, v in d.items():
            if hasattr(r, k):
                setattr(r, k, v)
        r.pool = tuple(d["pool"]) if d.get("pool") else None
        return r


@dataclass
class Translation:
    proto: int
    in_local: tuple[IPv4Address, int | None]
    in_global: tuple[IPv4Address, int | None]
    outside: tuple[IPv4Address, int | None]
    created: float
    last_used: float
    static: bool = False
    out_port: str | None = None


@dataclass
class NatConfig:
    inside: list[str] = field(default_factory=list)     # interfaces marked "ip nat inside"
    outside: list[str] = field(default_factory=list)    # interfaces marked "ip nat outside"
    rules: list[NatRule] = field(default_factory=list)
    pools: dict[str, tuple[str, str, int]] = field(default_factory=dict)   # name -> (start, end, prefixlen)

    def to_dict(self) -> dict:
        return {"inside": list(self.inside), "outside": list(self.outside),
                "rules": [r.to_dict() for r in self.rules],
                "pools": {k: list(v) for k, v in self.pools.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "NatConfig":
        return cls(list(d.get("inside", [])), list(d.get("outside", [])),
                   [NatRule.from_dict(r) for r in d.get("rules", [])],
                   {k: tuple(v) for k, v in d.get("pools", {}).items()})

    @property
    def empty(self) -> bool:
        return not (self.inside or self.outside or self.rules or self.pools)


class NatEngine:
    def __init__(self, router: "Router", cfg: NatConfig | None = None):
        self.r = router
        self.cfg = cfg or NatConfig()
        self.table: list[Translation] = []
        self._next_id = 1024
        self.hits = 0
        self.misses = 0

    # -- helpers ------------------------------------------------------------------------------------
    @property
    def now(self) -> float:
        return self.r.engine.now

    def _expire(self) -> None:
        now = self.now
        self.table = [t for t in self.table
                      if t.static or now - t.last_used <= TIMEOUTS_MS.get(t.proto, 60_000)]

    def clear(self) -> None:
        self.table = [t for t in self.table if t.static]

    @staticmethod
    def _l4_id(pkt: IPv4Packet, which: str) -> int | None:
        p = pkt.payload
        if isinstance(p, IcmpPacket):
            return p.ident if p.type in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY) else None
        return getattr(p, which, None)

    @staticmethod
    def _with(pkt: IPv4Packet, src=None, dst=None, sid=None, did=None) -> IPv4Packet:
        p = pkt.payload
        if isinstance(p, IcmpPacket) and p.type in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY):
            nid = sid if sid is not None else did
            if nid is not None:
                p = replace(p, ident=nid)
        else:
            if sid is not None and hasattr(p, "sport"):
                p = replace(p, sport=sid)
            if did is not None and hasattr(p, "dport"):
                p = replace(p, dport=did)
        return replace(pkt, src=src or pkt.src, dst=dst or pkt.dst, payload=p)

    def _role(self, port: "Port", role: str) -> bool:
        return port is not None and port.name in getattr(self.cfg, role)

    def _source_selected(self, rule: NatRule, ip: IPv4Address) -> bool:
        if rule.acl:
            acl = self.r.acls.get(rule.acl)
            return acl is not None and acl.matches_source(ip)
        if rule.prefix:
            return ip in IPv4Network(rule.prefix)
        return True

    def _rule_applies_out(self, rule: NatRule, in_port, out_port) -> bool:
        if rule.out_iface is not None:
            return out_port is not None and out_port.name == rule.out_iface
        # Cisco style: inside -> outside
        return self._role(out_port, "outside") and (in_port is None or self._role(in_port, "inside"))

    def global_addresses(self) -> set[IPv4Address]:
        """Addresses the router must answer ARP for on outside segments."""
        out = set()
        for r in self.cfg.rules:
            if r.kind == "static" and r.glob:
                out.add(IPv4Address(r.glob))
            if r.kind == "dnat" and r.glob:
                out.add(IPv4Address(r.glob))
            if r.pool:
                a, b = int(IPv4Address(r.pool[0])), int(IPv4Address(r.pool[1]))
                if b - a < 1024:
                    out.update(IPv4Address(i) for i in range(a, b + 1))
        return out

    def _alloc_id(self, gip: IPv4Address, proto: int) -> int:
        used = {t.in_global[1] for t in self.table if t.in_global[0] == gip and t.proto == proto}
        for _ in range(64512):
            self._next_id = 1024 + (self._next_id - 1024 + 1) % 64512
            if self._next_id not in used:
                return self._next_id
        raise RuntimeError("NAT ports exhausted")

    def _alloc_pool_ip(self, pool) -> IPv4Address | None:
        a, b = int(IPv4Address(pool[0])), int(IPv4Address(pool[1]))
        used = {t.in_global[0] for t in self.table}
        for i in range(a, b + 1):
            if IPv4Address(i) not in used:
                return IPv4Address(i)
        return None

    # -- inside -> outside --------------------------------------------------------------------------
    def outbound(self, pkt: IPv4Packet, in_port, out_port) -> IPv4Packet | None:
        """Translate the source of a forwarded packet leaving *out_port*. None = drop (no address)."""
        if self.cfg.empty:
            return pkt
        self._expire()
        sid = self._l4_id(pkt, "sport")
        did = self._l4_id(pkt, "dport")
        for t in self.table:
            if t.proto != pkt.protocol or t.in_local[0] != pkt.src:
                continue
            if t.in_local[1] is not None and t.in_local[1] != sid:
                continue
            if t.out_port and out_port is not None and t.out_port != out_port.name:
                continue
            t.last_used = self.now
            self.hits += 1
            return self._with(pkt, src=t.in_global[0],
                              sid=t.in_global[1] if t.in_local[1] is not None else None)
        for rule in self.cfg.rules:
            if rule.kind == "static" and rule.local and IPv4Address(rule.local) == pkt.src:
                if not self._rule_applies_out(rule, in_port, out_port):
                    continue
                if rule.local_port is not None and rule.local_port != sid:
                    continue
                g = IPv4Address(rule.glob)
                gid = rule.global_port if rule.local_port is not None else None
                self.table.append(Translation(pkt.protocol, (pkt.src, rule.local_port), (g, gid),
                                              (pkt.dst, did), self.now, self.now, static=False,
                                              out_port=out_port.name if out_port else None))
                self.hits += 1
                return self._with(pkt, src=g, sid=gid)
        for rule in self.cfg.rules:
            if rule.kind != "dynamic" or not self._rule_applies_out(rule, in_port, out_port):
                continue
            if not self._source_selected(rule, pkt.src):
                continue
            if rule.interface:
                p = self.r.get_port(rule.interface) or out_port
                if p is None or p.ip is None:
                    return None
                gip = p.ip.ip
                overload = True
            elif rule.pool:
                overload = rule.overload
                gip = None
                if not overload:
                    existing = next((t for t in self.table if t.in_local[0] == pkt.src and not t.static
                                     and t.in_local[1] is None), None)
                    gip = existing.in_global[0] if existing else self._alloc_pool_ip(rule.pool)
                else:
                    gip = IPv4Address(rule.pool[0])
                if gip is None:
                    self.misses += 1
                    return None              # pool exhausted: packet dropped (as on IOS)
            else:
                continue
            if overload and sid is not None:
                gid = self._alloc_id(gip, pkt.protocol)
                t = Translation(pkt.protocol, (pkt.src, sid), (gip, gid), (pkt.dst, did), self.now, self.now,
                                out_port=out_port.name if out_port else None)
            else:
                gid = None
                t = Translation(pkt.protocol, (pkt.src, None), (gip, None), (pkt.dst, did), self.now, self.now,
                                out_port=out_port.name if out_port else None)
            self.table.append(t)
            self.misses += 1
            return self._with(pkt, src=gip, sid=gid)
        return pkt

    # -- outside -> inside ---------------------------------------------------------------------------
    def inbound(self, pkt: IPv4Packet, in_port) -> IPv4Packet:
        """Translate the destination of a packet arriving from outside."""
        if self.cfg.empty:
            return pkt
        self._expire()
        cisco_style = bool(self.cfg.outside)
        if cisco_style and not self._role(in_port, "outside"):
            return pkt
        p = pkt.payload
        # ICMP error about a translated packet (traceroute through NAT)
        if isinstance(p, IcmpPacket) and p.original is not None:
            orig = p.original
            oid = self._l4_id(orig, "sport")
            for t in self.table:
                if t.in_global[0] == orig.src and (t.in_global[1] is None or t.in_global[1] == oid) \
                        and t.proto == orig.protocol:
                    t.last_used = self.now
                    new_orig = self._with(orig, src=t.in_local[0],
                                          sid=t.in_local[1] if t.in_global[1] is not None else None)
                    return replace(pkt, dst=t.in_local[0] if pkt.dst == t.in_global[0] else pkt.dst,
                                   payload=replace(p, original=new_orig))
        did = self._l4_id(pkt, "dport")
        for t in self.table:
            if t.proto == pkt.protocol and t.in_global[0] == pkt.dst and \
                    (t.in_global[1] is None or t.in_global[1] == did):
                t.last_used = self.now
                self.hits += 1
                return self._with(pkt, dst=t.in_local[0],
                                  did=t.in_local[1] if t.in_global[1] is not None else None)
        for rule in self.cfg.rules:
            if rule.kind == "static" and rule.glob and IPv4Address(rule.glob) == pkt.dst:
                if rule.global_port is not None:
                    if rule.global_port != did or (rule.proto and PROTO_NAMES.get(pkt.protocol) != rule.proto):
                        continue
                if not cisco_style and rule.out_iface and in_port is not None and in_port.name != rule.out_iface:
                    continue
                local = IPv4Address(rule.local)
                self.table.append(Translation(pkt.protocol, (local, rule.local_port), (pkt.dst, rule.global_port),
                                              (pkt.src, self._l4_id(pkt, "sport")), self.now, self.now))
                self.hits += 1
                return self._with(pkt, dst=local, did=rule.local_port if rule.global_port is not None else None)
            if rule.kind == "dnat" and rule.glob and IPv4Address(rule.glob) == pkt.dst:
                if rule.in_iface and in_port is not None and in_port.name != rule.in_iface:
                    continue
                if rule.proto and PROTO_NAMES.get(pkt.protocol) != rule.proto:
                    continue
                if rule.global_port is not None and rule.global_port != did:
                    continue
                local = IPv4Address(rule.local)
                lp = rule.local_port if rule.local_port is not None else (did if rule.global_port else None)
                self.table.append(Translation(pkt.protocol, (local, lp), (pkt.dst, did if lp else None),
                                              (pkt.src, self._l4_id(pkt, "sport")), self.now, self.now))
                self.hits += 1
                return self._with(pkt, dst=local, did=lp)
        return pkt

    def static_entries(self) -> list[tuple]:
        return [(r.local, r.glob, r.proto, r.local_port, r.global_port)
                for r in self.cfg.rules if r.kind == "static"]
