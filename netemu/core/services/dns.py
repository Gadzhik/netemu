"""DNS: A-record server and a resolver used by ping/tracert/nslookup/curl on hosts."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from ipaddress import IPv4Address

from ..addresses import is_valid_ip
from ..stack.icmp import Job

DNS_PORT = 53
_ids = itertools.count(0x1A2B)
_sports = itertools.count(49152)


@dataclass(frozen=True)
class DnsMessage:
    ident: int
    qname: str
    response: bool = False
    answer: IPv4Address | None = None
    rcode: str = "NOERROR"            # NOERROR | NXDOMAIN
    ttl: int | None = None            # seconds; None: a static record (shown as 86400, not cached by clients)
    label = "DNS"

    def summary(self) -> str:
        if not self.response:
            return f"Standard query 0x{self.ident:04x} A {self.qname}"
        if self.answer is None:
            return f"Standard query response 0x{self.ident:04x} No such name A {self.qname}"
        return f"Standard query response 0x{self.ident:04x} A {self.qname} A {self.answer}"

    def layer_lines(self) -> list[str]:
        lines = [f"Transaction ID: 0x{self.ident:04x}", f"Flags: {'response' if self.response else 'query'}",
                 f"Question: {self.qname} type A class IN"]
        if self.response:
            lines.append(f"Reply code: {self.rcode}")
            if self.answer is not None:
                lines.append(f"Answer: {self.qname} A {self.answer} (TTL {self.ttl or 86400})")
        return lines


class DnsServer:
    def __init__(self, owner, records: dict[str, str] | None = None, enabled: bool = True):
        self.dev = owner
        self.records = dict(records or {})
        self.enabled = enabled
        self.queries = 0

    def lookup(self, name: str) -> IPv4Address | None:
        name = name.lower().rstrip(".")
        ip = self.records.get(name)
        return IPv4Address(ip) if ip else None

    def ttl_of(self, name: str) -> int | None:
        return None

    def handle(self, pkt, port) -> None:
        if not self.enabled:
            return
        q = pkt.payload.data
        if not isinstance(q, DnsMessage) or q.response:
            return
        self.queries += 1
        ans = self.lookup(q.qname)
        resp = DnsMessage(q.ident, q.qname, True, ans, "NOERROR" if ans else "NXDOMAIN",
                          self.ttl_of(q.qname) if ans else None)
        self.dev.send_udp(pkt.src, DNS_PORT, pkt.payload.sport, resp, src=pkt.dst)


class Resolve(Job):
    """Resolve *name* through the host's DNS server; calls on_result(ip | None, error text)."""

    TIMEOUT_MS = 2000.0
    RETRIES = 2

    def __init__(self, host, name: str, on_result, server=None):
        super().__init__()
        self.host = host
        self.server = server
        self.name = name.rstrip(".")
        self.on_result = on_result
        self.ident = next(_ids) & 0xFFFF
        self.sport = 49152 + next(_sports) % 16000
        self.tries = 0
        self._timer = None

    def start(self) -> "Resolve":
        h = self.host
        if is_valid_ip(self.name):
            self._done(IPv4Address(self.name), "")
            return self
        if ":" in self.name:
            from ipaddress import IPv6Address
            try:
                self._done(IPv6Address(self.name), "")
            except ValueError:
                self._done(None, f"bad address {self.name}")
            return self
        hit = cached(h, self.name)
        if hit is not None:                          # still valid by the record's TTL
            self._done(hit, "")
            return self
        if self.server is None:
            self.server = getattr(h, "dns", None)
        if self.server is None:
            self._done(None, "no DNS server configured")
            return self
        h.bind_udp(self.sport, self._receive)
        self._query()
        return self

    def _query(self) -> None:
        if self.done:
            return
        self.tries += 1
        if self.tries > self.RETRIES:
            self._done(None, "DNS request timed out")
            return
        if not self.host.send_udp(self.server, self.sport, DNS_PORT, DnsMessage(self.ident, self.name)):
            self._done(None, "no route to the DNS server")
            return
        self._timer = self.host.engine.schedule(self.TIMEOUT_MS, self._query)

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, DnsMessage) or not m.response or m.ident != self.ident:
            return
        if m.answer and hasattr(self.host, "dns_cache"):
            self.host.dns_cache[self.name] = (m.answer, self.host.engine.now)
            if m.ttl:                                # records with a TTL are reused until it expires
                expiry = getattr(self.host, "dns_expiry", None)
                if expiry is not None:
                    expiry[self.name] = self.host.engine.now + m.ttl * 1000.0
        self._done(m.answer, "" if m.answer else f"*** {self.server} can't find {self.name}: Non-existent domain")

    def _done(self, ip, err) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_udp(self.sport)
        self.on_result(ip, err)
        self.finish()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_udp(self.sport)
        super().cancel()


def cached(host, name: str):
    """The cached address of *name* while its TTL lasts (like the Windows/Linux resolver cache), else None."""
    name = name.rstrip(".").lower()
    exp = getattr(host, "dns_expiry", None)
    cache = getattr(host, "dns_cache", None)
    if not exp or cache is None or name not in exp or name not in cache:
        return None
    if host.engine.now >= exp[name]:
        exp.pop(name, None)
        return None
    return cache[name][0]
