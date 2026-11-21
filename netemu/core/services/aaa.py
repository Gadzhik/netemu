"""AAA: RADIUS and TACACS+ servers, the client side used by routers/switches, and 802.1X.

* Server device: RADIUS (UDP 1812) and TACACS+ (TCP 49) services with a shared secret and a user
  list (Services tab).  A request with the wrong secret is ignored (as a real server drops it).
* Clients: ``AuthJob`` asks the configured servers one by one (1.5 s timeout each) and reports
  True (accept), False (reject) or None (no server answered) so the caller can fall back to the
  next method of its method list ("group tacacs+ local").
* 802.1X: a switch port with ``authentication port-control auto`` passes only EAPOL until the
  supplicant (a PC with 802.1X credentials) is accepted by the RADIUS server.
"""
from __future__ import annotations

import itertools
import zlib
from dataclasses import dataclass, field
from ipaddress import IPv4Address

from ..stack.icmp import Job

RADIUS_PORT, TACACS_PORT = 1812, 49
EAPOL_MAC = 0x0180C2000003
_ids = itertools.count(1)
_sports = itertools.count(0)


def key_id(secret: str | None) -> int:
    return zlib.crc32((secret or "").encode()) & 0xFFFFFFFF


@dataclass(frozen=True)
class AuthMsg:
    proto: str                     # RADIUS | TACACS+
    kind: str                      # request | accept | reject
    ident: int
    user: str = ""
    password: str = ""
    key: int = 0
    purpose: str = "login"         # login | dot1x

    @property
    def label(self) -> str:
        return self.proto

    def summary(self) -> str:
        names = {"request": "Access-Request" if self.proto == "RADIUS" else "Authentication START",
                 "accept": "Access-Accept" if self.proto == "RADIUS" else "Authentication PASS",
                 "reject": "Access-Reject" if self.proto == "RADIUS" else "Authentication FAIL"}
        return f"{self.proto} {names[self.kind]} id={self.ident}" + (f" user={self.user}" if self.user else "")

    def layer_lines(self) -> list[str]:
        out = [f"Code: {self.summary().split(' ', 1)[1]}", f"Identifier: {self.ident}"]
        if self.kind == "request":
            out += [f"User-Name: {self.user}", "User-Password: (encrypted with the shared secret)",
                    f"Service: {'Framed (802.1X)' if self.purpose == 'dot1x' else 'Login'}"]
        return out


@dataclass(frozen=True)
class EapolMsg:
    kind: str                      # start | response | success | failure
    user: str = ""
    password: str = ""
    label = "EAPOL"

    def summary(self) -> str:
        return {"start": "EAPOL-Start", "response": f"EAP-Response/Identity {self.user}", "success": "EAP-Success",
                "failure": "EAP-Failure"}[self.kind]

    def layer_lines(self) -> list[str]:
        return ["Version: 802.1X-2004", f"Type: {self.summary()}"]


# ------------------------------------------------------------------------------------ server
@dataclass
class AaaServer:
    radius: bool = False
    tacacs: bool = False
    secret: str = "netemu"
    users: dict = field(default_factory=dict)          # user -> password
    log: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"radius": self.radius, "tacacs": self.tacacs, "secret": self.secret, "users": dict(self.users)}

    @classmethod
    def from_dict(cls, d: dict | None) -> "AaaServer":
        d = d or {}
        return cls(bool(d.get("radius")), bool(d.get("tacacs")), d.get("secret", "netemu"), dict(d.get("users", {})))

    def handle(self, dev, pkt) -> None:
        m = pkt.payload.data
        if not isinstance(m, AuthMsg) or m.kind != "request":
            return
        if (m.proto == "RADIUS" and not self.radius) or (m.proto == "TACACS+" and not self.tacacs):
            return
        if m.key != key_id(self.secret):
            self.log.append(f"{m.proto}: request from {pkt.src} dropped (wrong shared secret)")
            return
        ok = m.user in self.users and self.users[m.user] == m.password
        self.log.append(f"{m.proto}: {m.user} from {pkt.src} {'accepted' if ok else 'rejected'}")
        reply = AuthMsg(m.proto, "accept" if ok else "reject", m.ident)
        l4 = pkt.payload
        if m.proto == "RADIUS":
            dev.send_udp(pkt.src, RADIUS_PORT, l4.sport, reply, src=pkt.dst)
        else:
            dev.send_tcp(pkt.src, TACACS_PORT, l4.sport, reply, "PSH,ACK", src=pkt.dst)
        dev.engine.emit("device_changed", dev)


