"""Device catalog: every device model available in the palette.

A *model* binds a device class (router/switch/...) to a vendor, which in turn
selects the interface naming scheme, defaults and the CLI dialect.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .base import Device
from .cloud import Internet
from .wireless import AccessPoint
from .host import Host, LinuxHost, LinuxServer, Server
from .l2 import Hub, Switch
from .phone import IPPhone
from .router import Router, StaticRoute

VENDOR_NAMES = {
    "cisco": "Cisco",
    "mikrotik": "MikroTik",
    "juniper": "Juniper",
    "huawei": "Huawei",
    "extreme": "Extreme",
    "fortinet": "Fortinet",
    "linux": "Linux",
    "isp": "ISP",
    "generic": "Generic",
}


@dataclass(frozen=True)
class DeviceModel:
    key: str                 # stable id stored in project files, e.g. "cisco-2911"
    category: str            # router | switch | hub | pc | server
    vendor: str
    title: str               # human readable model name
    name_prefix: str         # display-name prefix on the canvas ("Router", "Switch", ...)
    hostname: str
    ports: tuple[tuple[str, str], ...] = ()   # (full name, short name)
    ports_admin_up: bool = True
    ttl: int | None = None
    factory: Callable | None = field(default=None, compare=False)
    group: str | None = None             # palette group when it differs from the category (security, iot ...)
    dialect: str | None = None           # CLI dialect when it differs from the vendor's usual one


def _seq(fmt_full: str, fmt_short: str, numbers) -> tuple[tuple[str, str], ...]:
    return tuple((fmt_full.format(n), fmt_short.format(n)) for n in numbers)


MODELS: dict[str, DeviceModel] = {}


def _register(m: DeviceModel) -> None:
    MODELS[m.key] = m


# -- routers -------------------------------------------------------------------
_register(DeviceModel("cisco-2911", "router", "cisco", "Cisco 2911", "Router", "Router",
                      _seq("GigabitEthernet0/{}", "Gi0/{}", range(3)),
                      ports_admin_up=False, ttl=255))
_register(DeviceModel("mikrotik-rb4011", "router", "mikrotik", "MikroTik RB4011", "MikroTik",
                      "MikroTik", _seq("ether{}", "ether{}", range(1, 11)), ttl=64))
_register(DeviceModel("juniper-srx300", "router", "juniper", "Juniper SRX300", "SRX", "srx300",
                      _seq("ge-0/0/{}", "ge-0/0/{}", range(8)), ttl=64))
_register(DeviceModel("huawei-ar2220", "router", "huawei", "Huawei AR2220", "AR", "Huawei",
                      _seq("GigabitEthernet0/0/{}", "GE0/0/{}", range(3)), ttl=255))
# -- switches ------------------------------------------------------------------
_register(DeviceModel("cisco-2960", "switch", "cisco", "Cisco 2960-24TT", "Switch", "Switch",
                      _seq("FastEthernet0/{}", "Fa0/{}", range(1, 25))
                      + _seq("GigabitEthernet0/{}", "Gi0/{}", range(1, 3))))
_register(DeviceModel("mikrotik-crs326", "switch", "mikrotik", "MikroTik CRS326-24G",
                      "CRS", "MikroTik", _seq("ether{}", "ether{}", range(1, 25))))
_register(DeviceModel("juniper-ex2300", "switch", "juniper", "Juniper EX2300-24T", "EX",
                      "ex2300", _seq("ge-0/0/{}", "ge-0/0/{}", range(24))))
_register(DeviceModel("huawei-s5720", "switch", "huawei", "Huawei S5720-28P", "LSW", "HUAWEI",
                      _seq("GigabitEthernet0/0/{}", "GE0/0/{}", range(1, 25))))
_register(DeviceModel("extreme-x440g2", "switch", "extreme", "Extreme X440-G2-24t", "X440", "X440G2-24t",
                      _seq("{}", "{}", range(1, 25)), ttl=64))
# -- other ---------------------------------------------------------------------
_register(DeviceModel("hub", "hub", "generic", "Hub", "Hub", "Hub"))
_register(DeviceModel("pc", "pc", "generic", "PC", "PC", "PC"))
_register(DeviceModel("server", "server", "generic", "Server", "Server", "Server"))
_register(DeviceModel("internet", "cloud", "isp", "Internet (ISP)", "Internet", "Internet"))
_register(DeviceModel("access-point", "wireless", "generic", "Access Point", "AP", "AP"))
_register(DeviceModel("lap-1142", "wireless", "cisco", "Lightweight AP 1142", "LAP", "LAP"))
for _k, _t, _n in (("temperature", "Temperature sensor", "Temp"), ("motion", "Motion sensor", "Motion"),
                   ("smoke", "Smoke detector", "Smoke"), ("lamp", "Smart lamp", "Lamp"), ("fan", "Smart fan", "Fan"),
                   ("door", "Smart door", "Door")):
    _register(DeviceModel(f"iot-{_k}", "iot", "generic", _t, _n, _n))
_register(DeviceModel("wlc-2504", "wlc", "cisco", "Cisco WLC 2504", "WLC", "WLC", group="wireless"))
_register(DeviceModel("laptop", "pc", "generic", "Laptop (Wi-Fi)", "Laptop", "Laptop"))
_register(DeviceModel("laptop-linux", "pc", "linux", "Laptop (Linux, Wi-Fi)", "LinuxLaptop", "laptop"))
_register(DeviceModel("pc-linux", "pc", "linux", "PC (Linux)", "Linux", "linux"))
_register(DeviceModel("server-linux", "server", "linux", "Server (Linux)", "LinuxSrv", "linuxsrv"))
_register(DeviceModel("ip-phone", "phone", "cisco", "IP Phone 7960", "Phone", "IPPhone"))
_register(DeviceModel("botnet", "attack", "attack", "Botnet (DDoS)", "Botnet", "botnet", group="attack"))
_register(DeviceModel("lan-attacker", "attack", "attack", "LAN attacker", "Attacker", "attacker", group="attack"))
_register(DeviceModel("fortigate-60f", "router", "fortinet", "FortiGate 60F", "FGT", "FortiGate-60F",
                      _seq("port{}", "port{}", range(1, 8)), ports_admin_up=True, ttl=255,
                      group="security", dialect="fortios"))
_register(DeviceModel("cisco-asa5506", "router", "cisco", "Cisco ASA 5506-X", "ASA", "ciscoasa",
                      _seq("GigabitEthernet1/{}", "Gi1/{}", range(1, 9)), ports_admin_up=False, ttl=255,
                      group="security", dialect="asa"))

_CLASSES = {"router": Router, "switch": Switch, "hub": Hub, "pc": Host, "server": Server, "phone": IPPhone}


def create_device(engine, model_key: str, name: str, device_id: str | None = None) -> Device:
    m = MODELS[model_key]
    if m.category == "iot":
        from .iot import IotDevice
        dev = IotDevice(engine, name, device_id, dtype=m.key.split("-", 1)[1])
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        return dev
    if m.category == "attack":
        if m.key == "lan-attacker":
            from .attacker import L2Attacker
            dev = L2Attacker(engine, name, device_id)
        else:
            from .botnet import Botnet
            dev = Botnet(engine, name, device_id)
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        return dev
    if m.category == "wlc":
        from .wlc import WirelessController
        dev = WirelessController(engine, name, device_id)
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        return dev
    if m.key == "lap-1142":
        from .wlc import LightweightAP
        dev = LightweightAP(engine, name, device_id)
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        dev.add_port("Ethernet0", "Eth0")
        return dev
    if m.category == "wireless":
        dev = AccessPoint(engine, name, device_id)
        dev.model_key = m.key
        dev.add_port("Ethernet0", "Eth0")
        return dev
    if m.key in ("laptop", "laptop-linux"):
        cls = LinuxHost if m.vendor == "linux" else Host
        nic = ("wlan0", "wlan0") if m.vendor == "linux" else ("Wireless0", "Wl0")
        dev = type("Laptop", (cls,), {"NIC": nic, "model": m.title})(engine, name, device_id)
        dev.nic.is_wireless = True
        dev.wifi = {"ssid": None, "key": None}
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        if m.vendor == "linux":
            dev.default_ttl = 64
        from ..services.remote import default_access, install
        dev.access = default_access(dev)
        install(dev)
        return dev
    if m.category == "cloud":
        dev = Internet(engine, name, device_id)
        dev.model_key = m.key
        from ..services.remote import default_access, install
        dev.access = default_access(dev)
        install(dev)
        return dev
    if m.category == "phone":
        dev = IPPhone(engine, name, device_id)
        dev.model_key, dev.vendor, dev.model = m.key, m.vendor, m.title
        return dev
    cls = _CLASSES[m.category]
    if m.vendor == "linux":
        cls = LinuxServer if m.category == "server" else LinuxHost
    dev = cls(engine, name, device_id)
    dev.model_key = m.key
    dev.vendor = m.vendor
    dev.model = m.title
    dev.dialect = m.dialect
    if m.category in ("router", "switch"):
        dev.hostname = m.hostname
        dev.default_hostname = m.hostname
        for full, short in m.ports:
            dev.add_port(full, short, admin_up=m.ports_admin_up)
    if m.ttl is not None:
        dev.default_ttl = m.ttl
    if m.category == "switch":
        from .stp import default_mode
        dev.stp.mode = default_mode(m.vendor)
    if m.category == "switch" and m.vendor == "extreme":
        dev.vlans = {1: "Default"}
        dev.stp.enabled = False
    if m.category == "switch" and m.vendor == "mikrotik":
        dev.vlan_filtering = False   # RouterOS bridge default
    if m.category == "router" and m.vendor == "juniper":
        dev.add_loopback("lo0")      # built into every Junos router
        dev.add_tunnel("gr-0/0/0", "gr-0/0/0")
        dev.add_tunnel("st0", "st0")             # route-based IPsec VPN (security ipsec vpn ... bind-interface)
    from ..services.remote import default_access, install
    dev.access = default_access(dev)
    install(dev)                     # telnet/ssh listeners (they answer RST until the service is enabled)
    return dev


def models_by_category() -> dict[str, list[DeviceModel]]:
    out: dict[str, list[DeviceModel]] = {}
    for m in MODELS.values():
        out.setdefault(m.category, []).append(m)
    return out


__all__ = ["Device", "Host", "IPPhone", "Server", "LinuxHost", "LinuxServer", "Hub", "Switch", "Router", "StaticRoute",
           "DeviceModel", "MODELS", "VENDOR_NAMES", "create_device", "models_by_category"]
