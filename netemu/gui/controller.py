"""Glue between the (Qt-free) simulation core and the Qt event loop."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from ..core.engine import Event, SimEngine
from ..core.packets import EthernetFrame
from ..core.topology import Topology

REALTIME = "realtime"
SIMULATION = "simulation"

TICK_MS = 30
MAX_CATCHUP_MS = 1000          # most virtual time one tick may advance (see _on_tick)


@dataclass
class SimRecord:
    """One row of the simulation event list."""
    index: int
    time: float
    last_device: str
    at_device: str
    at_port: str
    protocol: str
    frame: EthernetFrame
    summary: str


class SimController(QObject):
    """Drives the engine clock and turns engine notifications into Qt signals."""

    tick = Signal()
    modeChanged = Signal(str)
    playingChanged = Signal(bool)
    recordAdded = Signal(object)          # SimRecord
    recordsCleared = Signal()
    linkStateChanged = Signal(object)     # Port
    deviceChanged = Signal(object)        # Device
    deviceAdded = Signal(object)
    deviceRemoved = Signal(object)
    linkAdded = Signal(object)
    linkRemoved = Signal(object)
    deviceMoved = Signal(object)

    def __init__(self, topology: Topology, parent=None):
        super().__init__(parent)
        self.topology = topology
        self.engine: SimEngine = topology.engine
        self.mode = REALTIME
        self.playing = False
        #: virtual milliseconds per real millisecond while playing a simulation
        self.speed = 0.004
        self.records: list[SimRecord] = []
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last = self._clock.elapsed()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(TICK_MS)

        self._subs = [
            ("frame_delivered", self._on_frame_delivered),
            ("link_state", self.linkStateChanged.emit),
            ("ddos_tick", lambda m: self.linkStateChanged.emit(None)),    # cable load colours
            ("device_changed", self.deviceChanged.emit),
            ("device_added", self.deviceAdded.emit),
            ("device_removed", self.deviceRemoved.emit),
            ("link_added", self.linkAdded.emit),
            ("link_removed", self.linkRemoved.emit),
            ("device_moved", self.deviceMoved.emit),
        ]
        for name, fn in self._subs:
            self.engine.subscribe(name, fn)

    def shutdown(self) -> None:
        """Stop the clock and unsubscribe from the engine (window closing)."""
        self._timer.stop()
        for name, fn in self._subs:
            self.engine.unsubscribe(name, fn)
        self._subs = []

    # -- mode ---------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.set_playing(False)
        self.clear_records()
        self.modeChanged.emit(mode)

    def set_playing(self, playing: bool) -> None:
        if self.playing != playing:
            self.playing = playing
            self.playingChanged.emit(playing)

    # -- clock ----------------------------------------------------------------------
    def _on_tick(self) -> None:
        now = self._clock.elapsed()
        # cap the catch-up: after the event loop stalled (laptop sleep, a modal dialog, a busy test run)
        # the simulation resumes instead of replaying the whole gap at once and freezing the window
        dt = min(max(0, now - self._last), MAX_CATCHUP_MS)
        self._last = now
        self.advance(dt)
        self.tick.emit()

    def advance(self, real_ms: float) -> None:
        e = self.engine
        if self.mode == REALTIME:
            e.advance_to(e.now + real_ms)
        elif self.playing:
            if e.frames_in_flight():
                e.advance_to(e.now + real_ms * self.speed)
            else:
                nxt = e.next_time()
                if nxt is not None:
                    e.advance_to(nxt)

    def step(self) -> list[Event]:
        """Simulation mode: advance to the next moment something visible happens.

        Protocol timers (OSPF housekeeping, ...) that send nothing are skipped, so every
        press of "Step" shows packets; at most one virtual minute is skipped at a time."""
        e = self.engine
        start = e.now
        done: list[Event] = []
        while True:
            evs = e.step()
            done += evs
            if not evs or any(ev.kind == "frame" for ev in evs) or e.frames_in_flight() \
                    or e.now - start > 60_000:
                return done

    def reset_simulation(self) -> None:
        self.engine.clear_frames()
        self.clear_records()

    def clear_records(self) -> None:
        self.records.clear()
        self.recordsCleared.emit()

    def _on_frame_delivered(self, ev: Event) -> None:
        if self.mode != SIMULATION:
            return
        info = ev.info
        frame: EthernetFrame = info["frame"]
        rec = SimRecord(len(self.records), ev.time, info["src"].device.name,
                        info["dst"].device.name, info["dst"].name, frame.protocol,
                        frame, frame.summary())
        self.records.append(rec)
        self.recordAdded.emit(rec)
