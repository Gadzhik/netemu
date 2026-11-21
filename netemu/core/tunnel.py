"""GRE tunnels and IPsec protection.

A tunnel is a virtual routed port.  Packets routed into it are wrapped in GRE (IP protocol
47) and sent to the tunnel destination through the normal routing table; the far end strips
the outer header and handles the inner packet as if it arrived on its own tunnel port, so
routing protocols (OSPF hellos to 224.0.0.5) run over tunnels too.  With IPsec protection
the GRE packet is carried in ESP (protocol 50): its contents are hidden in the simulation and
both ends must share the pre-shared key, otherwise the receiver drops everything."""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address

from .port import Port

PROTO_GRE, PROTO_ESP = 47, 50


@dataclass(frozen=True)
class GrePacket:
    inner: object

    @property
    def label(self) -> str:
        return "GRE"

    def layer_lines(self) -> list[str]:
        p = self.inner
        return ["GRE header: protocol 0x0800 (IPv4)",
                f"Inner IPv4: {getattr(p, 'src', '?')} -> {getattr(p, 'dst', '?')} (protocol {getattr(p, 'protocol', '?')})"]


@dataclass(frozen=True)
class EspPacket:
    spi: int
    key_id: int                    # hash of the pre-shared key (stands in for the negotiated SA)
    inner: object                  # the GRE packet, "encrypted"

    @property
    def label(self) -> str:
        return "IPsec"

    def layer_lines(self) -> list[str]:
        return [f"ESP SPI: 0x{self.spi:08x}", "Sequence: 1", "Encrypted payload (AES-256), not readable"]


def key_id(key: str | None) -> int:
    import zlib
    return zlib.crc32((key or "").encode()) & 0xFFFFFFFF


class TunnelPort(Port):
    is_tunnel = True

    def __init__(self, device, name: str, short: str | None = None):
        super().__init__(device, name, short, admin_up=True)
        self.source: str | None = None             # interface name or IPv4 address
        self.destination: IPv4Address | None = None
        self.ipsec_key: str | None = None          # None: plain GRE
        self.counters = {"encaps": 0, "decaps": 0, "drops": 0}

    @property
    def link(self):
        return None

    @link.setter
    def link(self, value) -> None:
        pass

    @property
    def peer(self):
        return None

    def source_ip(self) -> IPv4Address | None:
        if self.source is None:
            return None
        try:
            return IPv4Address(self.source)
        except ValueError:
            p = self.device.get_port(self.source)
            return p.ip.ip if p is not None and p.ip is not None and p.is_up else None

    @property
    def is_up(self) -> bool:
        if not (self.admin_up and self.device.powered and self.destination is not None):
            return False
        if getattr(self, "_checking", False):
            return False                     # asked while resolving our own destination: not via us
        self._checking = True
        try:
            if self.source_ip() is None:
                return False
            r = self.device.route_lookup(self.destination)
            return r is not None and r[0] is not self and not getattr(r[0], "is_tunnel", False)
        finally:
            self._checking = False

    def status_text(self) -> str:
        if not self.admin_up:
            return "administratively down"
        return "up" if self.is_up else "down"

    def send(self, frame) -> bool:
        from .packets import IPv4Packet
        pkt = frame.payload
        if not self.is_up or not isinstance(pkt, IPv4Packet):
            self.drops += 1
            return False
        self.tx_frames += 1
        self.counters["encaps"] += 1
        payload, proto = GrePacket(pkt), PROTO_GRE
        if self.ipsec_key is not None:
            payload, proto = EspPacket(0x1000 + (key_id(self.ipsec_key) & 0xFFFF), key_id(self.ipsec_key), payload), PROTO_ESP
        dev = self.device
        outer = IPv4Packet(self.source_ip(), self.destination, 255, payload, proto, next(dev._ip_ident))
        return dev.send_ip(outer)


def tunnel_receive(dev, pkt) -> bool:
    """Router hook for IP protocols 47/50: decapsulate into the matching tunnel."""
    if pkt.protocol not in (PROTO_GRE, PROTO_ESP):
        return False
    for t in dev.ports.values():
        if not getattr(t, "is_tunnel", False) or t.destination != pkt.src or t.source_ip() != pkt.dst:
            continue
        payload = pkt.payload
        if pkt.protocol == PROTO_ESP:
            if t.ipsec_key is None or payload.key_id != key_id(t.ipsec_key):
                t.counters["drops"] += 1                   # no SA / wrong key: cannot decrypt
                return True
            payload = payload.inner
        elif t.ipsec_key is not None:
            t.counters["drops"] += 1                       # protected tunnel refuses clear GRE
            return True
        if not t.admin_up:
            return True
        t.rx_frames += 1
        t.counters["decaps"] += 1
        dev._handle_ip(payload.inner, t)
        return True
    return True
