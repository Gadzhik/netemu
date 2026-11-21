"""Packet capture: frames to bytes and libpcap files that Wireshark opens.

The simulator keeps packets as Python objects; for a capture file they are turned into real
wire formats: Ethernet II (+802.1Q), ARP, IPv4 (with header checksum), ICMP echo / errors,
UDP and TCP (ports, flags; the application message travels as text), OSPF header, IPv6 +
ICMPv6, MPLS label stacks.  Protocols with no simple wire form (IS-IS, CAPWAP, EAPOL, LACP ...)
use a local EtherType with the text summary as payload, so they still show up in Wireshark."""
from __future__ import annotations

import struct
import time

from .packets import (ETH_ARP, ETH_IPV4, ArpPacket, EthernetFrame, IcmpPacket, IPv4Packet, OspfPacket, TcpSegment,
                      UdpDatagram)

ETHERTYPES = {ETH_IPV4: 0x0800, ETH_ARP: 0x0806, "IPv6": 0x86DD, "MPLS": 0x8847, "EAPOL": 0x888E, "LACP": 0x8809}
LOCAL_ETHERTYPE = 0x88B5            # IEEE "local experimental" for the rest


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _mac(m) -> bytes:
    return int(m.value).to_bytes(6, "big")


def _text(obj) -> bytes:
    if obj is None:
        return b""
    s = obj.summary() if hasattr(obj, "summary") else str(obj)
    return s.encode("utf-8", "replace")[:1400]


def _l4(pkt) -> tuple[int, bytes]:
    p = pkt.payload
    if isinstance(p, IcmpPacket):
        body = b""
        if p.original is not None and isinstance(p.original, IPv4Packet):
            body = ipv4_bytes(p.original)[:28]
        elif p.size:
            body = bytes((i & 0xFF for i in range(int(p.size))))
        hdr = struct.pack("!BBHHH", p.type, p.code, 0, p.ident & 0xFFFF, p.seq & 0xFFFF)
        cs = _checksum(hdr + body)
        return 1, struct.pack("!BBHHH", p.type, p.code, cs, p.ident & 0xFFFF, p.seq & 0xFFFF) + body
    if isinstance(p, UdpDatagram):
        data = _text(p.data)
        return 17, struct.pack("!HHHH", p.sport & 0xFFFF, p.dport & 0xFFFF, 8 + len(data), 0) + data
    if isinstance(p, TcpSegment):
        flags = 0
        for name, bit in (("FIN", 1), ("SYN", 2), ("RST", 4), ("PSH", 8), ("ACK", 16)):
            if name in (p.flags or ""):
                flags |= bit
        data = _text(p.data)
        return 6, struct.pack("!HHIIBBHHH", p.sport & 0xFFFF, p.dport & 0xFFFF, 1, 1 if flags & 16 else 0,
                              5 << 4, flags, 65535, 0, 0) + data
    if isinstance(p, OspfPacket):
        body = _text(p)
        rid = int.from_bytes(bytes(int(x) for x in str(p.router_id).split(".")), "big") if str(p.router_id).count(".") == 3 else 0
        kind = {"hello": 1, "dbd": 2, "lsr": 3, "lsu": 4, "lsack": 5}.get(getattr(p, "kind", "hello"), 1)
        return 89, struct.pack("!BBHII", 2, kind, 24 + len(body), rid, 0) + b"\0" * 12 + body
    proto = getattr(pkt, "protocol", 0)
    return proto, _text(p)


def ipv4_bytes(pkt: IPv4Packet) -> bytes:
    proto, payload = _l4(pkt)
    total = 20 + len(payload)
    tos = (getattr(pkt, "dscp", 0) & 0x3F) << 2
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, tos, total, pkt.ident & 0xFFFF, 0, max(0, min(255, pkt.ttl)),
                      proto & 0xFF, 0, pkt.src.packed, pkt.dst.packed)
    cs = _checksum(hdr)
    return hdr[:10] + struct.pack("!H", cs) + hdr[12:] + payload


def ipv6_bytes(pkt) -> bytes:
    p = pkt.payload
    nh = getattr(pkt, "next_header", 58)
    if nh == 58 and hasattr(p, "type"):
        body = struct.pack("!BBHHH", p.type, getattr(p, "code", 0) or 0, 0, (getattr(p, "ident", 0) or 0) & 0xFFFF,
                           (getattr(p, "seq", 0) or 0) & 0xFFFF)
        target = getattr(p, "target", None)
        if target is not None:
            body += target.packed
    elif isinstance(p, (UdpDatagram, TcpSegment)):
        body = _l4(type("X", (), {"payload": p, "protocol": nh})())[1]
    else:
        body = _text(p)
    return struct.pack("!IHBB16s16s", 0x60000000, len(body), nh & 0xFF, max(0, min(255, pkt.hlim)),
                       pkt.src.packed, pkt.dst.packed) + body


def frame_bytes(frame: EthernetFrame) -> bytes:
    p = frame.payload
    et = ETHERTYPES.get(frame.ethertype, LOCAL_ETHERTYPE)
    if isinstance(p, ArpPacket):
        body = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1 if p.op == "request" else 2,
                           _mac(p.sender_mac), p.sender_ip.packed, _mac(p.target_mac), p.target_ip.packed)
    elif isinstance(p, IPv4Packet):
        body = ipv4_bytes(p)
    elif frame.ethertype == "IPv6" and hasattr(p, "hlim"):
        body = ipv6_bytes(p)
    elif frame.ethertype == "MPLS" and hasattr(p, "labels"):
        body = b""
        for i, lab in enumerate(p.labels):
            s = 1 if i == len(p.labels) - 1 else 0
            body += struct.pack("!I", (lab << 12) | (s << 8) | (p.ttl & 0xFF))
        if isinstance(p.inner, IPv4Packet):
            body += ipv4_bytes(p.inner)
    else:
        body = _text(p)
    head = _mac(frame.dst) + _mac(frame.src)
    if frame.vlan is not None:
        head += struct.pack("!HH", 0x8100, frame.vlan & 0x0FFF)
    out = head + struct.pack("!H", et) + body
    return out + b"\0" * max(0, 60 - len(out))           # pad to the Ethernet minimum


def write_pcap(path, records) -> int:
    """records: [(time in ms, EthernetFrame)].  Returns the number of packets written."""
    base = time.time()
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        n = 0
        for t_ms, frame in records:
            data = frame_bytes(frame)
            ts = base + t_ms / 1000.0
            sec, usec = int(ts), int((ts - int(ts)) * 1_000_000)
            f.write(struct.pack("<IIII", sec, usec, len(data), len(data)) + data)
            n += 1
    return n
