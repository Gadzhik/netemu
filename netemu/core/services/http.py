"""HTTP over a simplified TCP exchange: SYN, SYN/ACK, ACK+GET, response (+FIN), ACK."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from ..stack.icmp import Job
from .dns import Resolve

HTTP_PORT = 80
_ports = itertools.count(1)

DEFAULT_PAGE = ("<html><head><title>NetEmu</title></head><body>"
                "<h1>Welcome to NetEmu</h1><p>This page is served by {host}.</p></body></html>")


@dataclass(frozen=True)
class HttpMessage:
    request: bool
    method: str = "GET"
    path: str = "/"
    host: str = ""
    status: int = 200
    body: str = ""
    label = "HTTP"

    def summary(self) -> str:
        if self.request:
            return f"{self.method} {self.path} HTTP/1.1"
        return f"HTTP/1.1 {self.status} {'OK' if self.status == 200 else 'Not Found'}"

    def layer_lines(self) -> list[str]:
        if self.request:
            return [f"{self.method} {self.path} HTTP/1.1", f"Host: {self.host}", "User-Agent: NetEmu"]
        return [self.summary(), "Content-Type: text/html", f"Content-Length: {len(self.body)}"]


class HttpServer:
    def __init__(self, owner, pages: dict[str, str] | None = None, enabled: bool = True):
        self.dev = owner
        self.pages = dict(pages or {})
        self.enabled = enabled
        self.requests = 0

    def page(self, path: str) -> tuple[int, str]:
        key = path.lstrip("/") or "index.html"
        if key in self.pages:
            return 200, self.pages[key]
        if key == "index.html":
            return 200, DEFAULT_PAGE.format(host=self.dev.hostname)
        return 404, "<html><body><h1>404 Not Found</h1></body></html>"

    def handle(self, pkt, port) -> None:
        if not self.enabled:
            return
        seg = pkt.payload
        if "SYN" in seg.flags and "ACK" not in seg.flags:
            self.dev.send_tcp(pkt.src, HTTP_PORT, seg.sport, None, "SYN,ACK", src=pkt.dst)
        elif isinstance(seg.data, HttpMessage) and seg.data.request:
            self.requests += 1
            status, body = self.page(seg.data.path)
            self.dev.send_tcp(pkt.src, HTTP_PORT, seg.sport, HttpMessage(False, status=status, body=body),
                              "PSH,ACK,FIN", src=pkt.dst)


class HttpGet(Job):
    """Fetch http://host[/path]; on_result(status | None, body or error text)."""

    TIMEOUT_MS = 4000.0

    def __init__(self, host, url: str, on_result):
        super().__init__()
        self.host = host
        self.on_result = on_result
        u = url.strip()
        if u.lower().startswith("http://"):
            u = u[7:]
        name, _, path = u.partition("/")
        if name.startswith("[") and "]" in name:        # IPv6 literal
            name = name[1:name.index("]")]
        self.name = name
        self.path = "/" + path
        self.sport = 1025 + next(_ports) % 30000
        self.dst = None
        self._timer = None
        self._resolver = None

    def start(self) -> "HttpGet":
        if not self.name:
            self._done(None, "bad URL")
            return self
        self._resolver = Resolve(self.host, self.name, self._resolved).start()
        return self

    def _resolved(self, ip, err) -> None:
        if ip is None:
            self._done(None, err or f"could not resolve host: {self.name}")
            return
        self.dst = ip
        self.host.bind_tcp(self.sport, self._receive)
        if not self.host.send_tcp(ip, self.sport, HTTP_PORT, None, "SYN"):
            self._done(None, "no route to host")
            return
        self._timer = self.host.engine.schedule(self.TIMEOUT_MS, lambda: self._done(None, "Request Timeout"))

    def _receive(self, pkt, port) -> None:
        seg = pkt.payload
        if pkt.src != self.dst:
            return
        if "SYN" in seg.flags and "ACK" in seg.flags:
            self.host.send_tcp(self.dst, self.sport, HTTP_PORT,
                               HttpMessage(True, path=self.path, host=self.name), "PSH,ACK")
        elif isinstance(seg.data, HttpMessage) and not seg.data.request:
            self.host.send_tcp(self.dst, self.sport, HTTP_PORT, None, "ACK")
            self._done(seg.data.status, seg.data.body)

    def _done(self, status, body) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_tcp(self.sport)
        self.on_result(status, body)
        self.finish()

    def cancel(self) -> None:
        if self._resolver is not None and not self._resolver.done:
            self._resolver.cancel()
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_tcp(self.sport)
        super().cancel()
