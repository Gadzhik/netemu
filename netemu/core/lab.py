"""Lab assignments with automatic checks, teacher mode and troubleshooting faults.

A lab is stored inside the project file: a title, the task text (Markdown) and checks
written one per line, ``<type> <arguments> | <description shown to the student>``::

    ping PC0 192.168.2.10 | PC0 reaches the server
    noping PC0 10.0.0.5 | the ACL blocks PC0 from the server
    route R1 10.0.2.0/24 | R1 has a route to the far LAN
    ospf R1 2 | R1 has two OSPF neighbours
    hostname R1 CORE | the router is named CORE

Check types: ping <device> <address> · noping <device> <address> · route <device> <prefix>
· ospf <device> <neighbours> · hostname <device> <name> · vlan <device> <id> · ip <device>
<address> · dhcp <device> · nat <device> · ssh <device> · stproot <switch> · fhrp <device>
active|standby · wifi <device> [ssid] · access <switch> <port> <vlan>.

Teacher mode: the solution (Markdown notes plus a per-device command script) and the list
of planted faults are teacher-only.  With a teacher password they are stored encrypted in
the file (PBKDF2-SHA256 key, SHA-256 keystream, HMAC tag), so a student who opens the
.netemu file in a text editor sees neither.  Student attempts are kept in ``attempts`` and
can be exported as an HTML report.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import ip_address, ip_network

CHECK_TYPES = ("ping", "noping", "route", "ospf", "hostname", "vlan", "ip", "dhcp", "nat", "ssh", "stproot", "fhrp",
               "wifi", "access", "bgp", "channel", "psec", "phone", "ospf6", "http", "available", "ddos",
               "clean", "immune", "outbreak", "c2blocked", "mitm", "stproot")
NEED = {"ping": 2, "noping": 2, "route": 2, "ospf": 2, "hostname": 2, "vlan": 2, "ip": 2, "dhcp": 1, "nat": 1,
        "ssh": 1, "stproot": 1, "fhrp": 2, "wifi": 1, "access": 3, "bgp": 2, "channel": 2, "psec": 2, "phone": 1,
        "ospf6": 2, "http": 2, "available": 2, "ddos": 2, "clean": 1, "immune": 2, "outbreak": 3, "c2blocked": 1,
        "mitm": 1}


# ------------------------------------------------------------------------------------ crypto
def _kdf(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 60_000)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    return "pbkdf2:" + salt.hex() + ":" + hashlib.sha256(_kdf(password, salt) + b"verify").hexdigest()


def check_password(stored: str | None, password: str) -> bool:
    if not stored:
        return True
    try:
        _, salt, digest = stored.split(":")
    except ValueError:
        return False
    want = hashlib.sha256(_kdf(password, bytes.fromhex(salt)) + b"verify").hexdigest()
    return hmac.compare_digest(want, digest)


def _stream(key: bytes, n: int) -> bytes:
    out, i = bytearray(), 0
    while len(out) < n:
        out += hashlib.sha256(key + i.to_bytes(4, "big")).digest()
        i += 1
    return bytes(out[:n])


def encrypt(data: dict, password: str) -> str:
    salt = os.urandom(16)
    key = _kdf(password, salt)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(raw, _stream(key, len(raw))))
    tag = hmac.new(key, ct, hashlib.sha256).digest()[:16]
    return base64.b64encode(salt + tag + ct).decode("ascii")


def decrypt(blob: str, password: str) -> dict | None:
    try:
        b = base64.b64decode(blob)
    except ValueError:
        return None
    salt, tag, ct = b[:16], b[16:32], b[32:]
    key = _kdf(password, salt)
    if not hmac.compare_digest(hmac.new(key, ct, hashlib.sha256).digest()[:16], tag):
        return None
    return json.loads(bytes(a ^ b for a, b in zip(ct, _stream(key, len(ct)))).decode("utf-8"))


# ------------------------------------------------------------------------------------ model
@dataclass
class Check:
    kind: str
    args: list[str]
    text: str

    def line(self) -> str:
        return f"{self.kind} {' '.join(self.args)} | {self.text}".strip()


@dataclass
class Lab:
    title: str = ""
    text: str = ""
    checks: list[Check] = field(default_factory=list)
    solution: str = ""                                   # teacher notes (Markdown)
    script: dict = field(default_factory=dict)           # device name -> CLI commands (auto-solve)
    faults: list = field(default_factory=list)           # planted faults: descriptions (teacher only)
    kind: str = "lab"                                    # lab | troubleshoot
    password: str | None = None                          # hash_password(...) when protected
    attempts: list = field(default_factory=list)         # student results
    translations: dict = field(default_factory=dict)     # {"en": {"title", "text", "checks": [descriptions]}}
    _secret: str | None = field(default=None, repr=False)        # encrypted teacher part (still locked)
    _key: str | None = field(default=None, repr=False)           # password while unlocked

    @property
    def locked(self) -> bool:
        return self.password is not None and self._key is None

    def unlock(self, password: str) -> bool:
        if not check_password(self.password, password):
            return False
        if self._secret:
            data = decrypt(self._secret, password) or {}
            self.solution, self.script = data.get("solution", ""), dict(data.get("script", {}))
            self.faults = list(data.get("faults", []))
            self._secret = None
        self._key = password
        return True

    def lock(self) -> None:
        """Forget the password; the teacher part goes back to the encrypted blob."""
        if self.password is None or self._key is None:
            return
        self._secret = self._encrypted()
        self.solution, self.script, self.faults, self._key = "", {}, [], None

    def set_password(self, password: str | None) -> None:
        if self.locked:
            raise PermissionError("unlock the lab first")
        self.password = hash_password(password) if password else None
        self._key = password or None

    def localized(self, lang: str) -> tuple[str, str, list[str]]:
        """(title, text, check descriptions) in *lang* when a translation exists."""
        t = self.translations.get(lang) or {}
        texts = [c.text for c in self.checks]
        tc = t.get("checks") or []
        if len(tc) == len(texts):
            texts = list(tc)
        return t.get("title") or self.title, t.get("text") or self.text, texts

    def _encrypted(self) -> str:
        """Encrypt the teacher part; reuse the blob while nothing changed (stable file / dirty check)."""
        plain = json.dumps(self._teacher(), sort_keys=True, ensure_ascii=False)
        cache = getattr(self, "_enc_cache", None)
        if cache is None or cache[0] != plain or cache[1] != self._key:
            self._enc_cache = (plain, self._key, encrypt(self._teacher(), self._key))
        return self._enc_cache[2]

    def _teacher(self) -> dict:
        return {"solution": self.solution, "script": dict(self.script), "faults": list(self.faults)}

    def to_dict(self) -> dict:
        d = {"title": self.title, "text": self.text, "checks": [c.line() for c in self.checks]}
        if self.kind != "lab":
            d["kind"] = self.kind
        if self.attempts:
            d["attempts"] = list(self.attempts)
        if self.translations:
            d["translations"] = {k: dict(v) for k, v in self.translations.items()}
        if self.password is not None:
            d["password"] = self.password
            d["secret"] = self._secret if self._key is None else self._encrypted()
        else:
            if self.solution:
                d["solution"] = self.solution
            if self.script:
                d["script"] = dict(self.script)
            if self.faults:
                d["faults"] = list(self.faults)
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "Lab | None":
        if not d:
            return None
        lab = cls(d.get("title", ""), d.get("text", ""))
        lab.checks = parse_checks("\n".join(d.get("checks", [])))[0]
        lab.kind = d.get("kind", "lab")
        lab.attempts = list(d.get("attempts", []))
        lab.translations = {k: dict(v) for k, v in (d.get("translations") or {}).items()}
        lab.password = d.get("password")
        if lab.password is not None:
            lab._secret = d.get("secret")
        else:
            lab.solution, lab.script = d.get("solution", ""), dict(d.get("script", {}))
            lab.faults = list(d.get("faults", []))
        return lab

    def solution_markdown(self) -> str:
        parts = [self.solution.strip()] if self.solution.strip() else []
        if self.faults:
            parts.append("**Внесённые неисправности:**\n\n" + "\n".join(f"- {f}" for f in self.faults))
        for dev, cmds in self.script.items():
            parts.append(f"**{dev}**\n\n```\n{cmds.strip()}\n```")
        return "\n\n".join(parts)


def script_to_text(script: dict) -> str:
    return "\n\n".join(f"[{dev}]\n{cmds.strip()}" for dev, cmds in script.items())


def text_to_script(text: str) -> dict:
    """'[R1]' headers followed by that device's commands."""
    out, cur = {}, None
    for line in text.splitlines():
        st = line.strip()
        if st.startswith("[") and st.endswith("]") and len(st) > 2:
            cur = st[1:-1].strip()
            out[cur] = []
        elif cur is not None and st:
            out[cur].append(line.rstrip())
    return {k: "\n".join(v) for k, v in out.items() if v}


