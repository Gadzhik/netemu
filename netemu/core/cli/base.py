"""Common CLI session machinery."""
from __future__ import annotations

from typing import Callable

from ..addresses import IPv4Interface
from ..stack.icmp import Job
from . import parser as P

Writer = Callable[[str], None]


class CliSession:
    """A console attached to one device.

    The terminal widget calls :meth:`execute` for each line; output is written
    through the *out* callback.  A command may return a :class:`Job` (ping,
    traceroute) that keeps writing output until it finishes.
    """

    #: word that negates a command in config modes ("no", "undo") or None
    negate_word: str | None = None

    def __init__(self, device):
        self.device = device
        self._out: Writer = lambda s: None
        self.job: Job | None = None
        self.attached = None      # RemoteSession while telnet/ssh to another device is open
        self.pending = None       # (prompt, secret, callback) while the session waits for an answer
        self.vty = False          # True when this session serves a remote (telnet/ssh) login

    # -- remote sessions and interactive prompts --------------------------------------------
    def active(self):
        """The session the terminal talks to: the innermost open remote session, or this one."""
        if self.attached is not None and getattr(self.attached, "open", False):
            return self.attached.active()
        return self

    def display_prompt(self) -> str:
        return self.pending[0] if self.pending else self.prompt

    def secret_input(self) -> bool:
        return bool(self.pending and self.pending[1])

    def ask(self, prompt: str, callback, secret: bool = True) -> None:
        """Make the next line an answer to *prompt* (passwords, confirmations)."""
        self.pending = (prompt, secret, callback)

    def answer(self, line: str, out: Writer):
        self._out = out
        _, _, cb = self.pending
        self.pending = None
        return self._finish(cb(line), out)

    def _finish(self, result, out):
        if isinstance(result, Job):
            self.job = result
            return result
        if result:
            out(result if result.endswith("\n") else result + "\n")
        return None

    # -- interface for the terminal --------------------------------------
    @property
    def prompt(self) -> str:
        raise NotImplementedError

    def banner(self) -> str:
        return ""

    def write(self, text: str) -> None:
        self._out(text)

    def execute(self, line: str, out: Writer) -> Job | None:
        if self.pending is not None:
            return self.answer(line, out)
        self._out = out
        self.job = None
        return self._finish(self._execute(line), out)

    def _execute(self, line: str):
        raise NotImplementedError

    def help(self, line: str) -> str:
        return ""

    def complete(self, line: str) -> str | None:
        return None

    def interrupt(self) -> None:
        """Ctrl+C / Ctrl+Shift+6: abort the running job."""
        if self.job and not self.job.done:
            self.job.cancel()

    def ctrl_z(self) -> str:
        """Ctrl+Z: leave configuration mode. Returns text to print."""
        return ""


