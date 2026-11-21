"""RIP version 2 (RFC 2453): distance vector with hop-count metric.

Periodic updates every 30 s plus triggered updates, split horizon, route
timeout 180 s and flush 240 s, metric 16 = unreachable, multicast 224.0.0.9.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from ..addresses import MacAddress
from ..packets import ETH_IPV4, PROTO_UDP, IPv4Packet, UdpDatagram
from ..stack.ipv4 import Route

if TYPE_CHECKING:
    from ..devices.router import Router

RIP_PORT = 520
RIP_GROUP = IPv4Address("224.0.0.9")
RIP_MAC = MacAddress(0x01005E000009)
UPDATE_MS, TIMEOUT_MS, FLUSH_MS = 30_000, 180_000, 240_000
INFINITY = 16


@dataclass(frozen=True)
class RipMessage:
    command: str                     # "response" | "request"
    entries: tuple = ()              # ((prefix str, metric), ...)
    label = "RIP"

    def summary(self) -> str:
        return f"RIPv2 {self.command.capitalize()} ({len(self.entries)} routes)"

    def layer_lines(self) -> list[str]:
        return [f"Command: {self.command}", "Version: 2"] + [f"{p} metric {m}" for p, m in self.entries]


@dataclass
class RipConfig:
    networks: list[str] = field(default_factory=list)        # classful "network" statements (Cisco/Huawei)
    interfaces: list[str] = field(default_factory=list)      # explicit interfaces (Junos/RouterOS)
    passive: list[str] = field(default_factory=list)
    passive_default: bool = False
    default_originate: bool = False
    redistribute: list[str] = field(default_factory=list)    # connected | static | ospf
    send_connected: bool = True                              # False on Junos: only what export allows
    version: int = 2
    enabled: bool = True
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "RipConfig":
        c = cls()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        return c


@dataclass
class RipRoute:
    network: IPv4Network
    metric: int
    next_hop: IPv4Address
    port: object
    updated: float
    dead_since: float | None = None


class RipProcess:
    def __init__(self, router: "Router", cfg: RipConfig):
        self.r = router
        self.cfg = cfg
        self.engine = router.engine
        self.table: dict[IPv4Network, RipRoute] = {}
        self.routes: list[Route] = []
        self.running = False
        self._timer = None
        self._next_update = 0.0
        self._triggered = False
        self.updates_sent = 0
        self._last_local: set = set()
        self._poisoned: dict[IPv4Network, float] = {}       # withdrawn local networks -> since

    # -- interfaces --------------------------------------------------------------------
    def enabled_on(self, port) -> bool:
        if port.ip is None or not port.is_up:
            return False
        if port.name in self.cfg.interfaces:
            return True
        for n in self.cfg.networks:
            if port.ip.ip in IPv4Network(n):
                return True
        return False

    def is_passive(self, port) -> bool:
        if port.is_loopback:
            return True
        if self.cfg.passive_default:
            return port.name not in self.cfg.extra.get("non_passive", [])
        return port.name in self.cfg.passive

    # -- lifecycle ------------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._next_update = self.engine.now + 100
        self._timer = self.engine.schedule(100.0, self._tick)

    def stop(self) -> None:
        self.running = False
        if self._timer is not None:
            self._timer.cancelled = True
        self.table.clear()
        self.routes = []
        self.r.routes_changed()

    def trigger(self) -> None:
        self._triggered = True

    def _tick(self) -> None:
        if not self.running:
            return
        now = self.engine.now
        changed = False
        for net, rr in list(self.table.items()):
            if not rr.port.is_up or not self.enabled_on(rr.port):
                if rr.metric < INFINITY:
                    rr.metric, rr.dead_since = INFINITY, now
                    changed = True
            elif rr.metric < INFINITY and now - rr.updated > TIMEOUT_MS:
                rr.metric, rr.dead_since = INFINITY, now          # invalid timer expired
                changed = True
            if rr.metric >= INFINITY and rr.dead_since is not None and now - rr.dead_since > FLUSH_MS - TIMEOUT_MS:
                del self.table[net]
                changed = True
        local = set(self._local_routes())
        for net in self._last_local - local:
            self._poisoned[net] = now                       # announce with metric 16 (poison)
        if local != self._last_local:
            self._triggered = True
        for net in list(self._poisoned):
            if net in local or now - self._poisoned[net] > FLUSH_MS - TIMEOUT_MS:
                del self._poisoned[net]
        self._last_local = local
        if changed:
            self._rebuild()
            self._triggered = True
        if now >= self._next_update or self._triggered:
            self._send_updates()
            if now >= self._next_update:
                self._next_update = now + UPDATE_MS
            self._triggered = False
        self._timer = self.engine.schedule(1000.0, self._tick)

    # -- send --------------------------------------------------------------------------------
    def _local_routes(self) -> dict[IPv4Network, int]:
        out: dict[IPv4Network, int] = {}
        if self.cfg.send_connected:
            for p in self.r.ports.values():
                if self.enabled_on(p):
                    out[p.ip.network] = 0
        for r in self.r.routing_table():
            code = r.code[:1]
            if ("connected" in self.cfg.redistribute and code == "C") or \
                    ("static" in self.cfg.redistribute and code == "S" and r.network.prefixlen > 0) or \
                    ("ospf" in self.cfg.redistribute and code == "O"):
                out.setdefault(r.network, 0)
        if self.cfg.default_originate:
            out[IPv4Network("0.0.0.0/0")] = 0
        return out

    def _send_updates(self) -> None:
        local = self._local_routes()
        for p in self.r.ports.values():
            if not self.enabled_on(p) or self.is_passive(p):
                continue
            entries = []
            for net, m in local.items():
                if p.ip is not None and net == p.ip.network:
                    continue
                entries.append((str(net), min(INFINITY, m + 1)))
            for net, rr in self.table.items():
                if net in local:
                    continue
                if rr.port is p and rr.metric < INFINITY:
                    continue                                   # split horizon
                entries.append((str(net), min(INFINITY, rr.metric + 1)))
            for net in self._poisoned:
                if p.ip is None or net != p.ip.network:
                    entries.append((str(net), INFINITY))
            msg = RipMessage("response", tuple(sorted(entries)))
            pkt = IPv4Packet(p.ip.ip, RIP_GROUP, 1, UdpDatagram(RIP_PORT, RIP_PORT, msg), PROTO_UDP,
                             next(self.r._ip_ident))
            self.r._send_frame(p, RIP_MAC, ETH_IPV4, pkt)
            self.updates_sent += 1

    # -- receive -------------------------------------------------------------------------------
    def receive(self, pkt: IPv4Packet, port) -> None:
        if not self.running or not self.enabled_on(port) or self.is_passive(port):
            return
        msg = pkt.payload.data
        if not isinstance(msg, RipMessage) or msg.command != "response":
            return
        if pkt.src not in port.ip.network:
            return
        now = self.engine.now
        own = {p.ip.network for p in self.r.ports.values() if p.ip is not None and p.is_up}
        changed = False
        for pfx, metric in msg.entries:
            net = IPv4Network(pfx)
            if net in own:
                continue
            metric = min(INFINITY, int(metric))
            cur = self.table.get(net)
            if cur is None:
                if metric < INFINITY:
                    self.table[net] = RipRoute(net, metric, pkt.src, port, now)
                    changed = True
            elif cur.next_hop == pkt.src and cur.port is port:
                if metric != cur.metric:
                    changed = True
                    cur.dead_since = now if metric >= INFINITY and cur.metric < INFINITY else cur.dead_since
                cur.metric = metric
                if metric < INFINITY:
                    cur.updated, cur.dead_since = now, None
            elif metric < cur.metric:
                self.table[net] = RipRoute(net, metric, pkt.src, port, now)
                changed = True
        if changed:
            self._rebuild()
            self._triggered = True

    def _rebuild(self) -> None:
        dist = self.r.rip_distance()
        self.routes = [Route(rr.network, "R", rr.port, rr.next_hop, dist, rr.metric)
                       for rr in sorted(self.table.values(), key=lambda x: (int(x.network.network_address),
                                                                            x.network.prefixlen))
                       if rr.metric < INFINITY]
        self.r.routes_changed()

    def age(self, rr: RipRoute) -> int:
        return int((self.engine.now - rr.updated) / 1000)