def parse_checks(text: str) -> tuple[list[Check], list[str]]:
    """Parse the check lines; returns (checks, error messages with line numbers)."""
    checks, errors = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        spec, _, desc = line.partition("|")
        words = spec.split()
        kind = words[0].lower()
        if kind not in CHECK_TYPES:
            errors.append(f"{n}: unknown check '{kind}'")
            continue
        if len(words) - 1 < NEED[kind]:
            errors.append(f"{n}: '{kind}' needs {NEED[kind]} argument(s)")
            continue
        checks.append(Check(kind, words[1:], desc.strip() or spec.strip()))
    return checks, errors


def _device(topology, name):
    name = name.lower()
    return next((d for d in topology.devices.values() if d.name.lower() == name or d.hostname.lower() == name), None)


def run_check(topology, c: Check) -> tuple[bool, str]:
    """Evaluate one check against the running simulation; returns (passed, explanation)."""
    dev = _device(topology, c.args[0])
    if dev is None:
        return False, f"no device named {c.args[0]}"
    k, a = c.kind, c.args
    try:
        if k == "ping":
            return _ping(topology, dev, a[1])
        if k == "noping":
            ok, detail = _ping(topology, dev, a[1])
            return not ok, "blocked" if not ok else f"still reachable ({detail})"
        if k == "route":
            net = ip_network(a[1], strict=False)
            if net.version == 6:
                ok = any(r.network == net for r in dev.routing_table6()) if hasattr(dev, "routing_table6") else False
            else:
                ok = any(r.network == net for r in dev.routing_table()) if hasattr(dev, "routing_table") else False
            return ok, "route present" if ok else f"no route to {net}"
        if k == "ospf":
            proc = getattr(dev, "ospf", None)
            n = len(getattr(proc, "neighbors", {}) or {}) if proc is not None else 0
            return n >= int(a[1]), f"{n} OSPF neighbour(s)"
        if k == "hostname":
            return dev.hostname == a[1], f"hostname is {dev.hostname}"
        if k == "vlan":
            ok = int(a[1]) in getattr(dev, "vlans", {})
            return ok, "VLAN exists" if ok else f"VLAN {a[1]} missing"
        if k == "ip":
            want = ip_address(a[1])
            ok = any((p.ip is not None and p.ip.ip == want) or any(x.ip == want for x in p.ipv6)
                     for p in dev.ports.values())
            return ok, "address configured" if ok else f"{want} not configured"
        if k == "dhcp":
            info = getattr(dev, "dhcp_info", {}) or {}
            ok = bool(info.get("server")) and dev.nic.ip is not None
            return ok, f"leased {dev.nic.ip.ip}" if ok else "no DHCP lease"
        if k == "nat":
            nat = getattr(dev, "nat", None)
            n = len(nat.table) if nat is not None else 0
            return n > 0, f"{n} translation(s)"
        if k == "ssh":
            from .services.remote import service_state
            ok = service_state(dev, "ssh") is None
            return ok, "SSH accepted" if ok else "SSH is not enabled"
        if k == "stproot":
            stp = getattr(dev, "stp", None)
            ok = stp is not None and stp.root_id == stp.bridge_id
            return ok, "root bridge" if ok else "not the root bridge"
        if k == "fhrp":
            groups = list(getattr(getattr(dev, "fhrp", None), "groups", {}).values())
            states = sorted({g.state.lower() for g in groups})
            ok = any(s == a[1].lower() or (a[1].lower() == "active" and s == "master") or
                     (a[1].lower() == "standby" and s == "backup") for s in states)
            return ok, ", ".join(states) or "no HSRP/VRRP group"
        if k == "wifi":
            nic = getattr(dev, "nic", None)
            link = getattr(nic, "link", None)
            ok = link is not None and getattr(link, "wireless", False)
            if ok and len(a) > 1:
                ok = (getattr(dev, "wifi", {}) or {}).get("ssid") == a[1]
            return ok, "associated" if ok else "not associated"
        if k == "bgp":
            bgp = getattr(dev, "bgp", None)
            n = sum(1 for p in (bgp.peers.values() if bgp else []) if p.state == "Established")
            return n >= int(a[1]), f"{n} established BGP session(s)"
        if k == "channel":
            want = int(a[2]) if len(a) > 2 else 2
            for p in dev.ports.values():
                if getattr(p, "is_channel", False) and (p.name.lower().endswith(a[1].lower().lstrip("po")) or
                                                         p.short.lower() == a[1].lower()):
                    n = len(p.active_members)
                    return n >= want, f"{n} bundled member(s)"
            return False, f"no port-channel {a[1]}"
        if k == "psec":
            p = dev.get_port(a[1])
            ok = p is not None and p.psec is not None
            return ok, "port security on" if ok else "port security off"
        if k == "phone":
            st = getattr(dev, "state", "")
            return st not in ("", "unregistered"), f"phone {st or 'n/a'}" + (f", DN {dev.number}" if getattr(dev, "number", None) else "")
        if k == "ospf6":
            proc = getattr(dev, "v6igp", {}).get("ospf6")
            n = len(getattr(proc, "neighbors", {}) or {}) if proc is not None else 0
            return n >= int(a[1]), f"{n} OSPFv3 neighbour(s)"
        if k == "http":
            return _http(topology, dev, a[1])
        if k == "available":
            return _available(topology, dev, a[1], float(a[2]) if len(a) > 2 else 90.0)
        if k == "ddos":
            from .security.ddos import human_bps, monitor
            m = monitor(topology)
            m.ensure_running()
            topology.engine.run(max_time=2500)               # two full measurement windows
            got = m.attack_bps_at(dev)
            ok = got <= float(a[1]) * 1e6
            return ok, f"attack traffic at {dev.name}: {human_bps(got)}" + ("" if ok else f" (> {a[1]} Mbit/s)")
        if k in ("clean", "immune", "outbreak", "c2blocked"):
            return _malware_check(topology, k, dev, a)
        if k == "mitm":                                  # mitm <attacker> [max]: at most N frames intercepted
            from .security import l2attack as _l2
            got = _l2.intercepted_by(topology, dev.name)
            cap = int(a[1]) if len(a) > 1 else 0
            return got <= cap, f"{dev.name} intercepted {got} frame(s)"
        if k == "access":
            p = dev.get_port(a[1])
            if p is None:
                return False, f"no port {a[1]}"
            ok = getattr(p, "mode", "access") == "access" and p.pvid == int(a[2])
            return ok, p.switchport_text() if hasattr(p, "switchport_text") else "?"
    except (ValueError, IndexError) as ex:
        return False, f"bad check: {ex}"
    return False, "unknown check"


