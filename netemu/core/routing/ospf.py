"""OSPFv2 (RFC 2328, simplified but behaviourally faithful).

What is modelled
  * Hello protocol on broadcast segments: neighbour discovery, 2-Way check,
    hello/dead interval and subnet-mask/area matching (mismatches prevent
    adjacencies, as in real labs), DR/BDR election shown in neighbour tables.
  * Link-state flooding: router LSAs (type 1) per area, summary LSAs (type 3)
    and ASBR summaries (type 4) originated by ABRs, AS-external LSAs (type 5)
    for redistribution and ``default-information originate``.
  * SPF (Dijkstra) per area with ECMP, intra-area (O), inter-area (O IA) and
    external type-2 (O E2) routes, administrative distance 110.

What is simplified
  * Database exchange (ExStart/Exchange/Loading) is collapsed: when two
    routers reach 2-Way they send each other their whole LSDB and become Full.
  * Every adjacency is treated as point-to-point in the LSDB (no network LSAs);
    the resulting routes are the same as with DR-based transit networks.
  * No LSA aging/refresh; unreachable routers are simply ignored by SPF.
"""
from __future__ import annotations

import heapq
import itertools
import zlib
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from ..addresses import MacAddress
from ..packets import ETH_IPV4, IPv4Packet, OspfPacket, PROTO_OSPF
from ..stack.ipv4 import Route

if TYPE_CHECKING:
    from ..devices.router import Router
    from ..port import Port

ALL_SPF_ROUTERS = IPv4Address("224.0.0.5")
OSPF_MAC = MacAddress(0x01005E000005)
OSPF_DISTANCE = 110
DEFAULT_HELLO = 10.0     # seconds
DEFAULT_DEAD = 40.0
BACKBONE = "0.0.0.0"
INFINITY = 1 << 24


def area_id(text) -> str:
    """'0' -> '0.0.0.0', '1' -> '0.0.0.1', '0.0.0.1' stays."""
    t = str(text).strip()
    if t.isdigit():
        return str(IPv4Address(int(t)))
    return str(IPv4Address(t))


def wildcard_to_prefix(addr: str, wildcard: str) -> IPv4Network:
    mask = IPv4Address(int(IPv4Address(wildcard)) ^ 0xFFFFFFFF)
    return IPv4Network(f"{addr}/{mask}", strict=False)


@dataclass
class OspfConfig:
    process_id: int = 1
    router_id: str | None = None
    networks: list[dict] = field(default_factory=list)       # {"prefix": "10.0.0.0/24", "area": "0.0.0.0"}
    interfaces: dict[str, dict] = field(default_factory=dict)  # explicit (Junos): name -> {"area", "passive", "cost"}
    passive: list[str] = field(default_factory=list)
    passive_default: bool = False
    non_passive: list[str] = field(default_factory=list)       # "no passive-interface X" with passive default
    costs: dict[str, int] = field(default_factory=dict)
    hello: dict[str, float] = field(default_factory=dict)       # per-interface hello interval
    dead: dict[str, float] = field(default_factory=dict)
    priority: dict[str, int] = field(default_factory=dict)
    default_originate: str | None = None                       # None | "yes" | "always"
    redistribute: list[str] = field(default_factory=list)      # "static", "connected"
    reference_bandwidth: int = 100                              # Mbit/s (Cisco default)
    enabled: bool = True          # False: only per-interface settings, no "router ospf" yet
    export: list[str] = field(default_factory=list)              # Junos export policy names
    # RouterOS v7: instance / areas / interface templates
    templates: list[dict] = field(default_factory=list)  # {"networks", "interfaces", "area", "area_name", ...}
    mt_instance: str = "default-v2"
    mt_areas: dict[str, str] = field(default_factory=dict)       # area name -> area id

    def has_interface_settings(self) -> bool:
        return bool(self.costs or self.hello or self.dead or self.priority)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "OspfConfig":
        c = cls()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c


