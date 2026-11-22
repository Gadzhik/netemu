"""Discrete event simulation engine.

Time is virtual and measured in milliseconds.  Everything that happens in the
network (a frame arriving at a port, a timer firing) is an event in a priority
queue.  The GUI drives the engine:

* realtime mode: virtual time follows wall-clock time (``advance_to``);
* simulation mode: the user steps through events one timestamp at a time.

The engine never imports Qt; observers subscribe to named notifications.
"""
from __future__ import annotations

import heapq
import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

KIND_FRAME = "frame"
KIND_TIMER = "timer"


@dataclass(order=True)
class Event:
    time: float
    seq: int
    callback: Callable = field(compare=False)
    args: tuple = field(compare=False, default=())
    kind: str = field(compare=False, default=KIND_TIMER)
    info: dict = field(compare=False, default_factory=dict)
    created: float = field(compare=False, default=0.0)
    cancelled: bool = field(compare=False, default=False)

    def cancel(self) -> None:
        self.cancelled = True


class SimEngine:
    def __init__(self) -> None:
        self.now: float = 0.0
        self._queue: list[Event] = []
        self._seq = itertools.count()
        self._listeners: dict[str, list[Callable]] = defaultdict(list)
        self._frame_uid = itertools.count(1)

    # -- observers -------------------------------------------------------
    def subscribe(self, name: str, fn: Callable) -> None:
        self._listeners[name].append(fn)

    def unsubscribe(self, name: str, fn: Callable) -> None:
        if fn in self._listeners.get(name, []):
            self._listeners[name].remove(fn)

    def emit(self, name: str, *args: Any) -> None:
        for fn in list(self._listeners.get(name, [])):
            fn(*args)

    def next_frame_uid(self) -> int:
        return next(self._frame_uid)

    # -- scheduling ------------------------------------------------------
    def schedule(self, delay: float, callback: Callable, *args: Any,
                 kind: str = KIND_TIMER, info: dict | None = None) -> Event:
        ev = Event(self.now + max(0.0, delay), next(self._seq), callback, args,
                   kind, info or {}, created=self.now)
        heapq.heappush(self._queue, ev)
        if kind == KIND_FRAME:
            self.emit("frame_sent", ev)
        return ev

    def _prune(self) -> None:
        while self._queue and self._queue[0].cancelled:
            heapq.heappop(self._queue)

    def peek(self) -> Event | None:
        self._prune()
        return self._queue[0] if self._queue else None

    def next_time(self) -> float | None:
        ev = self.peek()
        return ev.time if ev else None

    def has_pending(self) -> bool:
        return self.peek() is not None

    def pending(self, kind: str | None = None) -> list[Event]:
        return sorted(e for e in self._queue
                      if not e.cancelled and (kind is None or e.kind == kind))

    def frames_in_flight(self) -> list[Event]:
        return self.pending(KIND_FRAME)

    def _execute(self, ev: Event) -> None:
        self.now = max(self.now, ev.time)
        ev.callback(*ev.args)
        if ev.kind == KIND_FRAME:
            self.emit("frame_delivered", ev)

    def step_one(self) -> Event | None:
        """Execute exactly one event (the earliest)."""
        ev = self.peek()
        if ev is None:
            return None
        heapq.heappop(self._queue)
        self._execute(ev)
        return ev

    def step(self) -> list[Event]:
        """Advance to the next timestamp and execute every event scheduled for it."""
        t = self.next_time()
        if t is None:
            return []
        done = []
        while True:
            ev = self.peek()
            if ev is None or ev.time > t + 1e-9:
                break
            heapq.heappop(self._queue)
            self._execute(ev)
            done.append(ev)
        return done

    def advance_to(self, t: float) -> int:
        """Execute all events up to time *t* and set the clock to *t*."""
        count = 0
        while True:
            ev = self.peek()
            if ev is None or ev.time > t:
                break
            heapq.heappop(self._queue)
            self._execute(ev)
            count += 1
        self.now = max(self.now, t)
        return count

    def run(self, max_time: float | None = None, max_events: int = 1_000_000) -> int:
        """Run until the queue is empty (or *max_time* more virtual ms elapse: relative, not absolute)."""
        limit = None if max_time is None else self.now + max_time
        count = 0
        while count < max_events:
            ev = self.peek()
            if ev is None or (limit is not None and ev.time > limit):
                break
            heapq.heappop(self._queue)
            self._execute(ev)
            count += 1
        if limit is not None:
            self.now = max(self.now, limit)
        return count

    def run_until(self, predicate: Callable[[], bool], max_time: float = 600_000) -> bool:
        """Run events until *predicate()* is true; returns whether it became true."""
        limit = self.now + max_time
        while not predicate():
            ev = self.peek()
            if ev is None or ev.time > limit:
                return predicate()
            heapq.heappop(self._queue)
            self._execute(ev)
        return True

    def clear(self) -> None:
        """Drop every pending event (new/open project)."""
        self._queue.clear()

    def clear_frames(self) -> None:
        """Drop every frame currently in flight (simulation reset)."""
        for ev in self._queue:
            if ev.kind == KIND_FRAME:
                ev.cancelled = True
