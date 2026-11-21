"""Wi-Fi: access points and wireless clients.

An access point bridges its Ethernet port and the radio clients associated with it.  A
client (laptop) associates automatically when it is configured for the AP's SSID, knows the
WPA2 passphrase (unless the network is open) and is within range on the canvas.  Each
association is a wireless link (drawn dashed) between the client's NIC and a radio port the
AP creates for it, so frames, ARP and everything above work exactly as over a cable."""
from __future__ import annotations

import math

from .l2 import Switch

DEFAULT_RANGE = 250.0


class AccessPoint(Switch):
    category = "wireless"
    model = "Access Point"
    default_hostname = "AP"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.hostname = name
        self.wifi = {"ssid": "NetEmu", "security": "wpa2", "key": "netemu123", "channel": 6, "range": DEFAULT_RANGE}
        self.stp.enabled = False

    def radio_ports(self):
        return [p for p in self.ports.values() if getattr(p, "is_wireless", False)]

    def new_radio_port(self):
        n = 0
        while f"Radio0.{n}" in self.ports:
            n += 1
        p = self.add_port(f"Radio0.{n}", f"Radio0.{n}")
        p.is_wireless = True
        return p

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["interfaces"] = {k: v for k, v in cfg["interfaces"].items() if not k.startswith("Radio0.")}
        cfg["wifi"] = dict(self.wifi)
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.wifi.update(cfg.get("wifi", {}))
        self.stp.enabled = False


def signal(ap, client) -> int:
    """0..100 % from the canvas distance; 0 means out of range."""
    d = math.hypot(ap.x - client.x, ap.y - client.y)
    rng = float(ap.wifi.get("range", DEFAULT_RANGE))
    if d > rng:
        return 0
    return max(1, int(100 - 70 * d / rng))


def visible_networks(client, devices) -> list[tuple]:
    """[(ssid, security, channel, signal %, ap)] the client can hear, strongest first."""
    out = []
    for ap in devices:
        if isinstance(ap, AccessPoint) and ap.powered and ap.wifi.get("ssid"):
            s = signal(ap, client)
            if s > 0:
                out.append((ap.wifi["ssid"], ap.wifi["security"], ap.wifi["channel"], s, ap))
    return sorted(out, key=lambda x: -x[3])


def is_wireless_client(dev) -> bool:
    return getattr(getattr(dev, "nic", None), "is_wireless", False)


def wanted_ap(client, devices):
    """The AP this client should be associated with, or None."""
    want = getattr(client, "wifi", None) or {}
    ssid = want.get("ssid")
    if not ssid:
        return None
    for name, sec, ch, s, ap in visible_networks(client, devices):
        if name == ssid and (sec == "open" or want.get("key") == ap.wifi.get("key")):
            return ap
    return None


def rescan(topology) -> None:
    """(Re)associate every wireless client with the right access point."""
    from ..port import Link
    devices = list(topology.devices.values())
    for client in devices:
        if not is_wireless_client(client):
            continue
        nic = client.nic
        target = wanted_ap(client, devices)
        cur = nic.link
        if cur is not None:
            peer = cur.other(nic)
            if peer.device is target:
                continue
            topology.disconnect(cur)
            if getattr(peer, "is_wireless", False) and peer.device.ports.get(peer.name) is peer:
                del peer.device.ports[peer.name]
        if target is not None:
            radio = target.new_radio_port()
            link = Link(nic, radio)
            link.wireless = True
            topology.links.append(link)
            for p in (nic, radio):
                p.device.link_state_changed(p)
            topology.engine.emit("link_added", link)
            client.engine.emit("device_changed", client)
