"""Remote CLI access between devices: Telnet (TCP 23) and SSH (TCP 22).

A client command (``telnet 10.0.0.1``, ``ssh -l admin 10.0.0.1``, ...) opens a TCP
connection.  When the server accepts it, a :class:`RemoteSession` is attached to the
client's CLI session: from then on the terminal shows the remote prompt and every line
travels to the server in a TCP segment, is executed by a CLI session on the server and
the output comes back the same way.  Telnet carries everything in clear text (the
password is visible in the simulation's packet details); SSH segments are shown as
encrypted.

Who may log in is decided by the server's ``device.access`` settings, which every vendor
CLI edits with its own commands (``line vty`` / ``user-interface vty`` / ``system services``
/ ``/ip service`` / ``enable ssh2`` / ``systemctl start ssh``)."""
from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass

from ..addresses import IPv4Address
from ..stack.icmp import Job

PORTS = {"ssh": 22, "telnet": 23}
CONNECT_TIMEOUT_MS = 5000.0
REPLY_TIMEOUT_MS = 30000.0
_sports = itertools.count(0)

EXIT_WORDS = ("exit", "logout", "quit", "/quit", "bye")
# client port -> server-side connection.  Only used for '?' and Tab on a remote prompt, which the
# terminal needs synchronously (real terminals send keystrokes; here the answer is computed in place).
_REGISTRY: dict[int, "_Conn"] = {}


@dataclass(frozen=True)
class RemoteMsg:
    kind: str                 # hello, line, out, done, close, intr
    proto: str                # ssh | telnet
    text: str = ""
    user: str = ""
    prompt: str = ""
    secret: bool = False

    @property
    def label(self) -> str:
        return "SSH" if self.proto == "ssh" else "TELNET"

    def summary(self) -> str:
        if self.proto == "ssh":
            return f"Encrypted packet (len={len(self.text) + len(self.prompt) + 32})"
        shown = (self.text or self.prompt).strip().replace("\n", " ")
        return (shown[:40] + "…") if len(shown) > 40 else shown

    def layer_lines(self) -> list[str]:
        if self.proto == "ssh":
            return ["SSH Protocol 2.0", f"Encrypted packet (len={len(self.text) + len(self.prompt) + 32})",
                    "Contents are not readable without the session keys"]
        out = ["Telnet (clear text)"]
        for t in (self.text, self.prompt):
            for line in t.splitlines()[:6]:
                out.append(f"Data: {line}")
        return out


# -- server-side access settings -------------------------------------------------------------
def default_access(device) -> dict:
    v, cat = getattr(device, "vendor", "generic"), device.category
    if cat in ("pc", "server"):
        linux = getattr(device, "os", "windows") == "linux"
        return {"telnet": False, "ssh": linux and cat == "server", "users": {"user": "netemu"} if linux else {},
                "login": "local", "rsa": True}
    if v == "cisco":
        return {"telnet": True, "ssh": True, "rsa": False, "domain": None, "login": "line", "vty_password": None,
                "users": {}, "enable_secret": None}
    if v == "huawei":
        return {"telnet": False, "ssh": False, "login": "aaa", "vty_password": None, "users": {}, "services": {},
                "rsa": True, "protocols": "all"}
    if v == "juniper":
        return {"telnet": False, "ssh": False, "login": "local", "users": {}, "rsa": True}
    if v == "extreme":
        return {"telnet": True, "ssh": False, "login": "local", "users": {"admin": ""}, "rsa": True}
    if v == "mikrotik":
        return {"telnet": True, "ssh": True, "login": "local", "users": {"admin": ""}, "rsa": True}
    return {"telnet": False, "ssh": False, "login": "local", "users": {}, "rsa": True}


def access(device) -> dict:
    a = getattr(device, "access", None)
    if a is None:
        a = device.access = default_access(device)
    return a


def access_to_config(device) -> dict | None:
    a = access(device)
    return None if a == default_access(device) else copy.deepcopy(a)


def access_from_config(device, d: dict | None) -> None:
    device.access = default_access(device)
    if d:
        device.access.update(copy.deepcopy(d))


def service_state(device, proto: str) -> str | None:
    """None when *proto* is accepted, otherwise the reason it is refused ('refused' = no listener)."""
    a = access(device)
    if not a.get(proto):
        return "refused"
    if proto == "ssh" and not a.get("rsa", True):
        return "refused"
    if getattr(device, "vendor", "") == "huawei" and a.get("protocols", "all") not in ("all", proto):
        return "refused"
    return None