class TreeSession(CliSession):
    """Session driven by command trees (one list of root nodes per mode)."""

    #: modes in which the negate word is accepted
    negate_modes: tuple[str, ...] = ()
    #: sub-mode -> parent mode tried when a command is not valid in the sub-mode
    #: (IOS accepts global configuration commands in interface mode, for example)
    parent_modes: dict[str, str] = {}

    def __init__(self, device):
        super().__init__(device)
        self.mode = "user"
        self.iface = None  # current interface in interface mode
        self._trees: dict[str, list[P.Node]] = {}
        self.build_trees()

    def build_trees(self) -> None:
        raise NotImplementedError

    def roots(self, mode: str | None = None) -> list[P.Node]:
        return self._trees[mode or self.mode]

    # -- error formatting (overridden by dialects) ------------------------
    def err_invalid(self, prompt: str, pos: int) -> str:
        return " " * (len(prompt) + pos) + "^\n% Invalid input detected at '^' marker.\n"

    def err_ambiguous(self, prompt: str, pos: int, token: str) -> str:
        return f'% Ambiguous command:  "{token}"\n'

    def err_incomplete(self, prompt: str, pos: int) -> str:
        return "% Incomplete command.\n"

    # -- execution ---------------------------------------------------------
    def _split_negate(self, tokens: list[P.Token]) -> tuple[bool, list[P.Token]]:
        if (self.negate_word and self.mode in self.negate_modes and tokens
                and tokens[0].text.lower() == self.negate_word):
            return True, tokens[1:]
        return False, tokens

    def run_in_mode(self, mode: str, line: str):
        prev = self.mode
        self.mode = mode
        try:
            return self._execute(line)
        finally:
            if self.mode == mode:
                self.mode = prev

    def _execute(self, line: str):
        prompt = self.prompt
        tokens = P.tokenize(line)
        if not tokens:
            return None
        negate, tokens = self._split_negate(tokens)
        if negate and not tokens:
            return self.err_incomplete(prompt, len(line))
        r = P.parse(self.roots(), tokens, self, len(line))
        parent = self.parent_modes.get(self.mode)
        if r.status != "ok" and parent is not None:
            r2 = P.parse(self.roots(parent), tokens, self, len(line))
            if r2.status == "ok":
                # like IOS: run it at the parent level and stay there (unless it changed mode)
                if negate and not r2.node.negatable:
                    return self.err_invalid(prompt, tokens[0].pos)
                self.mode = parent
                self.iface = None
                return r2.handler(self, r2.args, negate)
        if r.status == "ok":
            if negate and not r.node.negatable:
                return self.err_invalid(prompt, tokens[0].pos)
            return r.handler(self, r.args, negate)
        if r.status == "invalid":
            return self.err_invalid(prompt, r.pos)
        if r.status == "ambiguous":
            return self.err_ambiguous(prompt, r.pos, r.token)
        if r.status == "incomplete":
            return self.err_incomplete(prompt, r.pos)
        return None

    def help(self, line: str) -> str:
        roots = self.roots()
        stripped = line.lstrip()
        if self.negate_word and self.mode in self.negate_modes:
            first = stripped.split(" ", 1)
            if len(first) == 2 and first[0].lower() == self.negate_word:
                line = first[1]
        status, items = P.help_for(roots, line, self)
        if status != "ok":
            return self.err_invalid(self.prompt, len(line))
        return P.format_help(items)

    def complete(self, line: str) -> str | None:
        prefix = ""
        if self.negate_word and self.mode in self.negate_modes:
            first = line.split(" ", 1)
            if len(first) == 2 and first[0].lower() == self.negate_word:
                prefix, line = first[0] + " ", first[1]
        r = P.complete(self.roots(), line, self)
        return prefix + r if r is not None else None


# -- helpers shared by dialects -----------------------------------------------
def overlapping_port(device, port, iface: IPv4Interface):
    """Return another port whose subnet overlaps *iface*, or None."""
    for p in device.ports.values():
        if p is port or p.ip is None:
            continue
        if any(a.network.overlaps(iface.network) for a in [p.ip] + list(getattr(p, "ip2", []))):
            return p
    return None


def secondary_cmd(device, port, iface, negate, overlap_msg):
    """Add/remove a secondary address; returns an error text or None."""
    if negate:
        port.ip2 = [a for a in port.ip2 if a != iface]
        device.arp.flush_port(port)
        return None
    if port.ip is None:
        return "% Primary address not configured" if overlap_msg.startswith("%") else \
            "Error: Please configure the primary address first."
    if any(a.network.overlaps(iface.network) for a in [port.ip] + port.ip2) or \
            overlapping_port(device, port, iface) is not None:
        return overlap_msg
    port.ip2.append(iface)
    device.arp.flush_port(port)
    device.routes_changed()
    return None


def is_l3(device) -> bool:
    return device.category in ("router", "pc", "server")


def v6_addr_cmd(port, text, negate):
    """Add/remove an IPv6 address on *port*; returns an error string or None."""
    from ipaddress import IPv6Interface
    a = IPv6Interface(text)
    if negate:
        if a in port.ipv6:
            port.ipv6.remove(a)
        return None
    if a not in port.ipv6:
        port.ipv6.append(a)
    port.ipv6_enabled = True
    return None


def v6_route_cmd(dev, prefix, nh, iface, negate, distance=1):
    from ipaddress import IPv6Address, IPv6Network
    from ..stack.ipv6 import StaticRoute6
    net = IPv6Network(prefix, strict=False)
    nh_ip = IPv6Address(nh) if nh else None
    if negate:
        dev.static_routes6 = [r for r in dev.static_routes6
                              if not (r.network == net and (nh_ip is None or r.next_hop == nh_ip))]
        return None
    dev.static_routes6 = [r for r in dev.static_routes6 if not (r.network == net and r.next_hop == nh_ip)]
    dev.static_routes6.append(StaticRoute6(net, nh_ip, iface, distance))
    return None


def make_trace(device, target, listener, **kw):
    """IPv4 TracerouteSession or IPv6 Trace6Session depending on the address."""
    from ipaddress import IPv4Address, IPv6Address
    from ..stack.icmp import TracerouteSession
    from ..stack.ipv6 import Trace6Session
    t = str(target)
    if ":" in t:
        return Trace6Session(device, IPv6Address(t), listener, **kw)
    return TracerouteSession(device, IPv4Address(t), listener, **kw)