def _ping(topology, dev, target) -> tuple[bool, str]:
    from .cli.base import make_ping
    from .stack.icmp import PingListener
    if not hasattr(dev, "originate") and not hasattr(dev, "send_ip"):
        return False, f"{dev.name} cannot send IP packets"
    res = {"ok": 0}

    class _L(PingListener):
        def on_reply(self, *x):
            res["ok"] += 1
    try:
        job = make_ping(dev, target, _L(), count=3, size=32, timeout=2000.0, interval=200.0).start()
    except ValueError:
        return False, f"bad address {target}"
    topology.engine.run_until(lambda: job.done)
    return res["ok"] > 0, f"{res['ok']}/3 replies"


def _available(topology, dev, url, percent) -> tuple[bool, str]:
    """A service is 'available' when at least *percent* of 10 requests succeed (under attack, too)."""
    from .services.http import HttpGet
    from .security.ddos import monitor
    monitor(topology).ensure_running()
    topology.engine.run(max_time=2500)                       # attacks have reached their full rate
    ok = 0
    for _ in range(10):
        res = {}
        job = HttpGet(dev, url, lambda s, b: res.__setitem__("s", s)).start()
        topology.engine.run_until(lambda: job.done)
        ok += res.get("s") == 200
    return ok * 10 >= percent, f"{ok}/10 requests answered"


