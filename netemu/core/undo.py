"""Undo / redo of canvas edits: adding and deleting devices and cables, moving devices.

The stack listens to the topology's events, so every way of editing the canvas is covered.
Operations that happen together (deleting a device removes its cables first) are grouped
until :meth:`UndoStack.commit` is called — the GUI does that once the event loop is idle and
after a drag ends.  Deleted devices come back with their full configuration."""
from __future__ import annotations

MAX_STEPS = 100


def _snap(dev) -> dict:
    return {"model": dev.model_key, "id": dev.id, "name": dev.name, "x": dev.x, "y": dev.y,
            "config": dev.to_config(), "startup": dev.startup_config, "powered": dev.powered}


def _link_key(link) -> tuple:
    return (link.a.device.id, link.a.name, link.b.device.id, link.b.name)


class UndoStack:
    def __init__(self, topology):
        self.t = topology
        self.undo_steps: list[list] = []
        self.redo_steps: list[list] = []
        self.pending: list = []
        self.positions: dict[str, tuple] = {}
        self.muted = False
        self.on_pending = None            # GUI hook: schedule commit() when something was recorded
        ev = topology.engine
        ev.subscribe("device_added", self._dev_added)
        ev.subscribe("device_removed", self._dev_removed)
        ev.subscribe("link_added", self._link_added)
        ev.subscribe("link_removed", self._link_removed)

    # -- recording ---------------------------------------------------------------------------
    def _rec(self, op) -> None:
        if self.muted:
            return
        self.pending.append(op)
        if self.on_pending is not None:
            self.on_pending()

    def _dev_added(self, dev) -> None:
        self.positions[dev.id] = (dev.x, dev.y)
        self._rec(("add_dev", _snap(dev)))

    def _dev_removed(self, dev) -> None:
        self.positions.pop(dev.id, None)
        self._rec(("del_dev", _snap(dev)))

    def _link_added(self, link) -> None:
        if not getattr(link, "wireless", False):
            self._rec(("add_link", _link_key(link)))

    def _link_removed(self, link) -> None:
        if not getattr(link, "wireless", False):
            self._rec(("del_link", _link_key(link)))

    def commit(self) -> None:
        ops = list(self.pending)
        self.pending = []
        for d in self.t.devices.values():
            old = self.positions.get(d.id)
            new = (d.x, d.y)
            if old is not None and old != new:
                ops.append(("move", d.id, old, new))
            self.positions[d.id] = new
        if ops and not self.muted:
            self.undo_steps.append(ops)
            del self.undo_steps[:-MAX_STEPS]
            self.redo_steps.clear()

    def reset(self) -> None:
        self.undo_steps.clear()
        self.redo_steps.clear()
        self.pending = []
        self.positions = {d.id: (d.x, d.y) for d in self.t.devices.values()}

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_steps or self.pending)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_steps)

    # -- replay ----------------------------------------------------------------------------------
    def undo(self) -> bool:
        self.commit()
        if not self.undo_steps:
            return False
        ops = self.undo_steps.pop()
        self._play([self._inverse(op) for op in reversed(ops)])
        self.redo_steps.append(ops)
        return True

    def redo(self) -> bool:
        if not self.redo_steps:
            return False
        ops = self.redo_steps.pop()
        self._play(ops)
        self.undo_steps.append(ops)
        return True

    @staticmethod
    def _inverse(op):
        kind = op[0]
        if kind == "move":
            return ("move", op[1], op[3], op[2])
        flip = {"add_dev": "del_dev", "del_dev": "add_dev", "add_link": "del_link", "del_link": "add_link"}
        return (flip[kind],) + tuple(op[1:])

    def _play(self, ops) -> None:
        self.muted = True
        try:
            for op in ops:
                self._apply(op)
        finally:
            self.muted = False
            self.positions = {d.id: (d.x, d.y) for d in self.t.devices.values()}

    def _apply(self, op) -> None:
        t = self.t
        kind = op[0]
        if kind == "add_dev":
            s = op[1]
            if s["id"] in t.devices:
                return
            dev = t.add_device(s["model"], s["name"], s["x"], s["y"], device_id=s["id"])
            dev.powered = s.get("powered", True)
            dev.startup_config = s.get("startup")
            dev.apply_config(s["config"])
            t.engine.emit("device_changed", dev)
        elif kind == "del_dev":
            dev = t.devices.get(op[1]["id"])
            if dev is not None:
                t.remove_device(dev)
        elif kind == "add_link":
            aid, an, bid, bn = op[1]
            a, b = t.devices.get(aid), t.devices.get(bid)
            if a is not None and b is not None and an in a.ports and bn in b.ports \
                    and a.ports[an].link is None and b.ports[bn].link is None:
                t.connect(a.ports[an], b.ports[bn])
        elif kind == "del_link":
            aid, an, bid, bn = op[1]
            for link in list(t.links):
                if _link_key(link) in ((aid, an, bid, bn), (bid, bn, aid, an)):
                    t.disconnect(link)
        elif kind == "move":
            dev = t.devices.get(op[1])
            if dev is not None:
                dev.x, dev.y = op[3]
                t.engine.emit("device_moved", dev)
