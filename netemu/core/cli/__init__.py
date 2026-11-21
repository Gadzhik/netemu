"""Vendor CLI dialects."""
from __future__ import annotations


def create_session(device):
    """Create the CLI session matching the device's vendor and category."""
    if device.category in ("pc", "server") and getattr(device, "os", "windows") == "linux":
        from .linux import LinuxSession
        return LinuxSession(device)
    if device.category in ("pc", "server"):
        from .host import HostSession
        return HostSession(device)
    if device.category in ("hub", "phone", "wlc", "iot"):
        return None
    vendor = getattr(device, "vendor", "cisco")
    if getattr(device, "dialect", None) == "fortios":
        from .fortigate import FortiSession
        return FortiSession(device)
    if getattr(device, "dialect", None) == "asa":
        from .asa import AsaSession
        return AsaSession(device)
    if vendor == "mikrotik":
        from .mikrotik import MikrotikSession
        return MikrotikSession(device)
    if vendor == "juniper":
        from .juniper import JunosSession
        return JunosSession(device)
    if vendor == "extreme":
        from .extreme import ExosSession
        return ExosSession(device)
    if vendor == "huawei":
        from .huawei import HuaweiSession
        return HuaweiSession(device)
    from .cisco import CiscoSession
    return CiscoSession(device)