# -- server ------------------------------------------------------------------------------------
class _Conn:
    """One accepted connection on the server: login state machine, then a CLI session."""

    def __init__(self, server: "RemoteServer", client_ip, client_port: int, proto: str, user: str):
        self.server, self.dev = server, server.dev
        self.client_ip, self.client_port, self.proto = client_ip, client_port, proto
        self.user = user
        self.tries = 0
        self.session = None
        self.job = None
        self.state = "user"
        self.password_needed = None

    # -- messages ------------------------------------------------------------------------
    def send(self, kind, text="", prompt="", secret=False):
        self.dev.send_tcp(self.client_ip, PORTS[self.proto], self.client_port,
                          RemoteMsg(kind, self.proto, text=text, prompt=prompt, secret=secret))

    def done(self, text=""):
        if self.session is None:
            return
        s = self.session.active()
        self.send("done", text, s.display_prompt(), s.secret_input())

    def close(self, text=""):
        self.send("close", text)
        self.server.conns.pop((self.client_ip, self.client_port), None)
        _REGISTRY.pop(self.client_port, None)

    # -- login -----------------------------------------------------------------------------
    def start(self):
        a = access(self.dev)
        dev = self.dev
        banner = ""
        if dev.category in ("pc", "server") and getattr(dev, "os", "") == "linux":
            banner = "Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.0-45-generic x86_64)\n"
        if self.proto == "ssh":
            if not self.user:
                self.close("Permission denied (publickey,password).")
                return
            self.state = "password"
            self.send("done", "", f"{self.user}@{self.client_server_ip()}'s password: " if dev.vendor in ("generic", "linux")
                      else "Password: ", True)
            self._banner = banner
            return
        self._banner = banner
        login = a.get("login")
        if dev.vendor == "cisco" and login == "line":
            if not a.get("vty_password"):
                self.close("\nPassword required, but none set\n")
                return
            self.state = "password"
            self.send("done", "\nUser Access Verification\n", "Password: ", True)
            return
        if dev.vendor == "cisco" and login == "none":
            self.open_shell()
            return
        if dev.vendor == "huawei" and login == "password":
            if not a.get("vty_password"):
                self.close("Error: The password is not configured.\n")
                return
            self.state = "password"
            self.send("done", "Login authentication\n", "Password:", True)
            return
        if dev.vendor == "huawei" and login == "none":
            self.open_shell()
            return
        prompt = {"cisco": "Username: ", "huawei": "Username:", "juniper": "login: ", "mikrotik": "Login: ",
                  "extreme": "login: "}.get(dev.vendor, f"{dev.hostname} login: ")
        lead = {"cisco": "\nUser Access Verification\n", "huawei": "Login authentication\n"}.get(dev.vendor, "")
        self.state = "user"
        self.send("done", lead, prompt, False)

    def client_server_ip(self):
        return self.server.last_dst or ""

    def check(self, user, password) -> bool:
        a = access(self.dev)
        d = self.dev
        if d.vendor == "cisco" and a.get("login") == "line" and self.proto == "telnet":
            return password == a.get("vty_password")
        if d.vendor == "huawei" and a.get("login") == "password" and self.proto == "telnet":
            return password == a.get("vty_password")
        users = a.get("users", {})
        if user not in users or users[user] != password:
            return False
        if d.vendor == "huawei":
            return self.proto in a.get("services", {}).get(user, [])
        return True

    def feed(self, line: str):
        a = access(self.dev)
        if self.state == "user":
            self.user = line.strip()
            self.state = "password"
            self.send("done", "", "Password: " if self.dev.vendor != "huawei" else "Password:", True)
            return
        if self.state == "password":
            if self.check(self.user, line):
                self.open_shell()
                return
            self.tries += 1
            if self.tries >= 3:
                self.close({"cisco": "% Bad passwords\n", "huawei": "Error: Failed to log in.\n"}.get(
                    self.dev.vendor, "Permission denied, please try again.\nConnection closed.\n"))
                return
            if self.proto == "ssh" or (self.dev.vendor == "cisco" and a.get("login") == "line") \
                    or (self.dev.vendor == "huawei" and a.get("login") == "password"):
                msg = {"cisco": "% Login invalid\n" if a.get("login") != "line" else "% Bad passwords\n",
                       "huawei": "Error: Authentication fail\n"}.get(self.dev.vendor, "Permission denied, please try again.\n")
                prompt = ("Password: " if self.dev.vendor != "huawei" else "Password:")
                if self.proto == "ssh" and self.dev.vendor in ("generic", "linux"):
                    prompt = f"{self.user}@{self.client_server_ip()}'s password: "
                self.send("done", msg, prompt, True)
                return
            self.state = "user"
            msg = {"cisco": "% Login invalid\n\n", "huawei": "Error: Local authentication is rejected.\n"}.get(
                self.dev.vendor, "Login incorrect\n")
            prompt = {"cisco": "Username: ", "huawei": "Username:", "juniper": "login: ", "mikrotik": "Login: ",
                      "extreme": "login: "}.get(self.dev.vendor, f"{self.dev.hostname} login: ")
            self.send("done", msg, prompt, False)
            return
        self.run_line(line)

    def open_shell(self):
        self.state = "shell"
        s = self.dev.create_cli()
        s.vty = True
        s.vty_user = self.user
        if getattr(self.dev, "os", "") == "linux":
            s.user_name = self.user or "user"
        self.session = s
        text = getattr(self, "_banner", "")
        b = s.banner() if self.dev.vendor in ("mikrotik", "juniper", "extreme") else ""
        if self.dev.vendor == "juniper":
            b = "--- JUNOS 21.4R3.15 Kernel 64-bit\n"
        if self.dev.vendor == "huawei":
            b = "Info: The max number of VTY users is 5, the number of current VTY users online is 1.\n"
        self.done(text + b)

    # -- shell -------------------------------------------------------------------------------
    def at_top(self) -> bool:
        s = self.session.active()
        if s is not self.session:
            return False
        mode = getattr(s, "mode", None)
        if getattr(self.dev, "os", "") == "linux":
            return not getattr(s, "root", False)
        return mode in (None, "user", "priv", "oper")

    def run_line(self, line: str):
        s = self.session.active()
        if s.pending is not None:
            out = []
            res = s.answer(line, out.append)
            if res is not None and not res.done:
                self._wait(res, out)
                return
            self.done("".join(out))
            return
        if line.strip().lower() in EXIT_WORDS and self.at_top():
            self.close("")
            return
        out = []

        def writer(text):
            if self.job is not None:
                self.send("out", text)
            else:
                out.append(text)
        res = s.execute(line, writer)
        if res is not None and not res.done:
            self._wait(res, out)
            return
        self.done("".join(out))

    def _wait(self, job, out):
        self.job = job
        if out:
            self.send("out", "".join(out))

        def finished():
            self.job = None
            self.done("")
        job.on_done(finished)

    def interrupt(self):
        if self.session is not None:
            self.session.active().interrupt()


