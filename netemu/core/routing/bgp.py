"""BGP-4 (RFC 4271), simplified but behaviourally faithful for labs.

Modelled
  * Sessions run over (simulated) TCP port 179 and are routed like any traffic:
    loopback peering needs an IGP route plus ``update-source``, eBGP to a
    non-adjacent peer needs ``ebgp-multihop`` (TTL 1 otherwise), ACLs can
    block port 179.  Wrong ``remote-as`` -> NOTIFICATION "Bad Peer AS";
    a session from an unexpected source address is ignored.
  * OPEN / KEEPALIVE / UPDATE / NOTIFICATION, states Idle/Active/OpenSent/
    OpenConfirm/Established, keepalive 60 s / hold 180 s, fast fallover
    when the peer address becomes unreachable.
  * Attributes: AS_PATH, NEXT_HOP, ORIGIN, MED, LOCAL_PREF, weight (Cisco).
  * Best-path selection: weight, local-pref, locally originated, AS_PATH
    length, origin, MED, eBGP over iBGP, IGP metric to next hop, router ID.
  * AS_PATH loop prevention, iBGP split horizon, simple route reflection,
    next-hop-self, next-hop reachability, network statements (exact RIB
    match), redistribution, default-originate.

Simplified
  * No real TCP state machine (a single OPEN exchange instead of SYN/ACK).
  * No route maps / communities / aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from typing import TYPE_CHECKING

from ..packets import PROTO_TCP, IPv4Packet, TcpSegment
from ..stack.ipv4 import Route

if TYPE_CHECKING:
    from ..devices.router import Router

BGP_PORT = 179
CONNECT_RETRY_MS = 3_000
OPEN_TIMEOUT_MS = 6_000
KEEPALIVE_MS = 60_000
HOLD_MS = 180_000
ORIGIN_RANK = {"i": 0, "e": 1, "?": 2}


@dataclass(frozen=True)
class PathAttrs:
    next_hop: IPv4Address
    as_path: tuple[int, ...] = ()
    origin: str = "i"
    med: int | None = None
    local_pref: int | None = None

    def text_path(self) -> str:
        return " ".join(map(str, self.as_path))


@dataclass(frozen=True)
class BgpMessage:
    kind: str                       # open | keepalive | update | notification
    asn: int = 0
    router_id: IPv4Address | None = None
    hold: int = 180
    announce: tuple = ()            # ((prefix str, PathAttrs), ...)
    withdraw: tuple = ()            # (prefix str, ...)
    error: str = ""
    label = "BGP"

    def summary(self) -> str:
        if self.kind == "update":
            return f"UPDATE ({len(self.announce)} NLRI, {len(self.withdraw)} withdrawn)"
        if self.kind == "open":
            return f"OPEN AS {self.asn} ID {self.router_id}"
        return self.kind.upper()

    def layer_lines(self) -> list[str]:
        lines = [f"Type: {self.kind.upper()}"]
        if self.kind == "open":
            lines += ["Version: 4", f"My AS: {self.asn}", f"Hold time: {self.hold}",
                      f"BGP identifier: {self.router_id}"]
        for pfx, a in self.announce:
            lines.append(f"NLRI {pfx}: next hop {a.next_hop}, AS_PATH [{a.text_path()}], origin {a.origin}"
                         + (f", MED {a.med}" if a.med is not None else "")
                         + (f", LOCAL_PREF {a.local_pref}" if a.local_pref is not None else ""))
        for pfx in self.withdraw:
            lines.append(f"Withdrawn: {pfx}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return lines


@dataclass
class BgpConfig:
    asn: int | None = None
    router_id: str | None = None
    neighbors: dict[str, dict] = field(default_factory=dict)
    networks: list[str] = field(default_factory=list)
    redistribute: list[str] = field(default_factory=list)       # connected | static | ospf
    enabled: bool = True

    def to_dict(self) -> dict:
        return {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "BgpConfig":
        c = cls()
        for k, v in d.items():
            if hasattr(c, k):
                setattr(c, k, v)
        c.neighbors = {k: dict(v) for k, v in c.neighbors.items()}
        return c


@dataclass
class Peer:
    ip: IPv4Address
    state: str = "Idle"
    sent_open: bool = False
    got_open: bool = False
    last_attempt: float = -1e12
    last_rx: float = 0.0
    last_tx: float = 0.0
    up_since: float = 0.0
    remote_rid: IPv4Address | None = None
    local_ip: IPv4Address | None = None
    adj_in: dict[IPv4Network, PathAttrs] = field(default_factory=dict)
    adj_out: dict[IPv4Network, PathAttrs] = field(default_factory=dict)
    msgs_rx: int = 0
    msgs_tx: int = 0
    last_error: str = ""
    flaps: int = 0


@dataclass
class BgpPath:
    prefix: IPv4Network
    attrs: PathAttrs
    peer: IPv4Address | None        # None = locally originated
    weight: int = 0
    ebgp: bool = False
    igp_metric: int = 0
    best: bool = False


class BgpProcess:
    HOUSEKEEPING_MS = 1000.0

    def __init__(self, router: "Router", config: BgpConfig):
        self.r = router
        self.cfg = config
        self.engine = router.engine
        self.peers: dict[IPv4Address, Peer] = {}
        self.paths: dict[IPv4Network, list[BgpPath]] = {}
        self.routes: list[Route] = []
        self.running = False
        self._timer = None
        self.table_version = 1
        self._sport = 11000

    # -- identity ------------------------------------------------------------------------------
    @property
    def router_id(self) -> IPv4Address:
        if self.cfg.router_id:
            return IPv4Address(self.cfg.router_id)
        if getattr(self.r, "global_router_id", None):
            return IPv4Address(self.r.global_router_id)
        return self.r.auto_router_id() or IPv4Address("0.0.0.0")

    def ncfg(self, ip) -> dict:
        return self.cfg.neighbors.get(str(ip), {})

    def is_ebgp(self, ip) -> bool:
        return int(self.ncfg(ip).get("remote_as", 0)) != int(self.cfg.asn or 0)

    # -- lifecycle -------------------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._timer = self.engine.schedule(50.0, self._housekeeping)

    def stop(self) -> None:
        for p in list(self.peers.values()):
            if p.state == "Established":
                self._send(p, BgpMessage("notification", error="Cease"))
        self.running = False
        if self._timer is not None:
            self._timer.cancelled = True
        self.peers.clear()
        self.paths.clear()
        self.routes = []
        self.r.routes_changed()

    def reset_peer(self, ip, reason="", notify=True) -> None:
        p = self.peers.get(IPv4Address(str(ip)))
        if p is None:
            return
        if p.state == "Established":
            if notify:
                self._send(p, BgpMessage("notification", error=reason or "Cease"))
            self.r.console(f"%BGP-5-ADJCHANGE: neighbor {p.ip} Down {reason or 'User reset'}")
            p.flaps += 1
        p.state, p.sent_open, p.got_open = "Idle", False, False
        p.adj_in.clear()
        p.adj_out.clear()
        p.last_attempt = self.engine.now
        self.decide()

    def _housekeeping(self) -> None:
        if not self.running:
            return
        now = self.engine.now
        wanted = {IPv4Address(ip) for ip in self.cfg.neighbors}
        for ip in list(self.peers):
            if ip not in wanted:
                self.reset_peer(ip, "Neighbor deleted")
                del self.peers[ip]
        if self.cfg.asn and int(self.router_id) != 0:
            for ip in wanted:
                p = self.peers.setdefault(ip, Peer(ip))
                nc = self.ncfg(ip)
                if nc.get("shutdown"):
                    if p.state != "Idle":
                        self.reset_peer(ip, "Admin. shutdown")
                    p.state = "Idle (Admin)"
                    continue
                if p.state == "Idle (Admin)":
                    p.state = "Idle"
                if p.state == "Established":
                    if self._local_addr(p) is None:                     # fast fallover
                        self.reset_peer(ip, "Interface flap", notify=False)
                        continue
                    if now - p.last_rx > HOLD_MS:
                        self.reset_peer(ip, "BGP Notification sent, hold time expired")
                        continue
                    if now - p.last_tx >= KEEPALIVE_MS:
                        self._send(p, BgpMessage("keepalive"))
                elif p.state in ("OpenSent", "OpenConfirm") and now - p.last_attempt > OPEN_TIMEOUT_MS:
                    p.state, p.sent_open, p.got_open = "Active", False, False
                elif p.state in ("Idle", "Active") and now - p.last_attempt >= CONNECT_RETRY_MS:
                    p.last_attempt = now
                    if not self._connected_ok(p):
                        p.state = "Idle"                      # eBGP peer not directly connected
                        p.last_error = "No route to multihop neighbor (ebgp-multihop not set)"
                    elif self._local_addr(p) is None:
                        p.state = "Active"
                    else:
                        self._send_open(p)
                        p.state = "OpenSent"
        self.decide()
        self._timer = self.engine.schedule(self.HOUSEKEEPING_MS, self._housekeeping)

    # -- transport ----------------------------------------------------------------------------------
    def _connected_ok(self, p: Peer) -> bool:
        """Cisco 'connected check': single-hop eBGP peers must be on a connected subnet."""
        nc = self.ncfg(p.ip)
        if not self.is_ebgp(p.ip) or nc.get("multihop") or nc.get("disable_connected_check"):
            return True
        return any(p.ip in port.ip.network for port in self.r.ports.values()
                   if port.ip is not None and port.is_up and not port.is_loopback)

    def _local_addr(self, p: Peer) -> IPv4Address | None:
        nc = self.ncfg(p.ip)
        src = nc.get("update_source")
        if src:
            port = self.r.get_port(src)
            if port is None or port.ip is None or not port.is_up:
                return None
            if self.r.route_lookup(p.ip) is None:
                return None
            return port.ip.ip
        r = self.r.route_lookup(p.ip)
        if r is None or r[0].ip is None or not r[0].is_up:
            return None
        return r[0].ip.ip

    def _send(self, p: Peer, msg: BgpMessage) -> bool:
        src = self._local_addr(p)
        if src is None:
            return False
        p.local_ip = src
        nc = self.ncfg(p.ip)
        ttl = 255 if not self.is_ebgp(p.ip) else int(nc.get("multihop") or 1)
        self._sport = 11000 + (self._sport - 11000 + 1) % 50000
        pkt = IPv4Packet(src, p.ip, ttl, TcpSegment(self._sport, BGP_PORT, msg), PROTO_TCP,
                         next(self.r._ip_ident))
        p.msgs_tx += 1
        p.last_tx = self.engine.now
        return self.r.send_ip(pkt)

    def _send_open(self, p: Peer) -> None:
        if self._send(p, BgpMessage("open", int(self.cfg.asn), self.router_id)):
            p.sent_open = True

    # -- receive -----------------------------------------------------------------------------------
    def receive(self, pkt: IPv4Packet, port) -> None:
        if not self.running or not self.cfg.asn:
            return
        seg: TcpSegment = pkt.payload
        msg = seg.data
        if not isinstance(msg, BgpMessage):
            return
        p = self.peers.get(pkt.src)
        nc = self.cfg.neighbors.get(str(pkt.src))
        if p is None or nc is None or nc.get("shutdown") or not self._connected_ok(p):
            return                       # connection from an unconfigured address: refused
        p.msgs_rx += 1
        p.last_rx = self.engine.now
        if msg.kind == "open":
            if int(msg.asn) != int(nc.get("remote_as", -1)):
                self._send(p, BgpMessage("notification", error="OPEN Message Error / Bad Peer AS"))
                p.last_error = f"peer in wrong AS {msg.asn}"
                self.r.console(f"%BGP-3-NOTIFICATION: sent to neighbor {p.ip} 2/2 (peer in wrong AS) "
                               f"2 bytes {msg.asn:04X}")
                p.state, p.sent_open = "Idle", False
                p.last_attempt = self.engine.now
                return
            if msg.router_id == self.router_id:
                self._send(p, BgpMessage("notification", error="OPEN Message Error / Bad BGP Identifier"))
                p.last_error = "duplicate router ID"
                p.state, p.sent_open = "Idle", False
                return
            p.remote_rid = msg.router_id
            p.got_open = True
            if not p.sent_open or p.state not in ("OpenSent", "OpenConfirm"):
                self._send_open(p)
            self._send(p, BgpMessage("keepalive"))
            if p.state != "Established":
                p.state = "OpenConfirm"
                p.last_attempt = self.engine.now
        elif msg.kind == "keepalive":
            if p.state == "OpenConfirm" and p.got_open:
                p.state = "Established"
                p.up_since = self.engine.now
                p.last_error = ""
                self.r.console(f"%BGP-5-ADJCHANGE: neighbor {p.ip} Up")
                p.adj_out.clear()
                self.decide(force_send=True)
        elif msg.kind == "update" and p.state == "Established":
            for pfx in msg.withdraw:
                p.adj_in.pop(IPv4Network(pfx), None)
            for pfx, attrs in msg.announce:
                p.adj_in[IPv4Network(pfx)] = attrs
            self.decide()
        elif msg.kind == "notification":
            p.last_error = msg.error
            if p.state == "Established":
                self.r.console(f"%BGP-5-ADJCHANGE: neighbor {p.ip} Down BGP Notification received")
                p.flaps += 1
            p.state, p.sent_open, p.got_open = "Idle", False, False
            p.adj_in.clear()
            p.adj_out.clear()
            p.last_attempt = self.engine.now
            self.decide()

    # -- decision process ---------------------------------------------------------------------------
    def _nh_metric(self, nh: IPv4Address) -> int | None:
        """IGP metric to a next hop, or None if it is unreachable (BGP routes are not used)."""
        if nh == IPv4Address("0.0.0.0"):
            return 0
        best = None
        for r in self.r.routing_table():
            if r.code.startswith("B") or nh not in r.network:
                continue
            if best is None or r.network.prefixlen > best.network.prefixlen:
                best = r
        if best is None:
            return None
        return best.metric if best.code.startswith("O") else 0

    def _local_routes(self) -> dict[IPv4Network, PathAttrs]:
        out: dict[IPv4Network, PathAttrs] = {}
        rib = [r for r in self.r.routing_table() if not r.code.startswith("B") and r.code != "L"]
        zero = IPv4Address("0.0.0.0")
        for pfx in self.cfg.networks:
            net = IPv4Network(pfx)
            match = [r for r in rib if r.network == net]
            if match:
                out[net] = PathAttrs(zero, (), "i", match[0].metric if match[0].code.startswith("O") else 0)
        for proto in self.cfg.redistribute:
            for r in rib:
                code = r.code[:1]
                if (proto == "connected" and code == "C") or (proto == "static" and code == "S") \
                        or (proto == "ospf" and code == "O"):
                    out.setdefault(r.network, PathAttrs(zero, (), "?", r.metric))
        return out

    def decide(self, force_send: bool = False) -> None:
        if not self.running:
            return
        asn = int(self.cfg.asn or 0)
        paths: dict[IPv4Network, list[BgpPath]] = {}
        for net, attrs in self._local_routes().items():
            paths.setdefault(net, []).append(BgpPath(net, attrs, None, 32768, False, 0))
        for p in self.peers.values():
            if p.state != "Established":
                continue
            nc = self.ncfg(p.ip)
            ebgp = self.is_ebgp(p.ip)
            for net, a in p.adj_in.items():
                if asn in a.as_path:
                    continue                                  # AS_PATH loop
                metric = self._nh_metric(a.next_hop)
                if metric is None:
                    continue                                  # next hop unreachable: not valid
                paths.setdefault(net, []).append(
                    BgpPath(net, a, p.ip, int(nc.get("weight", 0)), ebgp, metric))

        def key(bp: BgpPath):
            a = bp.attrs
            return (-bp.weight, -(a.local_pref if a.local_pref is not None else 100),
                    0 if bp.peer is None else 1, len(a.as_path), ORIGIN_RANK.get(a.origin, 2),
                    a.med or 0, 0 if bp.ebgp else 1, bp.igp_metric,
                    int(self.peers[bp.peer].remote_rid or 0) if bp.peer is not None else 0)
        routes: list[Route] = []
        for net, lst in paths.items():
            lst.sort(key=key)
            lst[0].best = True
            best = lst[0]
            if best.peer is not None:
                dist = self.r.bgp_distance(best.ebgp)
                routes.append(Route(net, "B", None, best.attrs.next_hop, dist, best.attrs.med or 0))
        old = [(r.network, r.next_hop, r.distance) for r in self.routes]
        self.paths = paths
        self.routes = sorted(routes, key=lambda r: (int(r.network.network_address), r.network.prefixlen))
        if [(r.network, r.next_hop, r.distance) for r in self.routes] != old:
            self.table_version += 1
            self.r.routes_changed()
        self._advertise(force_send)

    def _advertise(self, force: bool) -> None:
        asn = int(self.cfg.asn or 0)
        reflector = any(nc.get("rr_client") for nc in self.cfg.neighbors.values())
        for p in self.peers.values():
            if p.state != "Established":
                continue
            nc = self.ncfg(p.ip)
            ebgp = self.is_ebgp(p.ip)
            local = p.local_ip or self._local_addr(p) or IPv4Address("0.0.0.0")
            out: dict[IPv4Network, PathAttrs] = {}
            for net, lst in self.paths.items():
                best = next(bp for bp in lst if bp.best)
                if best.peer == p.ip:
                    continue
                if best.peer is not None and not best.ebgp and not ebgp:
                    src_client = bool(self.ncfg(best.peer).get("rr_client"))
                    if not (reflector and (src_client or nc.get("rr_client"))):
                        continue                              # iBGP split horizon
                a = best.attrs
                if ebgp:
                    out[net] = PathAttrs(local, (asn,) + a.as_path, a.origin,
                                         a.med if best.peer is None else None, None)
                else:
                    nh = local if (best.peer is None or nc.get("next_hop_self")) else a.next_hop
                    lp = a.local_pref if a.local_pref is not None else 100
                    out[net] = PathAttrs(nh, a.as_path, a.origin, a.med, lp)
            if nc.get("default_originate"):
                out[IPv4Network("0.0.0.0/0")] = PathAttrs(local, (asn,) if ebgp else (), "i", None,
                                                          None if ebgp else 100)
            announce = tuple((str(n), a) for n, a in out.items() if force or p.adj_out.get(n) != a)
            withdraw = tuple(str(n) for n in p.adj_out if n not in out)
            if announce or withdraw:
                self._send(p, BgpMessage("update", announce=announce, withdraw=withdraw))
            p.adj_out = out

    # -- show helpers ---------------------------------------------------------------------------------
    def table(self) -> list[BgpPath]:
        out = []
        for net in sorted(self.paths, key=lambda n: (int(n.network_address), n.prefixlen)):
            out += self.paths[net]
        return out

    def received_count(self, p: Peer) -> int:
        return len(p.adj_in) if p.state == "Established" else 0
