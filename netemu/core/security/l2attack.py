"""Layer-2 LAN attacks for training (a simulation, no real attack traffic beyond the model packets).

An *attacker* host sits on an access switch and runs one or more attacks; each spreads with real frames
(ARP, DHCP, BPDU) so the switch's own defences act on them.

* ``arp-spoof``  ARP cache poisoning / man-in-the-middle: the attacker answers ARP for the gateway (and
  tells the gateway it is each victim), so victims' traffic flows through it.  Stopped by Dynamic ARP
  Inspection (DAI) against the DHCP-snooping bindings on untrusted ports.
* ``mac-flood``  CAM-table overflow: the attacker floods the switch with random source MACs until it
  stops learning and floods every frame out of all ports (fail-open), so the attacker sees other hosts'
  traffic.  Stopped by port security (a MAC limit per port).
* ``stp-root``   the attacker sends superior BPDUs to become the spanning-tree root and reroute traffic.
  Stopped by BPDU Guard (err-disables the port) or Root Guard (ignores superior BPDUs on that port).

Host security state is ``l2sec(switch)`` on the switch; the attacker's running attacks are ``dev.l2attacks``.
A topology-wide ``L2Monitor`` (``monitor(topology)``) runs the attackers once per simulated second and keeps
the event log and counters shown in the GUI."""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from ...i18n import tr
from ..addresses import IPv4Address, MacAddress

TICK_MS = 1000.0
_ids = itertools.count(1)

KINDS = ("arp-spoof", "mac-flood", "stp-root")
NAMES = {"arp-spoof": "Подмена ARP (MITM)", "mac-flood": "Переполнение MAC-таблицы",
         "stp-root": "Захват корня STP"}


def name(kind: str) -> str:
    return tr(NAMES.get(kind, kind))


@dataclass
class L2Attack:
    kind: str
    target: str = ""                 # victim IP, or "" / "all" for the whole subnet
    gateway: str = ""                # ARP spoof: the IP to impersonate (default: the attacker's gateway)
    running: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "target": self.target, "gateway": self.gateway, "running": self.running}

    @classmethod
    def from_dict(cls, d: dict) -> "L2Attack":
        return cls(d["kind"], d.get("target", ""), d.get("gateway", ""), d.get("running", False))


# ---------------------------------------------------------------------------------- switch L2 security state
def l2sec(switch) -> dict:
    s = getattr(switch, "l2security", None)
    if s is None:
        s = switch.l2security = {"dai_vlans": [], "bpduguard": False, "rootguard_ports": []}
    return s


# ---------------------------------------------------------------------------------- monitor
@dataclass
class L2Monitor:
    topology: object
    events: list = field(default_factory=list)
    stats: dict = field(default_factory=lambda: {"poisoned": 0, "intercepted": 0, "dai_drops": 0,
                                                  "cam_flood": 0, "bpdu_blocked": 0})
    _timer: object = None
    _seen: set = field(default_factory=set)

    @property
    def engine(self):
        return self.topology.engine

    def log(self, dev, text, once=None) -> None:
        if once is not None:
            if once in self._seen:
                return
            self._seen.add(once)
        self.events.append((self.engine.now, getattr(dev, "name", str(dev)), text))
        del self.events[:-400]
        self.engine.emit("l2_event", (dev, text))

    def attackers(self) -> list:
        return [d for d in self.topology.devices.values() if getattr(d, "l2attacks", None)]

    def ensure_running(self) -> None:
        if self._timer is None and any(a.running for d in self.attackers() for a in d.l2attacks):
            self._timer = self.engine.schedule(TICK_MS, self._tick)

    def _tick(self) -> None:
        self._timer = None
        live = False
        for d in self.attackers():
            for i, a in enumerate(d.l2attacks):
                if a.running and getattr(d, "powered", True) and d.nic.is_up:
                    live = True
                    _run_attack(self, d, a)
        if live:
            self._timer = self.engine.schedule(TICK_MS, self._tick)


def monitor(topology) -> L2Monitor:
    m = getattr(topology, "l2mon", None)
    if m is None:
        m = topology.l2mon = L2Monitor(topology)
    return m


def monitor_of(dev) -> L2Monitor | None:
    topo = getattr(dev.engine, "topology", None)
    return monitor(topo) if topo is not None else None


# ---------------------------------------------------------------------------------- helpers
def access_switch(dev):
    """The switch (and its port) the attacker is directly connected to."""
    peer = dev.nic.peer
    if peer is None:
        return None, None
    owner = peer.device
    return (owner, peer) if owner.category == "switch" else (None, None)


