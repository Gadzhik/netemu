"""IPv6 interior routing: OSPFv3 and RIPng.

Both run on interfaces explicitly enabled for them (``ipv6 ospf 1 area 0``, ``ospfv3 1 area 0``,
``set protocols ospf3 area 0 interface …``, ``ipv6 rip NAME enable`` …).  Routers discover each
other with hellos sent to the link-local groups (OSPFv3 ff02::5, RIPng ff02::9) from their link-local
addresses, so neighbours are real: a cable, a shutdown, a missing ``ipv6 unicast-routing`` or a
protocol enabled only on one side all show.  Routes are then computed over the neighbour graph of
the whole routing domain — shortest path by interface cost for OSPFv3, hop count for RIPng (max 15)
— with the neighbour's link-local address as the next hop, as on real routers."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from ipaddress import IPv6Address, IPv6Network

OSPF6_GROUP = IPv6Address("ff02::5")
RIPNG_GROUP = IPv6Address("ff02::9")
TIMERS = {"ospf6": (10000.0, 40000.0), "ripng": (30000.0, 180000.0)}   # hello/update, dead


@dataclass(frozen=True)
class V6IgpMsg:
    kind: str                       # ospf6 | ripng
    router_id: str
    area: str = "0.0.0.0"
    prefixes: tuple = ()            # RIPng: advertised prefixes (for the packet details)
    proc: object = field(default=None, compare=False, repr=False)   # sender (simulator bookkeeping)
    mtype: str = "hello"            # OSPFv3: hello | lsu
    lsas: tuple = ()                # OSPFv3 LS Update: ((area, type, adv, id), seq, data)

    @property
    def label(self) -> str:
        return "OSPFv3" if self.kind == "ospf6" else "RIPng"

    @property
    def name(self) -> str:
        if self.kind == "ospf6":
            return "LS Update" if self.mtype == "lsu" else "Hello"
        return "Response"

    def layer_lines(self) -> list[str]:
        if self.kind == "ospf6" and self.mtype == "lsu":
            out = ["OSPFv3 LS Update", f"Router ID: {self.router_id}", f"Area: {self.area}", f"LSAs: {len(self.lsas)}"]
            names = {"router": "Router-LSA", "prefix": "Intra-Area-Prefix-LSA", "inter": "Inter-Area-Prefix-LSA"}
            for (area, typ, adv, lid), seq, data in self.lsas[:8]:
                out.append(f"  {names.get(typ, typ)} adv {adv} id {lid} seq 0x{seq:08x}" + ("" if data is not None else " (flushed)"))
            return out
        if self.kind == "ospf6":
            return ["OSPFv3 Hello", f"Router ID: {self.router_id}", f"Area: {self.area}"]
        return ["RIPng Response"] + [f"Route: {p} metric 1" for p in self.prefixes[:8]]


@dataclass
class V6IgpConfig:
    kind: str
    tag: str = "1"                 # process id / RIPng name
    router_id: str | None = None
    interfaces: dict = field(default_factory=dict)      # port name -> {"area": "0.0.0.0", "cost": int|None}
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"kind": self.kind, "tag": self.tag, "router_id": self.router_id,
                "interfaces": {k: dict(v) for k, v in self.interfaces.items()}, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict) -> "V6IgpConfig":
        return cls(d["kind"], str(d.get("tag", "1")), d.get("router_id"),
                   {k: dict(v) for k, v in d.get("interfaces", {}).items()}, d.get("enabled", True))


@dataclass
class Route6Info:
    network: IPv6Network
    port: object
    next_hop: IPv6Address
    metric: int
    code: str


class V6Igp:
    def __init__(self, dev, cfg: V6IgpConfig):
        self.dev, self.cfg = dev, cfg
        self.neighbors: dict[tuple[str, str], tuple] = {}   # (port, rid) -> (link-local, seen, process)
        self.routes: list[Route6Info] = []
        self._timer = None
        self._last = -1e18

    # -- identity ------------------------------------------------------------------------------
    @property
    def kind(self) -> str:
        return self.cfg.kind

    def router_id(self) -> str:
        if self.cfg.router_id:
            return self.cfg.router_id
        rid = self.dev.auto_router_id() if hasattr(self.dev, "auto_router_id") else None
        if rid is not None:
            return str(rid)
        p = next(iter(self.dev.ports.values()), None)
        return "0.0.0.%d" % ((p.mac.value & 0xFF) if p else 1) if p else "0.0.0.1"

    def iface_cost(self, port) -> int:
        if self.kind == "ripng":
            return 1
        c = self.cfg.interfaces.get(port.name, {}).get("cost")
        if c:
            return int(c)
        return 1 if port.name.lower().startswith(("gi", "ge", "ether", "10g", "xe")) or port.is_svi else 10

    def enabled_ports(self):
        if not self.cfg.enabled:
            return []
        out = []
        for name in self.cfg.interfaces:
            p = self.dev.ports.get(name)
            if p is not None and p.is_up and self.dev.link_local(p) is not None:
                out.append(p)
        return out

    # -- timers / hellos --------------------------------------------------------------------------
    def start(self) -> None:
        if self._timer is None:
            self._timer = self.dev.engine.schedule(500.0, self._tick)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancelled = True
            self._timer = None
        self.neighbors.clear()
        self.routes = []

    def kick(self) -> None:
        self._last = -1e18
        self.start()

    def _tick(self) -> None:
        self._timer = None
        now = self.dev.engine.now
        hello, dead = TIMERS[self.kind]
        self.neighbors = {k: v for k, v in self.neighbors.items() if now - v[1] <= dead
                          and k[0] in {p.name for p in self.enabled_ports()}}
        if now - self._last >= hello:
            self._last = now
            self._send_hellos()
        if set(self.neighbors) != getattr(self, "_snapshot", None):
            self._snapshot = set(self.neighbors)
            self._recompute_domain()
        if self.cfg.enabled and self.cfg.interfaces:
            self._timer = self.dev.engine.schedule(1000.0, self._tick)

    def _send_hellos(self) -> None:
        from ..stack.ipv6 import IPv6Packet
        group = OSPF6_GROUP if self.kind == "ospf6" else RIPNG_GROUP
        for p in self.enabled_ports():
            area = self.cfg.interfaces.get(p.name, {}).get("area", "0.0.0.0")
            pref = tuple(str(r.network) for r in self.routes[:8]) + tuple(str(a.network) for a in p.ipv6)
            msg = V6IgpMsg(self.kind, self.router_id(), area, pref if self.kind == "ripng" else (), self)
            self.dev._send6_on(p, IPv6Packet(self.dev.link_local(p).ip, group, 1, msg, next_header=89 if
                                             self.kind == "ospf6" else 17))

    def receive(self, pkt, port) -> None:
        m = pkt.payload
        if not isinstance(m, V6IgpMsg) or m.kind != self.kind or port.name not in self.cfg.interfaces:
            return
        if not self.cfg.enabled:
            return
        if self.kind == "ospf6" and m.area != self.cfg.interfaces[port.name].get("area", "0.0.0.0"):
            return                                   # area mismatch: no adjacency
        key = (port.name, m.router_id)
        new = key not in self.neighbors
        self.neighbors[key] = (pkt.src, self.dev.engine.now, m.proc)
        if new:
            self._send_hellos()                      # answer at once, as OSPF does on a new neighbour
            if self.kind == "ospf6":
                self.dev.console(f"%OSPFv3-5-ADJCHG: Process {self.cfg.tag}, Nbr {m.router_id} on {port.short} "
                                 "from LOADING to FULL, Loading Done")
            self._recompute_domain()

    # -- routes ---------------------------------------------------------------------------------------
    def _adjacent(self):
        """[(local port, neighbour process, neighbour link-local)] with two-way hellos."""
        out = []
        me = self.router_id()
        for (pname, rid), (ll, _, proc) in self.neighbors.items():
            p = self.dev.ports.get(pname)
            if p is None or proc is None:
                continue
            if any(k[1] == me for k in proc.neighbors):
                out.append((p, proc, ll))
        return out

    def _domain(self):
        seen, todo = {id(self): self}, [self]
        while todo:
            proc = todo.pop()
            for _, np_, _ in proc._adjacent():
                if id(np_) not in seen:
                    seen[id(np_)] = np_
                    todo.append(np_)
        return list(seen.values())

    def _recompute_domain(self) -> None:
        for proc in self._domain():
            proc.compute()
            proc.dev.engine.emit("routes_changed", proc.dev)

    def compute(self) -> None:
        dist = {id(self): 0}
        first: dict = {}                 # process id -> (local port, next hop link-local)
        procs = {id(self): self}
        heap = [(0, 0, id(self))]
        tie = 1
        done = set()
        while heap:
            cost, _, pid = heapq.heappop(heap)
            if pid in done:
                continue
            done.add(pid)
            cur = procs[pid]
            for p, np_, ll in cur._adjacent():
                c = cost + cur.iface_cost(p)
                if self.kind == "ripng" and c > 15:
                    continue
                if id(np_) not in dist or c < dist[id(np_)]:
                    dist[id(np_)] = c
                    procs[id(np_)] = np_
                    first[id(np_)] = (p, ll) if pid == id(self) else first[pid]
                    heapq.heappush(heap, (c, tie, id(np_)))
                    tie += 1
        mine = {a.network for p in self.dev.ports.values() for a in getattr(p, "ipv6", [])}
        best: dict = {}
        for pid, cost in dist.items():
            if pid == id(self) or pid not in first:
                continue
            proc = procs[pid]
            for p in proc.enabled_ports():
                for a in p.ipv6:
                    if a.network in mine or a.network.is_link_local:
                        continue
                    metric = cost + (proc.iface_cost(p) if self.kind == "ospf6" else 1)
                    if self.kind == "ripng" and metric > 15:
                        continue
                    cur = best.get(a.network)
                    if cur is None or metric < cur.metric:
                        lp, ll = first[pid]
                        best[a.network] = Route6Info(a.network, lp, ll, metric, "O" if self.kind == "ospf6" else "R")
        self.routes = sorted(best.values(), key=lambda r: int(r.network.network_address))


def distance(dev, kind: str) -> int:
    v = getattr(dev, "vendor", "cisco")
    if kind == "ospf6":
        return 10 if v in ("huawei", "juniper") else 110
    return 100 if v in ("huawei", "juniper") else 120


def make_process(dev, cfg: V6IgpConfig) -> V6Igp:
    if cfg.kind == "ospf6":
        from .ospf6 import Ospf6
        return Ospf6(dev, cfg)
    return V6Igp(dev, cfg)


def ensure(dev, kind: str, tag: str = "1") -> V6Igp:
    procs = dev.v6igp
    if kind not in procs:
        procs[kind] = make_process(dev, V6IgpConfig(kind, tag))
    return procs[kind]


def enable_iface(dev, kind: str, port, area: str = "0.0.0.0", tag: str = "1", cost=None, on: bool = True) -> None:
    proc = ensure(dev, kind, tag)
    if on:
        entry = proc.cfg.interfaces.setdefault(port.name, {"area": area})
        entry["area"] = area
        if cost is not None:
            entry["cost"] = cost
        proc.kick()
    else:
        proc.cfg.interfaces.pop(port.name, None)
        proc.neighbors = {k: v for k, v in proc.neighbors.items() if k[0] != port.name}
        proc._recompute_domain()