def make_ping(device, target, listener, **kw):
    """IPv4 PingSession or IPv6 Ping6Session depending on the address."""
    from ipaddress import IPv4Address, IPv6Address
    from ..stack.icmp import PingSession
    from ..stack.ipv6 import Ping6Session
    t = str(target)
    if ":" in t:
        return Ping6Session(device, IPv6Address(t), listener, **kw)
    return PingSession(device, IPv4Address(t), listener, **kw)


def lag_summary_rows(device):
    """(channel, protocol, [(member, flag)]) for show etherchannel / display eth-trunk / show sharing."""
    from ..port import channel_protocol
    out = []
    for po in device.channels():
        mem = []
        for m in po.members:
            flag = "P" if po.member_bundled(m) else ("D" if not m.is_up else ("I" if m.peer is not None and m.peer.channel_group is None else "s"))
            mem.append((m, flag))
        mode = next((m.channel_mode for m in po.members), None)
        out.append((po, channel_protocol(mode), mem))
    return out


def fhrp_set(device, port, proto, num, attr, value, negate=False):
    """Shared HSRP/VRRP configuration for all dialects; returns None or an error text."""
    from ipaddress import IPv4Address
    f = device.fhrp
    if negate and attr is None:
        f.remove(port.name, proto, num)
        return None
    g = f.group(port.name, proto, num)
    if attr == "vip":
        g.vip = None if negate else IPv4Address(str(value))
    elif attr == "priority":
        g.priority = 100 if negate else int(value)
    elif attr == "preempt":
        g.preempt = None if negate else bool(value)
    f.changed(g)
    return None


def fhrp_rows(device):
    """[(group, port, peer active ip, peer standby ip)] for show commands."""
    out = []
    for g in device.fhrp.groups.values():
        p = device.ports.get(g.port)
        act = next((str(ip) for ip, v in g.peers.items() if v[1] in ("active", "master")), None)
        stb = next((str(ip) for ip, v in g.peers.items() if v[1] in ("standby", "backup")), None)
        out.append((g, p, act, stb))
    return out


def v6igp_neighbors(device, kind):
    """[(router id, port, link-local)] of a router's OSPFv3 / RIPng neighbours."""
    proc = getattr(device, "v6igp", {}).get(kind)
    if proc is None:
        return []
    return [(rid, device.ports.get(pn), v[0]) for (pn, rid), v in proc.neighbors.items()]


def dhcp6_pool_cmd(device, pool, attr, value):
    from ipaddress import IPv6Address, IPv6Network
    from ..services.dhcp6 import Dhcp6Pool
    p = device.dhcp6.pools.setdefault(pool, Dhcp6Pool(pool))
    try:
        if attr == "prefix":
            p.prefix = IPv6Network(value, strict=False)
        elif attr == "dns":
            p.dns = str(IPv6Address(value))
    except ValueError:
        return "invalid address"
    return None


def psec_set(port, attr, value=None, negate=False):
    """Port security settings shared by the dialects."""
    if attr == "enable":
        port.psec = None if negate else (port.psec or {"max": 1, "violation": "shutdown", "sticky": False, "macs": []})
        return None
    if port.psec is None:
        port.psec = {"max": 1, "violation": "shutdown", "sticky": False, "macs": []}
    ps = port.psec
    if attr == "max":
        ps["max"] = 1 if negate else int(value)
    elif attr == "violation":
        ps["violation"] = "shutdown" if negate else value
    elif attr == "sticky":
        ps["sticky"] = not negate
        if not negate:
            ps.pop("dynamic", None)
    elif attr == "mac":
        macs = ps.setdefault("macs", [])
        if negate:
            if value in macs:
                macs.remove(value)
        elif value not in macs:
            macs.append(value)
    return None


def wifi_status(host):
    """(ap | None, signal %, visible networks) of a Wi-Fi client."""
    from ..devices.wireless import AccessPoint, signal, visible_networks
    topo = getattr(host.engine, "topology", None)
    devices = list(topo.devices.values()) if topo else []
    nets = visible_networks(host, devices)
    link = host.nic.link
    ap = None
    if link is not None and getattr(link, "wireless", False):
        peer = link.other(host.nic).device
        ap = peer if isinstance(peer, AccessPoint) else None
    return ap, (signal(ap, host) if ap else 0), nets