class RemoteServer:
    """Listens on TCP 22/23 of one device."""

    def __init__(self, dev):
        self.dev = dev
        self.conns: dict[tuple, _Conn] = {}
        self.last_dst = None
        for proto, port in PORTS.items():
            dev.bind_tcp(port, lambda pkt, p, proto=proto: self.receive(pkt, p, proto))

    def receive(self, pkt, port, proto):
        seg = pkt.payload
        key = (pkt.src, seg.sport)
        self.last_dst = pkt.dst
        if "SYN" in seg.flags and "ACK" not in seg.flags:
            if service_state(self.dev, proto) is not None:
                self.dev.send_tcp(pkt.src, seg.dport, seg.sport, None, "RST,ACK")
                return
            self.dev.send_tcp(pkt.src, seg.dport, seg.sport, None, "SYN,ACK")
            return
        m = seg.data
        if not isinstance(m, RemoteMsg):
            return
        conn = self.conns.get(key)
        if m.kind == "hello":
            conn = self.conns[key] = _Conn(self, pkt.src, seg.sport, proto, m.user)
            _REGISTRY[seg.sport] = conn
            conn.start()
        elif conn is None:
            self.dev.send_tcp(pkt.src, seg.dport, seg.sport, None, "RST,ACK")
        elif m.kind == "line":
            conn.feed(m.text)
        elif m.kind == "intr":
            conn.interrupt()
        elif m.kind == "close":
            self.conns.pop(key, None)
            _REGISTRY.pop(seg.sport, None)


def install(dev) -> None:
    if hasattr(dev, "bind_tcp") and getattr(dev, "remote_server", None) is None:
        dev.remote_server = RemoteServer(dev)


