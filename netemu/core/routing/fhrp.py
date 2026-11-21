"""First-hop redundancy: HSRP (Cisco) and VRRP (RFC 5798).

Routers on one segment share a virtual IP address that hosts use as their default
gateway.  They exchange hellos and elect the active (HSRP) / master (VRRP) router by
priority, then by the higher interface address.  The active router answers ARP for the
virtual IP with the virtual MAC and forwards the frames sent to it; when it fails, the
standby takes over within the hold time and announces the move with a gratuitous ARP.

HSRP: hello 3 s, hold 10 s, UDP 1985 to 224.0.0.2, MAC 0000.0c07.acXX, no preemption by default.
VRRP: advertisement 1 s, master down ~3.6 s, IP protocol 112 to 224.0.0.18,
      MAC 0000.5e00.01XX, preemption on by default, the address owner has priority 255."""
from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address

from ..addresses import MacAddress

PROTO_VRRP = 112
HSRP_PORT = 1985
GROUPS = {"hsrp": IPv4Address("224.0.0.2"), "vrrp": IPv4Address("224.0.0.18")}
GROUP_MACS = {"hsrp": MacAddress(0x01005E000002), "vrrp": MacAddress(0x01005E000012)}
TIMERS = {"hsrp": (3000.0, 10000.0), "vrrp": (1000.0, 3600.0)}       # hello, hold


def virtual_mac(proto: str, group: int) -> MacAddress:
    return MacAddress((0x00000C07AC00 if proto == "hsrp" else 0x00005E000100) | (group & 0xFF))


@dataclass(frozen=True)
class FhrpMsg:
    proto: str
    group: int
    priority: int
    state: str                 # active | standby | listen (HSRP) / master | backup (VRRP)
    vip: IPv4Address | None

    @property
    def label(self) -> str:
        return self.proto.upper()

    def summary(self) -> str:
        return f"{self.label} group {self.group} {self.state} prio {self.priority} vip {self.vip}"

    def layer_lines(self) -> list[str]:
        return [f"Protocol: {self.label}", f"Group: {self.group}", f"State: {self.state}",
                f"Priority: {self.priority}", f"Virtual IP: {self.vip}"]


@dataclass
class FhrpGroup:
    port: str
    proto: str
    group: int
    vip: IPv4Address | None = None
    priority: int = 100
    preempt: bool | None = None          # None: protocol default (HSRP off, VRRP on)
    disabled: bool = False               # EXOS: created but not yet "enable vrrp"
    state: str = "init"
    since: float = 0.0
    peers: dict = field(default_factory=dict)      # ip -> (priority, state, last_seen)
    changes: int = 0

    @property
    def preempting(self) -> bool:
        return self.preempt if self.preempt is not None else self.proto == "vrrp"

    @property
    def active_word(self) -> str:
        return "active" if self.proto == "hsrp" else "master"

    @property
    def standby_word(self) -> str:
        return "standby" if self.proto == "hsrp" else "backup"

    def to_dict(self) -> dict:
        return {"port": self.port, "proto": self.proto, "group": self.group,
                "vip": str(self.vip) if self.vip else None, "priority": self.priority, "preempt": self.preempt,
                "disabled": self.disabled}

    @classmethod
    def from_dict(cls, d: dict) -> "FhrpGroup":
        return cls(d["port"], d["proto"], int(d["group"]), IPv4Address(d["vip"]) if d.get("vip") else None,
                   int(d.get("priority", 100)), d.get("preempt"), bool(d.get("disabled", False)))


