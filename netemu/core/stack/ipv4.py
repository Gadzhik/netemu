"""IPv4 + ARP + ICMP stack shared by hosts and routers."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from ..addresses import BROADCAST_MAC, IPv4Address, IPv4Network, MacAddress
from ..packets import (ETH_ARP, ETH_IPV4, ICMP_DEST_UNREACHABLE, ICMP_ECHO_REPLY,
                       ICMP_ECHO_REQUEST, ICMP_TIME_EXCEEDED, PROTO_TCP, PROTO_UDP, ArpPacket,
                       EthernetFrame, IcmpPacket, IPv4Packet, TcpSegment, UdpDatagram)
from .arp import ARP_REQUEST_TIMEOUT_MS, ArpTable

if TYPE_CHECKING:
    from ..port import Port

ZERO_MAC_IP = IPv4Address("0.0.0.0")
LIMITED_BROADCAST = IPv4Address("255.255.255.255")


@dataclass
class Route:
    network: IPv4Network
    code: str                     # "C" connected, "L" local, "S" static
    port: "Port | None" = None    # exit interface (if known)
    next_hop: IPv4Address | None = None
    distance: int = 0
    metric: int = 0


class IpStack:
    """Mixin providing ARP resolution, IPv4 send/receive and ICMP."""

    default_ttl = 255
    ip_forwarding = False
    proxy_arp = False

    def _init_ip(self) -> None:
        self.arp = ArpTable()
        self._arp_pending: dict[tuple[int, IPv4Address], list[IPv4Packet]] = {}
        self._icmp_handlers: dict[int, Callable[[IPv4Packet], None]] = {}
        self._ip_ident = itertools.count(1)
        self._udp_handlers: dict[int, Callable] = {}
        self._tcp_handlers: dict[int, Callable] = {}

    # -- UDP / TCP sockets ---------------------------------------------------------
    def bind_udp(self, port: int, fn) -> None:
        self._udp_handlers[port] = fn

    def unbind_udp(self, port: int) -> None:
        self._udp_handlers.pop(port, None)

    def bind_tcp(self, port: int, fn) -> None:
        self._tcp_handlers[port] = fn

    def unbind_tcp(self, port: int) -> None:
        self._tcp_handlers.pop(port, None)

    def _l4_send(self, dst, payload, proto, src=None) -> bool:
        if ":" in str(dst) and hasattr(self, "l4_send6"):
            return self.l4_send6(dst, payload, proto, src)
        dst = IPv4Address(str(dst))
        if src is None:
            if self.owns_ip(dst):
                src = dst
            else:
                r = self.route_lookup(dst)
                if r is None or r[0].ip is None or not r[0].is_up:
                    return False
                src = r[0].ip.ip
        pkt = IPv4Packet(IPv4Address(str(src)), dst, self.default_ttl, payload, proto, next(self._ip_ident))
        return self.send_ip(pkt)

    def send_udp(self, dst, sport: int, dport: int, data, src=None) -> bool:
        return self._l4_send(dst, UdpDatagram(sport, dport, data), PROTO_UDP, src)

    def send_tcp(self, dst, sport: int, dport: int, data, flags: str = "PSH,ACK", src=None) -> bool:
        return self._l4_send(dst, TcpSegment(sport, dport, data, flags), PROTO_TCP, src)

    def broadcast_udp(self, port: "Port", sport: int, dport: int, data) -> None:
        src = port.ip.ip if port.ip is not None else ZERO_MAC_IP
        pkt = IPv4Packet(src, LIMITED_BROADCAST, 64, UdpDatagram(sport, dport, data), PROTO_UDP,
                         next(self._ip_ident))
        self._send_frame(port, BROADCAST_MAC, ETH_IPV4, pkt)

    def server_ip(self) -> IPv4Address:
        ips = [p.ip.ip for p in self.ports.values() if p.ip is not None]
        return ips[0] if ips else ZERO_MAC_IP

    # -- addressing --------------------------------------------------------
    def ip_ports(self) -> list["Port"]:
        return [p for p in self.ports.values() if p.ip is not None]

    def owns_ip(self, ip: IPv4Address) -> bool:
        return any(p.ip.ip == ip or any(s.ip == ip for s in p.ip2) for p in self.ip_ports())

    @staticmethod
    def port_ifaces(port) -> list:
        """Primary and secondary addresses of a port."""
        return ([port.ip] if port.ip is not None else []) + list(getattr(port, "ip2", []))

    def iface_for(self, port, ip):
        """The address of *port* whose subnet holds *ip* (primary when none does)."""
        return next((a for a in self.port_ifaces(port) if ip in a.network), port.ip)

    def port_for_own_ip(self, ip: IPv4Address) -> "Port | None":
        for p in self.ip_ports():
            if p.ip.ip == ip or any(s.ip == ip for s in p.ip2):
                return p
        return None

    def route_lookup(self, dst: IPv4Address) -> "tuple[Port, IPv4Address] | None":
        raise NotImplementedError

    def link_state_changed(self, port: "Port") -> None:
        if not port.is_up:
            self.arp.flush_port(port)

    # -- ICMP session registry --------------------------------------------
    def register_icmp(self, ident: int, handler: Callable[[IPv4Packet], None]) -> None:
        self._icmp_handlers[ident] = handler

    def unregister_icmp(self, ident: int) -> None:
        self._icmp_handlers.pop(ident, None)

    # -- receive path ------------------------------------------------------
    def accepts_mac(self, mac) -> bool:
        """Extra (multicast) MAC addresses this device listens to."""
        return False

    def joined_group(self, ip: IPv4Address) -> bool:
        return False

    def handle_l3_frame(self, frame: EthernetFrame, port: "Port") -> None:
        if frame.ethertype == "IPv6":
            if hasattr(self, "handle_ip6_frame"):
                self.handle_ip6_frame(frame, port)
            return
        if frame.dst != port.mac and not frame.dst.is_broadcast and not self.accepts_mac(frame.dst):
            return  # not for us
        if frame.ethertype == ETH_ARP:
            self._handle_arp(frame.payload, port)
        elif frame.ethertype == ETH_IPV4:
            self._handle_ip(frame.payload, port)

    def _handle_arp(self, arp: ArpPacket, port: "Port") -> None:
        if port.ip is None:
            return
        now = self.engine.now
        nets = self.port_ifaces(port)
        sender_in_subnet = any(arp.sender_ip in a.network for a in nets)
        if arp.op == "request":
            if any(arp.target_ip == a.ip for a in nets) or self.answers_arp_for(arp.target_ip, port):
                if sender_in_subnet:
                    self._learn(arp.sender_ip, arp.sender_mac, port)
                self._send_arp_reply(port, arp)
            elif self.proxy_arp and not any(arp.target_ip in a.network for a in nets):
                r = self.route_lookup(arp.target_ip)
                if r is not None and r[0] is not port:
                    if sender_in_subnet:
                        self._learn(arp.sender_ip, arp.sender_mac, port)
                    self._send_arp_reply(port, arp)
            elif sender_in_subnet and self.arp.lookup(arp.sender_ip, now):
                self._learn(arp.sender_ip, arp.sender_mac, port)
        elif arp.op == "reply":
            if any(arp.target_ip == a.ip for a in nets) or arp.target_mac == port.mac:
                self._learn(arp.sender_ip, arp.sender_mac, port)

    def _learn(self, ip: IPv4Address, mac, port: "Port") -> None:
        self.arp.learn(ip, mac, port, self.engine.now)
        queued = self._arp_pending.pop((id(port), ip), None)
        if queued:
            for pkt in queued:
                self._send_frame(port, mac, ETH_IPV4, pkt)

    def arp_mac_for(self, ip, port: "Port"):
        """MAC address to answer ARP for *ip* with (routers: HSRP/VRRP virtual MAC)."""
        return port.mac

    def _send_arp_reply(self, port: "Port", req: ArpPacket) -> None:
        mac = self.arp_mac_for(req.target_ip, port)
        reply = ArpPacket("reply", mac, req.target_ip, req.sender_mac, req.sender_ip)
        port.send(EthernetFrame(mac, req.sender_mac, ETH_ARP, reply))

    # -- filtering / translation hooks (routers override) -----------------------------
    def answers_arp_for(self, ip: IPv4Address, port: "Port") -> bool:
        return False

    def ingress_filter(self, pkt: IPv4Packet, port: "Port") -> IPv4Packet | None:
        return pkt

    def egress_filter(self, pkt: IPv4Packet, in_port: "Port", out_port: "Port") -> IPv4Packet | None:
        return pkt

    def _handle_ip(self, pkt: IPv4Packet, port: "Port") -> None:
        pkt = self.ingress_filter(pkt, port)
        if pkt is None:
            return
        if pkt.dst.is_multicast:
            if self.joined_group(pkt.dst):
                self._deliver_local(pkt, port)
            return
        if self.owns_ip(pkt.dst) or pkt.dst == LIMITED_BROADCAST or (
                port.ip is not None and pkt.dst == port.ip.network.broadcast_address):
            self._deliver_local(pkt, port)
            return
        if not self.ip_forwarding:
            return
        if pkt.ttl <= 1:
            self.send_icmp_error(pkt, ICMP_TIME_EXCEEDED, 0, port)
            return
        from dataclasses import replace as _replace
        fwd = _replace(pkt, ttl=pkt.ttl - 1)                  # keeps DSCP and identification
        if not self.send_ip(fwd, in_port=port):
            self.send_icmp_error(pkt, ICMP_DEST_UNREACHABLE, 0, port)

    def deliver_protocol(self, pkt: IPv4Packet, port: "Port") -> None:
        """Non-ICMP protocols: UDP/TCP sockets here; routers add OSPF/BGP."""
        l4 = pkt.payload
        if isinstance(l4, UdpDatagram):
            fn = self._udp_handlers.get(l4.dport)
        elif isinstance(l4, TcpSegment):
            fn = self._tcp_handlers.get(l4.dport)
        else:
            return
        if fn is not None:
            fn(pkt, port)

    def _deliver_local(self, pkt: IPv4Packet, port: "Port") -> None:
        icmp = pkt.payload
        if not isinstance(icmp, IcmpPacket):
            self.deliver_protocol(pkt, port)
            return
        if icmp.type == ICMP_ECHO_REQUEST:
            reply = IcmpPacket(ICMP_ECHO_REPLY, 0, icmp.ident, icmp.seq, icmp.size)
            src = pkt.dst if self.owns_ip(pkt.dst) else (port.ip.ip if port.ip else None)
            if src is not None:
                self.send_ip(IPv4Packet(src, pkt.src, self.default_ttl, reply,
                                        ident=next(self._ip_ident)))
            return
        ident = icmp.ident
        if icmp.type in (ICMP_TIME_EXCEEDED, ICMP_DEST_UNREACHABLE):
            orig = icmp.original
            if orig is None or not isinstance(orig.payload, IcmpPacket):
                return
            ident = orig.payload.ident
        handler = self._icmp_handlers.get(ident)
        if handler:
            handler(pkt)

    def send_icmp_error(self, orig: IPv4Packet, icmp_type: int, code: int,
                        in_port: "Port") -> None:
        inner = orig.payload
        if isinstance(inner, IcmpPacket) and inner.type not in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY):
            return  # never answer an ICMP error with an ICMP error
        if in_port.ip is None:
            return
        err = IcmpPacket(icmp_type, code, original=orig)
        self.send_ip(IPv4Packet(in_port.ip.ip, orig.src, self.default_ttl, err,
                                ident=next(self._ip_ident)))

    # -- send path ---------------------------------------------------------
    def send_ip(self, pkt: IPv4Packet, in_port: "Port | None" = None) -> bool:
        """Route and transmit *pkt* (forwarded from *in_port*, or local when None).

        Returns False when there is no route."""
        if self.owns_ip(pkt.dst):
            # Loopback to ourselves: deliver after a tiny delay.
            port = self.port_for_own_ip(pkt.dst)
            self.engine.schedule(0.0, self._deliver_local, pkt, port)
            return True
        r = self.route_lookup(pkt.dst)
        if r is None:
            return False
        port, next_hop = r
        if not port.is_up:
            return False
        if in_port is not None:
            pkt = self.egress_filter(pkt, in_port, port)
            if pkt is None:
                return True          # filtered or untranslatable: dropped silently
        if port.is_tunnel:                     # point-to-point: no ARP, the tunnel encapsulates
            self._send_frame(port, BROADCAST_MAC, ETH_IPV4, pkt)
            return True
        if next_hop == LIMITED_BROADCAST or (port.ip and next_hop == port.ip.network.broadcast_address):
            self._send_frame(port, BROADCAST_MAC, ETH_IPV4, pkt)
            return True
        entry = self.arp.lookup(next_hop, self.engine.now)
        if entry is not None and entry.port is port:
            self._send_frame(port, entry.mac, ETH_IPV4, pkt)
            return True
        if not getattr(port, "arp_broadcast", True):
            return True   # VRP sub-interface without "arp broadcast enable": no ARP, packet lost
        key = (id(port), next_hop)
        if key in self._arp_pending:
            self._arp_pending[key].append(pkt)
            return True
        self._arp_pending[key] = [pkt]
        src_if = self.iface_for(port, next_hop)
        req = ArpPacket("request", port.mac, src_if.ip if src_if is not None else ZERO_MAC_IP,
                        MacAddress(0), next_hop)
        self._send_frame(port, BROADCAST_MAC, ETH_ARP, req)
        self.engine.schedule(ARP_REQUEST_TIMEOUT_MS, self._arp_timeout, key)
        return True

    def _arp_timeout(self, key) -> None:
        self._arp_pending.pop(key, None)

    def originate(self, dst: IPv4Address, payload, ttl: int | None = None) -> IPv4Address | None:
        """Send a locally generated packet. Returns the source IP used, or None."""
        r = self.route_lookup(dst) if not self.owns_ip(dst) else (self.port_for_own_ip(dst), dst)
        if r is None or r[0].ip is None or not r[0].is_up:
            return None
        src = self.iface_for(r[0], r[1] if r[1] is not None else dst).ip
        pkt = IPv4Packet(src, dst, self.default_ttl if ttl is None else ttl, payload,
                         ident=next(self._ip_ident))
        return src if self.send_ip(pkt) else None

    def _send_frame(self, port: "Port", dst_mac, ethertype: str, payload) -> None:
        port.send(EthernetFrame(port.mac, dst_mac, ethertype, payload))
