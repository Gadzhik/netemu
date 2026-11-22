"""DDoS emulation for training: attack flows, link and server resources, mitigation hooks.

Real floods are millions of packets per second; simulating each one is neither possible nor useful.
NetEmu models an attack as an aggregated *flow*:

* A botnet sends, once per simulated second and per attack, one real **probe packet** that carries a
  ``FloodTag`` (``IPv4Packet.flood``): kind, bots, packets/s, bits/s.  The probe is routed by the real
  devices, so everything on the path acts on the whole flow: ACLs, NAT, uRPF, null routes, QoS policers
  (cap the flow to the policed rate), firewalls, the ISP cloud's scrubbing / RTBH / FlowSpec.
* Every cable the probe crosses adds the flow's rate to that cable's load for the second
  (``DdosMonitor``).  A cable carries at most its bandwidth (FastEthernet 100 Mbit/s, GigabitEthernet
  1 Gbit/s, ...): the flow is cut to it downstream, and **legitimate frames** on an overloaded cable are
  lost in proportion (utilisation 4.0 = 75 % loss) — pings time out, web pages fail.
* Servers have finite resources: a SYN backlog, HTTP requests/s and concurrent connections.  SYN floods,
  HTTP floods and Slowloris exhaust them unless SYN cookies, per-IP limits, a JS challenge or connection
  limits are switched on (``Server.ddos``).
* Reflection: a spoofed query (source = victim) to an open DNS resolver or an NTP server with ``monlist``
  makes the reflector send the victim a response many times bigger (``AMPLIFICATION``).

Everything is deterministic for a given topology (seeded random numbers) so lab checks are stable."""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

TICK_MS = 1000.0
#                kind: (protocol, default port, packet size B, amplification, rate unit, layer)
KINDS = {
    "udp": ("udp", 80, 1250, 1, "pps", "L3/L4"),
    "icmp": ("icmp", 0, 1000, 1, "pps", "L3/L4"),
    "syn": ("tcp", 80, 60, 1, "pps", "L4"),
    "http": ("http", 80, 600, 1, "rps", "L7"),
    "slowloris": ("slow", 80, 100, 1, "conn", "L7"),
    "dns-amp": ("udp", 53, 64, 54, "pps", "L3/L4"),
    "ntp-amp": ("udp", 123, 234, 556, "pps", "L3/L4"),
}
AMPLIFICATION = {k: v[3] for k, v in KINDS.items()}
REFLECTED_SIZE = {"dns-amp": 3000, "ntp-amp": 468}       # response size seen by the victim (bytes)
TITLES = {"udp": "UDP flood", "icmp": "ICMP (ping) flood", "syn": "TCP SYN flood", "http": "HTTP GET flood",
          "slowloris": "Slowloris", "dns-amp": "DNS amplification", "ntp-amp": "NTP amplification (monlist)"}

# server resources (a small web server)
SYN_BACKLOG_PPS = 25        # half-open connections it can absorb per second (backlog 128, SYN-ACK retries)
HTTP_CAPACITY_RPS = 300     # requests per second it can serve
MAX_CONNECTIONS = 256       # concurrent connections (worker slots)
SAFE_ATTACK_BPS = 0         # used by checks: attack traffic considered "stopped" below this


@dataclass(frozen=True)
class FloodTag:
    """The aggregated flow a probe packet stands for."""
    attack: str                 # attack id (botnet name/index)
    kind: str
    bots: int
    pps: float                  # packets (or requests / connections) per second in total
    bps: float                  # bits per second in total
    per_bot: float = 0.0        # rate of one bot (per-IP limits work on this)
    phase: str = "direct"       # direct | query (to a reflector) | reflected (towards the victim)

    def scaled(self, factor: float) -> "FloodTag":
        factor = max(0.0, min(1.0, factor))
        return replace(self, pps=self.pps * factor, bps=self.bps * factor)

    def capped_bps(self, limit_bps: float) -> "FloodTag":
        return self if self.bps <= limit_bps or self.bps <= 0 else self.scaled(limit_bps / self.bps)

    def text(self) -> str:
        return f"{TITLES.get(self.kind, self.kind)}: {human_bps(self.bps)}, {human_pps(self.pps)}, {self.bots} bots"


def human_bps(bps: float) -> str:
    for unit, div in (("Gbit/s", 1e9), ("Mbit/s", 1e6), ("kbit/s", 1e3)):
        if bps >= div:
            return f"{bps / div:.1f} {unit}"
    return f"{bps:.0f} bit/s"


def human_pps(pps: float) -> str:
    if pps >= 1e6:
        return f"{pps / 1e6:.1f} Mpps"
    if pps >= 1e3:
        return f"{pps / 1e3:.1f} kpps"
    return f"{pps:.0f} pps"


