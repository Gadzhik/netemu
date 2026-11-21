"""EIGRP (Cisco), simplified DUAL.

* Neighbours: hello every 5 s to 224.0.0.10 (IP protocol 88), hold time 15 s, same AS number.
* Metric: classic composite metric with K1 = K3 = 1:
  256 * (10^7 / minimum bandwidth in kbit/s + total delay in tens of microseconds).
  Interface bandwidth / delay come from the port type and can be changed with ``bandwidth`` and
  ``delay`` on the interface.
* Topology table: every prefix keeps what each neighbour reported (reported distance) and the
  resulting feasible distance.  The successor is the best path; a *feasible successor* is a
  neighbour whose reported distance is below the successor's feasible distance (DUAL's
  feasibility condition) and takes over immediately when the successor is lost.
* Updates are sent on new adjacencies and on changes only (no periodic updates), with split
  horizon and poison reverse; a withdrawn route is advertised with infinite delay.
* Routes: ``D`` (internal, AD 90) and ``D EX`` (redistributed, AD 170).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network

from ..addresses import MacAddress
from ..packets import ETH_IPV4, IPv4Packet
from ..stack.ipv4 import Route

PROTO_EIGRP = 88
EIGRP_GROUP = IPv4Address("224.0.0.10")
EIGRP_MAC = MacAddress(0x01005E00000A)
HELLO_MS, HOLD_MS = 5000, 15000
INF_DELAY = 0xFFFFFFFF
MAX_HOPS = 100


def port_bandwidth(port) -> int:
    """kbit/s"""
    if getattr(port, "eigrp_bw", None):
        return port.eigrp_bw
    n = port.name.lower()
    if port.is_loopback:
        return 8_000_000
    if n.startswith(("fast", "fa")):
        return 100_000
    if n.startswith(("ten", "te")):
        return 10_000_000
    if getattr(port, "is_tunnel", False):
        return 100
    return 1_000_000


def port_delay(port) -> int:
    """tens of microseconds"""
    if getattr(port, "eigrp_delay", None):
        return port.eigrp_delay
    if port.is_loopback:
        return 500
    if port.name.lower().startswith(("fast", "fa")):
        return 10
    if getattr(port, "is_tunnel", False):
        return 50000
    return 1


def composite(bw_kbps: int, delay: int) -> int:
    if delay >= INF_DELAY or bw_kbps <= 0:
        return INF_DELAY
    return 256 * (10_000_000 // bw_kbps + delay)


@dataclass(frozen=True)
class EigrpMsg:
    kind: str                          # hello | update
    asn: int
    router_id: str
    entries: tuple = ()                # ((prefix, bw_kbps, delay, hops, external), ...)
    label = "EIGRP"

    def summary(self) -> str:
        return f"EIGRP {self.kind.capitalize()} AS {self.asn}" + (f" ({len(self.entries)} routes)" if self.entries else "")

    def layer_lines(self) -> list[str]:
        out = [f"Opcode: {self.kind}", f"Autonomous system: {self.asn}", f"Router ID: {self.router_id}"]
        for p, bw, d, h, ex in self.entries:
            out.append(f"{'External' if ex else 'Internal'} route {p}: bandwidth {bw} kbit/s, delay {d * 10} usec"
                       if d < INF_DELAY else f"Route {p}: unreachable")
        return out


@dataclass
class EigrpConfig:
    asn: int = 100
    networks: list = field(default_factory=list)          # [["10.0.0.0", "0.0.0.255"], ...]
    passive: list = field(default_factory=list)
    router_id: str | None = None
    redistribute: list = field(default_factory=list)      # static | connected | ospf | rip
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"asn": self.asn, "networks": [list(n) for n in self.networks], "passive": list(self.passive),
                "router_id": self.router_id, "redistribute": list(self.redistribute), "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict) -> "EigrpConfig":
        return cls(int(d.get("asn", 100)), [list(n) for n in d.get("networks", [])], list(d.get("passive", [])),
                   d.get("router_id"), list(d.get("redistribute", [])), bool(d.get("enabled", True)))


@dataclass
class Neighbor:
    ip: IPv4Address
    port: object
    router_id: str
    up_since: float
    last_seen: float


class EigrpProcess:
    def __init__(self, router, cfg: EigrpConfig):
        self.r = router
        self.cfg = cfg
        self.engine = router.engine
        self.neighbors: dict[tuple, Neighbor] = {}         # (port name, ip) -> Neighbor
        self.topo: dict[IPv4Network, dict] = {}            # prefix -> {(port, ip): (bw, delay, hops, ext)}
        self.routes: list[Route] = []
        self.best: dict[IPv4Network, tuple] = {}           # prefix -> (fd, [(port name, ip), ...], ext)
        self.running = False
        self._timer = None
        self._advertised: dict = {}

    # -- identity / interfaces ---------------------------------------------------------------
    def router_id(self) -> str:
        if self.cfg.router_id:
            return self.cfg.router_id
        ips = [p.ip.ip for p in self.r.ports.values() if p.ip is not None]
        loops = [p.ip.ip for p in self.r.ports.values() if p.ip is not None and p.is_loopback]
        return str(max(loops or ips)) if (loops or ips) else "0.0.0.0"

    def enabled_on(self, port) -> bool:
        if port.ip is None or not port.is_up:
            return False
        for addr, wc in self.cfg.networks:
            mask = int(IPv4Address(wc or "0.0.0.0"))
            if int(port.ip.ip) & ~mask & 0xFFFFFFFF == int(IPv4Address(addr)) & ~mask & 0xFFFFFFFF:
                return True
        return False

    def is_passive(self, port) -> bool:
        return port.name in self.cfg.passive or "default" in self.cfg.passive

    # -- lifecycle -----------------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._timer = self.engine.schedule(100.0, self._tick)

    def stop(self) -> None:
        self.running = False
        if self._timer is not None:
            self._timer.cancelled = True
        self.neighbors.clear()
        self.topo.clear()
        self.best.clear()
        self.routes = []
        self.r.routes_changed()

    def _tick(self) -> None:
        if not self.running:
            return
        now = self.engine.now
        for key, nb in list(self.neighbors.items()):
            if now - nb.last_seen > HOLD_MS or not nb.port.is_up or not self.enabled_on(nb.port):
                self._lose(key, "holding time expired" if nb.port.is_up else "interface down")
        for p in self.r.ports.values():
            if self.enabled_on(p) and not self.is_passive(p):
                self._send(p, EigrpMsg("hello", self.cfg.asn, self.router_id()))
        self._recompute()
        self._timer = self.engine.schedule(float(HELLO_MS), self._tick)

    def link_changed(self, port) -> None:
        if not self.running:
            return
        if not port.is_up:
            for key in [k for k, nb in self.neighbors.items() if nb.port is port]:
                self._lose(key, "interface down")
        self.engine.schedule(0.0, self._recompute)

    def _lose(self, key, why: str) -> None:
        nb = self.neighbors.pop(key, None)
        if nb is None:
            return
        for entries in self.topo.values():
            entries.pop(key, None)
        self.r.mgmt.log(5, f"%DUAL-5-NBRCHANGE: EIGRP-IPv4 {self.cfg.asn}: Neighbor {nb.ip} ({nb.port.name}) is "
                           f"down: {why}")

    # -- messages ------------------------------------------------------------------------------------
    def _send(self, port, msg) -> None:
        pkt = IPv4Packet(port.ip.ip, EIGRP_GROUP, 2, msg, PROTO_EIGRP, next(self.r._ip_ident))
        self.r._send_frame(port, EIGRP_MAC, ETH_IPV4, pkt)

    def receive(self, pkt, port) -> None:
        m = pkt.payload
        if not self.running or not isinstance(m, EigrpMsg) or m.asn != self.cfg.asn:
            return
        if not self.enabled_on(port) or self.is_passive(port) or pkt.src not in port.ip.network:
            return
        key = (port.name, pkt.src)
        now = self.engine.now
        nb = self.neighbors.get(key)
        if nb is None:
            nb = self.neighbors[key] = Neighbor(pkt.src, port, m.router_id, now, now)
            self.r.mgmt.log(5, f"%DUAL-5-NBRCHANGE: EIGRP-IPv4 {self.cfg.asn}: Neighbor {pkt.src} ({port.name}) is "
                               "up: new adjacency")
            self._send_update(port, full=True)
        nb.last_seen = now
        if m.kind == "update":
            entries = self.topo
            for pfx, bw, delay, hops, ext in m.entries:
                net = IPv4Network(pfx)
                if delay >= INF_DELAY or hops >= MAX_HOPS:
                    entries.get(net, {}).pop(key, None)
                else:
                    entries.setdefault(net, {})[key] = (int(bw), int(delay), int(hops), bool(ext))
            self._recompute()

    # -- DUAL ------------------------------------------------------------------------------------------
    def _local(self) -> dict:
        """prefix -> (bw, delay, external) originated here."""
        out = {}
        for p in self.r.ports.values():
            if self.enabled_on(p):
                out[p.ip.network] = (port_bandwidth(p), port_delay(p), False)
        for rt in self.r.routing_table():
            code = rt.code[:1]
            if (("static" in self.cfg.redistribute and code == "S") or
                    ("connected" in self.cfg.redistribute and code == "C") or
                    ("ospf" in self.cfg.redistribute and code == "O") or
                    ("rip" in self.cfg.redistribute and code == "R")) and rt.network not in out:
                out[rt.network] = (10_000_000, 1, True)
        return out

    def _paths(self, net) -> list:
        """[(fd, rd, key, bw, delay, hops, ext)] sorted by feasible distance."""
        out = []
        for key, (bw, delay, hops, ext) in self.topo.get(net, {}).items():
            nb = self.neighbors.get(key)
            if nb is None or not nb.port.is_up:
                continue
            lbw, ldelay = port_bandwidth(nb.port), port_delay(nb.port)
            fd = composite(min(bw, lbw), delay + ldelay)
            out.append((fd, composite(bw, delay), key, min(bw, lbw), delay + ldelay, hops + 1, ext))
        return sorted(out, key=lambda x: (x[0], str(x[2])))

    def topology(self) -> list:
        """For show ip eigrp topology: [(net, fd, [(via ip, port, fd, rd, successor?)])]."""
        rows = []
        local = self._local()
        for net in sorted(set(self.topo) | set(local), key=lambda n: (int(n.network_address), n.prefixlen)):
            if net in local and not local[net][2]:
                rows.append((net, composite(local[net][0], local[net][1]), [("Connected", None, 0, 0, True)]))
                continue
            paths = self._paths(net)
            if not paths:
                continue
            fd = paths[0][0]
            vias = []
            for p_fd, rd, key, *_ in paths:
                succ = p_fd == fd
                if succ or rd < fd:                                  # feasibility condition
                    nb = self.neighbors[key]
                    vias.append((str(nb.ip), nb.port.name, p_fd, rd, succ))
            rows.append((net, fd, vias))
        return rows

    def _recompute(self) -> None:
        if not self.running:
            return
        local = self._local()
        best = {}
        for net in self.topo:
            if net in local and not local[net][2]:
                continue
            paths = self._paths(net)
            if not paths:
                continue
            fd = paths[0][0]
            succ = [p for p in paths if p[0] == fd][:4]                # equal-cost load sharing
            best[net] = (fd, succ, paths[0][6])
        changed = {n: v[0] for n, v in best.items()} != {n: v[0] for n, v in self.best.items()} or \
            {n: [p[2] for p in v[1]] for n, v in best.items()} != {n: [p[2] for p in v[1]] for n, v in self.best.items()}
        self.best = best
        routes = []
        for net in sorted(best, key=lambda n: (int(n.network_address), n.prefixlen)):
            fd, succ, ext = best[net]
            for p in succ:
                nb = self.neighbors[p[2]]
                routes.append(Route(net, "D EX" if ext else "D", nb.port, nb.ip, 170 if ext else 90, fd))
        if changed or len(routes) != len(self.routes):
            self.routes = routes
            self.r.routes_changed()
        for p in self.r.ports.values():
            if self.enabled_on(p) and not self.is_passive(p):
                self._send_update(p)

    def _advert(self, port) -> dict:
        out = {}
        for net, (bw, delay, ext) in self._local().items():
            out[str(net)] = (bw, delay, 0, ext)
        for net, (fd, succ, ext) in self.best.items():
            if str(net) in out:
                continue
            via_here = any(self.neighbors[p[2]].port is port for p in succ)
            _, _, _, bw, delay, hops, _ = succ[0]
            out[str(net)] = (bw, INF_DELAY, hops, ext) if via_here else (bw, delay, hops, ext)   # poison reverse
        return out

    def _send_update(self, port, full: bool = False) -> None:
        adv = self._advert(port)
        prev = {} if full else self._advertised.get(port.name, {})
        entries = []
        for pfx, (bw, delay, hops, ext) in adv.items():
            if full or prev.get(pfx) != (bw, delay, hops, ext):
                entries.append((pfx, bw, delay, hops, ext))
        for pfx in prev:
            if pfx not in adv:
                entries.append((pfx, 0, INF_DELAY, MAX_HOPS, False))
        self._advertised[port.name] = adv
        if entries and any(nb.port is port for nb in self.neighbors.values()):
            self._send(port, EigrpMsg("update", self.cfg.asn, self.router_id(), tuple(sorted(entries))))