# -- client --------------------------------------------------------------------------------------
class RemoteSession:
    """Client end of a connection; attached to the local CLI session while open."""

    def __init__(self, local, dst: IPv4Address, proto: str, sport: int, style: str):
        self.local, self.dev = local, local.device
        self.dst, self.proto, self.sport = dst, proto, sport
        self.style = style            # client flavour for the closing message
        self.open = False
        self.rprompt = ""
        self.rsecret = False
        self.attached = None
        self.pending = None
        self.wants_clear = False
        self._job: "_Waiter | None" = None
        self._out = lambda s: None
        self.device = self.dev

    # -- CliSession-like interface used by the terminal --------------------------------------
    def active(self):
        if self.attached is not None and getattr(self.attached, "open", False):
            return self.attached.active()
        return self

    @property
    def prompt(self) -> str:
        return self.rprompt

    def display_prompt(self) -> str:
        return self.rprompt

    def secret_input(self) -> bool:
        return self.rsecret

    def banner(self) -> str:
        return ""

    def execute(self, line: str, out):
        self._out = out
        self._send("line", line)
        self._job = _Waiter(self)
        return self._job

    def answer(self, line, out):
        return self.execute(line, out)

    def help(self, line: str) -> str:
        conn = _REGISTRY.get(self.sport)
        if conn is None or conn.session is None:
            return ""
        return conn.session.active().help(line)

    def complete(self, line: str):
        conn = _REGISTRY.get(self.sport)
        if conn is None or conn.session is None:
            return None
        return conn.session.active().complete(line)

    def interrupt(self):
        self._send("intr")

    def ctrl_z(self) -> str:
        return ""

    def write(self, text):
        self._out(text)

    # -- plumbing --------------------------------------------------------------------------------
    def _send(self, kind, text=""):
        self.dev.send_tcp(self.dst, self.sport, PORTS[self.proto], RemoteMsg(kind, self.proto, text=text))

    def receive(self, pkt, port):
        seg = pkt.payload
        if "RST" in seg.flags:
            self._closed("% Connection reset by remote host\n" if self.open else "")
            return
        m = seg.data
        if not isinstance(m, RemoteMsg):
            return
        if m.kind == "out":
            self._out(m.text)
        elif m.kind == "done":
            self.rprompt, self.rsecret = m.prompt, m.secret
            if m.text:
                self._out(m.text)
            self._finish_wait()
        elif m.kind == "close":
            if m.text:
                self._out(m.text if m.text.endswith("\n") else m.text + "\n")
            self._closed(None)

    def _finish_wait(self):
        j, self._job = self._job, None
        if j is not None and not j.done:
            j.finish()

    def _closed(self, text):
        if not self.open and self._job is None and text is None:
            return
        self.open = False
        self.dev.unbind_tcp(self.sport)
        msg = text if text is not None else _close_text(self.style, self.proto, self.dst)
        if msg:
            self._out(msg)
        if self.local.attached is self:
            self.local.attached = None
        self._finish_wait()


def _close_text(style, proto, dst) -> str:
    if style in ("linux", "windows"):
        return f"Connection to {dst} closed.\n" if proto == "ssh" else "Connection closed by foreign host.\n"
    if style == "cisco":
        return f"\n[Connection to {dst} closed by foreign host]\n"
    if style == "huawei":
        return "\nInfo: The connection was closed by the remote host.\n"
    if style == "juniper":
        return f"Connection to {dst} closed.\n"
    return "\nConnection closed.\n"


class _Waiter(Job):
    """Waits for the server's 'done' after one line; times out if the server stops answering."""

    def __init__(self, rs: RemoteSession):
        super().__init__()
        self.rs = rs
        self._timer = rs.dev.engine.schedule(REPLY_TIMEOUT_MS, self._timeout)

    def _timeout(self):
        if not self.done:
            self.rs._closed("\n% Connection timed out; remote host not responding\n")

    def finish(self):
        if self._timer is not None:
            self._timer.cancelled = True
        super().finish()

    def cancel(self):
        self.rs.interrupt()


