"""OSPFv3 with a real link-state database.

Every router originates, per area, a Router-LSA (its two-way neighbours and link costs) and
an Intra-Area-Prefix-LSA (its IPv6 prefixes).  LSAs travel in LS Update packets to ff02::5,
are installed when newer (sequence numbers) and re-flooded on the other interfaces of the
area, and a new adjacency receives the whole area database.  Each router then runs SPF on
its own copy of every area's database.  An ABR (interfaces in the backbone and another area)
summarises the intra-area prefixes of each area into the others as Inter-Area-Prefix-LSAs,
so routes are O (intra-area) or OI (inter-area), as on Cisco."""
from __future__ import annotations

import heapq
from ipaddress import IPv6Network

from .v6igp import OSPF6_GROUP, Route6Info, V6Igp, V6IgpMsg

BACKBONE = "0.0.0.0"


class Ospf6(V6Igp):
    def __init__(self, dev, cfg):
        super().__init__(dev, cfg)
        self.lsdb: dict[tuple, dict] = {}          # (area, type, adv router, id) -> {"seq", "data"}
        self._seq = 0x80000001
        self._mine: dict[tuple, object] = {}       # last content originated for each of my keys

    # -- areas ---------------------------------------------------------------------------------
    def areas(self) -> set:
        return {v.get("area", BACKBONE) for v in self.cfg.interfaces.values()}

    def is_abr(self) -> bool:
        a = self.areas()
        return BACKBONE in a and len(a) > 1

    def area_of(self, port) -> str:
        return self.cfg.interfaces.get(port.name, {}).get("area", BACKBONE)

    # -- origination ---------------------------------------------------------------------------
    def _originate(self) -> list:
        """Build my LSAs; returns those whose content changed (to flood)."""
        rid = self.router_id()
        want: dict[tuple, object] = {}
        adj = self._adjacent()
        for area in self.areas():
            links = tuple(sorted((np_.router_id(), self.iface_cost(p)) for p, np_, _ in adj if self.area_of(p) == area))
            want[(area, "router", rid, rid)] = links
            prefixes = tuple(sorted((str(a.network), self.iface_cost(p)) for p in self.enabled_ports()
                                    if self.area_of(p) == area for a in p.ipv6 if not a.network.is_link_local))
            want[(area, "prefix", rid, rid)] = prefixes
        if self.is_abr():
            intra = self._intra_routes()
            for area in self.areas():
                for (src_area, net), cost in intra.items():
                    if src_area != area and (area == BACKBONE or src_area == BACKBONE or True):
                        want[(area, "inter", rid, str(net))] = cost
        changed = []
        for key, data in want.items():
            if self._mine.get(key) != data:
                self._mine[key] = data
                self._seq += 1
                self.lsdb[key] = {"seq": self._seq, "data": data}
                changed.append((key, self._seq, data))
        for key in [k for k in self._mine if k not in want]:          # withdrawn: flush with empty content
            del self._mine[key]
            self._seq += 1
            self.lsdb[key] = {"seq": self._seq, "data": None}
            changed.append((key, self._seq, None))
        return changed

    # -- flooding ------------------------------------------------------------------------------
    def _flood(self, lsas, except_port=None) -> None:
        from ..stack.ipv6 import IPv6Packet
        if not lsas:
            return
        for p in self.enabled_ports():
            if p is except_port:
                continue
            area = self.area_of(p)
            mine = tuple(l for l in lsas if l[0][0] == area)
            if not mine or not any(k[0] == p.name for k in self.neighbors):
                continue
            msg = V6IgpMsg("ospf6", self.router_id(), area, (), self, "lsu", mine)
            self.dev._send6_on(p, IPv6Packet(self.dev.link_local(p).ip, OSPF6_GROUP, 1, msg, next_header=89))

    def _sync(self, port) -> None:
        """New adjacency: send the whole database of the area."""
        area = self.area_of(port)
        lsas = [(k, v["seq"], v["data"]) for k, v in self.lsdb.items() if k[0] == area]
        from ..stack.ipv6 import IPv6Packet
        if lsas:
            msg = V6IgpMsg("ospf6", self.router_id(), area, (), self, "lsu", tuple(lsas))
            self.dev._send6_on(port, IPv6Packet(self.dev.link_local(port).ip, OSPF6_GROUP, 1, msg, next_header=89))

    def receive(self, pkt, port) -> None:
        m = pkt.payload
        if isinstance(m, V6IgpMsg) and m.kind == "ospf6" and getattr(m, "mtype", "hello") == "lsu":
            if port.name not in self.cfg.interfaces or not self.cfg.enabled or m.area != self.area_of(port):
                return
            fresh = []
            for key, seq, data in m.lsas:
                cur = self.lsdb.get(key)
                if key[2] == self.router_id():
                    continue                                   # my own LSA coming back
                if cur is None or seq > cur["seq"]:
                    self.lsdb[key] = {"seq": seq, "data": data}
                    fresh.append((key, seq, data))
            if fresh:
                self._flood(fresh, except_port=port)
                self._refresh()
            return
        before = set(self.neighbors)
        super().receive(pkt, port)
        if set(self.neighbors) != before:
            self._sync(port)

    # -- SPF -------------------------------------------------------------------------------------
    def _refresh(self) -> None:
        self._flood(self._originate())
        self.compute()
        self.dev.engine.emit("routes_changed", self.dev)

    def _recompute_domain(self) -> None:                     # called on adjacency changes
        self._refresh()

    def _spf(self, area):
        """dist and first hop (local port, next-hop link-local) per router id in *area*."""
        rid = self.router_id()
        routers = {k[2]: v["data"] for k, v in self.lsdb.items() if k[0] == area and k[1] == "router" and v["data"] is not None}
        nbr = {}
        for p, np_, ll in self._adjacent():
            if self.area_of(p) == area:
                nbr.setdefault(np_.router_id(), (p, ll))
        dist, first = {rid: 0}, {}
        heap = [(0, rid)]
        done = set()
        while heap:
            c, r = heapq.heappop(heap)
            if r in done:
                continue
            done.add(r)
            for other, cost in routers.get(r, ()):
                if not any(o == r for o, _ in routers.get(other, ())):
                    continue                                   # two-way check: the link must be in both LSAs
                nc = c + cost
                if other not in dist or nc < dist[other]:
                    dist[other] = nc
                    first[other] = nbr.get(other) if r == rid else first.get(r)
                    heapq.heappush(heap, (nc, other))
        return dist, first

    def _intra_routes(self) -> dict:
        """(area, prefix) -> cost for prefixes reachable inside each of my areas (used by an ABR)."""
        out = {}
        for area in self.areas():
            dist, _ = self._spf(area)
            for k, v in self.lsdb.items():
                if k[0] == area and k[1] == "prefix" and v["data"] and k[2] in dist:
                    for net, cost in v["data"]:
                        key = (area, IPv6Network(net))
                        val = dist[k[2]] + cost
                        if key not in out or val < out[key]:
                            out[key] = val
        return out

    def compute(self) -> None:
        mine = {a.network for p in self.dev.ports.values() for a in getattr(p, "ipv6", [])}
        best: dict = {}

        def offer(net, metric, hop, code):
            if net in mine or hop is None:
                return
            cur = best.get(net)
            # intra-area beats inter-area regardless of cost (RFC 2328 16.4)
            if cur is None or (code == "O" and cur.code != "O") or (code == cur.code and metric < cur.metric):
                best[net] = Route6Info(net, hop[0], hop[1], metric, code)

        for area in self.areas():
            dist, first = self._spf(area)
            for k, v in self.lsdb.items():
                if k[0] != area or not v["data"] or k[2] == self.router_id() or k[2] not in dist:
                    continue
                if k[1] == "prefix":
                    for net, cost in v["data"]:
                        offer(IPv6Network(net), dist[k[2]] + cost, first.get(k[2]), "O")
                elif k[1] == "inter" and not (self.is_abr() and area != BACKBONE):
                    offer(IPv6Network(k[3]), dist[k[2]] + v["data"], first.get(k[2]), "OI")
        self.routes = sorted(best.values(), key=lambda r: int(r.network.network_address))

    def kick(self) -> None:
        super().kick()
        self.dev.engine.schedule(600.0, self._refresh)

    def stop(self) -> None:
        super().stop()
        self.lsdb.clear()
        self._mine.clear()

    def database(self) -> list:
        return sorted(((k, v) for k, v in self.lsdb.items() if v["data"] is not None), key=lambda kv: kv[0])
