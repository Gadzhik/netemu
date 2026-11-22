"""Wireless LAN controller and lightweight access points (CAPWAP, Layer-2 discovery).

* A lightweight AP has no Wi-Fi settings of its own: its radio stays off until it joins a
  controller.  While unjoined it broadcasts a CAPWAP discovery every 3 s on its Ethernet port.
* The controller (Cisco WLC 2504 here) answers with a join response carrying its WLANs; the AP
  turns its radio on with the first enabled WLAN.  Editing the WLANs on the controller pushes the
  new configuration to every joined AP, so one change reconfigures the whole wireless network.
* Joined APs send a heartbeat every 10 s; an AP that hears nothing for 30 s drops off the
  controller and switches its radio off (clients lose the network)."""
from __future__ import annotations

from dataclasses import dataclass

from ..addresses import MacAddress
from ..packets import EthernetFrame
from .host import Host
from .wireless import AccessPoint

ETH_CAPWAP = "CAPWAP"
BROADCAST = MacAddress(0xFFFFFFFFFFFF)
DISCOVERY_MS, ECHO_MS, DEAD_MS = 3000.0, 10000.0, 30000.0


@dataclass(frozen=True)
class CapwapMsg:
    kind: str                    # discovery | join | echo | config
    name: str = ""
    wlans: tuple = ()            # ((ssid, security, key), ...)
    label = "CAPWAP"

    def summary(self) -> str:
        extra = f" ({len(self.wlans)} WLAN)" if self.wlans else ""
        return f"CAPWAP {self.kind.capitalize()} {self.name}{extra}".rstrip()

    def layer_lines(self) -> list[str]:
        return [f"Message: {self.kind}", f"Name: {self.name}"] + [f"WLAN: {s} ({sec})" for s, sec, _ in self.wlans]


class LightweightAP(AccessPoint):
    model = "Lightweight AP"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.wifi.update(ssid=None)
        self.controller = None            # (MAC, name) of the joined WLC
        self._last_heard = 0.0
        self.engine.schedule(500.0, self._tick)

    def receive(self, frame, port) -> None:
        if frame.ethertype == ETH_CAPWAP:
            m = frame.payload
            if isinstance(m, CapwapMsg) and m.kind == "echo" and (self.controller is None or
                                                                frame.src != self.controller[0]):
                return                                     # another AP's keepalive, not our controller
            if isinstance(m, CapwapMsg) and m.kind in ("join", "config", "echo"):
                if m.kind in ("join", "config"):
                    self._apply(m.wlans)
                if self.controller is None or self.controller[0] != frame.src:
                    self.mgmt.log(5, f"%CAPWAP-5-JOINEDCONTROLLER: AP has joined controller {m.name}")
                self.controller = (frame.src, m.name)
                self._last_heard = self.engine.now
            return
        super().receive(frame, port)

    def _apply(self, wlans) -> None:
        if wlans:
            ssid, sec, key = wlans[0]
            self.wifi.update(ssid=ssid, security=sec, key=key, extra=[list(x) for x in wlans[1:]])
        else:
            self.wifi.update(ssid=None, extra=[])
        topo = getattr(self.engine, "topology", None)
        if topo is not None:
            from .wireless import rescan
            rescan(topo)
        self.engine.emit("device_changed", self)

    def _uplink(self):
        return next((p for p in self.ports.values() if not getattr(p, "is_wireless", False)), None)

    def _tick(self) -> None:
        up = self._uplink()
        now = self.engine.now
        if self.controller is not None and now - self._last_heard > DEAD_MS:
            self.controller = None
            self._apply(())
        if up is not None and up.is_up and self.powered:
            kind = "discovery" if self.controller is None else "echo"
            dst = BROADCAST if self.controller is None else self.controller[0]
            up.send(EthernetFrame(up.mac, dst, ETH_CAPWAP, CapwapMsg(kind, self.hostname)))
        self.engine.schedule(DISCOVERY_MS if self.controller is None else ECHO_MS, self._tick)

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["wifi"] = {k: v for k, v in cfg["wifi"].items() if k in ("channel", "range")}
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.wifi.update(ssid=None)
        self.controller = None


class WirelessController(Host):
    category = "wlc"
    model = "Cisco WLC 2504"
    default_hostname = "WLC"
    NIC = ("GigabitEthernet0/0", "Gi0/0")

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.wlans: list[dict] = [{"ssid": "Corp", "security": "wpa2", "key": "corp12345", "enabled": True}]
        self.aps: dict[str, dict] = {}        # AP name -> {"mac", "last"}
        self.engine.schedule(1000.0, self._expire)

    def _wlan_tuple(self):
        return tuple((w["ssid"], w.get("security", "wpa2"), w.get("key")) for w in self.wlans if w.get("enabled", True))

    def receive(self, frame, port) -> None:
        if frame.ethertype == ETH_CAPWAP:
            m = frame.payload
            if isinstance(m, CapwapMsg) and m.kind in ("discovery", "echo"):
                new = m.name not in self.aps
                self.aps[m.name] = {"mac": frame.src, "last": self.engine.now}
                kind = "join" if (m.kind == "discovery" or new) else "echo"
                self.nic.send(EthernetFrame(self.nic.mac, frame.src, ETH_CAPWAP,
                                            CapwapMsg(kind, self.hostname, self._wlan_tuple() if kind == "join" else ())))
                if new:
                    self.engine.emit("device_changed", self)
            return
        super().receive(frame, port)

    def push(self) -> None:
        """WLAN changes: reconfigure every joined AP."""
        for ap in self.aps.values():
            self.nic.send(EthernetFrame(self.nic.mac, ap["mac"], ETH_CAPWAP,
                                        CapwapMsg("config", self.hostname, self._wlan_tuple())))
        self.engine.emit("device_changed", self)

    def _expire(self) -> None:
        now = self.engine.now
        gone = [n for n, a in self.aps.items() if now - a["last"] > DEAD_MS]
        for n in gone:
            del self.aps[n]
        if gone:
            self.engine.emit("device_changed", self)
        self.engine.schedule(5000.0, self._expire)

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["wlans"] = [dict(w) for w in self.wlans]
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.wlans = [dict(w) for w in cfg.get("wlans", self.wlans)]
