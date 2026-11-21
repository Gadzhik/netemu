"""File transfer (TFTP, FTP) and e-mail (SMTP, POP3) on the Server device.

* TFTP (UDP 69): write / read requests carry the whole file in one message (the simulation does not
  split files into 512-byte blocks).  No authentication, as in real TFTP.
* FTP (TCP 21): the same with a user name and password checked against the server's FTP users.
* SMTP (TCP 25) delivers a message to the mailbox of the recipient's user name (the part before @);
  POP3 (TCP 110) hands a user their mailbox after checking the password.
Routers use TFTP/FTP for ``copy running-config tftp:`` / ``copy tftp: running-config``; PCs use the
e-mail client on their Mail tab.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from ipaddress import IPv4Address

from ..stack.icmp import Job

TFTP_PORT, FTP_PORT, SMTP_PORT, POP3_PORT = 69, 21, 25, 110
_ids = itertools.count(1)
_sports = itertools.count(0)


@dataclass(frozen=True)
class FileMsg:
    proto: str                    # TFTP | FTP | SMTP | POP3
    op: str                       # put | get | ok | error | send | fetch | mail
    ident: int
    name: str = ""                # file name / mail recipient
    data: object = None           # file text / message dict / list of messages
    user: str = ""
    password: str = ""
    error: str = ""

    @property
    def label(self) -> str:
        return self.proto

    def summary(self) -> str:
        words = {("TFTP", "put"): f"Write Request, File: {self.name}", ("TFTP", "get"): f"Read Request, File: {self.name}",
                 ("FTP", "put"): f"STOR {self.name}", ("FTP", "get"): f"RETR {self.name}",
                 ("SMTP", "send"): f"MAIL TO:<{self.name}>", ("POP3", "fetch"): f"USER {self.user} / RETR"}
        if self.op == "ok":
            return f"{self.proto} {'Data / Acknowledgement' if self.proto == 'TFTP' else 'OK'}"
        if self.op == "error":
            return f"{self.proto} Error: {self.error}"
        return f"{self.proto} {words.get((self.proto, self.op), self.op)}"

    def layer_lines(self) -> list[str]:
        out = [self.summary()]
        if isinstance(self.data, str):
            out.append(f"Data: {len(self.data)} bytes")
        return out


@dataclass
class FileServer:
    tftp: bool = True
    ftp: bool = True
    mail: bool = False
    domain: str = "netemu.lab"
    files: dict = field(default_factory=dict)            # name -> text
    ftp_users: dict = field(default_factory=lambda: {"cisco": "cisco"})
    mail_users: dict = field(default_factory=dict)       # user -> password
    mailboxes: dict = field(default_factory=dict)        # user -> [message dict]

    def to_dict(self) -> dict:
        return {"tftp": self.tftp, "ftp": self.ftp, "mail": self.mail, "domain": self.domain,
                "files": dict(self.files), "ftp_users": dict(self.ftp_users), "mail_users": dict(self.mail_users),
                "mailboxes": {k: list(v) for k, v in self.mailboxes.items()}}

    @classmethod
    def from_dict(cls, d: dict | None) -> "FileServer":
        d = d or {}
        return cls(d.get("tftp", True), d.get("ftp", True), d.get("mail", False), d.get("domain", "netemu.lab"),
                   dict(d.get("files", {})), dict(d.get("ftp_users", {"cisco": "cisco"})),
                   dict(d.get("mail_users", {})), {k: list(v) for k, v in d.get("mailboxes", {}).items()})

    def handle(self, dev, pkt) -> None:
        m = pkt.payload.data
        if not isinstance(m, FileMsg) or m.op in ("ok", "error"):
            return
        reply = self._answer(m)
        if reply is None:
            return
        l4 = pkt.payload
        if m.proto == "TFTP":
            dev.send_udp(pkt.src, TFTP_PORT, l4.sport, reply, src=pkt.dst)
        else:
            dev.send_tcp(pkt.src, l4.dport, l4.sport, reply, "PSH,ACK", src=pkt.dst)
        dev.engine.emit("device_changed", dev)

    def _answer(self, m):
        ok = lambda data=None: FileMsg(m.proto, "ok", m.ident, m.name, data)
        err = lambda text: FileMsg(m.proto, "error", m.ident, m.name, error=text)
        if m.proto in ("TFTP", "FTP"):
            if (m.proto == "TFTP" and not self.tftp) or (m.proto == "FTP" and not self.ftp):
                return None
            if m.proto == "FTP" and self.ftp_users.get(m.user) != m.password:
                return err("530 Login incorrect")
            if m.op == "put":
                self.files[m.name] = str(m.data or "")
                return ok()
            if m.name not in self.files:
                return err("File not found" if m.proto == "TFTP" else "550 File not found")
            return ok(self.files[m.name])
        if not self.mail:
            return None
        if m.proto == "SMTP":
            rcpt = m.name.split("@")[0]
            if rcpt not in self.mail_users:
                return err("550 No such user here")
            self.mailboxes.setdefault(rcpt, []).append(dict(m.data or {}))
            return ok()
        if self.mail_users.get(m.user) != m.password:
            return err("-ERR authentication failed")
        msgs = self.mailboxes.pop(m.user, [])
        return ok(msgs)


class Transfer(Job):
    """One request/answer exchange with a file or mail server: on_done(ok, data or error text)."""

    TIMEOUT_MS = 3000.0

    def __init__(self, dev, proto: str, server, op: str, name: str, on_done, data=None, user="", password=""):
        super().__init__()
        self.dev, self.proto, self.server, self.op = dev, proto, IPv4Address(str(server)), op
        self.name, self.data, self.user, self.password, self.on_done = name, data, user, password, on_done
        self.ident = next(_ids)
        self.sport = 49000 + next(_sports) % 1000
        self._timer = None

    def start(self) -> "Transfer":
        port = {"TFTP": TFTP_PORT, "FTP": FTP_PORT, "SMTP": SMTP_PORT, "POP3": POP3_PORT}[self.proto]
        msg = FileMsg(self.proto, self.op, self.ident, self.name, self.data, self.user, self.password)
        if self.proto == "TFTP":
            self.dev.bind_udp(self.sport, self._receive)
            sent = self.dev.send_udp(self.server, self.sport, port, msg)
        else:
            self.dev.bind_tcp(self.sport, self._receive)
            sent = self.dev.send_tcp(self.server, self.sport, port, msg, "PSH,ACK")
        if not sent:
            self._finish(False, "no route to host")
            return self
        self._timer = self.dev.engine.schedule(self.TIMEOUT_MS, lambda: self._finish(False, "timed out"))
        return self

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if isinstance(m, FileMsg) and m.ident == self.ident and m.op in ("ok", "error"):
            self._finish(m.op == "ok", m.data if m.op == "ok" else m.error)

    def _finish(self, ok, data) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        (self.dev.unbind_udp if self.proto == "TFTP" else self.dev.unbind_tcp)(self.sport)
        self.on_done(ok, data)
        self.finish()

    def cancel(self) -> None:
        self._finish(False, "cancelled")


# ------------------------------------------------------------------------------------ mail client (hosts)
def send_mail(host, to: str, subject: str, body: str, on_done):
    """SMTP from the host's mail settings ({"address", "server", "user", "password"})."""
    m = getattr(host, "mail", None) or {}
    if not m.get("server"):
        on_done(False, "no mail server configured")
        return None
    msg = {"from": m.get("address", ""), "to": to, "subject": subject, "body": body}
    return Transfer(host, "SMTP", m["server"], "send", to, on_done, msg).start()


def fetch_mail(host, on_done):
    """POP3: download (and remove from the server) the messages of the host's mail user."""
    m = getattr(host, "mail", None) or {}
    if not m.get("server"):
        on_done(False, "no mail server configured")
        return None

    def done(ok, data):
        if ok:
            host.inbox = list(getattr(host, "inbox", []) or []) + list(data or [])
            host.engine.emit("device_changed", host)
        on_done(ok, data)
    return Transfer(host, "POP3", m["server"], "fetch", "", done, None, m.get("user", ""), m.get("password", "")).start()
