"""DHCP (RFC 2131): server with pools and exclusions, client, relay agent (giaddr)."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import TYPE_CHECKING

from ..addresses import BROADCAST_MAC
from ..packets import ETH_IPV4, PROTO_UDP, IPv4Packet, UdpDatagram
from ..stack.icmp import Job

if TYPE_CHECKING:
    from ..port import Port

SERVER_PORT, CLIENT_PORT = 67, 68
ZERO = IPv4Address("0.0.0.0")
BCAST = IPv4Address("255.255.255.255")
_xid = itertools.count(0x3903F326)


@dataclass(frozen=True)
class DhcpMessage:
    kind: str                       # DISCOVER | OFFER | REQUEST | ACK | NAK | RELEASE
    xid: int
    chaddr: object                  # client MAC
    yiaddr: IPv4Address = ZERO      # "your" (offered) address
    siaddr: IPv4Address = ZERO      # server identifier
    giaddr: IPv4Address = ZERO      # relay agent address
    ciaddr: IPv4Address = ZERO
    mask: IPv4Address | None = None
    router: IPv4Address | None = None
    dns: IPv4Address | None = None
    domain: str = ""
    lease: int = 86400
    hostname: str = ""
    tftp: str | None = None         # option 150 (Cisco IP phones: call manager / TFTP server)
    option82: tuple | None = None   # relay agent information: (circuit-id, remote-id)
    label = "DHCP"

    def summary(self) -> str:
        extra = f" {self.yiaddr}" if self.kind in ("OFFER", "ACK") else ""
        return f"{self.kind}{extra} xid 0x{self.xid:08x}"

    def layer_lines(self) -> list[str]:
        lines = [f"Message type: DHCP {self.kind}", f"Transaction ID: 0x{self.xid:08x}",
                 f"Client MAC: {self.chaddr}", f"Your IP (yiaddr): {self.yiaddr}",
                 f"Relay agent (giaddr): {self.giaddr}"] + \
            ([f"Option 82: circuit-id {self.option82[0]}, remote-id {self.option82[1]}"] if self.option82 else [])
        if self.siaddr != ZERO:
            lines.append(f"Server identifier: {self.siaddr}")
        if self.mask:
            lines += [f"Subnet mask: {self.mask}", f"Router: {self.router or '-'}", f"DNS server: {self.dns or '-'}",
                      f"Lease time: {self.lease} s"]
        return lines


@dataclass
class DhcpPool:
    name: str
    network: str | None = None            # "192.168.1.0/24"
    gateway: str | None = None
    dns: str | None = None
    domain: str = ""
    start: str | None = None              # optional range inside the network
    end: str | None = None
    lease: int = 86400
    tftp: str | None = None               # option 150

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "DhcpPool":
        p = cls(d["name"])
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p

    def net(self) -> IPv4Network | None:
        return IPv4Network(self.network) if self.network else None

    def candidates(self):
        net = self.net()
        if net is None:
            return []
        lo = int(IPv4Address(self.start)) if self.start else int(net.network_address) + 1
        hi = int(IPv4Address(self.end)) if self.end else int(net.broadcast_address) - 1
        return (IPv4Address(i) for i in range(lo, hi + 1))


@dataclass
class Binding:
    ip: IPv4Address
    mac: object
    pool: str
    expires: float
    hostname: str = ""


@dataclass
class DhcpServerConfig:
    enabled: bool = True
    pools: list[DhcpPool] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    interfaces: list[str] | None = None     # None: every interface (Cisco); else only these

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "pools": [p.to_dict() for p in self.pools],
                "excluded": [list(e) for e in self.excluded],
                "interfaces": None if self.interfaces is None else list(self.interfaces)}

    @classmethod
    def from_dict(cls, d: dict) -> "DhcpServerConfig":
        return cls(d.get("enabled", True), [DhcpPool.from_dict(p) for p in d.get("pools", [])],
                   [tuple(e) for e in d.get("excluded", [])],
                   None if d.get("interfaces") is None else list(d["interfaces"]))

    def pool(self, name: str) -> DhcpPool | None:
        return next((p for p in self.pools if p.name == name), None)


class DhcpServer:
    """Runs on a server PC or a router; bound to UDP 67 on the owner's IP stack."""

    def __init__(self, owner, cfg: DhcpServerConfig | None = None):
        self.dev = owner
        self.cfg = cfg or DhcpServerConfig()
        self.bindings: dict[object, Binding] = {}
        self.offers: dict[object, IPv4Address] = {}

    def _excluded(self, ip: IPv4Address) -> bool:
        for a, b in self.cfg.excluded:
            if int(IPv4Address(a)) <= int(ip) <= int(IPv4Address(b or a)):
                return True
        return False

    def _pool_for(self, giaddr: IPv4Address, port: "Port") -> DhcpPool | None:
        key = giaddr if giaddr != ZERO else (port.ip.ip if port is not None and port.ip else None)
        for p in self.cfg.pools:
            n = p.net()
            if n is not None and key is not None and key in n:
                return p
        if giaddr == ZERO and not self.dev.ip_forwarding and self.cfg.pools:
            return self.cfg.pools[0]          # a server PC serves its single pool to its own segment
        return None

    def _allocate(self, pool: DhcpPool, mac) -> IPv4Address | None:
        b = self.bindings.get(mac)
        if b is not None and b.pool == pool.name:
            return b.ip
        if mac in self.offers:
            return self.offers[mac]
        used = {x.ip for x in self.bindings.values()} | set(self.offers.values())
        own = {p.ip.ip for p in self.dev.ports.values() if p.ip is not None}
        gw = IPv4Address(pool.gateway) if pool.gateway else None
        for ip in pool.candidates():
            if ip in used or ip in own or ip == gw or self._excluded(ip):
                continue
            return ip
        return None

    def _reply(self, req: DhcpMessage, kind: str, ip: IPv4Address, pool: DhcpPool, port: "Port") -> None:
        net = pool.net()
        me = port.ip.ip if port is not None and port.ip else self.dev.server_ip()
        msg = DhcpMessage(kind, req.xid, req.chaddr, ip, me, req.giaddr, ZERO, net.netmask,
                          IPv4Address(pool.gateway) if pool.gateway else None,
                          IPv4Address(pool.dns) if pool.dns else None, pool.domain, pool.lease, req.hostname,
                          tftp=pool.tftp)
        if req.giaddr != ZERO:
            self.dev.send_udp(req.giaddr, SERVER_PORT, SERVER_PORT, msg)        # back to the relay agent
        else:
            self.dev.broadcast_udp(port, SERVER_PORT, CLIENT_PORT, msg)

    def handle(self, pkt: IPv4Packet, port: "Port") -> None:
        if not self.cfg.enabled:
            return
        m: DhcpMessage = pkt.payload.data
        if not isinstance(m, DhcpMessage):
            return
        ifs = self.cfg.interfaces
        if ifs is not None and m.giaddr == ZERO and (port is None or port.name not in ifs):
            return
        if m.option82 and m.giaddr == ZERO and getattr(self.dev, "category", "") == "router" \
                and not (getattr(self.dev, "extra", {}) or {}).get("dhcp_trust_all") \
                and not getattr(port, "dhcp_relay_trusted", False):
            self.dropped82 = getattr(self, "dropped82", 0) + 1     # IOS: inconsistent relay information
            return
        if m.option82:
            self.option82 = getattr(self, "option82", {})
            self.option82[str(m.chaddr)] = m.option82
        pool = self._pool_for(m.giaddr, port)
        if m.kind == "DISCOVER":
            if pool is None:
                return
            ip = self._allocate(pool, m.chaddr)
            if ip is not None:
                self.offers[m.chaddr] = ip
                self._reply(m, "OFFER", ip, pool, port)
        elif m.kind == "REQUEST":
            if pool is None:
                return
            ip = self.offers.pop(m.chaddr, None) or m.ciaddr
            mine = m.siaddr in (ZERO,) or self.dev.owns_ip(m.siaddr) or m.siaddr == self.dev.server_ip()
            if not mine:
                return
            if ip is None or ip == ZERO or ip not in pool.net():
                self._reply(m, "NAK", ZERO, pool, port)
                return
            self.bindings[m.chaddr] = Binding(ip, m.chaddr, pool.name, self.dev.engine.now + pool.lease * 1000,
                                              m.hostname)
            self._reply(m, "ACK", ip, pool, port)
        elif m.kind == "RELEASE":
            self.bindings.pop(m.chaddr, None)