def _malware_check(topology, k, dev, a) -> tuple[bool, str]:
    from .security import malware as mw
    if k == "clean":
        mw.monitor(topology).ensure_running()
        topology.engine.run(max_time=3000)               # a reinfection would show up meanwhile
        st = mw.state(dev)
        return st == "clean", f"{dev.name}: {st}"
    if k == "immune":
        fam = mw.FAMILIES[a[1]]
        why = mw.protection(dev, fam)
        return why is not None, f"{dev.name}: " + (why or f"vulnerable to {fam.title}")
    if k == "outbreak":                                  # outbreak <patient> <family> <dev,dev,...>
        hit = mw.outbreak(topology, dev.name, a[1])
        bad = [n for n in a[2].split(",") if n.strip() in hit]
        return not bad, "infected: " + (", ".join(hit) or "none")
    from .services.http import HttpGet                  # c2blocked <dev>
    res = {}
    job = HttpGet(dev, f"http://{mw.C2_NAME}{mw.C2_PATH}", lambda s, b: res.__setitem__("s", s)).start()
    topology.engine.run_until(lambda: job.done)
    ok = res.get("s") is None
    return ok, f"{dev.name} -> {mw.C2_NAME}: " + ("blocked" if ok else f"HTTP {res['s']}")


def _http(topology, dev, url) -> tuple[bool, str]:
    from .services.http import HttpGet
    res = {}

    def done(status, body):
        res["s"] = status
    job = HttpGet(dev, url, done).start()
    topology.engine.run_until(lambda: job.done)
    return res.get("s") == 200, f"HTTP {res.get('s') or 'failed'}"