@dataclass
class Attack:
    """One attack of a botnet (stored in the project)."""
    kind: str = "udp"
    target: str = ""                         # victim IPv4 address
    port: int | None = None                  # destination port (default: per kind)
    bots: int = 100
    rate: float = 1000.0                     # per bot: packets/s, requests/s (http) or connections (slowloris)
    size: int | None = None                  # packet size in bytes (default: per kind)
    spoof: bool = False                      # random source addresses (not for http / slowloris)
    reflectors: list = field(default_factory=list)   # amplification: reflector addresses
    running: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "port": self.port, "bots": self.bots, "rate": self.rate,
                "size": self.size, "spoof": self.spoof, "reflectors": list(self.reflectors), "running": self.running}

    @classmethod
    def from_dict(cls, d: dict) -> "Attack":
        return cls(d.get("kind", "udp"), d.get("target", ""), d.get("port"), int(d.get("bots", 100)),
                   float(d.get("rate", 1000)), d.get("size"), bool(d.get("spoof", False)),
                   list(d.get("reflectors", [])), bool(d.get("running", False)))

    @property
    def dport(self) -> int:
        return int(self.port if self.port is not None else KINDS[self.kind][1])

    @property
    def packet_size(self) -> int:
        return int(self.size or KINDS[self.kind][2])

    @property
    def total_pps(self) -> float:
        return self.bots * self.rate

    @property
    def total_bps(self) -> float:
        if self.kind == "slowloris":
            return self.bots * self.rate * 200 * 8 / 10       # a few header bytes every ~10 s per connection
        return self.total_pps * self.packet_size * 8

    @property
    def amplified_bps(self) -> float:
        """What reaches the victim of a reflection attack (when every reflector is open)."""
        if self.kind in REFLECTED_SIZE:
            return self.total_pps * REFLECTED_SIZE[self.kind] * 8
        return self.total_bps


# -- monitor ---------------------------------------------------------------------------------------
class DdosMonitor:
    """Per-second load of every cable direction and what each device receives (attached to a topology)."""

    def __init__(self, topology):
        self.topology = topology
        self.engine = topology.engine
        self.cur: dict = {}          # (id(link), id(from port)) -> [bps, pps, {attack kinds}]
        self.last: dict = {}
        self.cur_rx: dict = {}       # device id -> {kind: [bps, pps, per_bot, bots]}
        self.last_rx: dict = {}
        self.links: dict = {}        # id(link) -> link (for the GUI)
        self.drops = 0               # legitimate frames lost on overloaded cables
        self.rng = random.Random(7)
        self._timer = None
        self._idle = 0
        self.engine.subscribe("frame_delivered", self._on_frame)

    # -- clock -------------------------------------------------------------------------------------
    def ensure_running(self) -> None:
        if self._timer is None:
            self._idle = 0
            self._timer = self.engine.schedule(TICK_MS, self._tick)

    def _tick(self) -> None:
        self._timer = None
        self.last, self.cur = self.cur, {}
        self.last_rx, self.cur_rx = self.cur_rx, {}
        self.engine.emit("ddos_tick", self)
        active = any(getattr(d, "attacks", None) and any(a.running for a in d.attacks)
                     for d in self.topology.devices.values())
        self._idle = 0 if active else self._idle + 1
        if self._idle < 3:
            self._timer = self.engine.schedule(TICK_MS, self._tick)

    # -- accounting -------------------------------------------------------------------------------
    def _on_frame(self, ev) -> None:
        info = ev.info
        frame = info.get("frame")
        pkt = getattr(frame, "payload", None)
        tag = getattr(pkt, "flood", None)
        if tag is None:
            return
        dst = info.get("dst")
        if dst is not None:
            r = self.cur_rx.setdefault(dst.device.id, {}).setdefault(tag.kind, [0.0, 0.0, 0.0, 0])
            r[0] += tag.bps
            r[1] += tag.pps
            r[2] = max(r[2], tag.per_bot)
            r[3] += tag.bots

    def offer(self, link, from_port, tag) -> None:
        """A flow is put on a cable (before the cable cuts it to its bandwidth): the offered load."""
        e = self.cur.setdefault((id(link), id(from_port)), [0.0, 0.0, set()])
        e[0] += tag.bps
        e[1] += tag.pps
        e[2].add(tag.kind)
        self.links[id(link)] = link

    def load(self, link, from_port) -> tuple[float, float]:
        e = self.last.get((id(link), id(from_port)))
        return (e[0], e[1]) if e else (0.0, 0.0)

    def utilization(self, link, from_port) -> float:
        bps, _ = self.load(link, from_port)
        cap = capacity_bps(from_port)
        return bps / cap if cap else 0.0

    def received(self, dev) -> dict:
        """{kind: (bps, pps, per_bot, bots)} the device received last second."""
        return {k: tuple(v) for k, v in self.last_rx.get(dev.id, {}).items()}

    def attack_bps_at(self, dev) -> float:
        return sum(v[0] for v in self.received(dev).values())

    def legit_drop(self, link, from_port) -> bool:
        """Should a legitimate frame on this cable direction be lost (the cable is saturated)?"""
        u = self.utilization(link, from_port)
        if u <= 1.0:
            return False
        if self.rng.random() < min(0.97, 1.0 - 1.0 / u):
            self.drops += 1
            return True
        return False

    def rows(self):
        """[(link, from port, bps, pps, utilisation, kinds)] for the GUI, busiest first."""
        out = []
        for (lid, pid), (bps, pps, kinds) in self.last.items():
            link = self.links.get(lid)
            if link is None:
                continue
            port = link.a if id(link.a) == pid else link.b
            out.append((link, port, bps, pps, bps / max(1.0, capacity_bps(port)), sorted(kinds)))
        return sorted(out, key=lambda r: -r[4])