class Connect(Job):
    """TCP handshake + login banner; on success attaches a RemoteSession to *local*."""

    def __init__(self, local, dst: IPv4Address, proto: str, user: str = "", style: str = "cisco",
                 port: int | None = None):
        super().__init__()
        self.local, self.dev = local, local.device
        self.dst, self.proto, self.user, self.style = dst, proto, user, style
        self.port = port or PORTS[proto]
        self.sport = 32768 + next(_sports) % 28000
        self.rs = RemoteSession(local, dst, proto, self.sport, style)
        self._timer = None

    def start(self) -> "Connect":
        self.dev.bind_tcp(self.sport, self._receive)
        if not self.dev.send_tcp(self.dst, self.sport, self.port, None, "SYN"):
            self._fail("unreachable")
            return self
        self._timer = self.dev.engine.schedule(CONNECT_TIMEOUT_MS, lambda: self._fail("timeout"))
        return self

    def _receive(self, pkt, port):
        seg = pkt.payload
        if self.rs.open:
            self.rs.receive(pkt, port)
            return
        if "RST" in seg.flags:
            self._fail("refused")
            return
        if "SYN" in seg.flags and "ACK" in seg.flags:
            if self._timer is not None:
                self._timer.cancelled = True
            self.dev.send_tcp(self.dst, self.sport, self.port, None, "ACK")
            if self.style == "cisco" and self.proto == "telnet":
                self.local.write("Open\n")
            elif self.style == "linux" and self.proto == "telnet":
                self.local.write(f"Connected to {self.dst}.\nEscape character is '^]'.\n")
            elif self.style == "windows" and self.proto == "telnet":
                self.local.write(f"Connecting To {self.dst}...\n")
            self.rs.open = True
            self.rs._out = self.local.write
            self.local.attached = self.rs
            self.rs._job = self
            self.dev.send_tcp(self.dst, self.sport, self.port, RemoteMsg("hello", self.proto, user=self.user))
            self._timer = self.dev.engine.schedule(REPLY_TIMEOUT_MS, lambda: self._fail("timeout"))

    def finish(self):
        if self._timer is not None:
            self._timer.cancelled = True
        super().finish()

    def _fail(self, why):
        if self.done:
            return
        self.dev.unbind_tcp(self.sport)
        if self.local.attached is self.rs:
            self.local.attached = None
        self.rs.open = False
        self.local.write(_fail_text(self.style, self.proto, self.dst, self.port, why))
        self.finish()

    def cancel(self):
        self._fail("aborted")


def _fail_text(style, proto, dst, port, why) -> str:
    if why == "aborted":
        return "\n"
    if style == "cisco":
        lead = "\n" if proto == "telnet" else ""
        return lead + {"refused": "% Connection refused by remote host\n",
                "timeout": "% Connection timed out; remote host not responding\n",
                "unreachable": "% Destination unreachable; gateway or host down\n"}[why]
    if style == "windows":
        if proto == "telnet":
            return f"Connecting To {dst}...Could not open connection to the host, on port {port}: Connect failed\n"
        return {"refused": f"ssh: connect to host {dst} port {port}: Connection refused\n",
                "timeout": f"ssh: connect to host {dst} port {port}: Connection timed out\n",
                "unreachable": f"ssh: connect to host {dst} port {port}: Network is unreachable\n"}[why]
    if style == "linux":
        if proto == "telnet":
            return ("telnet: Unable to connect to remote host: "
                    + {"refused": "Connection refused", "timeout": "Connection timed out",
                       "unreachable": "Network is unreachable"}[why] + "\n")
        return {"refused": f"ssh: connect to host {dst} port {port}: Connection refused\n",
                "timeout": f"ssh: connect to host {dst} port {port}: Connection timed out\n",
                "unreachable": f"ssh: connect to host {dst} port {port}: Network is unreachable\n"}[why]
    if style == "huawei":
        return {"refused": "Error: Can't connect to the remote host\n", "timeout": "Error: Failed to connect to the remote host.\n",
                "unreachable": "Error: Can't connect to the remote host\n"}[why]
    if style == "juniper":
        return {"refused": f"telnet: connect to address {dst}: Connection refused\n" if proto == "telnet"
                else f"ssh: connect to host {dst} port {port}: Connection refused\n",
                "timeout": f"ssh: connect to host {dst} port {port}: Operation timed out\n",
                "unreachable": f"ssh: connect to host {dst} port {port}: No route to host\n"}[why]
    return {"refused": "failure: connection refused\n", "timeout": "failure: connection timed out\n",
            "unreachable": "failure: no route to host\n"}[why]


def connect(local, target: str, proto: str, user: str = "", style: str = "cisco", port: int | None = None):
    """Start a connection from the CLI session *local* to *target* (IPv4 address)."""
    return Connect(local, IPv4Address(target), proto, user, style, port).start()