def evaluate(topology, lab: Lab) -> list[tuple[Check, bool, str]]:
    return [(c, *run_check(topology, c)) for c in lab.checks]


# ------------------------------------------------------------------------------------ auto-solve
def apply_script(topology, script: dict, settle_ms: float = 60000.0) -> str:
    """Run the teacher's command script on each device (by name); returns the combined output."""
    out = []
    for name, cmds in script.items():
        dev = _device(topology, name)
        if dev is None:
            out.append(f"! {name}: no such device")
            continue
        if cmds.lstrip().startswith("@"):
            out.append(_host_settings(dev, cmds))
            continue
        s = dev.create_cli()
        if s is None:
            continue
        for line in cmds.splitlines():
            if not line.strip():
                continue
            r = []
            job = s.execute(line, r.append)
            if job is not None:
                topology.engine.run_until(lambda: job.done)
            if getattr(s, "secret_input", lambda: False)() and line.strip().split()[:1] and \
                    "enable".startswith(line.strip().split()[0]):
                from .services.remote import access     # the script acts with the device's own secret
                s.execute(access(dev).get("enable_secret") or "", r.append)
            out.append("".join(r))
    topology.engine.run(max_time=settle_ms)            # max_time is relative to now
    return "".join(out)


def _host_settings(dev, text: str) -> str:
    """End devices and APs: '@ip 192.168.1.10/24 gw 192.168.1.1', '@dhcp', '@ip6 X/64 gw Y', '@wifi SSID KEY',
    '@ssid NAME key KEY' (access point)."""
    for line in text.splitlines():
        w = line.strip().lstrip("@").split()
        if not w:
            continue
        if w[0] == "ip":
            addr, _, plen = w[1].partition("/")
            from .addresses import prefix_to_mask
            dev.dhcp_enabled = False
            dev.set_ip(addr, prefix_to_mask(int(plen or 24)))
            if "gw" in w:
                dev.set_gateway(w[w.index("gw") + 1])
        elif w[0] == "dhcp":
            dev.start_dhcp()
        elif w[0] == "ip6":
            dev.set_ipv6(w[1])
            if "gw" in w:
                dev.set_gateway6(w[w.index("gw") + 1])
        elif w[0] == "ssid":                                 # access point: @ssid NAME [key KEY]
            dev.wifi["ssid"] = w[1]
            if "key" in w:
                dev.wifi.update(security="wpa2", key=w[w.index("key") + 1])
            from .devices.wireless import rescan
            rescan(dev.engine.topology)
        elif w[0] == "wifi":
            dev.wifi = {"ssid": w[1], "key": w[2] if len(w) > 2 else None}
            from .devices.wireless import rescan
            rescan(dev.engine.topology)
        elif w[0] == "ddos":                                # server protection: @ddos syncookies on | httplimit 5 ...
            from .security.ddos import guard
            key = {"syncookies": "syncookies", "httplimit": "http_limit", "challenge": "challenge",
                   "connlimit": "conn_limit", "resolver": "open_resolver", "monlist": "ntp_monlist"}[w[1]]
            val = w[2] if len(w) > 2 else "on"
            guard(dev)[key] = int(val) if val.isdigit() else val in ("on", "yes", "true")
        elif w[0] == "isp" and w[1] == "dns":               # @isp dns NAME [ADDRESS] [ttl N] [shield on|off]
            rest = w[3:]
            a = rest[0] if rest and rest[0] not in ("ttl", "shield") else None
            ttl = int(rest[rest.index("ttl") + 1]) if "ttl" in rest else None
            shield = rest[rest.index("shield") + 1] == "on" if "shield" in rest else None
            dev.set_zone(w[2], a, ttl, shield)
        elif w[0] == "isp":                                 # provider protection: @isp ISP0 scrub on | rtbh IP | flowspec RULE
            prot = dev.protect.setdefault(w[1], {})
            if w[2] == "scrub":
                prot["scrub"] = (w[3] if len(w) > 3 else "on") == "on"
            elif w[2] == "rtbh":
                prot.setdefault("rtbh", []).append(w[3])
            elif w[2] == "flowspec":
                prot.setdefault("flowspec", []).append(" ".join(w[3:]))
        elif w[0] == "sec":                                 # malware training: @sec patch MS17-010 | avupdate ...
            _sec_directive(dev, w[1:])
        elif w[0] == "l2":                                  # LAN attacker: @l2 start | stop
            (dev.start if (len(w) < 2 or w[1] == "start") else dev.stop)()
        elif w[0] == "attack":                              # botnet: @attack start | stop
            (dev.start if w[1] == "start" else dev.stop)()
    return ""