class DhcpClient(Job):
    """Obtains an address for a host (DISCOVER/OFFER/REQUEST/ACK, 3 attempts, then APIPA)."""

    ATTEMPTS = 3
    TIMEOUT_MS = 3000.0

    def __init__(self, host, on_result=None):
        super().__init__()
        self.host = host
        self.on_result = on_result
        self.xid = next(_xid)
        self.attempt = 0
        self.state = "INIT"
        self._timer = None
        self.server = None
        self.result = None

    def start(self) -> "DhcpClient":
        h = self.host
        h.bind_udp(CLIENT_PORT, self._receive)
        self._discover()
        return self

    def _send(self, msg: DhcpMessage) -> None:
        h = self.host
        pkt = IPv4Packet(ZERO, BCAST, 64, UdpDatagram(CLIENT_PORT, SERVER_PORT, msg), PROTO_UDP,
                         next(h._ip_ident))
        h._send_frame(h.nic, BROADCAST_MAC, ETH_IPV4, pkt)

    def _discover(self) -> None:
        if self.done:
            return
        self.attempt += 1
        if self.attempt > self.ATTEMPTS:
            self._fail()
            return
        self.state = "SELECTING"
        self._send(DhcpMessage("DISCOVER", self.xid, self.host.nic.mac, hostname=self.host.hostname))
        self._timer = self.host.engine.schedule(self.TIMEOUT_MS, self._discover)

    def _receive(self, pkt: IPv4Packet, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, DhcpMessage) or m.xid != self.xid or m.chaddr != self.host.nic.mac or self.done:
            return
        if m.kind == "OFFER" and self.state == "SELECTING":
            self.state = "REQUESTING"
            self.server = m.siaddr
            self._send(DhcpMessage("REQUEST", self.xid, self.host.nic.mac, siaddr=m.siaddr, ciaddr=m.yiaddr,
                                   hostname=self.host.hostname))
        elif m.kind == "ACK" and self.state == "REQUESTING":
            self._cancel_timer()
            plen = IPv4Network(f"0.0.0.0/{m.mask}").prefixlen
            self.result = (IPv4Interface(f"{m.yiaddr}/{plen}"), m.router, m.dns)
            self.host.dhcp_bound(self.result, m)
            self._finish(True)
        elif m.kind == "NAK":
            self.state = "INIT"

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancelled = True

    def _fail(self) -> None:
        self.host.dhcp_failed()
        self._finish(False)

    def _finish(self, ok: bool) -> None:
        self._cancel_timer()
        self.host.unbind_udp(CLIENT_PORT)
        if self.on_result:
            self.on_result(ok)
        self.finish()

    def cancel(self) -> None:
        self._cancel_timer()
        self.host.unbind_udp(CLIENT_PORT)
        super().cancel()