def wifi_connect(host, ssid, key):
    """Returns 'ok', 'absent' (SSID not heard) or 'auth' (wrong / missing key)."""
    from ..devices.wireless import ap_networks
    host.wifi = {"ssid": ssid, "key": key}
    topo = getattr(host.engine, "topology", None)
    if topo is not None:
        topo.rescan_wireless()
    ap, _, nets = wifi_status(host)
    if ap is not None and any(n[0] == ssid for n in ap_networks(ap)):
        return "ok"
    return "auth" if any(n[0] == ssid for n in nets) else "absent"


def wifi_disconnect(host):
    host.wifi = {"ssid": None, "key": None}
    topo = getattr(host.engine, "topology", None)
    if topo is not None:
        topo.rescan_wireless()


def ospf6_database_text(device) -> str:
    """OSPFv3 LSDB grouped by area and LSA type (all vendors print it this way, roughly)."""
    proc = getattr(device, "v6igp", {}).get("ospf6")
    if proc is None or not hasattr(proc, "database"):
        return ""
    names = {"router": "Router Link States", "prefix": "Intra Area Prefix Link States",
             "inter": "Inter Area Prefix Link States"}
    lines = [f"            OSPFv3 Router with ID ({proc.router_id()}) (Process ID {proc.cfg.tag})"]
    last = None
    for (area, typ, adv, lid), v in proc.database():
        if (area, typ) != last:
            last = (area, typ)
            lines += ["", f"                {names[typ]} (Area {area})", "",
                      "ADV Router      Age   Seq#        " + ("Prefix" if typ == "inter" else "Link count / Prefixes")]
        data = v["data"]
        extra = lid if typ == "inter" else (str(len(data)) if typ == "router" else ", ".join(n for n, _ in data))
        lines.append(f"{adv:<16}{12:<6}0x{v['seq']:08x}  {extra}")
    return "\n".join(lines)


def tunnel_set(tun, attr, value):
    """Shared tunnel settings: source (interface or IP), destination."""
    from ipaddress import IPv4Address
    if attr == "source":
        tun.source = value
    elif attr == "destination":
        tun.destination = IPv4Address(value) if value else None
    tun.device.link_state_changed(tun)
    return None


def tunnel_rows(device):
    return [p for p in device.ports.values() if getattr(p, "is_tunnel", False)]


def qos_class_match(device, cname, what, value, negate=False):
    """class-map / traffic classifier match statements."""
    from ..security.qos import parse_dscp
    m = device.qos_classes.setdefault(cname, {})
    if negate:
        m.pop(what, None)
        return None
    if what == "dscp":
        m["dscp"] = parse_dscp(value)
    elif what == "any":
        m["any"] = True
    else:
        m[what] = value
    return None


def qos_policy_class(device, pname, cname, attr=None, value=None, negate=False):
    """policy-map NAME / class NAME / set dscp / police."""
    from ..security.qos import QosClass, QosPolicy, parse_dscp
    pol = device.qos.setdefault(pname, QosPolicy(pname))
    c = next((x for x in pol.classes if x.name == cname), None)
    if c is None:
        c = QosClass(cname, {"any": True} if cname == "class-default" else {"class": cname})
        pol.classes = [x for x in pol.classes if x.name != "class-default"] + [c] + \
            [x for x in pol.classes if x.name == "class-default"]
    if attr == "dscp":
        c.set_dscp = None if negate else parse_dscp(value)
    elif attr == "police":
        c.police_bps = None if negate else int(value)
    return None


def qos_stats(device, port, direction):
    from ..security.qos import DSCP_NAMES
    name = port.qos_in if direction == "in" else port.qos_out
    pol = device.qos.get(name) if name else None
    if pol is None:
        return []
    out = [f" Service-policy {'input' if direction == 'in' else 'output'}: {name}", ""]
    for c in pol.classes:
        out += [f"   Class-map: {c.name} (match-any)", f"     {c.matched} packets, {c.bytes} bytes"]
        if c.set_dscp is not None:
            out.append(f"     QoS Set\n       dscp {DSCP_NAMES.get(c.set_dscp, c.set_dscp)}\n         Packets marked {c.matched - c.dropped}")
        if c.police_bps:
            out.append(f"     police:\n         cir {c.police_bps} bps\n       conformed {c.matched - c.dropped} packets; "
                       f"exceeded {c.dropped} packets; actions: drop")
        out.append("")
    return out


