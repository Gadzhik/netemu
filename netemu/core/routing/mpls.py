"""MPLS with LDP (Cisco "mpls ip"), frame-mode, downstream unsolicited, liberal retention.

* LDP discovery: hellos to 224.0.0.2 UDP 646 every 5 s on "mpls ip" interfaces; two routers that
  hear each other form a session (the TCP 646 session is not modelled separately).  The LDP
  router ID is the highest loopback address.  Each side tells the peer its interface addresses
  (Address message) so next hops can be matched to peers.
* Label bindings: every LSR gives each prefix in its routing table a local label (16, 17, ...;
  connected networks get implicit-null = 3) and advertises the bindings to all peers.
* Forwarding: an IP packet whose next hop is an LDP peer with a label for the prefix gets that
  label pushed (ingress LER); a labelled frame is switched by the LFIB (swap); a binding of
  implicit-null pops the label one hop before the egress (penultimate-hop popping).  The MPLS TTL
  is copied from the IP TTL and back on pop; expiry inside the core sends ICMP time exceeded.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from ipaddress import IPv4Address, IPv4Network

from ..addresses import MacAddress
from ..packets import PROTO_UDP, IPv4Packet, UdpDatagram

LDP_PORT = 646
ALL_ROUTERS = IPv4Address("224.0.0.2")
ALL_ROUTERS_MAC = MacAddress(0x01005E000002)
ETH_MPLS = "MPLS"
IMPLICIT_NULL = 3
HELLO_MS, HOLD_MS = 5000, 15000


@dataclass(frozen=True)
class MplsPacket:
    labels: tuple                 # top of stack first
    ttl: int
    inner: object                 # IPv4Packet
    label = "MPLS"

    def summary(self) -> str:
        inner = self.inner
        what = f"{inner.src} -> {inner.dst}" if isinstance(inner, IPv4Packet) else ""
        return f"MPLS label {'/'.join(map(str, self.labels))} TTL {self.ttl} {what}".rstrip()

    def layer_lines(self) -> list[str]:
        out = []
        for i, lab in enumerate(self.labels):
            out.append(f"MPLS label: {lab}, Exp: 0, S: {1 if i == len(self.labels) - 1 else 0}, TTL: {self.ttl}")
        return out


@dataclass(frozen=True)
class LdpMsg:
    kind: str                     # hello | address | mapping
    lsr_id: str
    addresses: tuple = ()
    bindings: tuple = ()          # ((prefix, label), ...)
    label = "LDP"

    def summary(self) -> str:
        return f"LDP {self.kind.capitalize()} {self.lsr_id}" + (f" ({len(self.bindings)} labels)" if self.bindings else "")

    def layer_lines(self) -> list[str]:
        out = [f"Message: {self.kind}", f"LSR ID: {self.lsr_id}:0"]
        out += [f"Address: {a}" for a in self.addresses]
        out += [f"FEC {p} -> label {'imp-null' if lab == IMPLICIT_NULL else lab}" for p, lab in self.bindings]
        return out


@dataclass
class LdpPeer:
    lsr_id: str
    port: object
    ip: IPv4Address
    up_since: float
    last_seen: float
    addresses: set
    bindings: dict                # prefix -> label


class Mpls:
    def __init__(self, router):
        self.r = router
        self.engine = router.engine
        self.interfaces: list[str] = []
        self.router_id_iface: str | None = None
        self.peers: dict[str, LdpPeer] = {}            # LSR id -> peer
        self.local: dict[IPv4Network, int] = {}        # prefix -> local label
        self._next_label = 16
        self.lfib: dict[int, tuple] = {}               # local label -> (prefix, out label | None, port, next hop)
        self.running = False
        self._timer = None
        self._advertised: dict = {}
        self.switched = 0

    # -- config ------------------------------------------------------------------------------
    def to_dict(self) -> dict | None:
        if not self.interfaces:
            return None
        return {"interfaces": list(self.interfaces), "router_id": self.router_id_iface}

    def apply(self, d: dict | None) -> None:
        d = d or {}
        self.interfaces = list(d.get("interfaces", []))
        self.router_id_iface = d.get("router_id")
        self.peers.clear()
        self.local.clear()
        self.lfib.clear()
        self._advertised.clear()
        self.ensure()

    def ensure(self) -> None:
        if self.interfaces and not self.running:
            self.running = True
            self.r.bind_udp(LDP_PORT, self._receive)
            self._timer = self.engine.schedule(200.0, self._tick)
        elif not self.interfaces and self.running:
            self.running = False
            self.r.unbind_udp(LDP_PORT)
            if self._timer is not None:
                self._timer.cancelled = True
            self.peers.clear()
            self.lfib.clear()

    def enabled_on(self, port) -> bool:
        return port.name in self.interfaces and port.ip is not None and port.is_up

    def lsr_id(self) -> str:
        if self.router_id_iface:
            p = self.r.get_port(self.router_id_iface)
            if p is not None and p.ip is not None:
                return str(p.ip.ip)
        loops = [p.ip.ip for p in self.r.ports.values() if p.is_loopback and p.ip is not None]
        ips = [p.ip.ip for p in self.r.ports.values() if p.ip is not None]
        return str(max(loops or ips)) if (loops or ips) else "0.0.0.0"

    # -- LDP ---------------------------------------------------------------------------------
    def _tick(self) -> None:
        if not self.running:
            return
        now = self.engine.now
        for lid, peer in list(self.peers.items()):
            if now - peer.last_seen > HOLD_MS or not peer.port.is_up:
                del self.peers[lid]
                self.r.mgmt.log(5, f"%LDP-5-NBRCHG: LDP Neighbor {lid}:0 (1) is DOWN (Discovery Hold Timer expired)")
        for p in self.r.ports.values():
            if self.enabled_on(p):
                self._send(p, ALL_ROUTERS, LdpMsg("hello", self.lsr_id()))
        self.refresh()
        self._timer = self.engine.schedule(float(HELLO_MS), self._tick)

    def _send(self, port, dst, msg) -> None:
        from ..packets import ETH_IPV4
        pkt = IPv4Packet(port.ip.ip, dst, 1, UdpDatagram(LDP_PORT, LDP_PORT, msg), PROTO_UDP, next(self.r._ip_ident))
        if dst == ALL_ROUTERS:
            self.r._send_frame(port, ALL_ROUTERS_MAC, ETH_IPV4, pkt)
        else:
            self.r.send_ip(pkt)

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, LdpMsg) or not self.enabled_on(port) or m.lsr_id == self.lsr_id():
            return
        now = self.engine.now
        peer = self.peers.get(m.lsr_id)
        if peer is None:
            if m.kind != "hello":
                return
            peer = self.peers[m.lsr_id] = LdpPeer(m.lsr_id, port, pkt.src, now, now, {pkt.src}, {})
            self.r.mgmt.log(5, f"%LDP-5-NBRCHG: LDP Neighbor {m.lsr_id}:0 (1) is UP")
            self._advertised.pop(m.lsr_id, None)
            self._send(port, pkt.src, LdpMsg("address", self.lsr_id(), tuple(
                str(p.ip.ip) for p in self.r.ports.values() if p.ip is not None)))
            self.refresh()
        peer.last_seen = now
        if m.kind == "address":
            peer.addresses |= {IPv4Address(a) for a in m.addresses}
            self._build_lfib()
        elif m.kind == "mapping":
            for pfx, lab in m.bindings:
                net = IPv4Network(pfx)
                if lab is None:
                    peer.bindings.pop(net, None)
                else:
                    peer.bindings[net] = int(lab)
            self._build_lfib()

    def refresh(self) -> None:
        """(Re)allocate local labels for the routing table and advertise changes to every peer."""
        if not self.running:
            return
        prefixes = {rt.network: rt for rt in self.r.routing_table() if rt.code != "L"}
        for net, rt in prefixes.items():
            if net not in self.local:
                if rt.code == "C":
                    self.local[net] = IMPLICIT_NULL
                else:
                    self.local[net] = self._next_label
                    self._next_label += 1
        for net in [n for n in self.local if n not in prefixes]:
            del self.local[net]
        for lid, peer in self.peers.items():
            adv = {str(n): lab for n, lab in self.local.items()}
            prev = self._advertised.get(lid, {})
            diff = [(p, lab) for p, lab in adv.items() if prev.get(p) != lab] + \
                   [(p, None) for p in prev if p not in adv]
            if diff:
                self._send(peer.port, peer.ip, LdpMsg("mapping", self.lsr_id(), bindings=tuple(sorted(
                    diff, key=lambda x: x[0]))))
                self._advertised[lid] = adv
        self._build_lfib()

    def peer_for(self, next_hop) -> LdpPeer | None:
        return next((p for p in self.peers.values() if next_hop in p.addresses), None)

    def _build_lfib(self) -> None:
        lfib = {}
        for rt in self.r.routing_table():
            lab = self.local.get(rt.network)
            if lab is None or lab == IMPLICIT_NULL or rt.next_hop is None or rt.port is None:
                continue
            peer = self.peer_for(rt.next_hop)
            out = peer.bindings.get(rt.network) if peer is not None else None
            lfib[lab] = (rt.network, out, rt.port, rt.next_hop)
        self.lfib = lfib

    # -- data plane -------------------------------------------------------------------------
    def push(self, pkt, port, next_hop) -> bool:
        """Ingress: label an IP packet whose next hop is an LDP peer. True when sent labelled."""
        if not self.running or not self.enabled_on(port) or next_hop is None:
            return False
        peer = self.peer_for(next_hop)
        if peer is None:
            return False
        best = None
        for net, lab in peer.bindings.items():
            if pkt.dst in net and (best is None or net.prefixlen > best[0].prefixlen):
                best = (net, lab)
        if best is None or best[1] == IMPLICIT_NULL:
            return False
        return self._send_labelled(port, next_hop, MplsPacket((best[1],), pkt.ttl, pkt))

    def receive(self, frame, port) -> None:
        m = frame.payload
        if not isinstance(m, MplsPacket) or not m.labels:
            return
        top = m.labels[0]
        ttl = m.ttl - 1
        if ttl <= 0:
            self._ttl_expired(m, port)
            return
        if "_te" in self.r.__dict__:
            from .te import te_switch
            if te_switch(self.r, m, ttl, port):
                return
        if not self.running:
            return
        entry = self.lfib.get(top)
        vrf = self.r.vrf_for_label(top) if entry is None and hasattr(self.r, "vrf_for_label") else None
        if vrf is not None and isinstance(m.inner, IPv4Packet):              # L3VPN egress: VPN label -> VRF
            with self.r.in_vrf(vrf):
                self.r.send_ip(replace(m.inner, ttl=ttl))
            return
        if entry is None:
            if len(m.labels) == 1 and isinstance(m.inner, IPv4Packet):      # explicit/unknown: route the payload
                self.r._handle_ip(replace(m.inner, ttl=ttl), port)
            return
        net, out, oport, nh = entry
        self.switched += 1
        if out is None or out == IMPLICIT_NULL:                              # penultimate hop: pop
            inner = replace(m.inner, ttl=ttl)
            if len(m.labels) > 1:
                self._send_labelled(oport, nh, MplsPacket(m.labels[1:], ttl, inner))
            else:
                self.r.send_ip(inner)
            return
        self._send_labelled(oport, nh, MplsPacket((out,) + m.labels[1:], ttl, m.inner))

    def receive_stack(self, mp, port) -> None:
        """Handle an inner label stack (after a TE tail end popped its label)."""
        from types import SimpleNamespace
        self.receive(SimpleNamespace(payload=replace(mp, ttl=mp.ttl + 1)), port)

    def _send_labelled(self, port, next_hop, mp) -> bool:
        from ..packets import EthernetFrame
        e = self.r.arp.lookup(next_hop, self.engine.now)
        if e is None or e.port is not port:
            self.r._arp_prime(port, next_hop)              # resolve for the next packets
            return False
        port.send(EthernetFrame(port.mac, e.mac, ETH_MPLS, mp))
        return True

    def _ttl_expired(self, m, port) -> None:
        from ..packets import ICMP_TIME_EXCEEDED
        inner = m.inner
        if isinstance(inner, IPv4Packet):
            self.r.send_icmp_error(inner, ICMP_TIME_EXCEEDED, 0, port)
