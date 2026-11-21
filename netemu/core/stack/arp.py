"""ARP cache."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..addresses import IPv4Address, MacAddress

if TYPE_CHECKING:
    from ..port import Port

ARP_ENTRY_TTL_MS = 4 * 60 * 60 * 1000  # 4 hours, Cisco default
ARP_REQUEST_TIMEOUT_MS = 2000.0


@dataclass
class ArpEntry:
    ip: IPv4Address
    mac: MacAddress
    port: "Port"
    learned_at: float
    static: bool = False


class ArpTable:
    def __init__(self, ttl: float = ARP_ENTRY_TTL_MS):
        self.ttl = ttl
        self._entries: dict[IPv4Address, ArpEntry] = {}

    def lookup(self, ip: IPv4Address, now: float) -> ArpEntry | None:
        e = self._entries.get(ip)
        if e and not e.static and now - e.learned_at > self.ttl:
            del self._entries[ip]
            return None
        return e

    def learn(self, ip: IPv4Address, mac: MacAddress, port: "Port", now: float) -> None:
        self._entries[ip] = ArpEntry(ip, mac, port, now)

    def flush_port(self, port: "Port") -> None:
        for ip in [ip for ip, e in self._entries.items() if e.port is port]:
            del self._entries[ip]

    def clear(self) -> None:
        self._entries.clear()

    def entries(self, now: float) -> list[ArpEntry]:
        for ip in list(self._entries):
            self.lookup(ip, now)
        return sorted(self._entries.values(), key=lambda e: int(e.ip))

    def __len__(self) -> int:
        return len(self._entries)