class Fhrp:
    """All HSRP/VRRP groups of one router."""

    def __init__(self, dev):
        self.dev = dev
        self.groups: dict[tuple[str, str, int], FhrpGroup] = {}
        self._timer = None

    # -- configuration ---------------------------------------------------------------------
    def group(self, port: str, proto: str, num: int, create: bool = True) -> FhrpGroup | None:
        key = (port, proto, num)
        g = self.groups.get(key)
        if g is None and create:
            g = self.groups[key] = FhrpGroup(port, proto, num, since=self.dev.engine.now)
            self._arm()
        return g

    def remove(self, port: str, proto: str, num: int) -> bool:
        return self.groups.pop((port, proto, num), None) is not None

    def changed(self, g: FhrpGroup) -> None:
        g.since = min(g.since, self.dev.engine.now)
        self._arm()
        self.tick(send=True)

    def _arm(self) -> None:
        if self._timer is None and self.groups:
            self._timer = self.dev.engine.schedule(1000.0, self._periodic)

    def _periodic(self) -> None:
        self._timer = None
        self.tick()
        self._arm()

    # -- runtime ----------------------------------------------------------------------------
    def eff_priority(self, g: FhrpGroup) -> int:
        p = self.dev.ports.get(g.port)
        if g.proto == "vrrp" and p is not None and p.ip is not None and g.vip == p.ip.ip:
            return 255                          # VRRP address owner
        return g.priority

    def tick(self, send: bool = False) -> None:
        now = self.dev.engine.now
        for g in list(self.groups.values()):
            p = self.dev.ports.get(g.port)
            if p is None or not p.is_up or p.ip is None or g.vip is None or g.disabled:
                self._set(g, "init")
                g.peers.clear()
                continue
            hello, hold = TIMERS[g.proto]
            g.peers = {ip: v for ip, v in g.peers.items() if now - v[2] <= hold}
            if g.state == "init":
                self._set(g, "listen" if g.proto == "hsrp" else g.standby_word)
                g.since = now
            self._elect(g, now)
            last = getattr(g, "_last_hello", -1e18)
            if send or now - last >= hello - 1:
                g._last_hello = now
                self._hello(g, p)

    def _elect(self, g: FhrpGroup, now: float) -> None:
        p = self.dev.ports[g.port]
        me = (self.eff_priority(g), int(p.ip.ip))
        claims = [(v[0], int(ip)) for ip, v in g.peers.items() if v[1] in ("active", "master")]
        others = [(v[0], int(ip)) for ip, v in g.peers.items() if v[1] not in ("active", "master")]
        if g.state in ("active", "master"):
            if any(c > me for c in claims):          # two active routers: the better one keeps the role
                self._set(g, self._waiting(g, me, others))
            return
        if claims:
            if g.preempting and me > max(claims):
                self._set(g, g.active_word)
            else:
                self._set(g, self._waiting(g, me, others))
            return
        if any(o > me for o in others):
            self._set(g, self._waiting(g, me, others))
        elif now - g.since >= TIMERS[g.proto][1]:
            self._set(g, g.active_word)

    @staticmethod
    def _waiting(g: FhrpGroup, me, others) -> str:
        if g.proto == "vrrp":
            return "backup"
        return "standby" if all(me > o for o in others) else "listen"

    def _set(self, g: FhrpGroup, state: str) -> None:
        if g.state == state:
            return
        old, g.state = g.state, state
        g.changes += 1
        p = self.dev.ports.get(g.port)
        if state in ("active", "master") and p is not None:
            self._gratuitous_arp(g, p)
        if state in ("active", "standby", "master", "backup") or old in ("active", "standby", "master", "backup"):
            name = {"hsrp": "HSRP-5-STATECHANGE", "vrrp": "VRRP-6-STATECHANGE"}[g.proto]
            short = p.short if p is not None else g.port
            self.dev.console(f"%{name}: {short} Grp {g.group} state {old.capitalize()} -> {state.capitalize()}")
        self.dev.engine.emit("routes_changed", self.dev)

    def _hello(self, g: FhrpGroup, p) -> None:
        from ..packets import EthernetFrame, IPv4Packet, UdpDatagram
        msg = FhrpMsg(g.proto, g.group, self.eff_priority(g), g.state, g.vip)
        if g.proto == "hsrp":
            pkt = IPv4Packet(p.ip.ip, GROUPS["hsrp"], 1, UdpDatagram(HSRP_PORT, HSRP_PORT, msg), 17)
        else:
            pkt = IPv4Packet(p.ip.ip, GROUPS["vrrp"], 255, msg, PROTO_VRRP)
        src = virtual_mac(g.proto, g.group) if g.state in ("active", "master") else p.mac
        p.send(EthernetFrame(src, GROUP_MACS[g.proto], "IPv4", pkt))

    def _gratuitous_arp(self, g: FhrpGroup, p) -> None:
        from ..addresses import MacAddress as M
        from ..packets import ArpPacket, EthernetFrame
        vmac = virtual_mac(g.proto, g.group)
        arp = ArpPacket("reply", vmac, g.vip, M(0xFFFFFFFFFFFF), g.vip)
        p.send(EthernetFrame(vmac, M(0xFFFFFFFFFFFF), "ARP", arp))

    def receive(self, pkt, port) -> None:
        msg = pkt.payload.data if hasattr(pkt.payload, "data") else pkt.payload
        if not isinstance(msg, FhrpMsg):
            return
        g = self.groups.get((port.name, msg.proto, msg.group))
        if g is None or pkt.src == (port.ip.ip if port.ip else None):
            return
        if g.vip is None and msg.vip is not None and msg.proto == "hsrp":
            g.vip = msg.vip                        # HSRP learns the virtual IP from the active router
        g.peers[pkt.src] = (msg.priority, msg.state, self.dev.engine.now)
        self._elect(g, self.dev.engine.now)

    # -- data plane helpers --------------------------------------------------------------------
    def active_on(self, port) -> list[FhrpGroup]:
        return [g for g in self.groups.values() if g.port == port.name and g.state in ("active", "master")]

    def vmac_for(self, ip, port) -> MacAddress | None:
        for g in self.active_on(port):
            if g.vip == ip:
                return virtual_mac(g.proto, g.group)
        return None

    def owns(self, ip) -> bool:
        return any(g.vip == ip and g.state in ("active", "master") for g in self.groups.values())

    def macs(self) -> set:
        out = set()
        for g in self.groups.values():
            out.add(GROUP_MACS[g.proto])
            if g.state in ("active", "master"):
                out.add(virtual_mac(g.proto, g.group))
        return out