def subnet_hosts(dev) -> list:
    """(ip, device) pairs of other hosts in the attacker's subnet."""
    own = dev.nic.ip
    out = []
    if own is None:
        return out
    for d in dev.engine.topology.devices.values():
        if d is dev:
            continue
        for p in d.ports.values():
            if p.ip is not None and p.ip.ip in own.network and not getattr(p, "is_loopback", False):
                out.append((p.ip.ip, d, p))
    return out


# ---------------------------------------------------------------------------------- attack behaviour
def _run_attack(m: L2Monitor, dev, a: L2Attack) -> None:
    fn = {"arp-spoof": _arp_spoof, "mac-flood": _mac_flood, "stp-root": _stp_root}.get(a.kind)
    if fn is not None:
        fn(m, dev, a)


def _arp_spoof(m: L2Monitor, dev, a: L2Attack) -> None:
    from ..packets import ETH_ARP, ArpPacket, EthernetFrame
    gw = a.gateway or (str(dev.gateway) if dev.gateway else "")
    if not gw:
        return
    try:
        gw_ip = IPv4Address(gw)
    except ValueError:
        return
    victims = subnet_hosts(dev)
    targets = [(ip, d, p) for ip, d, p in victims if (not a.target or a.target in ("all", "")) or str(ip) == a.target]
    poisoned = 0
    for ip, d, p in targets:
        if ip == gw_ip:
            continue
        # tell the victim that the gateway is at the attacker's MAC
        reply = ArpPacket("reply", dev.nic.mac, gw_ip, p.mac, ip)
        dev.nic.send(EthernetFrame(dev.nic.mac, p.mac, ETH_ARP, reply))
        poisoned += 1
    # tell the gateway that every victim is at the attacker's MAC (full MITM)
    for ip, d, p in victims:
        if ip == gw_ip:
            for vip, vd, vp in targets:
                if vip != gw_ip:
                    reply = ArpPacket("reply", dev.nic.mac, vip, p.mac, gw_ip)
                    dev.nic.send(EthernetFrame(dev.nic.mac, p.mac, ETH_ARP, reply))
    if poisoned:
        m.stats["poisoned"] += poisoned
        m.log(dev, tr("подмена ARP: шлюз {gw} выдан за MAC атакующего {n} жертвам", gw=gw, n=poisoned),
              once=(dev.name, "arp", gw))


def _mac_flood(m: L2Monitor, dev, a: L2Attack) -> None:
    sw, port = access_switch(dev)
    if sw is None:
        return
    # port security with a finite MAC limit catches the flood before the table overflows
    ps = getattr(port, "psec", None)
    if ps is not None and port.err_disabled:
        return
    rng = getattr(dev, "_l2rng", None) or random.Random(dev.name)
    dev._l2rng = rng
    from ..packets import EthernetFrame
    from ..addresses import BROADCAST_MAC
    vlan = port.pvid or 1
    for _ in range(2000):                             # a real flood is thousands of MACs per second
        mac = MacAddress(0x02_0000_0000_00 | rng.randrange(1 << 40))
        if port.psec is not None and not sw._port_security(EthernetFrame(mac, BROADCAST_MAC, "FLOOD", None), port):
            return                                    # port security limit hit: violation handled
        sw.mac_table[(vlan, mac)] = _mac_entry(sw, mac, port)
    overflow = len(sw.mac_table) >= cam_limit(sw)
    sw.cam_overflow = overflow
    if overflow:
        m.stats["cam_flood"] += 1
        m.log(dev, tr("переполнение MAC-таблицы: коммутатор {sw} рассылает кадры во все порты", sw=sw.name),
              once=(dev.name, "cam", sw.name))


def _mac_entry(sw, mac, port):
    from ..devices.l2 import MacEntry
    return MacEntry(mac, port, sw.engine.now, port.pvid)


def cam_limit(sw) -> int:
    return getattr(sw, "mac_limit", 8192)




def _stp_root(m: L2Monitor, dev, a: L2Attack) -> None:
    sw, port = access_switch(dev)
    if sw is None:
        return
    if getattr(port, "err_disabled", False):
        return
    # always send the superior BPDU; the switch decides (accept -> root takeover, or BPDU/Root Guard -> blocked)
    from ..devices.stp import Bpdu, BPDU_MAC
    from ..packets import EthernetFrame
    bpdu = Bpdu((0, dev.nic.mac.value), 0, (0, dev.nic.mac.value), 0x8001, False)
    dev.nic.send(EthernetFrame(dev.nic.mac, BPDU_MAC, "STP", bpdu))
    if not getattr(dev, "_stp_claimed", False):
        dev._stp_claimed = True
        m.log(dev, tr("захват корня STP: отправлены лучшие BPDU (приоритет 0)"), once=(dev.name, "stp"))