def mgmt_add(device, what, value, negate=False):
    """Shared Syslog/SNMP/NTP configuration used by every vendor CLI."""
    m = device.mgmt
    if what == "syslog":
        hosts = [h for h in m.syslog_hosts if h != value]
        m.syslog_hosts = hosts if negate else hosts + [value]
    elif what == "ntp":
        servers = [h for h in m.ntp_servers if h != value]
        m.set_ntp_servers(servers if negate else servers + [value])
    elif what == "ntp_master":
        m.ntp_master = None if negate else int(value or 8)
        if negate:
            m.synced, m.stratum = None, 16
        m.ensure()
    elif what in ("ro", "rw"):
        if negate:
            m.communities.pop(value, None)
        else:
            m.communities[value] = what
    elif what in ("location", "contact"):
        setattr(m, what, "" if negate else value.strip('"'))
    elif what == "trap":
        from ..services.mgmt import SEVERITY
        m.trap_level = 6 if negate else (int(value) if str(value).isdigit() else SEVERITY.index(value))
    return None


def mgmt_logging_text(device):
    from ..services.mgmt import SEVERITY
    m = device.mgmt
    lines = ["Syslog logging: enabled (0 messages dropped, 0 messages rate-limited, 0 flushes, 0 overruns)",
             f"    Console logging: level debugging, {len(m.buffer)} messages logged",
             f"    Buffer logging:  level debugging, {len(m.buffer)} messages logged",
             f"    Trap logging: level {SEVERITY[m.trap_level]}, {len(m.buffer)} message lines logged"]
    lines += [f"        Logging to {h}  (udp port 514, audit disabled, link up)" for h in m.syslog_hosts]
    lines += ["", "Log Buffer (8192 bytes):", ""] + list(m.buffer)
    return "\n".join(lines)


def mgmt_ntp_status(device):
    m = device.mgmt
    if m.synced:
        ref = "127.127.1.1" if m.ntp_master is not None and m.synced == "127.127.1.1" else m.synced
        return (f"Clock is synchronized, stratum {m.stratum}, reference is {ref}\n"
                "nominal freq is 250.0000 Hz, actual freq is 250.0000 Hz, precision is 2**10\n"
                "reference time is synchronized by NTP\nclock offset is 0.0000 msec, root delay is 1.00 msec")
    return "Clock is unsynchronized, stratum 16, no reference clock"


def mgmt_ntp_assoc(device):
    m = device.mgmt
    lines = ["  address         ref clock       st   when   poll reach  delay  offset   disp"]
    for s in m.ntp_servers:
        sync = m.synced == s
        lines.append(f"{'*~' if sync else ' ~'}{s:<16}{'.GPS.' if sync else '.INIT.':<16}"
                     f"{(m.stratum - 1) if sync else 16:<5}{'12':<7}{'64':<6}{'377' if sync else '0':<6}"
                     f"{'1.000' if sync else '0.000':<7}{'0.000':<9}{'0.937' if sync else '15937.'}")
    lines.append(" * sys.peer, # selected, + candidate, - outlyer, x falseticker, ~ configured")
    return "\n".join(lines)


def set_voice_vlan(port, vlan):
    """switchport voice vlan: the phone behind the port re-learns it (CDP / LLDP-MED)."""
    port.voice_vlan = vlan
    peer = port.peer
    if peer is not None and hasattr(peer.device, "_discover"):
        peer.device.engine.schedule(100.0, peer.device._discover)
    return None


def isis_neighbors_rows(device):
    """[(system id, hostname, interface, ip, state, hold)] for show isis neighbors / display isis peer."""
    p = getattr(device, "isis", None)
    if p is None:
        return []
    now = device.engine.now
    return [(a.system_id, a.hostname, a.port, str(a.ip or ""), a.state.upper(),
             max(0, 10 - int((now - a.last_seen) / 1000))) for a in p.adj.values()]


def isis_database_lines(device, star_own=True):
    p = getattr(device, "isis", None)
    if p is None:
        return []
    out = []
    for sid, e in sorted(p.lsdb.items()):
        own = "*" if sid == p.system_id and star_own else " "
        out.append(f"{e['hostname'] + '.00-00':<26}{own} 0x{e['seq']:08X}   0x{(e['seq'] * 7919) & 0xFFFF:04X}"
                   f"        1199              0/0/0")
        for n, m in e["neighbors"]:
            nh = next((x["hostname"] for s, x in p.lsdb.items() if s == n), n)
            out.append(f"  Metric: {m:<10} IS-Extended {nh}.00")
        for pfx, m in e["prefixes"]:
            out.append(f"  Metric: {m:<10} IP {pfx}")
    return out
