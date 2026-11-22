"""A botnet for DDoS training: one canvas device stands for many infected hosts.

It has an ordinary network card (IP, mask, gateway) and a list of attacks.  A running attack sends one
probe packet per simulated second that carries the whole flow (see ``security/ddos.py``)."""
from __future__ import annotations

import itertools
import random
from ipaddress import IPv4Address

from ..security.ddos import KINDS, Attack, FloodTag, monitor
from .host import Host

_sport = itertools.count(20000)


class Botnet(Host):
    category = "attack"
    model = "Botnet (DDoS)"
    default_hostname = "Botnet"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.attacks: list[Attack] = []
        self._timer = None
        self._rng = random.Random(name)
        self._names: dict[str, IPv4Address] = {}       # attacked domain -> the address it resolved to
        self._resolving: set[str] = set()

    # -- control ---------------------------------------------------------------------------------
    def start(self, index: int | None = None) -> None:
        for i, a in enumerate(self.attacks):
            if index is None or i == index:
                a.running = True
        self._kick()

    def stop(self, index: int | None = None) -> None:
        for i, a in enumerate(self.attacks):
            if index is None or i == index:
                a.running = False
        self.engine.emit("device_changed", self)

    def _kick(self) -> None:
        topo = getattr(self.engine, "topology", None)
        if topo is not None:
            monitor(topo).ensure_running()
        if self._timer is None and any(a.running for a in self.attacks):
            self._timer = self.engine.schedule(50.0, self._tick)
        self.engine.emit("device_changed", self)

    def _tick(self) -> None:
        self._timer = None
        if not any(a.running for a in self.attacks) or not self.powered:
            return
        topo = getattr(self.engine, "topology", None)
        if topo is not None:
            monitor(topo).ensure_running()
        for i, a in enumerate(self.attacks):
            if a.running:
                self._fire(i, a)
        self._timer = self.engine.schedule(1000.0, self._tick)

    # -- probes ----------------------------------------------------------------------------------
    def _src(self, a: Attack) -> IPv4Address | None:
        if a.spoof and a.kind not in ("http", "slowloris"):
            # spoofed: a random public address (never the botnet's own network)
            return IPv4Address(self._rng.choice((45, 77, 91, 103, 185, 193, 212)) << 24 | self._rng.randrange(1, 1 << 24))
        return self.nic.ip.ip if self.nic.ip is not None else None

    def _fire(self, index: int, a: Attack) -> None:
        from ..packets import PROTO_ICMP, PROTO_TCP, PROTO_UDP, IPv4Packet, IcmpPacket, TcpSegment, UdpDatagram
        target = self._target(a.target)
        if target is None:
            return
        proto, _, _, _, _, _ = KINDS[a.kind]
        aid = f"{self.name}#{index + 1}"
        if a.kind in ("dns-amp", "ntp-amp"):
            refl = [IPv4Address(r) for r in a.reflectors if r.strip()]
            if not refl:
                return
            share = 1.0 / len(refl)
            for r in refl:                              # spoofed queries: source = the victim
                tag = FloodTag(aid, a.kind, a.bots, a.total_pps * share, a.total_bps * share, a.rate, "query")
                pkt = IPv4Packet(target, r, 64, UdpDatagram(next(_sport) % 40000 + 20000, a.dport, None), PROTO_UDP,
                                 next(self._ip_ident), flood=tag)
                self.send_ip(pkt)
            return
        src = self._src(a)
        if src is None:
            return
        tag = FloodTag(aid, a.kind, a.bots, a.total_pps, a.total_bps, a.rate)
        sport = next(_sport) % 40000 + 20000
        if proto == "icmp":
            l4, num = IcmpPacket(8, 0, 0xDD05, 1, a.packet_size), PROTO_ICMP
        elif proto == "udp":
            l4, num = UdpDatagram(sport, a.dport, None), PROTO_UDP
        elif a.kind == "syn":
            l4, num = TcpSegment(sport, a.dport, None, "SYN"), PROTO_TCP
        else:                                           # http / slowloris: established connections
            from ..services.http import HttpMessage
            data = HttpMessage(True, path="/", host=str(target)) if a.kind == "http" else None
            l4, num = TcpSegment(sport, a.dport, data, "PSH,ACK" if data else "ACK"), PROTO_TCP
        self.send_ip(IPv4Packet(src, target, 64, l4, num, next(self._ip_ident), flood=tag))

    def _target(self, name: str) -> IPv4Address | None:
        """An address, or a domain name the bots look up through DNS (and follow when it changes,
        once the TTL of the cached record runs out)."""
        try:
            return IPv4Address(name)
        except ValueError:
            pass
        key = name.lower().rstrip(".")
        if key not in self._resolving:
            from ..services.dns import Resolve

            def done(ip, _err, key=key):
                self._resolving.discard(key)
                if ip is not None:
                    self._names[key] = ip
            self._resolving.add(key)
            Resolve(self, key, done).start()
        return self._names.get(key)

    # -- the botnet ignores whatever comes back (it never completes handshakes) ---------------------
    def deliver_protocol(self, pkt, port) -> None:
        if getattr(pkt, "flood", None) is not None:
            return
        super().deliver_protocol(pkt, port)

    # -- persistence -------------------------------------------------------------------------------
    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["attacks"] = [a.to_dict() for a in self.attacks]
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.attacks = [Attack.from_dict(d) for d in cfg.get("attacks", [])]
        if any(a.running for a in self.attacks):
            self._kick()
