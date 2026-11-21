"""IS-IS (level 2), simplified but link-state for real.

* IS-IS runs over CLNS, not IP: hellos and LSPs are Ethernet frames (ethertype shown as "ISIS")
  to the AllL2ISs MAC 01:80:C2:00:00:15.
* A router is identified by its NET, e.g. 49.0001.0000.0000.0001.00 (area 49.0001,
  system ID 0000.0000.0001).  Level-2 adjacencies form across areas.
* Hellos (IIH) every 3 s, hold time 10 s; an adjacency is Up once each side lists the other
  in its hello on that interface (three-way handshake).
* Every router floods its LSP (sequence number, adjacent systems with the interface metric,
  IP prefixes of its IS-IS interfaces).  On a new adjacency the whole database is sent.
* SPF (Dijkstra, two-way checked) gives ``i L2`` routes (administrative distance 115).
"""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from ..addresses import MacAddress
from ..stack.ipv4 import Route

ALL_L2_IS = MacAddress(0x0180C2000015)
ETH_ISIS = "ISIS"
HELLO_MS, HOLD_MS = 3000, 10000
DISTANCE = 115
NET_RE = re.compile(r"^(49(?:\.[0-9a-fA-F]{4})+)\.([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\.00$")


def parse_net(net: str) -> tuple[str, str] | None:
    """'49.0001.0000.0000.0001.00' -> ('49.0001', '0000.0000.0001')"""
    m = NET_RE.match(net.strip())
    return (m.group(1), m.group(2).lower()) if m else None


@dataclass(frozen=True)
class IsisPdu:
    kind: str                      # iih | lsp
    system_id: str
    area: str = ""
    seen: tuple = ()               # IIH: system IDs heard on this interface
    ip: str = ""                   # IIH: interface IPv4 address (IP interface address TLV)
    hostname: str = ""
    lsps: tuple = ()               # LSP: ((system id, seq, hostname, neighbours, prefixes), ...)
    label = "IS-IS"

    def summary(self) -> str:
        if self.kind == "iih":
            return f"L2 IIH from {self.system_id}"
        return "L2 LSP " + ", ".join(f"{s[0]}.00-00 seq 0x{s[1]:08x}" for s in self.lsps)

    def layer_lines(self) -> list[str]:
        if self.kind == "iih":
            return ["PDU type: L2 LAN IIH", f"System ID: {self.system_id}", f"Area address: {self.area}",
                    f"IP interface address: {self.ip}", "IS neighbors: " + (", ".join(self.seen) or "-")]
        out = ["PDU type: L2 LSP"]
        for sid, seq, host, nbrs, pfx in self.lsps:
            out.append(f"LSP {sid}.00-00 ({host}) seq 0x{seq:08x}")
            out += [f"  IS neighbor {n} metric {m}" for n, m in nbrs]
            out += [f"  IP prefix {p} metric {m}" for p, m in pfx]
        return out


@dataclass
class IsisConfig:
    tag: str = "1"
    net: str | None = None
    interfaces: list = field(default_factory=list)
    passive: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)          # interface -> metric
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"tag": self.tag, "net": self.net, "interfaces": list(self.interfaces), "passive": list(self.passive),
                "metrics": dict(self.metrics), "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict) -> "IsisConfig":
        return cls(str(d.get("tag", "1")), d.get("net"), list(d.get("interfaces", [])), list(d.get("passive", [])),
                   dict(d.get("metrics", {})), bool(d.get("enabled", True)))


@dataclass
class Adjacency:
    system_id: str
    port: object
    ip: IPv4Address | None
    hostname: str
    state: str                     # init | up
    last_seen: float
    up_since: float = 0.0