# ---------------------------------------------------------------------------------- receive hooks
def attacker_rx(dev, frame, port) -> bool:
    """The attacker's NIC: count (and relay) traffic that MITM/flooding delivered to it."""
    from ..packets import ETH_IPV4, IPv4Packet
    if not any(a.running and a.kind in ("arp-spoof", "mac-flood") for a in getattr(dev, "l2attacks", [])):
        return False
    if frame.ethertype != ETH_IPV4 or not isinstance(frame.payload, IPv4Packet):
        return False
    pkt = frame.payload
    if dev.nic.ip is not None and pkt.dst == dev.nic.ip.ip:
        return False                                  # really for us: normal handling
    if frame.dst.is_broadcast or frame.dst.is_multicast:
        return False                                  # ordinary flooded broadcast, not an interception
    # a unicast frame for someone else reached us: MITM (dst = our MAC) or CAM fail-open (dst = a third MAC)
    m = monitor_of(dev)
    if m is not None:
        m.stats["intercepted"] += 1
        dev._intercepted = getattr(dev, "_intercepted", 0) + 1
        m.log(dev, tr("перехвачен трафик: {src} → {dst}", src=pkt.src, dst=pkt.dst), once=(dev.name, "mitm"))
    _relay(dev, frame, pkt)
    return True


def _relay(dev, frame, pkt) -> None:
    """Forward intercepted traffic to its real destination so the victim keeps connectivity (stealthy MITM)."""
    real = _real_mac(dev, pkt.dst)
    if real is not None and real != dev.nic.mac:
        from ..packets import ETH_IPV4, EthernetFrame
        dev.nic.send(EthernetFrame(dev.nic.mac, real, ETH_IPV4, pkt))


def _real_mac(dev, dst_ip):
    """The true MAC for *dst_ip* (same subnet host, or the gateway) resolved from the topology."""
    own = dev.nic.ip
    if own is None:
        return None
    target = dst_ip if dst_ip in own.network else (dev.gateway or dst_ip)
    for ip, d, p in subnet_hosts(dev):
        if ip == target:
            return p.mac
    return None



# ---------------------------------------------------------------------------------- DAI (switch hook)
def dai_blocks(switch, arp, port, vlan) -> bool:
    """Dynamic ARP Inspection: drop ARP on an untrusted port whose sender IP/MAC is not a known binding."""
    s = l2sec(switch)
    if vlan not in s["dai_vlans"]:
        return False
    if getattr(port, "arp_trust", False) or getattr(port, "dhcp_trust", False):
        return False
    binding = switch.snoop_bindings.get(arp.sender_mac.cisco())
    static = {(b["mac"], b["ip"]) for b in s.get("static_bindings", [])}
    ok = (binding is not None and binding.get("ip") == str(arp.sender_ip)) or \
         (arp.sender_mac.cisco(), str(arp.sender_ip)) in static
    if ok:
        return False
    switch.dai_drops = getattr(switch, "dai_drops", 0) + 1
    m = monitor_of(switch)
    if m is not None:
        m.stats["dai_drops"] += 1
        m.log(switch, tr("DAI отбросил ARP: {ip} заявлен с чужого MAC {mac} на {port}",
                         ip=arp.sender_ip, mac=arp.sender_mac.cisco(), port=port.short),
              once=(switch.name, "dai", str(arp.sender_ip)))
    return True


def on_bpdu(switch, frame, port) -> None:
    """A BPDU not stopped by a guard: a superior one from a non-STP neighbour (an attacker) takes the root."""
    from ..devices.stp import Bpdu
    bpdu = getattr(frame, "payload", None)
    peer = port.peer.device if port.peer is not None else None
    if not isinstance(bpdu, Bpdu) or peer is None:
        return
    from ..devices.stp import _stp_switch
    if _stp_switch(peer):
        return                                        # normal inter-switch BPDU: central STP handles it
    if switch.stp.enabled and bpdu.root < switch.stp.root_id:
        switch.stp.root_id = bpdu.root                # superior claim from an end port: root takeover
        switch.stp.root_cost = bpdu.cost
        switch.stp.root_port = port.name
        switch.engine.emit("device_changed", switch)
        m = monitor_of(switch)
        if m is not None:
            m.stats["bpdu_blocked"] += 0
            m.log(switch, tr("корень STP захвачен через порт {port}", port=port.short),
                  once=(switch.name, "stphijack", port.name))