# ------------------------------------------------------------------------------------ client
class AuthJob(Job):
    """Try the servers in order: on_done(True | False | None)."""

    TIMEOUT_MS = 1500.0

    def __init__(self, dev, servers: list, user: str, password: str, on_done, purpose: str = "login"):
        super().__init__()
        self.dev, self.servers = dev, list(servers)        # [(proto, ip, secret)]
        self.user, self.password, self.on_done, self.purpose = user, password, on_done, purpose
        self.sport = 52000 + next(_sports) % 8000
        self.ident = 0
        self.i = -1
        self._timer = None

    def start(self) -> "AuthJob":
        self.dev.bind_udp(self.sport, self._receive)
        self.dev.bind_tcp(self.sport, self._receive)
        self._next()
        return self

    def _next(self) -> None:
        if self.done:
            return
        self.i += 1
        if self.i >= len(self.servers):
            self._finish(None)
            return
        proto, ip, secret = self.servers[self.i]
        self.ident = next(_ids) & 0xFF
        msg = AuthMsg(proto, "request", self.ident, self.user, self.password, key_id(secret), self.purpose)
        sent = (self.dev.send_udp(IPv4Address(ip), self.sport, RADIUS_PORT, msg) if proto == "RADIUS" else
                self.dev.send_tcp(IPv4Address(ip), self.sport, TACACS_PORT, msg, "PSH,ACK"))
        if not sent:
            self._next()
            return
        self._timer = self.dev.engine.schedule(self.TIMEOUT_MS, self._next)

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if isinstance(m, AuthMsg) and m.kind in ("accept", "reject") and m.ident == self.ident:
            self._finish(m.kind == "accept")

    def _finish(self, result) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        self.dev.unbind_udp(self.sport)
        self.dev.unbind_tcp(self.sport)
        self.on_done(result)
        self.finish()


def servers_for(dev, group: str) -> list:
    """[(proto, ip, secret)] of the device's server group 'radius' or 'tacacs+'."""
    aaa = (getattr(dev, "extra", {}) or {}).get("aaa", {})
    proto = "RADIUS" if group == "radius" else "TACACS+"
    return [(proto, s["ip"], s.get("key", "")) for s in aaa.get(group, {}).values() if s.get("ip")]


# ------------------------------------------------------------------------------------ 802.1X authenticator
def dot1x_receive(switch, frame, port) -> None:
    """EAPOL from a supplicant on an 802.1X port: relay the credentials to RADIUS."""
    from ..packets import EthernetFrame
    m = frame.payload
    if not isinstance(m, EapolMsg):
        return
    if m.kind == "start":
        return                                             # identity follows in the response
    if m.kind != "response":
        return
    servers = servers_for(switch, "radius")

    def done(ok):
        port.dot1x_ok = bool(ok)
        port.dot1x_user = m.user
        reply = EapolMsg("success" if ok else "failure")
        port.send(EthernetFrame(port.mac, frame.src, "EAPOL", reply))
        switch.console(f"%DOT1X-5-{'SUCCESS' if ok else 'FAIL'}: Authentication {'successful' if ok else 'failed'} "
                       f"for client ({frame.src.cisco()}) on Interface {port.short}")
        switch.mgmt.log(5, f"%DOT1X-5-{'SUCCESS' if ok else 'FAIL'}: Authentication for {m.user} on {port.short}")
        switch.engine.emit("device_changed", switch)
    if not servers:
        done(False)
        return
    AuthJob(switch, servers, m.user, m.password, done, purpose="dot1x").start()
