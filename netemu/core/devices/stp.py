"""Spanning Tree (802.1D / 802.1w, single instance shared by all VLANs).

Roles are computed for the whole L2 domain of interconnected STP switches (the
result real BPDU exchange converges to): root bridge = lowest (priority, MAC);
root port = lowest (root path cost, upstream bridge ID, upstream port ID);
on every link the end with the better (cost, bridge ID, port ID) is designated,
the other one is alternate (blocking).  Classic STP (Cisco PVST) moves a port to
forwarding through listening and learning (15 s each); RSTP/MSTP do it at once.
Ports towards hosts/routers are edge ports and forward immediately.
BPDUs are still sent every 2 s so they can be watched in simulation mode.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..addresses import MacAddress

BPDU_MAC = MacAddress(0x0180C2000000)
HELLO_MS = 2000.0
FWD_DELAY_MS = 15_000.0


@dataclass(frozen=True)
class Bpdu:
    root: tuple            # (priority, mac int)
    cost: int
    bridge: tuple
    port_id: int
    rstp: bool
    label = "STP"

    def summary(self) -> str:
        return f"{'RST' if self.rstp else 'Conf'}. Root = {self.root[0]}/{MacAddress(self.root[1])}  Cost = {self.cost}"

    def layer_lines(self) -> list[str]:
        return [f"Protocol: {'Rapid Spanning Tree' if self.rstp else 'Spanning Tree'}",
                f"Root identifier: {self.root[0]} / {MacAddress(self.root[1])}",
                f"Root path cost: {self.cost}",
                f"Bridge identifier: {self.bridge[0]} / {MacAddress(self.bridge[1])}",
                f"Port identifier: 0x{self.port_id:04x}", "Hello time: 2, Max age: 20, Forward delay: 15"]


def default_mode(vendor: str) -> str:
    return {"cisco": "pvst", "huawei": "mstp", "juniper": "rstp", "mikrotik": "rstp",
            "extreme": "stp"}.get(vendor, "rstp")


def default_enabled(vendor: str) -> bool:
    """EXOS ships with STP domain s0 disabled; everybody else runs STP out of the box."""
    return vendor != "extreme"


class Stp:
    def __init__(self, switch):
        self.sw = switch
        self.enabled = default_enabled(switch.vendor)
        self.mode = default_mode(switch.vendor)
        self.priority = 32768
        self.costs: dict[str, int] = {}          # per-port cost overrides
        self.edge: set[str] = set()              # portfast / edged-port
        self.roles: dict[str, str] = {}          # port -> root | designated | alternate | disabled | edge
        self.states: dict[str, str] = {}         # port -> forwarding | learning | listening | blocking
        self._since: dict[str, float] = {}
        self.root_id: tuple | None = None
        self.root_cost = 0
        self.root_port: str | None = None
        self.topology_changes = 0
        self._hello = None
        self._pending = False

    # -- identity -----------------------------------------------------------------------------
    @property
    def bridge_id(self) -> tuple:
        mac = min(p.mac.value for p in self.sw.physical_ports())
        return (self.priority, mac)

    @property
    def rapid(self) -> bool:
        return self.mode in ("rapid-pvst", "rstp", "mstp")

    def port_cost(self, port) -> int:
        if port.name in self.costs:
            return self.costs[port.name]
        if port.is_channel:
            act = port.active_members or port.members
            if not act:
                return 200000000
            return max(1, int(self.port_cost(act[0]) / (1 + 0.5 * (len(act) - 1))))
        fast = port.name.lower().startswith(("fast", "fa"))
        v = self.sw.vendor
        if v == "cisco":
            return 19 if fast else 4
        if v == "mikrotik":
            return 10
        return 200_000 if fast else 20_000

    def port_id(self, port) -> int:
        return (128 << 8) | (list(self.sw.ports).index(port.name) + 1)

    # -- state ----------------------------------------------------------------------------------
    def forwarding(self, port) -> bool:
        if not self.enabled:
            return True
        return self.states.get(port.name, "forwarding") == "forwarding"

    def to_config(self) -> dict:
        return {"enabled": self.enabled, "mode": self.mode, "priority": self.priority,
                "costs": dict(self.costs), "edge": sorted(self.edge)}

    def apply_config(self, d: dict) -> None:
        self.enabled = d.get("enabled", default_enabled(self.sw.vendor))
        self.mode = d.get("mode", default_mode(self.sw.vendor))
        self.priority = int(d.get("priority", 32768))
        self.costs = {k: int(v) for k, v in d.get("costs", {}).items()}
        self.edge = set(d.get("edge", []))
        self.schedule()

    # -- recompute -------------------------------------------------------------------------------
    def schedule(self) -> None:
        if not self._pending:
            self._pending = True
            self.sw.engine.schedule(1.0, self._run)
        if self._hello is None:
            self._hello = self.sw.engine.schedule(HELLO_MS, self._send_hellos)

    def _run(self) -> None:
        self._pending = False
        compute_domain(self.sw)

    def set_role(self, port, role: str, now: float) -> bool:
        old = self.roles.get(port.name)
        self.roles[port.name] = role
        if role in ("alternate", "disabled"):
            new_state = "blocking" if role == "alternate" else "disabled"
            changed = self.states.get(port.name) != new_state
            self.states[port.name] = new_state
            return changed
        if role == "edge" or self.rapid:
            changed = self.states.get(port.name) != "forwarding"
            self.states[port.name] = "forwarding"
            return changed and old not in (None,)
        if self.states.get(port.name) in ("forwarding", "learning", "listening") and old in ("root", "designated"):
            return False
        self.states[port.name] = "listening"
        self._since[port.name] = now
        self.sw.engine.schedule(FWD_DELAY_MS, self._advance, port.name, now)
        return True

    def _advance(self, name: str, started: float) -> None:
        if self._since.get(name) != started:
            return
        st = self.states.get(name)
        if st == "listening":
            self.states[name] = "learning"
            self.sw.engine.schedule(FWD_DELAY_MS, self._advance, name, started)
        elif st == "learning":
            self.states[name] = "forwarding"
            self.sw.engine.emit("link_state", self.sw.ports[name])

    def _send_hellos(self) -> None:
        self._hello = None
        if not self.enabled:
            return
        from ..packets import EthernetFrame
        sent = False
        for p in self.sw.l2_ports():
            if p.is_up and self.roles.get(p.name) == "designated" and p.peer is not None \
                    and p.peer.device.category == "switch":
                bpdu = Bpdu(self.root_id or self.bridge_id, self.root_cost, self.bridge_id, self.port_id(p), self.rapid)
                p.send(EthernetFrame(p.mac, BPDU_MAC, "STP", bpdu))
                sent = True
        if sent:                                   # restarted by schedule() on the next link change
            self._hello = self.sw.engine.schedule(HELLO_MS, self._send_hellos)


def _stp_switch(dev) -> bool:
    return dev.category == "switch" and getattr(dev, "stp", None) is not None and dev.stp.enabled


def compute_domain(start) -> None:
    """Assign roles/states for every switch reachable from *start* over switch-switch links."""
    now = start.engine.now
    if not _stp_switch(start):
        for p in start.l2_ports():
            start.stp.roles[p.name] = "disabled"
            start.stp.states[p.name] = "forwarding"
        return
    # collect the domain
    domain, todo = {start.id: start}, [start]
    while todo:
        sw = todo.pop()
        for p in sw.l2_ports():
            if p.is_up and p.peer is not None and _stp_switch(p.peer.device) and p.peer.device.id not in domain:
                domain[p.peer.device.id] = p.peer.device
                todo.append(p.peer.device)
    sws = list(domain.values())
    root = min(sws, key=lambda s: s.stp.bridge_id)
    root_id = root.stp.bridge_id
    # Dijkstra: best root path cost per switch
    import heapq
    best = {root.id: (0, root_id, 0, None)}         # id -> (cost, upstream bridge, upstream port id, local port)
    heap = [(0, root_id, 0, root.id)]
    done = set()
    while heap:
        cost, _, _, sid = heapq.heappop(heap)
        if sid in done:
            continue
        done.add(sid)
        sw = domain[sid]
        for p in sw.l2_ports():
            if not (p.is_up and p.peer is not None and p.peer.device.id in domain):
                continue
            nb, np_ = p.peer.device, p.peer
            cand = (cost + nb.stp.port_cost(np_), sw.stp.bridge_id, sw.stp.port_id(p), np_.name)
            cur = best.get(nb.id)
            if nb.id != root.id and (cur is None or cand[:3] < cur[:3]):
                best[nb.id] = cand
                heapq.heappush(heap, (cand[0], cand[1], cand[2], nb.id))
    changed = False
    for sw in sws:
        stp = sw.stp
        cost, _, _, rport = best.get(sw.id, (0, root_id, 0, None))
        if stp.root_id != root_id or stp.root_port != rport:
            changed = changed or stp.root_id is not None
        stp.root_id, stp.root_cost, stp.root_port = root_id, cost, rport
        for p in sw.l2_ports():
            if not p.is_up:
                role = "disabled"
            elif p.peer is None or p.peer.device.id not in domain:
                role = "designated" if (p.peer is not None and p.peer.device.category == "switch") else "edge"
                if p.name in stp.edge:
                    role = "edge"
            elif p.name == rport:
                role = "root"
            else:
                nb = p.peer.device
                mine = (cost, stp.bridge_id, stp.port_id(p))
                theirs = (best.get(nb.id, (0,))[0], nb.stp.bridge_id, nb.stp.port_id(p.peer))
                role = "designated" if mine < theirs else "alternate"
            changed = stp.set_role(p, role, now) or changed
    if changed:
        for sw in sws:
            sw.stp.topology_changes += 1
            sw.mac_table.clear()                       # topology change: flush learned MACs