@dataclass
class Neighbor:
    rid: IPv4Address
    ip: IPv4Address
    port: "Port"
    priority: int = 1
    last_seen: float = 0.0
    two_way: bool = False
    dr: IPv4Address | None = None
    bdr: IPv4Address | None = None
    full_since: float = 0.0


@dataclass
class Lsa:
    type: int                  # 1 router, 3 summary, 4 asbr-summary, 5 external
    area: str | None           # None for type 5
    ls_id: IPv4Address         # router id / network / asbr id
    adv: IPv4Address           # advertising router
    seq: int
    body: tuple = ()           # type 1: tuple of links; 3/4: (mask_len, metric); 5: (mask_len, metric, etype)

    @property
    def key(self):
        return (self.area, self.type, self.ls_id, self.adv)


# router LSA link records: ("router", nbr_rid, cost, local_ip) | ("stub", network, cost)


class OspfProcess:
    def __init__(self, router: "Router", config: OspfConfig):
        self.r = router
        self.cfg = config
        self.engine = router.engine
        self.neighbors: dict[tuple[str, IPv4Address], Neighbor] = {}
        self.lsdb: dict[tuple, Lsa] = {}
        self._seq = itertools.count(0x80000001)
        self._timers: dict[str, object] = {}
        self._spf_pending = False
        self.routes: list[Route] = []
        self._rid: IPv4Address | None = None
        self.running = False
        self.spf_runs = 0

    # -- identity --------------------------------------------------------------------
    @property
    def router_id(self) -> IPv4Address:
        if self.cfg.router_id:
            return IPv4Address(self.cfg.router_id)
        if getattr(self.r, "global_router_id", None):
            return IPv4Address(self.r.global_router_id)
        if self._rid is None:
            rid = self.r.auto_router_id()
            if rid is None:
                return IPv4Address("0.0.0.0")      # no address yet: process cannot start (IOS NORTRID)
            self._rid = rid
        return self._rid

    def reset_router_id(self) -> None:
        self._rid = None

    # -- interfaces --------------------------------------------------------------------
    def template(self, port: "Port") -> dict | None:
        """RouterOS interface-template that matches the port (by interface or network)."""
        if port.ip is None:
            return None
        for t in self.cfg.templates:
            if t.get("interfaces") and port.name not in t["interfaces"]:
                continue
            nets = t.get("networks") or []
            if nets and not any(port.ip.ip in IPv4Network(n) for n in nets):
                continue
            if t.get("area") is None:
                continue
            return t
        return None

    def _opt(self, port, key):
        ex = self.cfg.interfaces.get(port.name) or self.template(port) or {}
        return ex.get(key)

    def iface_area(self, port: "Port") -> str | None:
        """Area the port runs OSPF in, or None."""
        if port.ip is None:
            return None
        ex = self.cfg.interfaces.get(port.name)
        if ex is not None:
            return ex.get("area", BACKBONE)
        t = self.template(port)
        if t is not None:
            return t["area"]
        for n in self.cfg.networks:
            if port.ip.ip in IPv4Network(n["prefix"]):
                return n["area"]
        return None

    def is_passive(self, port: "Port") -> bool:
        if port.is_loopback:
            return True
        if self._opt(port, "passive"):
            return True
        if self.cfg.passive_default:
            return port.name not in self.cfg.non_passive
        return port.name in self.cfg.passive

    def cost(self, port: "Port") -> int:
        if self._opt(port, "cost"):
            return int(self._opt(port, "cost"))
        if port.name in self.cfg.costs:
            return int(self.cfg.costs[port.name])
        if port.is_loopback:
            return 0 if self.r.vendor == "juniper" else 1
        if self.r.vendor == "mikrotik":
            return 1                       # RouterOS: cost 1 regardless of speed
        bw = 100 if port.name.lower().startswith(("fast", "fa")) else 1000
        return max(1, self.cfg.reference_bandwidth // bw)

    def hello_interval(self, port) -> float:
        if self._opt(port, "hello"):
            return float(self._opt(port, "hello"))
        return float(self.cfg.hello.get(port.name, DEFAULT_HELLO))

    def dead_interval(self, port) -> float:
        if self._opt(port, "dead"):
            return float(self._opt(port, "dead"))
        if self._opt(port, "hello"):
            return 4 * float(self._opt(port, "hello"))
        if port.name in self.cfg.dead:
            return float(self.cfg.dead[port.name])
        return 4 * self.hello_interval(port) if port.name in self.cfg.hello else DEFAULT_DEAD

    def priority(self, port) -> int:
        default = 128 if self.r.vendor in ("juniper", "mikrotik") else 1
        if self._opt(port, "priority") is not None:
            return int(self._opt(port, "priority"))
        return int(self.cfg.priority.get(port.name, default))

    def active_ports(self) -> list["Port"]:
        return [p for p in self.r.ports.values() if p.is_up and self.iface_area(p) is not None]

    def areas(self) -> set[str]:
        return {self.iface_area(p) for p in self.active_ports()}

    @property
    def is_abr(self) -> bool:
        a = self.areas()
        return BACKBONE in a and len(a) > 1

    @property
    def is_asbr(self) -> bool:
        return bool(self.cfg.default_originate or self.cfg.redistribute)

    # -- lifecycle -------------------------------------------------------------------------
    HOUSEKEEPING_MS = 1000.0

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        for p in self.r.ports.values():
            self._ensure_hello(p, first=True)
        self.originate()
        self._timers["__housekeeping"] = self.engine.schedule(self.HOUSEKEEPING_MS, self._housekeeping)

    def _housekeeping(self) -> None:
        """Pick up address/route changes made anywhere (CLI, forms) within a second."""
        if not self.running:
            return
        for key in [k for k, n in self.neighbors.items()
                    if not n.port.is_up or self.iface_area(n.port) is None or self.is_passive(n.port)]:
            del self.neighbors[key]
        self._check_dead()
        for p in self.r.ports.values():
            self._ensure_hello(p, first=True)
        self.originate()
        self._timers["__housekeeping"] = self.engine.schedule(self.HOUSEKEEPING_MS, self._housekeeping)

    def stop(self) -> None:
        self.running = False
        for ev in self._timers.values():
            ev.cancelled = True
        self._timers.clear()
        self.neighbors.clear()
        self.lsdb.clear()
        self.routes = []
        self.r.routes_changed()

    def reconfigure(self) -> None:
        """Configuration changed (networks, costs, passive, ...)."""
        if not self.running:
            return
        for key in [k for k, n in self.neighbors.items()
                    if self.iface_area(n.port) is None or self.is_passive(n.port)]:
            del self.neighbors[key]
        for p in self.r.ports.values():
            self._ensure_hello(p, first=True)
        self.originate()

    def link_changed(self, port: "Port") -> None:
        if not self.running:
            return
        if not port.is_up:
            for key in [k for k, n in self.neighbors.items()
                        if n.port is port or (n.port.is_sub and n.port.parent is port)]:
                del self.neighbors[key]
        self._ensure_hello(port, first=True)
        for sub in port.subinterfaces:
            self._ensure_hello(sub, first=True)
        self.originate()

    # -- hello -----------------------------------------------------------------------------------
    def _ensure_hello(self, port: "Port", first: bool = False) -> None:
        ev = self._timers.get(port.name)
        wanted = (self.running and port.is_up and self.iface_area(port) is not None
                  and not self.is_passive(port))
        if not wanted:
            if ev is not None:
                ev.cancelled = True
                del self._timers[port.name]
            return
        if ev is None or ev.cancelled:
            jitter = zlib.crc32(f"{self.r.id}{port.name}".encode()) % 200
            delay = 0.05 + jitter / 1000.0 if first else 0
            self._timers[port.name] = self.engine.schedule(delay * 1000, self._hello_tick, port)

    def _hello_tick(self, port: "Port") -> None:
        self._timers.pop(port.name, None)
        if not (self.running and port.is_up and self.iface_area(port) is not None
                and not self.is_passive(port)):
            return
        self._check_dead()
        self.send_hello(port)
        self._timers[port.name] = self.engine.schedule(self.hello_interval(port) * 1000,
                                                        self._hello_tick, port)

    def _check_dead(self) -> None:
        now = self.engine.now
        dead = [k for k, n in self.neighbors.items()
                if now - n.last_seen > self.dead_interval(n.port) * 1000]
        if dead:
            for k in dead:
                n = self.neighbors.pop(k)
                self.r.console(f"%OSPF-5-ADJCHG: Process {self.cfg.process_id}, Nbr {n.rid} on "
                               f"{n.port.name} from FULL to DOWN, Neighbor Down: Dead timer expired")
            self.originate()

    def send_hello(self, port: "Port") -> None:
        if int(self.router_id) == 0:
            return
        dr, bdr = self.dr_bdr(port)
        seen = tuple(sorted(n.rid for n in self.neighbors.values() if n.port is port))
        pkt = OspfPacket("hello", self.router_id, self.iface_area(port),
                         hello=(int(self.hello_interval(port)), int(self.dead_interval(port)),
                                port.ip.network.prefixlen, self.priority(port), dr, bdr, seen))
        self._send(port, pkt)

    def _send(self, port: "Port", pkt: OspfPacket) -> None:
        ip = IPv4Packet(port.ip.ip, ALL_SPF_ROUTERS, 1, pkt, PROTO_OSPF, next(self.r._ip_ident))
        self.r._send_frame(port, OSPF_MAC, ETH_IPV4, ip)

    def dr_bdr(self, port: "Port") -> tuple[IPv4Address | None, IPv4Address | None]:
        cands = [(self.priority(port), self.router_id, port.ip.ip)] if self.priority(port) > 0 else []
        cands += [(n.priority, n.rid, n.ip) for n in self.neighbors.values()
                  if n.port is port and n.two_way and n.priority > 0]
        cands.sort(reverse=True)
        dr = cands[0][2] if cands else None
        bdr = cands[1][2] if len(cands) > 1 else None
        return dr, bdr

    def neighbor_state(self, n: Neighbor) -> str:
        if not n.two_way:
            return "INIT/DROTHER"
        dr, bdr = self.dr_bdr(n.port)
        role = "DR" if n.ip == dr else "BDR" if n.ip == bdr else "DROTHER"
        me_dr = n.port.ip.ip in (dr, bdr)
        if role != "DROTHER" or me_dr:
            return f"FULL/{role}"
        return f"2WAY/{role}"

    # -- receive -----------------------------------------------------------------------------------
    def receive(self, ip: IPv4Packet, port: "Port") -> None:
        if not self.running or port.ip is None:
            return
        pkt: OspfPacket = ip.payload
        area = self.iface_area(port)
        if area is None or self.is_passive(port) or pkt.router_id == self.router_id:
            return
        if pkt.area != area:
            return            # area mismatch: packet ignored (no adjacency)
        if pkt.kind == "hello":
            self._on_hello(pkt, ip.src, port)
        elif pkt.kind == "lsu":
            key = (port.name, pkt.router_id)
            if key in self.neighbors and self.neighbors[key].two_way:
                self._on_lsu(pkt.lsas, port)

    def _on_hello(self, pkt: OspfPacket, src: IPv4Address, port: "Port") -> None:
        hello, dead, plen, prio, dr, bdr, seen = pkt.hello
        if (hello != int(self.hello_interval(port)) or dead != int(self.dead_interval(port))
                or plen != port.ip.network.prefixlen or src not in port.ip.network):
            return            # parameter mismatch: no adjacency, as on real routers
        key = (port.name, pkt.router_id)
        n = self.neighbors.get(key)
        new = n is None
        if new:
            n = self.neighbors[key] = Neighbor(pkt.router_id, src, port)
        n.ip, n.priority, n.dr, n.bdr = src, prio, dr, bdr
        n.last_seen = self.engine.now
        was = n.two_way
        n.two_way = self.router_id in seen
        if new and not n.two_way:
            self.send_hello(port)                      # let it see us right away
        if n.two_way and not was:
            n.full_since = self.engine.now
            self.r.console(f"%OSPF-5-ADJCHG: Process {self.cfg.process_id}, Nbr {n.rid} on "
                           f"{port.name} from LOADING to FULL, Loading Done")
            self.send_hello(port)
            self._send(port, OspfPacket("lsu", self.router_id, self.iface_area(port),
                                        lsas=tuple(self._lsas_for_area(self.iface_area(port)))))
            self.originate()
        elif was and not n.two_way:
            self.originate()

    def _lsas_for_area(self, area: str) -> list[Lsa]:
        return [l for l in self.lsdb.values() if l.area == area or l.type == 5]

    def _on_lsu(self, lsas, in_port: "Port") -> None:
        changed = []
        for lsa in lsas:
            cur = self.lsdb.get(lsa.key)
            if lsa.adv == self.router_id:
                if cur is None or lsa.seq > cur.seq:    # an old incarnation of our LSA: outbid it
                    self._seq = itertools.count(lsa.seq + 1)
                    self.originate(force=True)
                continue
            if cur is None or lsa.seq > cur.seq:
                lsa = replace(lsa)                      # each router keeps its own copy
                self.lsdb[lsa.key] = lsa
                changed.append(lsa)
        if changed:
            self._flood(changed, exclude=in_port)
            self._schedule_spf()

    def _flood(self, lsas, exclude=None) -> None:
        for p in self.active_ports():
            if p is exclude or self.is_passive(p):
                continue
            if not any(n.port is p and n.two_way for n in self.neighbors.values()):
                continue
            area = self.iface_area(p)
            send = [l for l in lsas if l.area == area or l.type == 5]
            if send:
                self._send(p, OspfPacket("lsu", self.router_id, area, lsas=tuple(send)))

    # -- origination -------------------------------------------------------------------------------
    def _install_own(self, lsa: Lsa, force: bool) -> Lsa | None:
        cur = self.lsdb.get(lsa.key)
        if cur is not None and cur.body == lsa.body and not force:
            return None
        lsa.seq = next(self._seq)
        self.lsdb[lsa.key] = lsa
        return lsa

    def originate(self, force: bool = False) -> None:
        if not self.running or int(self.router_id) == 0:
            return
        rid = self.router_id
        new: list[Lsa] = []
        areas = self.areas()
        # type 1 per area
        for area in areas:
            links = []
            for p in self.active_ports():
                if self.iface_area(p) != area:
                    continue
                c = self.cost(p)
                net = IPv4Network(f"{p.ip.ip}/32") if p.is_loopback else p.ip.network
                links.append(("stub", net, c))
                for n in self.neighbors.values():
                    if n.port is p and n.two_way:
                        links.append(("router", n.rid, c, p.ip.ip))
            lsa = self._install_own(Lsa(1, area, rid, rid, 0, tuple(sorted(links, key=str))), force)
            if lsa:
                new.append(lsa)
        # withdraw router LSAs of areas we left
        for key in [k for k, l in self.lsdb.items() if l.adv == rid and l.type == 1 and l.area not in areas]:
            dead = self.lsdb.pop(key)
            dead.body, dead.seq = (), next(self._seq)
            new.append(dead)
        # type 5 externals
        for net, metric in self._externals():
            lsa = self._install_own(Lsa(5, None, net.network_address, rid, 0, (net.prefixlen, metric, 2)), force)
            if lsa:
                new.append(lsa)
        wanted = {n.network_address for n, _ in self._externals()}
        for key in [k for k, l in self.lsdb.items() if l.adv == rid and l.type == 5
                    and l.ls_id not in wanted and l.body]:
            old = self.lsdb[key]
            old.body, old.seq = (), next(self._seq)   # premature aging: empty body = withdrawn
            new.append(old)
        if new:
            self._flood(new)
        self._schedule_spf()

    def _externals(self) -> list[tuple[IPv4Network, int]]:
        out = []
        r = self.r
        if self.cfg.default_originate:
            has_default = any(rt.network.prefixlen == 0 and not rt.code.startswith("O")
                              for rt in r.routing_table())
            if has_default or self.cfg.default_originate == "always":
                out.append((IPv4Network("0.0.0.0/0"), 1))
        if "static" in self.cfg.redistribute:
            for s in r.static_routes:
                if s.network.prefixlen > 0 and r._resolve(s.network.network_address) is not None:
                    out.append((s.network, 20))
        if "connected" in self.cfg.redistribute:
            for p in r.ip_ports():
                if p.is_up and self.iface_area(p) is None:
                    out.append((p.ip.network, 20))
        return sorted(set(out), key=lambda x: (int(x[0].network_address), x[0].prefixlen))

    # -- SPF -------------------------------------------------------------------------------------
    def _schedule_spf(self) -> None:
        if not self._spf_pending:
            self._spf_pending = True
            self.engine.schedule(5.0, self._run_spf)

    def _spf_area(self, area: str):
        """Dijkstra over router LSAs of *area*: {rid: (cost, {(port, nexthop_ip)})}."""
        rid = self.router_id
        lsas = {l.ls_id: l for l in self.lsdb.values() if l.type == 1 and l.area == area and l.body}
        if rid not in lsas:
            return {}, lsas

        def links_to(u):
            return [lk for lk in lsas[u].body if lk[0] == "router"] if u in lsas else []

        def has_link(u, v):
            return any(lk[1] == v for lk in links_to(u))

        dist = {rid: (0, frozenset())}
        heap = [(0, int(rid), rid)]
        done = set()
        while heap:
            d, _, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            for lk in links_to(u):
                v = lk[1]
                if v not in lsas or not has_link(v, u):
                    continue              # two-way check
                nd = d + lk[2]
                if u == rid:
                    port = next((p for p in self.active_ports() if p.ip and p.ip.ip == lk[3]), None)
                    nbr = next((n for n in self.neighbors.values() if n.rid == v and n.port is port), None)
                    hops = frozenset({(port, nbr.ip)}) if port and nbr else frozenset()
                else:
                    hops = dist[u][1]
                if not hops:
                    continue
                cur = dist.get(v)
                if cur is None or nd < cur[0]:
                    dist[v] = (nd, hops)
                    heapq.heappush(heap, (nd, int(v), v))
                elif nd == cur[0]:
                    dist[v] = (nd, cur[1] | hops)
        return dist, lsas

    def _run_spf(self) -> None:
        self._spf_pending = False
        if not self.running:
            return
        self.spf_runs += 1
        rid = self.router_id
        best: dict[IPv4Network, tuple[int, int, set, str]] = {}   # net -> (rank, cost, hops, code)

        def offer(net, rank, cost, hops, code):
            if not hops:
                return
            cur = best.get(net)
            if cur is None or (rank, cost) < (cur[0], cur[1]):
                best[net] = (rank, cost, set(hops), code)
            elif (rank, cost) == (cur[0], cur[1]):
                cur[2].update(hops)

        own_nets = {p.ip.network for p in self.r.ip_ports() if p.is_up}
        area_dist = {}
        intra: dict[str, dict[IPv4Network, int]] = {}
        for area in self.areas():
            dist, lsas = self._spf_area(area)
            area_dist[area] = dist
            intra[area] = {}
            for v, (cost, hops) in dist.items():
                if v == rid:
                    continue
                for lk in lsas[v].body:
                    if lk[0] == "stub" and lk[1] not in own_nets:
                        offer(lk[1], 0, cost + lk[2], hops, "O")
                        intra[area][lk[1]] = cost + lk[2]
        # inter-area via ABR summaries (only from the backbone on an ABR)
        for area, dist in area_dist.items():
            if self.is_abr and area != BACKBONE:
                continue
            for l in self.lsdb.values():
                if l.type == 3 and l.area == area and l.body and l.adv in dist and l.adv != rid:
                    cost, hops = dist[l.adv]
                    net = IPv4Network(f"{l.ls_id}/{l.body[0]}")
                    if net not in own_nets:
                        offer(net, 1, cost + l.body[1], hops, "O IA")
        # ASBR reachability (intra or via type-4)
        asbr: dict[IPv4Address, tuple[int, set]] = {}
        for area, dist in area_dist.items():
            for v, (cost, hops) in dist.items():
                if v != rid and hops and (v not in asbr or cost < asbr[v][0]):
                    asbr[v] = (cost, set(hops))
            for l in self.lsdb.values():
                if l.type == 4 and l.area == area and l.body and l.adv in dist and l.adv != rid:
                    cost, hops = dist[l.adv]
                    c = cost + l.body[1]
                    if hops and (l.ls_id not in asbr or c < asbr[l.ls_id][0]):
                        asbr[l.ls_id] = (c, set(hops))
        for l in self.lsdb.values():
            if l.type == 5 and l.body and l.adv != rid and l.adv in asbr:
                net = IPv4Network(f"{l.ls_id}/{l.body[0]}")
                cost, hops = asbr[l.adv]
                # E2: compare on the external metric, tie-break on the path to the ASBR
                offer(net, 2, l.body[1] * INFINITY + cost, hops, "O E2")
        routes = []
        for net, (rank, cost, hops, code) in sorted(best.items(), key=lambda x: (int(x[0].network_address),
                                                                                x[0].prefixlen)):
            metric = cost // INFINITY if code == "O E2" else cost
            for port, nh in sorted(hops, key=lambda h: (int(h[1]), h[0].name)):
                routes.append(Route(net, code, port, nh, self.r.ospf_distance(code), metric))
        changed = [(r.network, r.code, r.port, r.next_hop, r.metric) for r in routes] != \
                  [(r.network, r.code, r.port, r.next_hop, r.metric) for r in self.routes]
        self.routes = routes
        if self.is_abr:
            self._originate_summaries(intra, area_dist, asbr)
        if changed:
            self.r.routes_changed()

    def _originate_summaries(self, intra, area_dist, asbr) -> None:
        rid = self.router_id
        new = []
        wanted = set()
        for target in self.areas():
            entries: dict[IPv4Network, int] = {}
            # networks of the other areas (+ inter-area routes learned in the backbone for non-backbone)
            for area, nets in intra.items():
                if area == target:
                    continue
                for net, c in nets.items():
                    entries[net] = min(c, entries.get(net, INFINITY))
            for p in self.active_ports():
                a = self.iface_area(p)
                if a != target:
                    net = IPv4Network(f"{p.ip.ip}/32") if p.is_loopback else p.ip.network
                    entries[net] = min(self.cost(p), entries.get(net, INFINITY))
            if target != BACKBONE:
                for r in self.routes:
                    if r.code == "O IA":
                        entries[r.network] = min(r.metric, entries.get(r.network, INFINITY))
            for net, metric in entries.items():
                wanted.add((target, 3, net.network_address))
                lsa = self._install_own(Lsa(3, target, net.network_address, rid, 0,
                                            (net.prefixlen, metric)), False)
                if lsa:
                    new.append(lsa)
            for a_rid, (c, _) in asbr.items():
                if any(a_rid in area_dist[a] for a in area_dist if a != target):
                    wanted.add((target, 4, a_rid))
                    lsa = self._install_own(Lsa(4, target, a_rid, rid, 0, (32, c)), False)
                    if lsa:
                        new.append(lsa)
        for key, l in list(self.lsdb.items()):
            if l.adv == rid and l.type in (3, 4) and l.body and (l.area, l.type, l.ls_id) not in wanted:
                l.body, l.seq = (), next(self._seq)
                new.append(l)
        if new:
            self._flood(new)
            self._schedule_spf()

    # -- show helpers ----------------------------------------------------------------------------
    def neighbor_rows(self):
        now = self.engine.now
        rows = []
        for n in sorted(self.neighbors.values(), key=lambda n: (int(n.rid), n.port.name)):
            dead_left = max(0.0, self.dead_interval(n.port) - (now - n.last_seen) / 1000)
            rows.append((n, self.neighbor_state(n), dead_left))
        return rows

    def database(self):
        return sorted(self.lsdb.values(), key=lambda l: (l.area or "~", l.type, int(l.ls_id), int(l.adv)))
