"""Stateful DHCPv6 (RFC 8415, simplified): Solicit -> Advertise -> Request -> Reply.

Clients send from their link-local address (UDP 546) to ff02::1:2 (UDP 547); a router with
``ipv6 dhcp server POOL`` / ``dhcpv6 server POOL`` on that interface leases the next free
address from the pool prefix.  The default gateway still comes from router advertisements."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from ipaddress import IPv6Address, IPv6Interface, IPv6Network

from ..stack.icmp import Job

ALL_DHCP = IPv6Address("ff02::1:2")
CLIENT_PORT, SERVER_PORT = 546, 547
_xids = itertools.count(0x1000)


@dataclass(frozen=True)
class Dhcp6Msg:
    kind: str                      # SOLICIT | ADVERTISE | REQUEST | REPLY
    xid: int
    duid: str                      # client MAC as identifier
    address: str | None = None
    dns: str | None = None
    server: str | None = None

    @property
    def label(self) -> str:
        return "DHCPv6"

    @property
    def name(self) -> str:
        return self.kind.capitalize()

    def layer_lines(self) -> list[str]:
        out = [f"Message type: {self.kind}", f"Transaction ID: 0x{self.xid:06x}", f"Client DUID: {self.duid}"]
        if self.address:
            out.append(f"IA_NA address: {self.address}")
        if self.dns:
            out.append(f"DNS recursive name server: {self.dns}")
        return out


@dataclass
class Dhcp6Pool:
    name: str
    prefix: IPv6Network | None = None
    dns: str | None = None
    bindings: dict = field(default_factory=dict)      # duid -> address

    def lease(self, duid: str) -> IPv6Interface | None:
        if self.prefix is None:
            return None
        if duid in self.bindings:
            return IPv6Interface(f"{self.bindings[duid]}/{self.prefix.prefixlen}")
        used = set(self.bindings.values())
        for i in range(0x10, 0x10000):
            ip = str(self.prefix.network_address + i)
            if ip not in used:
                self.bindings[duid] = ip
                return IPv6Interface(f"{ip}/{self.prefix.prefixlen}")
        return None

    def to_dict(self) -> dict:
        return {"name": self.name, "prefix": str(self.prefix) if self.prefix else None, "dns": self.dns}


class Dhcp6Server:
    def __init__(self, dev):
        self.dev = dev
        self.pools: dict[str, Dhcp6Pool] = {}
        self.ifaces: dict[str, str] = {}             # port name -> pool name

    def to_config(self) -> dict | None:
        if not self.pools and not self.ifaces:
            return None
        return {"pools": [p.to_dict() for p in self.pools.values()], "ifaces": dict(self.ifaces)}

    def apply_config(self, d: dict | None) -> None:
        self.pools, self.ifaces = {}, {}
        for p in (d or {}).get("pools", []):
            self.pools[p["name"]] = Dhcp6Pool(p["name"], IPv6Network(p["prefix"]) if p.get("prefix") else None, p.get("dns"))
        self.ifaces = dict((d or {}).get("ifaces", {}))

    def handle(self, pkt, port) -> None:
        from ..stack.ipv6 import IPv6Packet
        m = pkt.payload
        pool = self.pools.get(self.ifaces.get(port.name, ""))
        if pool is None or m.kind not in ("SOLICIT", "REQUEST"):
            return
        addr = pool.lease(m.duid)
        if addr is None:
            return
        ll = self.dev.link_local(port)
        kind = "ADVERTISE" if m.kind == "SOLICIT" else "REPLY"
        reply = Dhcp6Msg(kind, m.xid, m.duid, str(addr), pool.dns, str(ll.ip))
        self.dev.send_ip6(IPv6Packet(ll.ip, pkt.src, 64, reply, next_header=17), out_port=port)


class Dhcp6Client(Job):
    """Host side: Solicit, then Request the advertised address."""

    TIMEOUT_MS = 3000.0

    def __init__(self, host, on_result=None):
        super().__init__()
        self.host, self.on_result = host, on_result
        self.xid = next(_xids) & 0xFFFFFF
        self.duid = host.nic.mac.cisco()
        self._timer = None
        self.tries = 0

    def start(self) -> "Dhcp6Client":
        self.host._dhcp6 = self
        self._send("SOLICIT")
        return self

    def _send(self, kind, server=None, address=None):
        from ..stack.ipv6 import IPv6Packet, mcast_mac
        h = self.host
        ll = h.link_local(h.nic)
        if ll is None or not h.nic.is_up:
            self._done(None)
            return
        msg = Dhcp6Msg(kind, self.xid, self.duid, address)
        h._send6_on(h.nic, IPv6Packet(ll.ip, ALL_DHCP, 1, msg, next_header=17), mac=mcast_mac(ALL_DHCP))
        if self._timer is not None:
            self._timer.cancelled = True
        self._timer = h.engine.schedule(self.TIMEOUT_MS, self._timeout)

    def _timeout(self):
        self.tries += 1
        if self.tries >= 3:
            self._done(None)
        else:
            self._send("SOLICIT")

    def receive(self, pkt) -> None:
        m = pkt.payload
        if self.done or m.xid != self.xid:
            return
        if m.kind == "ADVERTISE":
            self._send("REQUEST", m.server, m.address)
        elif m.kind == "REPLY":
            self._done(m)

    def _done(self, m) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        h = self.host
        if m is not None:
            addr = IPv6Interface(m.address)
            h.nic.ipv6 = [a for a in h.nic.ipv6 if a.network != addr.network] + [addr]
            h.dhcp6_info = {"server": m.server, "dns": m.dns, "address": m.address}
            h.engine.emit("device_changed", h)
        if self.on_result:
            self.on_result(m)
        self.finish()