class IsisProcess:
    def __init__(self, router, cfg: IsisConfig):
        self.r = router
        self.cfg = cfg
        self.engine = router.engine
        self.adj: dict[tuple, Adjacency] = {}             # (port name, system id) -> Adjacency
        self.lsdb: dict[str, dict] = {}                   # system id -> {"seq", "hostname", "neighbors", "prefixes"}
        self.routes: list[Route] = []
        self.running = False
        self._seq = 0
        self._timer = None

    # -- identity ------------------------------------------------------------------------------
    @property
    def system_id(self) -> str | None:
        p = parse_net(self.cfg.net or "")
        return p[1] if p else None

    @property
    def area(self) -> str:
        p = parse_net(self.cfg.net or "")
        return p[0] if p else ""

    def enabled_on(self, port) -> bool:
        return port.name in self.cfg.interfaces and port.ip is not None and port.is_up

    def metric(self, port) -> int:
        return int(self.cfg.metrics.get(port.name, 10))

    def passive(self, port) -> bool:
        return port.is_loopback or port.name in self.cfg.passive

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        if self.running or self.system_id is None:
            return
        self.running = True
        self._timer = self.engine.schedule(100.0, self._tick)

    def stop(self) -> None:
        self.running = False
        if self._timer is not None:
            self._timer.cancelled = True
        self.adj.clear()
        self.lsdb.clear()
        self.routes = []
        self.r.routes_changed()

    def restart(self) -> None:
        self.stop()
        self.start()

    def _tick(self) -> None:
        if not self.running:
            return
        now = self.engine.now
        lost = [k for k, a in self.adj.items() if now - a.last_seen > HOLD_MS or not a.port.is_up
                or not self.enabled_on(a.port)]
        for k in lost:
            a = self.adj.pop(k)
            if a.state == "up":
                self.r.mgmt.log(5, f"%CLNS-5-ADJCHANGE: ISIS: Adjacency to {a.hostname} ({a.port.name}) Down, "
                                   "hold time expired")
        for p in self.r.ports.values():
            if self.enabled_on(p) and not self.passive(p):
                seen = tuple(sorted(a.system_id for a in self.adj.values() if a.port is p))
                self._send(p, IsisPdu("iih", self.system_id, self.area, seen, str(p.ip.ip), self.r.hostname))
        self._refresh(force=bool(lost))
        self._timer = self.engine.schedule(float(HELLO_MS), self._tick)

    def link_changed(self, port) -> None:
        if self.running:
            self.engine.schedule(0.0, self._tick_once)

    def _tick_once(self) -> None:
        for k in [k for k, a in self.adj.items() if not a.port.is_up or not self.enabled_on(a.port)]:
            self.adj.pop(k)
        self._refresh()

    # -- LSPs ----------------------------------------------------------------------------------
    def _own_lsp(self) -> dict:
        nbrs = sorted((a.system_id, self.metric(a.port)) for a in self.adj.values() if a.state == "up")
        pfx = sorted((str(p.ip.network), self.metric(p) if not p.is_loopback else 0)
                     for p in self.r.ports.values() if self.enabled_on(p))
        return {"hostname": self.r.hostname, "neighbors": nbrs, "prefixes": pfx}

    def _refresh(self, force: bool = False) -> None:
        if not self.running:
            return
        mine = self._own_lsp()
        cur = self.lsdb.get(self.system_id)
        if cur is None or force or {k: cur[k] for k in mine} != mine:
            self._seq += 1
            self.lsdb[self.system_id] = dict(mine, seq=self._seq)
            self._flood([self.system_id])
        self._spf()

    def _lsp_tuple(self, sid):
        e = self.lsdb[sid]
        return (sid, e["seq"], e["hostname"], tuple(map(tuple, e["neighbors"])), tuple(map(tuple, e["prefixes"])))

    def _flood(self, sids, except_port=None) -> None:
        lsps = tuple(self._lsp_tuple(s) for s in sids if s in self.lsdb)
        if not lsps:
            return
        for p in {a.port for a in self.adj.values() if a.state == "up"}:
            if p is not except_port:
                self._send(p, IsisPdu("lsp", self.system_id, self.area, lsps=lsps))

    def _send(self, port, pdu) -> None:
        from ..packets import EthernetFrame
        port.send(EthernetFrame(port.mac, ALL_L2_IS, ETH_ISIS, pdu))

    def receive(self, frame, port) -> None:
        pdu = frame.payload
        if not self.running or not isinstance(pdu, IsisPdu) or not self.enabled_on(port) or self.passive(port):
            return
        if pdu.system_id == self.system_id:
            return
        now = self.engine.now
        key = (port.name, pdu.system_id)
        if pdu.kind == "iih":
            a = self.adj.get(key)
            if a is None:
                a = self.adj[key] = Adjacency(pdu.system_id, port, IPv4Address(pdu.ip) if pdu.ip else None,
                                              pdu.hostname or pdu.system_id, "init", now)
            a.last_seen, a.hostname = now, pdu.hostname or a.hostname
            if pdu.ip:
                a.ip = IPv4Address(pdu.ip)
            if self.system_id in pdu.seen and a.state != "up":
                a.state, a.up_since = "up", now
                self.r.mgmt.log(5, f"%CLNS-5-ADJCHANGE: ISIS: Adjacency to {a.hostname} ({port.name}) Up, new adjacency")
                self._refresh()
                self._send(port, IsisPdu("lsp", self.system_id, self.area,
                                         lsps=tuple(self._lsp_tuple(s) for s in self.lsdb)))
            return
        if key not in self.adj or self.adj[key].state != "up":
            return
        newer = []
        for sid, seq, host, nbrs, pfx in pdu.lsps:
            if sid == self.system_id:
                if seq >= self._seq:                       # an old copy of ours survived: outdo it
                    self._seq = seq + 1
                    self.lsdb[sid]["seq"] = self._seq
                    newer.append(sid)
                continue
            cur = self.lsdb.get(sid)
            if cur is None or seq > cur["seq"]:
                self.lsdb[sid] = {"seq": seq, "hostname": host, "neighbors": [list(n) for n in nbrs],
                                  "prefixes": [list(p) for p in pfx]}
                newer.append(sid)
        if newer:
            self._flood(newer, except_port=port)
            self._spf()

    # -- SPF ---------------------------------------------------------------------------------------
    def _spf(self) -> None:
        me = self.system_id
        graph = {}
        for sid, e in self.lsdb.items():
            graph[sid] = {n: m for n, m in e["neighbors"]}
        dist, first = {me: 0}, {me: None}
        heap = [(0, me)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, 1 << 30):
                continue
            for v, m in graph.get(u, {}).items():
                if u not in graph.get(v, {}):                # two-way check
                    continue
                nd = d + int(m)
                if nd < dist.get(v, 1 << 30):
                    dist[v] = nd
                    first[v] = v if u == me else first[u]
                    heapq.heappush(heap, (nd, v))
        own = {p.ip.network for p in self.r.ports.values() if p.ip is not None and p.is_up}
        best: dict[IPv4Network, tuple] = {}
        for sid, d in dist.items():
            if sid == me:
                continue
            hop = first[sid]
            adj = next((a for a in self.adj.values() if a.system_id == hop and a.state == "up"), None)
            if adj is None or adj.ip is None:
                continue
            for pfx, m in self.lsdb.get(sid, {}).get("prefixes", []):
                net = IPv4Network(pfx)
                if net in own:
                    continue
                total = d + int(m)
                if net not in best or total < best[net][0]:
                    best[net] = (total, adj)
        ad = {"huawei": 15, "juniper": 18}.get(getattr(self.r, "vendor", ""), DISTANCE)
        routes = [Route(net, "i L2", a.port, a.ip, ad, m) for net, (m, a) in
                  sorted(best.items(), key=lambda x: (int(x[0].network_address), x[0].prefixlen))]
        if [(r.network, r.next_hop, r.metric) for r in routes] != [(r.network, r.next_hop, r.metric) for r in self.routes]:
            self.routes = routes
            self.r.routes_changed()