def bpdu_guard_trips(switch, port) -> bool:
    """BPDU Guard / Root Guard on receiving a BPDU on *port*.  Returns True when the BPDU must be ignored."""
    s = l2sec(switch)
    if s["bpduguard"] or getattr(port, "bpduguard", False):
        if not getattr(port, "err_disabled", False):
            port.err_disabled = True
            port.set_admin(False)
            from ..devices.stp import compute_domain
            compute_domain(switch)                    # reconverge: the real switch reclaims the root role
            switch.console(f"%SPANTREE-2-BLOCK_BPDUGUARD: Received BPDU on port {port.short} with BPDU Guard "
                           f"enabled. Disabling port.")
            m = monitor_of(switch)
            if m is not None:
                m.stats["bpdu_blocked"] += 1
                m.log(switch, tr("BPDU Guard выключил порт {port}: получен BPDU", port=port.short),
                      once=(switch.name, "bpduguard", port.name))
        return True
    if port.name in s["rootguard_ports"] or getattr(port, "rootguard", False):
        from ..devices.stp import compute_domain
        if switch.stp.root_id != switch.stp.bridge_id:
            compute_domain(switch)                    # ignore the superior BPDU: the switch keeps the real root
        m = monitor_of(switch)
        if m is not None:
            m.stats["bpdu_blocked"] += 1
            m.log(switch, tr("Root Guard отклонил лучший BPDU на {port}", port=port.short),
                  once=(switch.name, "rootguard", port.name))
        return True                                   # superior BPDU ignored, port stays root-inconsistent
    return False


# ---------------------------------------------------------------------------------- persistence
def to_config(dev, cfg: dict) -> None:
    if getattr(dev, "l2attacks", None):
        cfg["l2attacks"] = [a.to_dict() for a in dev.l2attacks]
    s = getattr(dev, "l2security", None)
    if s and (s["dai_vlans"] or s["bpduguard"] or s["rootguard_ports"] or s.get("static_bindings")):
        cfg["l2security"] = {"dai_vlans": sorted(s["dai_vlans"]), "bpduguard": s["bpduguard"],
                             "rootguard_ports": sorted(s["rootguard_ports"]),
                             "static_bindings": s.get("static_bindings", [])}
    if getattr(dev, "mac_limit", None) is not None:
        cfg["mac_limit"] = dev.mac_limit


def apply_config(dev, cfg: dict) -> None:
    if "l2attacks" in cfg:
        dev.l2attacks = [L2Attack.from_dict(d) for d in cfg["l2attacks"]]
        if any(a.running for a in dev.l2attacks):
            def kick():
                mon = monitor_of(dev)
                if mon is not None:
                    mon.ensure_running()
            dev.engine.schedule(60.0, kick)
    if "mac_limit" in cfg:
        dev.mac_limit = cfg["mac_limit"]
    if "l2security" in cfg:
        s = l2sec(dev)
        c = cfg["l2security"]
        s["dai_vlans"] = list(c.get("dai_vlans", []))
        s["bpduguard"] = bool(c.get("bpduguard"))
        s["rootguard_ports"] = list(c.get("rootguard_ports", []))
        s["static_bindings"] = list(c.get("static_bindings", []))


# ---------------------------------------------------------------------------------- what-if (lab checks)
def intercepted_by(topology, attacker_name: str, seconds: float = 6.0) -> int:
    """How many victim frames the attacker intercepts (MITM/flood) over *seconds*, in a copy of the network."""
    from ..persistence import topology_from_dict, topology_to_dict
    from ..topology import Topology
    t2 = topology_from_dict(topology_to_dict(topology), Topology())
    dev = t2.find(attacker_name)
    if dev is None:
        return 0
    dev._intercepted = 0
    for a in getattr(dev, "l2attacks", []):
        a.running = True
    monitor(t2).ensure_running()
    t2.engine.run(max_time=2000)
    _generate_victim_traffic(t2, dev)
    t2.engine.run(max_time=seconds * 1000)
    return getattr(dev, "_intercepted", 0)


def _generate_victim_traffic(topology, attacker) -> None:
    """Make the other hosts in the subnet ping their gateway, so a MITM has something to intercept."""
    hosts = [d for ip, d, p in subnet_hosts(attacker) if d.category in ("pc", "server")]
    from ..stack.icmp import PingListener, PingSession
    for d in hosts:
        if d.gateway is not None:
            PingSession(d, d.gateway, PingListener(), count=6, interval=500.0).start()
