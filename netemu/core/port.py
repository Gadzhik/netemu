"""Network interfaces (ports) and cables (links)."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from .addresses import IPv4Interface, MacAddress, new_mac
from .engine import KIND_FRAME
from .packets import EthernetFrame

if TYPE_CHECKING:
    from .devices.base import Device

LINK_DELAY_MS = 1.0

_NAME_RE = re.compile(r"^([A-Za-z][A-Za-z-]*?)-?\s*(\d[\d/.:]*)$")


def split_name(name: str) -> tuple[str, str]:
    """'GigabitEthernet0/0' -> ('GigabitEthernet', '0/0'); 'ge-0/0/0' -> ('ge', '0/0/0')."""
    m = _NAME_RE.match(name.strip())
    if not m:
        return name.strip(), ""
    return m.group(1), m.group(2)


class Port:
    def __init__(self, device: "Device", name: str, short: str | None = None,
                 admin_up: bool = True, mac: MacAddress | None = None):
        self.device = device
        self.name = name
        self.short = short or name
        self.mac = mac or new_mac()
        self.link: Link | None = None
        self.admin_up = admin_up
        self.ip: IPv4Interface | None = None
        self.description = ""
        self.tx_frames = 0
        self.rx_frames = 0
        self.drops = 0
        # -- 802.1Q switchport settings (used by switches) --------------------
        self.mode = "access"                       # "access" | "trunk"
        self.pvid = 1                              # active untagged VLAN (access or native)
        self.access_vlan = 1                       # remembered per mode, as IOS does
        self.native_vlan = 1
        self.tagged: frozenset[int] | None = None  # VLANs allowed tagged on a trunk; None = all
        # -- packet filters bound to this interface (routers) -------------------
        self.acl_in: str | None = None
        self.acl_out: str | None = None
        self.helpers: list[str] = []               # DHCP relay targets (ip helper-address)
        # -- IPv6 -------------------------------------------------------------------
        self.ipv6: list = []                       # global/unique-local IPv6Interface list
        self.ipv6_enabled = False                  # link-local only ("ipv6 enable")
        self.ipv6_auto = False                     # SLAAC (hosts)
        self.ra: bool | None = None                # send router advertisements (None = vendor default)

    is_sub = False
    is_loopback = False
    is_svi = False
    is_channel = False
    is_tunnel = False
    channel_group: int | None = None       # EtherChannel member: group number
    channel_mode: str | None = None        # on | active | passive | desirable | auto
    psec: dict | None = None               # port security: {max, violation, sticky, macs, violations}
    err_disabled: bool = False             # shut down by a port-security violation
    dhcp_trust: bool = False               # DHCP snooping trusted port
    is_wireless: bool = False              # radio port / Wi-Fi NIC: never cabled by hand
    voice_vlan: int | None = None          # switchport voice vlan (tagged for IP phones on an access port)
    qos_in: str | None = None              # service-policy input
    qos_out: str | None = None             # service-policy output

    # -- VLANs ----------------------------------------------------------------
    def vlan_allowed(self, vlan: int) -> bool:
        return self.tagged is None or vlan in self.tagged

    def vlan_in(self, frame: EthernetFrame) -> int | None:
        """VLAN a frame received on this switchport belongs to (None = drop)."""
        if self.mode == "access":
            if frame.vlan is not None and frame.vlan == self.voice_vlan:
                return self.voice_vlan
            return self.pvid if frame.vlan in (None, self.pvid) else None
        vlan = self.pvid if frame.vlan is None else frame.vlan
        return vlan if self.vlan_allowed(vlan) else None

    def vlan_out(self, vlan: int) -> tuple[bool, int | None]:
        """(is member, tag to put on the wire) for sending a frame of *vlan* here."""
        if self.mode == "access":
            if self.voice_vlan is not None and vlan == self.voice_vlan and vlan != self.pvid:
                return True, vlan
            return vlan == self.pvid, None
        if not self.vlan_allowed(vlan):
            return False, None
        return True, (None if vlan == self.pvid else vlan)

    def switchport_text(self) -> str:
        if self.mode == "access":
            return f"access {self.pvid}"
        allowed = "all" if self.tagged is None else ",".join(map(str, sorted(self.tagged))) or "none"
        return f"trunk {allowed} (native {self.pvid})"

    @property
    def subinterfaces(self) -> list["SubInterface"]:
        return [p for p in self.device.ports.values() if p.is_sub and p.parent is self]

    # -- state -------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.link is not None

    @property
    def peer(self) -> "Port | None":
        return self.link.other(self) if self.link else None

    @property
    def is_up(self) -> bool:
        """Line protocol: both ends administratively up and a cable plugged in."""
        p = self.peer
        return bool(self.admin_up and p is not None and p.admin_up
                    and self.device.powered and p.device.powered)

    def status_text(self) -> str:
        """Cisco-style status column."""
        if self.err_disabled:
            return "err-disabled"
        if not self.admin_up:
            return "administratively down"
        return "up" if self.is_up else "down"

    def set_admin(self, up: bool) -> None:
        if up:
            self.err_disabled = False
        if self.admin_up == up:
            return
        self.admin_up = up
        self.device.link_state_changed(self)
        if self.peer:
            self.peer.device.link_state_changed(self.peer)
        self.device.engine.emit("link_state", self)

    # -- traffic -----------------------------------------------------------
    def send(self, frame: EthernetFrame) -> bool:
        if not self.is_up:
            self.drops += 1
            return False
        self.tx_frames += 1
        return self.link.transmit(frame, self)

    def deliver(self, frame: EthernetFrame) -> None:
        if not self.is_up:
            self.drops += 1
            return
        self.rx_frames += 1
        self.device.receive(frame, self)

    def __repr__(self) -> str:
        return f"<Port {self.device.name}:{self.name}>"


class LoopbackPort(Port):
    """Virtual interface that is always up (no cable); used for router IDs and BGP peering."""

    is_loopback = True

    @property
    def is_up(self) -> bool:
        return self.admin_up and self.device.powered

    def status_text(self) -> str:
        return "up" if self.admin_up else "administratively down"

    def send(self, frame: EthernetFrame) -> bool:
        return False


class SubInterface(Port):
    """Logical 802.1Q subinterface of a routed port (router-on-a-stick).

    Shares the parent's MAC and cable; frames it sends are tagged with ``vlan``
    (untagged when ``native``)."""

    is_sub = True

    def __init__(self, parent: Port, name: str, short: str | None = None, vlan: int | None = None):
        self.parent = parent
        super().__init__(parent.device, name, short, admin_up=True, mac=parent.mac)
        self.vlan = vlan
        self.native = False
        self.arp_broadcast = True   # Huawei: "arp broadcast enable" on termination sub-interfaces

    # the cable belongs to the parent port
    @property
    def link(self):
        return self.parent.link

    @link.setter
    def link(self, value) -> None:
        pass

    @property
    def peer(self) -> "Port | None":
        return self.parent.peer

    @property
    def is_up(self) -> bool:
        return self.admin_up and self.parent.is_up

    def status_text(self) -> str:
        if not self.admin_up:
            return "administratively down"
        return self.parent.status_text()

    def send(self, frame: EthernetFrame) -> bool:
        if not self.is_up or self.vlan is None:
            self.drops += 1
            return False
        self.tx_frames += 1
        tag = None if self.native else self.vlan
        return self.parent.send(replace(frame, vlan=tag))

    def __repr__(self) -> str:
        return f"<SubInterface {self.device.name}:{self.name} vlan={self.vlan}>"


class Link:
    def __init__(self, a: Port, b: Port, delay: float = LINK_DELAY_MS):
        if a is b or a.device is b.device:
            raise ValueError("cannot connect a device to itself")
        if a.link or b.link:
            raise ValueError("port already connected")
        self.a = a
        self.b = b
        self.delay = delay
        self.wireless = False
        a.link = self
        b.link = self

    @property
    def engine(self):
        return self.a.device.engine

    def other(self, port: Port) -> Port:
        return self.b if port is self.a else self.a

    def transmit(self, frame: EthernetFrame, from_port: Port) -> bool:
        to_port = self.other(from_port)
        if frame.uid == 0:
            object.__setattr__(frame, "uid", self.engine.next_frame_uid())
        self.engine.schedule(self.delay, to_port.deliver, frame, kind=KIND_FRAME,
                             info={"frame": frame, "link": self,
                                   "src": from_port, "dst": to_port})
        return True

    def detach(self) -> None:
        a, b = self.a, self.b
        a.link = b.link = None
        for p in (a, b):
            p.device.link_state_changed(p)
        self.engine.emit("link_state", a)

    def __repr__(self) -> str:
        return f"<Link {self.a.device.name}:{self.a.name} <-> {self.b.device.name}:{self.b.name}>"


class VlanInterface(Port):
    """Switch virtual interface (interface Vlan10 / Vlanif10 / irb.10 / EXOS VLAN ipaddress).

    Routed traffic enters and leaves through the switch's own VLAN; the interface is up
    while at least one member port of its VLAN is up and forwarding (Cisco "autostate")."""

    is_svi = True

    def __init__(self, device, name: str, short: str | None, vlan: int):
        super().__init__(device, name, short, admin_up=True)
        self.vlan = vlan

    @property
    def link(self):
        return None

    @link.setter
    def link(self, value) -> None:
        pass

    @property
    def peer(self):
        return None

    @property
    def is_up(self) -> bool:
        return self.admin_up and self.device.powered and self.device.svi_line_up(self)

    def status_text(self) -> str:
        if not self.admin_up:
            return "administratively down"
        return "up" if self.is_up else "down"

    def send(self, frame: EthernetFrame) -> bool:
        if not self.is_up:
            self.drops += 1
            return False
        self.tx_frames += 1
        return self.device.svi_send(self, frame)


LACP_MODES = ("active", "passive")
PAGP_MODES = ("desirable", "auto")


def channel_modes_match(a: str | None, b: str | None) -> bool:
    """Whether two ends of a link agree to bundle (Cisco rules)."""
    if a is None or b is None:
        return False
    if a == "on" or b == "on":
        return a == b == "on"
    if a in LACP_MODES and b in LACP_MODES:
        return "active" in (a, b)
    if a in PAGP_MODES and b in PAGP_MODES:
        return "desirable" in (a, b)
    return False


def channel_protocol(mode: str | None) -> str:
    return "LACP" if mode in LACP_MODES else ("PAgP" if mode in PAGP_MODES else "-")


class PortChannel(Port):
    """Logical link aggregation port (Port-channel / Eth-Trunk / ae / bond / LAG).

    Switching and STP see the bundle as one port; frames leave through one active member
    chosen by a hash of the MAC addresses."""

    is_channel = True

    def __init__(self, device, name: str, short: str | None, group: int):
        super().__init__(device, name, short, admin_up=True)
        self.group = group

    @property
    def members(self) -> list[Port]:
        return [p for p in self.device.physical_ports() if p.channel_group == self.group]

    def member_bundled(self, m: Port) -> bool:
        """A member forwards for the bundle when its link is up and both ends agree."""
        if not (self.admin_up and m.is_up and m.channel_group == self.group):
            return False
        peer = m.peer
        if peer is None or peer.channel_group is None:
            return False
        return channel_modes_match(m.channel_mode, peer.channel_mode)

    @property
    def active_members(self) -> list[Port]:
        return [m for m in self.members if self.member_bundled(m)]

    @property
    def link(self):
        return None

    @link.setter
    def link(self, value) -> None:
        pass

    @property
    def peer(self):
        """The bundle at the other end (its logical port), when bundled."""
        for m in self.active_members:
            other = m.peer.device
            if hasattr(other, "channel_for_group"):
                return other.channel_for_group(m.peer.channel_group)
        return None

    @property
    def connected(self) -> bool:
        return any(m.connected for m in self.members)

    @property
    def is_up(self) -> bool:
        return bool(self.admin_up and self.device.powered and self.active_members)

    def status_text(self) -> str:
        if not self.admin_up:
            return "administratively down"
        return "up" if self.is_up else "down"

    def send(self, frame: EthernetFrame) -> bool:
        act = self.active_members
        if not act:
            self.drops += 1
            return False
        self.tx_frames += 1
        m = act[(frame.src.value ^ frame.dst.value) % len(act)]
        return m.send(frame)
