"""Base class for every simulated device."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..port import LoopbackPort, Port, PortChannel, SubInterface, VlanInterface, split_name

if TYPE_CHECKING:
    from ..engine import SimEngine
    from ..packets import EthernetFrame


class Device:
    #: device category: "router", "switch", "hub", "pc", "server"
    category = "device"
    #: vendor key: "cisco", "mikrotik", "juniper", "huawei", "generic"
    vendor = "generic"
    model = "Generic"
    default_hostname = "Device"

    def __init__(self, engine: "SimEngine", name: str, device_id: str | None = None):
        self.engine = engine
        self.id = device_id or uuid.uuid4().hex[:12]
        self.name = name          # display name on the canvas
        self.hostname = self.default_hostname
        self.ports: dict[str, Port] = {}
        self.powered = True
        self.x = 0.0
        self.y = 0.0
        self.startup_config: dict | None = None

    # -- ports -------------------------------------------------------------
    def add_port(self, name: str, short: str | None = None, admin_up: bool = True) -> Port:
        p = Port(self, name, short, admin_up)
        self.ports[name] = p
        return p

    def get_port(self, name: str) -> Port | None:
        """Look up a port by full name or an unambiguous abbreviation (g0/0, Gi0/0, fa0/1)."""
        if not name:
            return None
        key = name.strip().lower().replace(" ", "")
        for p in self.ports.values():
            if p.name.lower() == key or p.short.lower() == key:
                return p
        typ, num = split_name(name.replace(" ", ""))
        if not num:
            return None
        typ = typ.lower()
        matches = []
        for p in self.ports.values():
            ptyp, pnum = split_name(p.name)
            if pnum == num and ptyp.lower().startswith(typ):
                matches.append(p)
        return matches[0] if len(matches) == 1 else None

    def free_ports(self) -> list[Port]:
        return [p for p in self.physical_ports() if p.link is None and not p.is_wireless]

    def physical_ports(self) -> list[Port]:
        return [p for p in self.ports.values()
                if not p.is_sub and not p.is_loopback and not p.is_svi and not p.is_channel and not p.is_tunnel]

    # -- tunnels -------------------------------------------------------------------------
    def add_tunnel(self, name: str, short: str | None = None) -> Port:
        from ..tunnel import TunnelPort
        existing = self.ports.get(name)
        if existing is not None:
            return existing
        t = TunnelPort(self, name, short or name)
        self.ports[name] = t
        return t

    def remove_tunnel(self, t: Port) -> None:
        if t.is_tunnel and self.ports.get(t.name) is t:
            del self.ports[t.name]
            if hasattr(self, "arp"):
                self.arp.flush_port(t)

    # -- link aggregation -----------------------------------------------------------------
    def add_channel(self, group: int, name: str, short: str | None = None) -> Port:
        existing = self.channel_for_group(group)
        if existing is not None:
            return existing
        po = PortChannel(self, name, short or name, group)
        self.ports[name] = po
        return po

    def channels(self) -> list[Port]:
        return [p for p in self.ports.values() if p.is_channel]

    def channel_for_group(self, group) -> Port | None:
        return next((p for p in self.ports.values() if p.is_channel and p.group == group), None)

    def remove_channel(self, po: Port) -> None:
        if po.is_channel and self.ports.get(po.name) is po:
            for m in po.members:
                m.channel_group = m.channel_mode = None
            del self.ports[po.name]
            self.link_state_changed(po)

    def set_channel_member(self, port: Port, group: int | None, mode: str | None = None) -> None:
        port.channel_group = group
        port.channel_mode = mode if group is not None else None
        self.link_state_changed(port)
        if port.peer is not None:
            port.peer.device.link_state_changed(port.peer)
        self.engine.emit("link_state", port)

    # -- switch virtual interfaces (L3 switches) ----------------------------------------
    def add_svi(self, vlan: int, name: str | None = None, short: str | None = None) -> Port:
        name = name or f"Vlan{vlan}"
        existing = self.ports.get(name)
        if existing is not None:
            return existing
        svi = VlanInterface(self, name, short or name, vlan)
        self.ports[name] = svi
        self.link_state_changed(svi)
        return svi

    def svis(self) -> list[Port]:
        return [p for p in self.ports.values() if p.is_svi]

    def svi_for_vlan(self, vlan) -> Port | None:
        return next((p for p in self.ports.values() if p.is_svi and p.vlan == vlan), None)

    def remove_svi(self, port: Port) -> None:
        if port.is_svi and self.ports.get(port.name) is port:
            del self.ports[port.name]
            if hasattr(self, "arp"):
                self.arp.flush_port(port)

    def add_loopback(self, name: str, short: str | None = None) -> Port:
        existing = self.ports.get(name)
        if existing is not None:
            return existing
        lo = LoopbackPort(self, name, short or name)
        self.ports[name] = lo
        return lo

    def remove_loopback_named(self, name: str) -> None:
        p = self.ports.get(name)
        if p is not None and p.is_loopback:
            self.remove_loopback(p)

    def remove_loopback(self, port: Port) -> None:
        if port.is_loopback and self.ports.get(port.name) is port:
            del self.ports[port.name]

    # -- subinterfaces (802.1Q) ------------------------------------------------
    def add_subinterface(self, parent: Port, number: int, name: str | None = None,
                         vlan: int | None = None) -> SubInterface:
        name = name or f"{parent.name}.{number}"
        existing = self.ports.get(name)
        if existing is not None:
            return existing  # type: ignore[return-value]
        short = f"{parent.short}.{number}" if name == f"{parent.name}.{number}" else name
        sub = SubInterface(parent, name, short, vlan)
        # keep subinterfaces right after their parent in listings
        items = list(self.ports.items())
        idx = max(i for i, (_, p) in enumerate(items)
                  if p is parent or (p.is_sub and p.parent is parent))
        items.insert(idx + 1, (name, sub))
        self.ports = dict(items)
        return sub

    def remove_subinterface(self, sub: Port) -> None:
        if sub.is_sub and self.ports.get(sub.name) is sub:
            del self.ports[sub.name]
            if hasattr(self, "arp"):
                self.arp.flush_port(sub)

    def link_state_changed(self, port: Port) -> None:
        """Hook: called when a port's line state may have changed."""

    # -- traffic -----------------------------------------------------------
    def receive(self, frame: "EthernetFrame", port: Port) -> None:  # pragma: no cover
        raise NotImplementedError

    def log(self, message: str) -> None:
        self.engine.emit("log", self, message)

    # -- configuration -----------------------------------------------------
    location: str = ""                       # "Building / Floor / Rack" (physical view)

    def to_config(self) -> dict[str, Any]:
        """Vendor-neutral configuration state (used for save/load)."""
        cfg = {
            "hostname": self.hostname,
            "interfaces": {p.name: self._port_config(p) for p in self.ports.values()},
        }
        if hasattr(self, "access"):
            from ..services.remote import access_to_config
            acc = access_to_config(self)
            if acc is not None:
                cfg["access"] = acc
        return cfg

    def _port_config(self, p: Port) -> dict[str, Any]:
        d: dict[str, Any] = {"admin_up": p.admin_up, "ip": str(p.ip) if p.ip else None,
                             "description": p.description}
        if p.is_sub:
            d.update(parent=p.parent.name, vlan=p.vlan, native=p.native, arp_broadcast=p.arp_broadcast)
        if p.is_loopback:
            d.update(loopback=True, short=p.short)
        if p.is_svi:
            d.update(svi=True, vlan=p.vlan, short=p.short)
        if p.is_channel:
            d.update(channel=True, group=p.group, short=p.short)
        if p.is_tunnel:
            d.update(tunnel=True, short=p.short, source=p.source,
                     destination=str(p.destination) if p.destination else None, ipsec_key=p.ipsec_key)
        if p.channel_group is not None:
            d.update(channel_group=p.channel_group, channel_mode=p.channel_mode)
        if p.psec is not None:
            ps = dict(p.psec)
            ps["macs"] = [m for m in ps.get("macs", []) if ps.get("sticky") or m not in ps.get("dynamic", [])]
            ps.pop("dynamic", None)
            ps.pop("violations", None)
            ps.pop("last", None)
            d["port_security"] = ps
        if p.dhcp_trust:
            d["dhcp_trust"] = True
        if p.voice_vlan is not None:
            d["voice_vlan"] = p.voice_vlan
        if p.vrf:
            d["vrf"] = p.vrf
        if p.dot1x:
            d["dot1x"] = True
        if p.eigrp_bw:
            d["bandwidth"] = p.eigrp_bw
        if p.eigrp_delay:
            d["delay"] = p.eigrp_delay
        if p.qos_in:
            d["qos_in"] = p.qos_in
        if p.qos_out:
            d["qos_out"] = p.qos_out
        if p.acl_in:
            d["acl_in"] = p.acl_in
        if p.acl_out:
            d["acl_out"] = p.acl_out
        if p.helpers:
            d["helpers"] = list(p.helpers)
        if p.ipv6 or p.ipv6_enabled or p.ipv6_auto or p.ra is not None:
            d["ipv6"] = [str(a) for a in p.ipv6 if not getattr(p, "_slaac", set()) or a not in p._slaac]
            d["ipv6_enabled"] = p.ipv6_enabled
            if p.ipv6_auto:
                d["ipv6_auto"] = True
            if p.ra is not None:
                d["ra"] = p.ra
        if self.category == "switch" and (p.mode != "access" or p.pvid != 1 or p.tagged is not None
                                          or p.access_vlan != 1 or p.native_vlan != 1):
            d.update(mode=p.mode, pvid=p.pvid, access_vlan=p.access_vlan, native_vlan=p.native_vlan,
                     tagged=None if p.tagged is None else sorted(p.tagged))
        return d

    def apply_config(self, cfg: dict[str, Any]) -> None:
        from ..addresses import IPv4Interface
        self.hostname = cfg.get("hostname", self.hostname)
        if hasattr(self, "access"):
            from ..services.remote import access_from_config
            access_from_config(self, cfg.get("access"))
        ifaces = cfg.get("interfaces", {})
        for sub in [p for p in self.ports.values() if p.is_sub and p.name not in ifaces]:
            self.remove_subinterface(sub)
        for lo in [p for p in self.ports.values() if p.is_loopback and p.name not in ifaces]:
            self.remove_loopback(lo)
        for sv in [p for p in self.ports.values() if p.is_svi and p.name not in ifaces]:
            self.remove_svi(sv)
        for po in [p for p in self.ports.values() if p.is_channel and p.name not in ifaces]:
            self.remove_channel(po)
        for tu in [p for p in self.ports.values() if p.is_tunnel and p.name not in ifaces]:
            self.remove_tunnel(tu)
        for name, icfg in ifaces.items():
            p = self.ports.get(name)
            if p is None and icfg.get("loopback"):
                p = self.add_loopback(name, icfg.get("short"))
            if p is None and icfg.get("tunnel"):
                p = self.add_tunnel(name, icfg.get("short"))
            if p is not None and p.is_tunnel:
                from ipaddress import IPv4Address as _A
                p.source = icfg.get("source")
                p.destination = _A(icfg["destination"]) if icfg.get("destination") else None
                p.ipsec_key = icfg.get("ipsec_key")
            if p is None and icfg.get("channel"):
                p = self.add_channel(int(icfg["group"]), name, icfg.get("short"))
            if p is None and icfg.get("svi"):
                p = self.add_svi(int(icfg["vlan"]), name, icfg.get("short"))
            if p is not None and p.is_svi:
                p.vlan = int(icfg.get("vlan", p.vlan))
            if p is None and icfg.get("parent") in self.ports:
                num = name.rsplit(".", 1)[-1]
                p = self.add_subinterface(self.ports[icfg["parent"]],
                                          int(num) if num.isdigit() else 0, name)
            if p is None:
                continue
            p.ip = IPv4Interface(icfg["ip"]) if icfg.get("ip") else None
            p.description = icfg.get("description", "")
            if p.is_sub:
                p.vlan = icfg.get("vlan")
                p.native = bool(icfg.get("native", False))
                p.arp_broadcast = bool(icfg.get("arp_broadcast", True))
            p.acl_in = icfg.get("acl_in")
            p.acl_out = icfg.get("acl_out")
            p.helpers = list(icfg.get("helpers", []))
            from ipaddress import IPv6Interface
            p.ipv6 = [IPv6Interface(a) for a in icfg.get("ipv6", [])]
            p.ipv6_enabled = bool(icfg.get("ipv6_enabled", p.ipv6_enabled if self.category in ("pc", "server") else False))
            p.ipv6_auto = bool(icfg.get("ipv6_auto", False))
            p.ra = icfg.get("ra")
            p.mode = icfg.get("mode", "access")
            p.pvid = int(icfg.get("pvid", 1))
            p.access_vlan = int(icfg.get("access_vlan", p.pvid if p.mode == "access" else 1))
            p.native_vlan = int(icfg.get("native_vlan", p.pvid if p.mode == "trunk" else 1))
            if not p.is_channel and not p.is_sub and not p.is_svi:
                p.channel_group = icfg.get("channel_group")
                p.channel_mode = icfg.get("channel_mode")
            p.psec = dict(icfg["port_security"]) if icfg.get("port_security") is not None else None
            p.dhcp_trust = bool(icfg.get("dhcp_trust"))
            p.qos_in, p.qos_out = icfg.get("qos_in"), icfg.get("qos_out")
            p.voice_vlan = icfg.get("voice_vlan")
            p.eigrp_bw, p.eigrp_delay = icfg.get("bandwidth"), icfg.get("delay")
            p.vrf = icfg.get("vrf")
            p.dot1x = bool(icfg.get("dot1x"))
            p.err_disabled = False
            tagged = icfg.get("tagged")
            p.tagged = None if tagged is None else frozenset(int(v) for v in tagged)
            p.set_admin(icfg.get("admin_up", p.admin_up))

    def factory_reset(self) -> None:
        """Erase the running configuration (back to factory defaults)."""
        from . import MODELS
        m = MODELS.get(getattr(self, "model_key", ""))
        if self.category not in ("pc", "server"):
            self.hostname = self.default_hostname
        for sub in [p for p in self.ports.values() if p.is_sub]:
            self.remove_subinterface(sub)
        for lo in [p for p in self.ports.values() if p.is_loopback]:
            self.remove_loopback(lo)
        for sv in [p for p in self.ports.values() if p.is_svi]:
            self.remove_svi(sv)
        for po in [p for p in self.ports.values() if p.is_channel]:
            self.remove_channel(po)
        for tu in [p for p in self.ports.values() if p.is_tunnel]:
            self.remove_tunnel(tu)
        if hasattr(self, "reset_protocols"):
            self.reset_protocols()
        if hasattr(self, "access"):
            from ..services.remote import default_access
            self.access = default_access(self)
        for p in self.ports.values():
            p.ip = None
            p.description = ""
            p.mode, p.pvid, p.tagged = "access", 1, None
            p.access_vlan = p.native_vlan = 1
            p.acl_in = p.acl_out = None
            p.helpers = []
            p.psec, p.dhcp_trust, p.err_disabled = None, False, False
            p.ipv6, p.ipv6_auto, p.ra = [], False, None
            p.ipv6_enabled = self.category in ("pc", "server")
            p.set_admin(m.ports_admin_up if m else True)
        if hasattr(self, "stp"):
            self.stp.apply_config({})
        if hasattr(self, "vlans"):
            self.vlans = {1: "Default" if self.vendor == "extreme" else "default"}
            self.bridge_vlans = []
            self.vlan_filtering = self.vendor != "mikrotik"
        if hasattr(self, "static_routes"):
            self.static_routes = []
        if hasattr(self, "arp"):
            self.arp.clear()
        if hasattr(self, "mac_table"):
            self.mac_table.clear()
        if hasattr(self, "gateway"):
            self.gateway = None

    def save_startup(self) -> None:
        self.startup_config = self.to_config()

    def create_cli(self):
        """Return a new CLI session object for this device's vendor dialect."""
        from ..cli import create_session
        return create_session(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