def _sec_directive(dev, w) -> None:
    from .security import malware as mw
    s = mw.sec(dev)
    on = (w[1] if len(w) > 1 else "on") in ("on", "yes", "true")
    if w[0] == "patch":
        mw.patch(dev, w[1])
    elif w[0] == "av":
        s["av"] = on
    elif w[0] == "avupdate":
        job = mw.update_sigs(dev, lambda ok, msg: None)
        dev.engine.run_until(lambda: job.done)
    elif w[0] == "scan":
        mw.av_scan(dev)
    elif w[0] in ("smb", "telnet", "samba", "fail2ban"):
        s[w[0]] = on
    elif w[0] == "sshpass":                             # @sec sshpass: SSH password is no longer weak
        s["ssh_weak"] = False
    elif w[0] == "upgrade":                             # @sec upgrade: install all patches (apt upgrade)
        for v in mw.PATCHES:
            mw.patch(dev, v)
    elif w[0] == "password":                           # @sec password NEW: factory password changed
        s["default_password"] = False
    elif w[0] == "block":
        s["fw_rules"].append({"name": f"Block {w[1]}", "port": w[1], "proto": "TCP"})
    elif w[0] == "reboot":
        mw.reboot(dev)
    elif w[0] == "backup":
        mw.backup(dev)
    elif w[0] == "restore":
        mw.restore(dev)
    elif w[0] == "infect":
        mw.infect(dev, w[1])
    elif w[0] == "clean":                              # reinstall: the infection is gone (files stay as they are)
        mw.clean(dev, "переустановкой системы")
    dev.engine.emit("device_changed", dev)


# ------------------------------------------------------------------------------------ faults
def _fault_options(topology) -> list:
    options = []
    for d in topology.devices.values():
        if d.category in ("router", "switch"):
            for p in d.ports.values():
                if p.is_up and p.peer is not None and not getattr(p, "is_wireless", False):
                    options.append(("shut", d, p))
            for r in list(getattr(d, "static_routes", []) or []):
                options.append(("route", d, r))
        if d.category in ("pc", "server") and getattr(d, "nic", None) is not None and d.nic.ip is not None:
            if d.gateway is not None:
                options.append(("gw", d, None))
            options.append(("mask", d, None))
        if d.category == "switch":
            for p in d.ports.values():
                if getattr(p, "mode", None) == "access" and p.pvid != 1 and p.is_up:
                    options.append(("vlan", d, p))
    return options