BACKBONE_KBPS = 100_000_000        # botnets and the ISP backbone: 100 Gbit/s, never the bottleneck


def capacity_bps(port) -> float:
    """A cable runs at the speed of its slower end (a Gi port facing a Fa port: 100 Mbit/s)."""
    from ..routing.eigrp import port_bandwidth

    def bw(p):
        if p is None:
            return BACKBONE_KBPS
        if getattr(p.device, "category", "") in ("attack", "cloud"):
            return BACKBONE_KBPS
        return port_bandwidth(p)
    return float(min(bw(port), bw(getattr(port, "peer", None)))) * 1000.0


def monitor(topology) -> DdosMonitor:
    m = getattr(topology, "ddos", None)
    if m is None:
        m = topology.ddos = DdosMonitor(topology)
    return m


def monitor_of(device) -> DdosMonitor | None:
    topo = getattr(device.engine, "topology", None)
    return getattr(topo, "ddos", None) if topo is not None else None


# -- server side ----------------------------------------------------------------------------------
DEFAULT_GUARD = {"syncookies": False, "http_limit": 0, "challenge": False, "conn_limit": 0,
                 "open_resolver": False, "ntp_monlist": False}


def guard(server) -> dict:
    g = getattr(server, "ddos", None)
    if g is None:
        g = server.ddos = dict(DEFAULT_GUARD)
    return g


def server_pressure(server) -> dict:
    """What the attacks do to a server's resources last second, after its own protections.

    Returns {"syn": (load, capacity), "http": (rps, capacity), "conn": (connections, capacity)}."""
    m = monitor_of(server)
    rx = m.received(server) if m is not None else {}
    g = guard(server)
    syn = rx.get("syn", (0, 0, 0, 0))[1]
    if g["syncookies"]:
        syn = 0.0                                    # no state kept for half-open connections
    _, rps, per_bot, bots = rx.get("http", (0, 0, 0, 0))
    if g["challenge"]:
        rps = 0.0                                    # bots cannot run the JavaScript challenge
    elif g["http_limit"] and bots:
        rps = bots * min(per_bot, g["http_limit"])
    _, conns, per_bot_c, bots_c = rx.get("slowloris", (0, 0, 0, 0))
    if g["conn_limit"] and bots_c:
        conns = bots_c * min(per_bot_c, g["conn_limit"])
    return {"syn": (syn, SYN_BACKLOG_PPS), "http": (rps, HTTP_CAPACITY_RPS), "conn": (conns, MAX_CONNECTIONS)}


def refuses_connection(server, rng: random.Random) -> bool:
    """A legitimate SYN gets no answer: SYN backlog or connection slots are exhausted."""
    p = server_pressure(server)
    syn, cap = p["syn"]
    if syn > cap and rng.random() < min(0.98, 1 - cap / syn):
        return True
    conns, ccap = p["conn"]
    return conns >= ccap


def overloaded_http(server, rng: random.Random) -> bool:
    rps, cap = server_pressure(server)["http"]
    return rps > cap and rng.random() < min(0.98, 1 - cap / rps)


def reflect(reflector, pkt, open_: bool) -> None:
    """A reflector got a spoofed amplification query: answer the 'source' (the victim), amplified."""
    tag = pkt.flood
    if not open_ or tag.phase != "query":
        return
    from ..packets import PROTO_UDP, IPv4Packet, UdpDatagram
    size = REFLECTED_SIZE.get(tag.kind, 512)
    out = replace(tag, phase="reflected", bps=tag.pps * size * 8)
    l4 = pkt.payload
    reply = IPv4Packet(pkt.dst, pkt.src, 64, UdpDatagram(l4.dport, l4.sport, None), PROTO_UDP,
                       next(reflector._ip_ident), flood=out)
    reflector.send_ip(reply)
