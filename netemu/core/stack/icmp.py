"""Ping and traceroute sessions.

A session is a long-running *job*: it sends ICMP echo requests through the
engine and reports results to a *listener* (a vendor-specific formatter that
prints Cisco / MikroTik / Junos / Huawei / Windows style output).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

from ..addresses import IPv4Address
from ..packets import (ICMP_DEST_UNREACHABLE, ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST,
                       ICMP_TIME_EXCEEDED, IcmpPacket, IPv4Packet)

_idents = itertools.count(0x1000)


class Job:
    """Something a CLI command left running in the background (ping, traceroute)."""

    def __init__(self) -> None:
        self.done = False
        self._done_callbacks: list[Callable[[], None]] = []

    def on_done(self, fn: Callable[[], None]) -> None:
        if self.done:
            fn()
        else:
            self._done_callbacks.append(fn)

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        for fn in self._done_callbacks:
            fn()

    def cancel(self) -> None:
        self.finish()


@dataclass
class PingStats:
    sent: int = 0
    received: int = 0
    rtts: list[float] = field(default_factory=list)

    @property
    def lost(self) -> int:
        return self.sent - self.received

    @property
    def loss_percent(self) -> int:
        return round(100 * self.lost / self.sent) if self.sent else 0

    @property
    def success_percent(self) -> int:
        return 100 - self.loss_percent if self.sent else 0

    def rtt(self, which: str) -> int:
        if not self.rtts:
            return 0
        if which == "min":
            return round(min(self.rtts))
        if which == "max":
            return round(max(self.rtts))
        return round(sum(self.rtts) / len(self.rtts))


class PingListener:
    """Override in vendor formatters; every method is optional."""

    def on_start(self, session: "PingSession") -> None: ...
    def on_reply(self, seq: int, src: IPv4Address, rtt: float, ttl: int, size: int) -> None: ...
    def on_timeout(self, seq: int) -> None: ...
    def on_error(self, seq: int, kind: str, src: IPv4Address) -> None: ...
    def on_send_fail(self, seq: int) -> None: ...
    def on_finish(self, stats: PingStats) -> None: ...


class PingSession(Job):
    def __init__(self, device, dst: IPv4Address, listener: PingListener, count: int = 5,
                 timeout: float = 2000.0, size: int = 100, interval: float = 0.0,
                 ttl: int | None = None):
        super().__init__()
        self.device = device
        self.engine = device.engine
        self.dst = dst
        self.listener = listener
        self.count = count
        self.timeout = timeout
        self.size = size
        self.interval = interval
        self.ttl = ttl
        self.ident = next(_idents) & 0xFFFF
        self.stats = PingStats()
        self.source: IPv4Address | None = None
        self._seq = 0
        self._outstanding: dict[int, tuple[float, object]] = {}

    def start(self) -> "PingSession":
        self.device.register_icmp(self.ident, self._on_icmp)
        self.listener.on_start(self)
        self._send_next()
        return self

    def _send_next(self) -> None:
        if self.done:
            return
        if self._seq >= self.count:
            self._finish()
            return
        seq = self._seq
        self._seq += 1
        self.stats.sent += 1
        icmp = IcmpPacket(ICMP_ECHO_REQUEST, 0, self.ident, seq, self.size)
        src = self.device.originate(self.dst, icmp, self.ttl)
        if src is None:
            self.listener.on_send_fail(seq)
            self.engine.schedule(max(self.interval, 1.0), self._send_next)
            return
        self.source = self.source or src
        timer = self.engine.schedule(self.timeout, self._on_timeout, seq)
        self._outstanding[seq] = (self.engine.now, timer)

    def _on_icmp(self, pkt: IPv4Packet) -> None:
        if self.done:
            return
        icmp: IcmpPacket = pkt.payload
        if icmp.type == ICMP_ECHO_REPLY:
            seq = icmp.seq
        else:
            seq = icmp.original.payload.seq
        if seq not in self._outstanding:
            return
        sent_at, timer = self._outstanding.pop(seq)
        timer.cancel()
        rtt = self.engine.now - sent_at
        if icmp.type == ICMP_ECHO_REPLY:
            self.stats.received += 1
            self.stats.rtts.append(rtt)
            self.listener.on_reply(seq, pkt.src, rtt, pkt.ttl, icmp.size)
        elif icmp.type == ICMP_DEST_UNREACHABLE:
            self.listener.on_error(seq, "unreachable", pkt.src)
        elif icmp.type == ICMP_TIME_EXCEEDED:
            self.listener.on_error(seq, "ttl", pkt.src)
        self.engine.schedule(self.interval, self._send_next)

    def _on_timeout(self, seq: int) -> None:
        if self.done or seq not in self._outstanding:
            return
        self._outstanding.pop(seq)
        self.listener.on_timeout(seq)
        self._send_next()

    def _finish(self) -> None:
        self.device.unregister_icmp(self.ident)
        self.listener.on_finish(self.stats)
        self.finish()

    def cancel(self) -> None:
        if self.done:
            return
        for _, timer in self._outstanding.values():
            timer.cancel()
        self._outstanding.clear()
        self._finish()


@dataclass
class HopResult:
    ttl: int
    probes: list[float | None] = field(default_factory=list)  # rtt or None (timeout)
    address: IPv4Address | None = None
    reached: bool = False
    unreachable: bool = False


class TraceListener:
    def on_start(self, session: "TracerouteSession") -> None: ...
    def on_probe(self, hop: HopResult, index: int) -> None: ...
    def on_hop(self, hop: HopResult) -> None: ...
    def on_finish(self, hops: list[HopResult], reached: bool) -> None: ...


class TracerouteSession(Job):
    def __init__(self, device, dst: IPv4Address, listener: TraceListener,
                 max_hops: int = 30, probes: int = 3, timeout: float = 2000.0):
        super().__init__()
        self.device = device
        self.engine = device.engine
        self.dst = dst
        self.listener = listener
        self.max_hops = max_hops
        self.probes = probes
        self.timeout = timeout
        self.ident = next(_idents) & 0xFFFF
        self.hops: list[HopResult] = []
        self._hop: HopResult | None = None
        self._probe_index = 0
        self._pending: tuple[int, float, object] | None = None
        self._seq = itertools.count()

    def start(self) -> "TracerouteSession":
        self.device.register_icmp(self.ident, self._on_icmp)
        self.listener.on_start(self)
        self._next_hop()
        return self

    def _next_hop(self) -> None:
        if self.done:
            return
        ttl = len(self.hops) + 1
        if ttl > self.max_hops:
            self._finish(False)
            return
        self._hop = HopResult(ttl)
        self.hops.append(self._hop)
        self._probe_index = 0
        self._send_probe()

    def _send_probe(self) -> None:
        if self.done:
            return
        seq = next(self._seq)
        icmp = IcmpPacket(ICMP_ECHO_REQUEST, 0, self.ident, seq, 32)
        src = self.device.originate(self.dst, icmp, ttl=self._hop.ttl)
        if src is None:
            # No route at all: mark as unreachable and stop.
            self._hop.probes.append(None)
            self._hop.unreachable = True
            self.listener.on_probe(self._hop, self._probe_index)
            self.listener.on_hop(self._hop)
            self._finish(False)
            return
        timer = self.engine.schedule(self.timeout, self._on_timeout, seq)
        self._pending = (seq, self.engine.now, timer)

    def _record(self, rtt: float | None, addr: IPv4Address | None) -> None:
        hop = self._hop
        hop.probes.append(rtt)
        if addr is not None and hop.address is None:
            hop.address = addr
        self.listener.on_probe(hop, self._probe_index)
        self._probe_index += 1
        if self._probe_index < self.probes:
            self.engine.schedule(0.0, self._send_probe)
            return
        self.listener.on_hop(hop)
        if hop.reached or hop.unreachable:
            self._finish(hop.reached)
        else:
            self.engine.schedule(0.0, self._next_hop)

    def _on_icmp(self, pkt: IPv4Packet) -> None:
        if self.done or self._pending is None:
            return
        icmp: IcmpPacket = pkt.payload
        seq = icmp.seq if icmp.type == ICMP_ECHO_REPLY else icmp.original.payload.seq
        pseq, sent_at, timer = self._pending
        if seq != pseq:
            return
        timer.cancel()
        self._pending = None
        if icmp.type == ICMP_ECHO_REPLY:
            self._hop.reached = True
        elif icmp.type == ICMP_DEST_UNREACHABLE:
            self._hop.unreachable = True
        self._record(self.engine.now - sent_at, pkt.src)

    def _on_timeout(self, seq: int) -> None:
        if self.done or self._pending is None or self._pending[0] != seq:
            return
        self._pending = None
        self._record(None, None)

    def _finish(self, reached: bool) -> None:
        self.device.unregister_icmp(self.ident)
        self.listener.on_finish(self.hops, reached)
        self.finish()

    def cancel(self) -> None:
        if self.done:
            return
        if self._pending:
            self._pending[2].cancel()
            self._pending = None
        self._finish(False)