def _apply_fault(kind, d, x):
    """Break one thing; returns (description, undo function)."""
    if kind == "shut":
        x.set_admin(False)
        return f"{d.name}: interface {x.name} administratively shut down", lambda: x.set_admin(True)
    if kind == "route":
        idx = d.static_routes.index(x)
        d.static_routes.remove(x)
        if hasattr(d, "routes_changed"):
            d.routes_changed()

        def undo():
            d.static_routes.insert(idx, x)
            if hasattr(d, "routes_changed"):
                d.routes_changed()
        return f"{d.name}: static route {getattr(x, 'network', x)} removed", undo
    if kind == "gw":
        net = d.nic.ip.network
        bad = next(h for h in net.hosts() if h != d.gateway and h != d.nic.ip.ip)
        old = d.gateway
        d.set_gateway(str(bad))
        return f"{d.name}: default gateway changed from {old} to {bad}", lambda: d.set_gateway(str(old))
    if kind == "mask":
        ip = d.nic.ip
        from .addresses import prefix_to_mask
        new_len = 30 if ip.network.prefixlen < 30 else 24
        d.set_ip(str(ip.ip), prefix_to_mask(new_len))
        return (f"{d.name}: subnet mask changed from /{ip.network.prefixlen} to /{new_len}",
                lambda: d.set_ip(str(ip.ip), str(ip.netmask)))
    old = x.pvid
    new = 999 if old != 999 else 998
    old_access = x.access_vlan
    x.access_vlan = x.pvid = new
    d.link_state_changed(x)

    def undo_vlan():
        x.access_vlan, x.pvid = old_access, old
        d.link_state_changed(x)
    return f"{d.name}: port {x.name} moved from VLAN {old} to VLAN {new}", undo_vlan


def plant_fault(topology, rng: random.Random | None = None, lab=None, tries: int = 30) -> str | None:
    """Break something at random (vendor-neutral, on the device objects); returns the description.

    With a *lab*, the fault must make at least one check fail that passes now (otherwise "find the fault"
    has nothing to find): candidates are tried in random order and undone until one does.  When no
    candidate breaks a check, the last one tried stays (the network has no checks to protect)."""
    rng = rng or random.Random()
    options = _fault_options(topology)
    if not options:
        return None
    rng.shuffle(options)
    checks = list(getattr(lab, "checks", None) or [])
    passing = [c for c in checks if run_check(topology, c)[0]] if checks else []
    desc = None
    for kind, d, x in options[:max(1, tries)] if passing else options[:1]:
        desc, undo = _apply_fault(kind, d, x)
        if not passing:
            break
        topology.engine.run(max_time=3000)              # let link state / ARP react before checking
        if any(not run_check(topology, c)[0] for c in passing):
            break
        undo()
        topology.engine.run(max_time=500)
        desc = None
    if desc is None:                                     # nothing breaks a check: keep a random fault
        kind, d, x = options[0]
        desc, _ = _apply_fault(kind, d, x)
    d.engine.emit("device_changed", d)
    return desc


# ------------------------------------------------------------------------------------ report
def record_attempt(lab: Lab, student: str, results, lang: str = "ru") -> dict:
    texts = dict(zip((id(c) for c in lab.checks), lab.localized(lang)[2]))
    a = {"student": student, "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
         "passed": sum(1 for _, ok, _ in results if ok), "total": len(results),
         "results": [[texts.get(id(c), c.text), bool(ok), detail] for c, ok, detail in results]}
    lab.attempts.append(a)
    return a


def report_html(lab: Lab, attempt: dict, lang: str = "ru") -> str:
    from html import escape
    en = lang == "en"
    title = lab.localized(lang)[0]
    w_student, w_date, w_of, w_check, w_res = (("Student", "Date", "of", "Check", "Result") if en else
                                               ("Студент", "Дата", "из", "Проверка", "Результат"))
    pct = round(100 * attempt["passed"] / max(1, attempt["total"]))
    rows = "".join(f"<tr><td>{'✔' if ok else '✘'}</td><td>{escape(t)}</td><td>{escape(d)}</td></tr>"
                   for t, ok, d in attempt["results"])
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#222}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px 10px;text-align:left}}th{{background:#f0f3f7}}
.score{{font-size:28px;font-weight:600}}</style></head><body>
<h1>{escape(title)}</h1>
<p>{w_student}: <b>{escape(attempt['student'])}</b><br>{w_date}: {escape(attempt['time'])}</p>
<p class="score">{attempt['passed']} {w_of} {attempt['total']} ({pct}%)</p>
<table><tr><th></th><th>{w_check}</th><th>{w_res}</th></tr>{rows}</table>
<p style="color:#888;font-size:12px">NetEmu</p></body></html>"""
