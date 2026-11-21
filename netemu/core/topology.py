"""Topology: the set of devices and cables in a project."""
from __future__ import annotations

from .devices import MODELS, Device, create_device
from .engine import SimEngine
from .port import Link, Port


class Topology:
    def __init__(self, engine: SimEngine | None = None):
        self.engine = engine or SimEngine()
        self.devices: dict[str, Device] = {}
        self.links: list[Link] = []
        self.lab = None                          # core.lab.Lab of a lab assignment, if any
        self.annotations: list[dict] = []        # canvas notes and frames
        # canvas label follows the hostname set on routers/switches
        self._seen_hostname: dict[str, str] = {}
        self._auto_name: dict[str, str] = {}
        self.engine.subscribe("device_changed", self._sync_name)
        self.engine.subscribe("device_changed", self._wifi_changed)
        self.engine.topology = self                 # lets Wi-Fi clients list the networks they can hear
        from .undo import UndoStack
        self.undo = UndoStack(self)
        self._rescanning = False

    def _wifi_changed(self, dev) -> None:
        if self._rescanning or dev.category not in ("wireless", "pc", "server"):
            return
        self.rescan_wireless()

    def rescan_wireless(self) -> None:
        """Associate wireless clients with access points (after any change in SSIDs, keys or positions)."""
        from .devices.wireless import rescan
        if self._rescanning:
            return
        self._rescanning = True
        try:
            rescan(self)
        finally:
            self._rescanning = False

    # -- devices -------------------------------------------------------------
    def _unique_name(self, prefix: str) -> str:
        names = {d.name for d in self.devices.values()}
        i = 0
        while f"{prefix}{i}" in names:
            i += 1
        return f"{prefix}{i}"

    def add_device(self, model_key: str, name: str | None = None, x: float = 0, y: float = 0,
                   device_id: str | None = None) -> Device:
        m = MODELS[model_key]
        dev = create_device(self.engine, model_key, name or self._unique_name(m.name_prefix),
                            device_id)
        dev.x, dev.y = x, y
        self.devices[dev.id] = dev
        self._seen_hostname[dev.id] = dev.hostname
        self.engine.emit("device_added", dev)
        if dev.category in ("wireless",) or getattr(getattr(dev, "nic", None), "is_wireless", False):
            self.rescan_wireless()
        return dev

    def remove_device(self, dev: Device) -> None:
        for link in [l for l in self.links if l.a.device is dev or l.b.device is dev]:
            self.disconnect(link)
        self.devices.pop(dev.id, None)
        self._seen_hostname.pop(dev.id, None)
        self._auto_name.pop(dev.id, None)
        self.engine.emit("device_removed", dev)

    # -- label follows hostname ------------------------------------------------
    def _free_name(self, base: str, dev: Device) -> str:
        taken = {d.name for d in self.devices.values() if d is not dev}
        name, i = base, 2
        while name in taken:
            name, i = f"{base}-{i}", i + 1
        return name

    def _sync_name(self, dev: Device) -> None:
        if dev.id not in self.devices or dev.category not in ("router", "switch"):
            return
        host = dev.hostname
        if self._seen_hostname.get(dev.id) == host:
            return
        self._seen_hostname[dev.id] = host
        if host == dev.default_hostname:
            # hostname removed: drop the auto label, unless the user renamed it by hand
            if self._auto_name.get(dev.id) == dev.name:
                del self._auto_name[dev.id]
                prefix = MODELS[dev.model_key].name_prefix
                self.rename(dev, self._unique_name(prefix))
            return
        if host != dev.name:
            new = self._free_name(host, dev)
            self._auto_name[dev.id] = new
            self.rename(dev, new)

    def sync_names(self) -> None:
        """Apply hostname -> label for devices configured without events (loading)."""
        for dev in list(self.devices.values()):
            self._sync_name(dev)

    def find(self, name: str) -> Device | None:
        for d in self.devices.values():
            if d.name == name:
                return d
        return None

    def rename(self, dev: Device, name: str) -> None:
        dev.name = name
        if dev.category in ("pc", "server", "phone"):
            dev.hostname = name
        self.engine.emit("device_changed", dev)

    # -- cables --------------------------------------------------------------
    def connect(self, a: Port, b: Port) -> Link:
        link = Link(a, b)
        self.links.append(link)
        for p in (a, b):
            p.device.link_state_changed(p)
        self.engine.emit("link_added", link)
        self.engine.emit("link_state", a)
        return link

    def connect_names(self, dev_a: str, port_a: str, dev_b: str, port_b: str) -> Link:
        a, b = self.find(dev_a), self.find(dev_b)
        if a is None or b is None:
            raise KeyError("device not found")
        pa, pb = a.get_port(port_a), b.get_port(port_b)
        if pa is None or pb is None:
            raise KeyError("port not found")
        return self.connect(pa, pb)

    def disconnect(self, link: Link) -> None:
        if link in self.links:
            self.links.remove(link)
            link.detach()
            self.engine.emit("link_removed", link)

    def clear(self) -> None:
        for dev in list(self.devices.values()):
            self.remove_device(dev)
        self.lab = None
        self.annotations = []
