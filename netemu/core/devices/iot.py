"""Internet of Things: sensors and actuators, and the IoT service of the Server device.

* IoT devices are small Wi-Fi hosts (DHCP on by default).  Sensors (temperature, motion, smoke)
  report their value every 5 s and on every change; actuators (lamp, fan, door) report their
  state and obey commands.  Messages are CoAP-like datagrams to UDP 5683.
* The server's IoT service keeps the registry of devices with their last value and evaluates
  rules written one per line:  ``IF Temp1 > 30 THEN Fan1 on``  (operators > < = !=; values are
  numbers or on/off/open/closed/yes/no).  When a rule's condition becomes true the command is
  sent to the actuator's address.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from ipaddress import IPv4Address

from .host import Host

COAP_PORT = 5683
REPORT_MS = 5000.0
SENSORS = {"temperature": 22.0, "motion": "no", "smoke": "no"}
ACTUATORS = {"lamp": "off", "fan": "off", "door": "closed"}
RULE_RE = re.compile(r"^\s*IF\s+(\S+)\s*(>=|<=|!=|>|<|=)\s*(\S+)\s+THEN\s+(\S+)\s+(\S+)\s*$", re.I)


@dataclass(frozen=True)
class IotMsg:
    kind: str                    # report | command
    name: str
    dtype: str = ""
    value: object = None
    label = "CoAP"

    def summary(self) -> str:
        return f"CoAP {'POST /report' if self.kind == 'report' else 'PUT /state'} {self.name}={self.value}"

    def layer_lines(self) -> list[str]:
        return [f"Method: {'POST' if self.kind == 'report' else 'PUT'}", f"Device: {self.name} ({self.dtype})",
                f"Value: {self.value}"]


class IotDevice(Host):
    category = "iot"
    model = "IoT device"
    NIC = ("Wireless0", "Wl0")
    default_ttl = 64

    def __init__(self, engine, name, device_id=None, dtype: str = "temperature"):
        super().__init__(engine, name, device_id)
        self.dtype = dtype
        self.value = SENSORS.get(dtype, ACTUATORS.get(dtype))
        self.server: str | None = None
        self.nic.is_wireless = True
        self.wifi = {"ssid": None, "key": None}
        self.dhcp_enabled = True
        self.bind_udp(COAP_PORT, self._receive)
        self.engine.schedule(1000.0, self._tick)

    @property
    def is_sensor(self) -> bool:
        return self.dtype in SENSORS

    def set_value(self, value) -> None:
        self.value = value
        self.report()
        self.engine.emit("device_changed", self)

    def report(self) -> None:
        if self.server and self.nic.ip is not None:
            self.send_udp(IPv4Address(self.server), COAP_PORT, COAP_PORT, IotMsg("report", self.name, self.dtype, self.value))

    def _tick(self) -> None:
        self.report()
        self.engine.schedule(REPORT_MS, self._tick)

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if isinstance(m, IotMsg) and m.kind == "command" and not self.is_sensor:
            self.value = m.value
            self.report()
            self.engine.emit("device_changed", self)

    def link_state_changed(self, port) -> None:
        super().link_state_changed(port)
        if port.is_up and self.dhcp_enabled and self.nic.ip is None:
            self.engine.schedule(200.0, self.start_dhcp)

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["iot"] = {"type": self.dtype, "value": self.value, "server": self.server}
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        i = cfg.get("iot") or {}
        self.dtype = i.get("type", self.dtype)
        self.value = i.get("value", self.value)
        self.server = i.get("server")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v).lower()


def _true(left, op, right) -> bool:
    a, b = _num(left), _num(right)
    if isinstance(a, float) != isinstance(b, float):
        a, b = str(left).lower(), str(right).lower()
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b, "=": a == b, "!=": a != b}[op]


class IotService:
    def __init__(self):
        self.enabled = False
        self.devices: dict[str, dict] = {}            # name -> {"type", "value", "ip", "last"}
        self.rules: list[str] = []
        self._fired: dict[int, bool] = {}

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "rules": list(self.rules)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "IotService":
        s = cls()
        d = d or {}
        s.enabled, s.rules = bool(d.get("enabled")), list(d.get("rules", []))
        return s

    @staticmethod
    def check_rules(text: str) -> list[str]:
        """Error messages for rule lines that cannot be parsed."""
        return [f"{n}: {line.strip()}" for n, line in enumerate(text.splitlines(), 1)
                if line.strip() and not RULE_RE.match(line)]

    def handle(self, dev, pkt) -> None:
        m = pkt.payload.data
        if not self.enabled or not isinstance(m, IotMsg) or m.kind != "report":
            return
        self.devices[m.name] = {"type": m.dtype, "value": m.value, "ip": str(pkt.src), "last": dev.engine.now}
        for i, rule in enumerate(self.rules):
            r = RULE_RE.match(rule)
            if not r:
                continue
            src, op, val, target, state = r.groups()
            if src not in self.devices:
                continue
            now_true = _true(self.devices[src]["value"], op, val)
            if now_true and not self._fired.get(i):
                t = self.devices.get(target)
                if t is not None:
                    dev.send_udp(IPv4Address(t["ip"]), COAP_PORT, COAP_PORT, IotMsg("command", target, t["type"], state))
            self._fired[i] = now_true
        dev.engine.emit("device_changed", dev)
