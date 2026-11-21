"""Network management: Syslog (UDP 514), SNMP v2c (UDP 161) and NTP (UDP 123).

* Every router/switch keeps a local log buffer (``show logging``) and copies each message
  to the configured syslog hosts.  Link up/down and "configured from console" are logged in
  the vendor's own format.
* The SNMP agent answers get/getnext/set for the system and interfaces groups of MIB-2
  when the community matches (wrong community: silently ignored, as on real devices).
* NTP clients poll their servers (first poll after 1 s, then every 64 s); once a reply
  arrives the device clock is "synchronized" with stratum = server stratum + 1.  Servers on
  the Server device and routers with ``ntp master`` answer requests.
"""
from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass
from ipaddress import IPv4Address

from ..stack.icmp import Job

SYSLOG_PORT, SNMP_PORT, NTP_PORT = 514, 161, 123
SEVERITY = ["emergencies", "alerts", "critical", "errors", "warnings", "notifications", "informational", "debugging"]
_ids = itertools.count(1000)
_sports = itertools.count(0)


@dataclass(frozen=True)
class SyslogMsg:
    host: str
    severity: int
    text: str
    stamp: str
    label = "Syslog"

    def summary(self) -> str:
        return f"<{8 * 23 + self.severity}> {self.text}"

    def layer_lines(self) -> list[str]:
        return ["Facility: local7 (23)", f"Severity: {SEVERITY[self.severity]} ({self.severity})",
                f"Hostname: {self.host}", f"Message: {self.text}"]


@dataclass(frozen=True)
class NtpMsg:
    mode: str                 # "client" | "server"
    stratum: int = 16
    stamp: float = 0.0
    label = "NTP"

    def summary(self) -> str:
        return f"NTP Version 4, {self.mode}" + (f", stratum {self.stratum}" if self.mode == "server" else "")

    def layer_lines(self) -> list[str]:
        return [f"Mode: {self.mode}", f"Stratum: {self.stratum}", f"Transmit timestamp: {self.stamp:.3f}"]


@dataclass(frozen=True)
class SnmpMsg:
    op: str                   # get | getnext | set | response
    community: str
    ident: int
    oid: tuple = ()
    value: object = None
    error: str = ""
    label = "SNMP"

    def summary(self) -> str:
        oid = ".".join(map(str, self.oid))
        return f"{'get-response' if self.op == 'response' else self.op + '-request'} {oid}"

    def layer_lines(self) -> list[str]:
        return ["Version: v2c", f"Community: {self.community}", f"PDU: {self.op}", f"Request ID: {self.ident}",
                f"OID: {'.'.join(map(str, self.oid))}"] + ([f"Value: {self.value}"] if self.value is not None else []) + \
               ([f"Error: {self.error}"] if self.error else [])


# ------------------------------------------------------------------------------------ MIB
MIB2 = (1, 3, 6, 1, 2, 1)
NAMES = {"sysDescr": MIB2 + (1, 1), "sysObjectID": MIB2 + (1, 2), "sysUpTime": MIB2 + (1, 3),
         "sysContact": MIB2 + (1, 4), "sysName": MIB2 + (1, 5), "sysLocation": MIB2 + (1, 6),
         "ifNumber": MIB2 + (2, 1), "ifIndex": MIB2 + (2, 2, 1, 1), "ifDescr": MIB2 + (2, 2, 1, 2),
         "ifPhysAddress": MIB2 + (2, 2, 1, 6), "ifAdminStatus": MIB2 + (2, 2, 1, 7),
         "ifOperStatus": MIB2 + (2, 2, 1, 8), "ifInUcastPkts": MIB2 + (2, 2, 1, 11),
         "ifOutUcastPkts": MIB2 + (2, 2, 1, 17), "system": MIB2 + (1,), "interfaces": MIB2 + (2,),
         "mib-2": MIB2, "iso": (1,)}
_REV = sorted(((v, k) for k, v in NAMES.items() if k not in ("system", "interfaces", "mib-2", "iso")),
              key=lambda x: -len(x[0]))


def parse_oid(text: str) -> tuple | None:
    t = text.split("::")[-1].strip().lstrip(".")
    base, _, rest = t.partition(".")
    if base in NAMES:
        tail = tuple(int(x) for x in rest.split(".") if x) if rest else ()
        return NAMES[base] + tail
    try:
        return tuple(int(x) for x in t.split(".") if x)
    except ValueError:
        return None


def oid_name(oid: tuple) -> str:
    for base, name in _REV:
        if oid[:len(base)] == base:
            mib = "IF-MIB" if name.startswith("if") and name != "ifNumber" else ("IF-MIB" if name == "ifNumber" else "SNMPv2-MIB")
            rest = ".".join(map(str, oid[len(base):]))
            return f"{mib}::{name}" + (f".{rest}" if rest else "")
    return "iso." + ".".join(map(str, oid[1:]))


def mib_rows(dev) -> list[tuple[tuple, str, object]]:
    """Sorted (oid, type, value) for the agent."""
    m = dev.mgmt
    ports = [p for p in dev.ports.values()]
    descr = f"{getattr(dev, 'vendor', '').title()} {getattr(dev, 'model', '')} (NetEmu)".strip()
    ticks = int(dev.engine.now // 10)
    rows = [(NAMES["sysDescr"] + (0,), "STRING", descr), (NAMES["sysObjectID"] + (0,), "OID", "SNMPv2-SMI::enterprises.9"),
            (NAMES["sysUpTime"] + (0,), "Timeticks", ticks), (NAMES["sysContact"] + (0,), "STRING", m.contact),
            (NAMES["sysName"] + (0,), "STRING", dev.hostname), (NAMES["sysLocation"] + (0,), "STRING", m.location),
            (NAMES["ifNumber"] + (0,), "INTEGER", len(ports))]
    for i, p in enumerate(ports, 1):
        rows += [(NAMES["ifIndex"] + (i,), "INTEGER", i), (NAMES["ifDescr"] + (i,), "STRING", p.name),
                 (NAMES["ifPhysAddress"] + (i,), "Hex-STRING", str(p.mac).replace(":", " ").upper() if p.mac else ""),
                 (NAMES["ifAdminStatus"] + (i,), "INTEGER", "up(1)" if p.admin_up else "down(2)"),
                 (NAMES["ifOperStatus"] + (i,), "INTEGER", "up(1)" if p.is_up else "down(2)"),
                 (NAMES["ifInUcastPkts"] + (i,), "Counter32", p.rx_frames),
                 (NAMES["ifOutUcastPkts"] + (i,), "Counter32", p.tx_frames)]
    return sorted(rows)


# ------------------------------------------------------------------------------------ device side
class Mgmt:
    def __init__(self, dev):
        self.dev = dev
        self.syslog_hosts: list[str] = []
        self.trap_level = 6                           # messages up to "informational" go to hosts
        self.buffer: deque = deque(maxlen=200)
        self.ntp_servers: list[str] = []
        self.ntp_master: int | None = None            # stratum when the device serves time itself
        self.synced: str | None = None
        self.stratum = 16
        self.communities: dict[str, str] = {}         # community -> "ro" | "rw"
        self.location = ""
        self.contact = ""
        self._last: dict[str, bool] = {}
        self._timer = None
        self._bound = False

    # -- persistence
    def to_dict(self) -> dict | None:
        d = {k: v for k, v in (("syslog", list(self.syslog_hosts)), ("ntp", list(self.ntp_servers)),
                               ("ntp_master", self.ntp_master), ("snmp", dict(self.communities)),
                               ("location", self.location), ("contact", self.contact)) if v}
        if self.trap_level != 6:
            d["trap_level"] = self.trap_level
        return d or None

    def apply(self, d: dict | None) -> None:
        d = d or {}
        self.syslog_hosts = list(d.get("syslog", []))
        self.ntp_servers = list(d.get("ntp", []))
        self.ntp_master = d.get("ntp_master")
        self.communities = dict(d.get("snmp", {}))
        self.location, self.contact = d.get("location", ""), d.get("contact", "")
        self.trap_level = d.get("trap_level", 6)
        if self.synced not in self.ntp_servers:          # a commit that keeps the server keeps the sync
            self.synced, self.stratum = None, 16
        self.ensure()

    def ensure(self) -> None:
        dev = self.dev
        if not self._bound and hasattr(dev, "bind_udp"):
            dev.bind_udp(SNMP_PORT, self._snmp)
            dev.bind_udp(NTP_PORT, self._ntp)
            self._bound = True
        if self.ntp_servers and self._timer is None:
            self._timer = dev.engine.schedule(1000.0, self._poll)
        if self.ntp_master is not None:
            self.synced, self.stratum = "127.127.1.1", self.ntp_master

    # -- syslog
    def stamp(self) -> str:
        from ..cli.render import sim_clock
        return ("" if self.synced else "*") + sim_clock(self.dev.engine.now).strftime("%b %d %H:%M:%S.000")

    def log(self, severity: int, text: str) -> None:
        st = self.stamp()
        self.buffer.append(f"{st}: {text}")
        if severity > self.trap_level:
            return
        for h in self.syslog_hosts:
            try:
                dst = IPv4Address(h)
            except ValueError:
                continue
            self.dev.send_udp(dst, SYSLOG_PORT, SYSLOG_PORT, SyslogMsg(self.dev.hostname, severity, text, st))

    def link_event(self, port) -> None:
        if getattr(port, "is_svi", False) and port.name not in self._last and port.is_up:
            self._last[port.name] = True
            return
        up = bool(port.is_up)
        prev = self._last.get(port.name)
        self._last[port.name] = up
        if prev is None and not up or prev == up:
            return
        vendor = getattr(self.dev, "vendor", "cisco")
        word = "up" if up else "down"
        if vendor == "huawei":
            text = (f"%%01IFNET/4/LINK_STATE(l)[0]:The line protocol IP on the interface {port.name} has entered "
                    f"the {word.upper()} state.")
        elif vendor == "mikrotik":
            text = f"interface,info {port.name} link {word}"
        elif vendor == "juniper":
            text = (f"mib2d: SNMP_TRAP_LINK_{word.upper()}: ifIndex 5{len(self._last):02d}, ifAdminStatus "
                    f"{'up(1)' if port.admin_up else 'down(2)'}, ifOperStatus {'up(1)' if up else 'down(2)'}, ifName {port.name}")
        elif vendor == "extreme":
            text = f"<Info:vlan.msgs.portLink{'Active' if up else 'Down'}> Port {port.name} link {word}"
        else:
            self.log(3, f"%LINK-3-UPDOWN: Interface {port.name}, changed state to {word}")
            text = f"%LINEPROTO-5-UPDOWN: Line protocol on Interface {port.name}, changed state to {word}"
        self.log(5 if vendor == "cisco" else 4, text)

    # -- NTP client / master
    def _poll(self) -> None:
        self._timer = None
        if not self.ntp_servers:
            return
        for s in self.ntp_servers:
            try:
                self.dev.send_udp(IPv4Address(s), NTP_PORT, NTP_PORT, NtpMsg("client", 16, self.dev.engine.now))
            except ValueError:
                pass
        self._timer = self.dev.engine.schedule(64000.0, self._poll)

    def _ntp(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, NtpMsg):
            return
        if m.mode == "client" and self.ntp_master is not None:
            self.dev.send_udp(pkt.src, NTP_PORT, NTP_PORT, NtpMsg("server", self.ntp_master, self.dev.engine.now),
                              src=pkt.dst)
        elif m.mode == "server" and str(pkt.src) in self.ntp_servers and m.stratum < 16:
            first = self.synced is None
            self.synced, self.stratum = str(pkt.src), m.stratum + 1
            if first:
                self.log(5, "%NTP-5-PEERSYNC: NTP synced to peer " + str(pkt.src)
                         if getattr(self.dev, "vendor", "") == "cisco" else f"NTP: synchronized to {pkt.src}")

    def set_ntp_servers(self, servers: list[str]) -> None:
        self.ntp_servers = list(servers)
        if not servers and self.ntp_master is None:
            self.synced, self.stratum = None, 16
        if self.synced and self.synced not in servers and self.ntp_master is None:
            self.synced, self.stratum = None, 16
        self.ensure()

    # -- SNMP agent
    def _snmp(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, SnmpMsg) or m.op == "response":
            return
        access = self.communities.get(m.community)
        if access is None:
            return
        rows = mib_rows(self.dev)
        oid, typ, value, err = m.oid, None, None, ""
        if m.op == "get":
            row = next((r for r in rows if r[0] == m.oid), None)
            if row is None:
                err = "noSuchObject"
            else:
                oid, typ, value = row
        elif m.op == "getnext":
            row = next((r for r in rows if r[0] > m.oid), None)
            if row is None:
                err = "endOfMibView"
            else:
                oid, typ, value = row
        elif m.op == "set":
            if access != "rw":
                err = "noAccess"
            elif m.oid == NAMES["sysName"] + (0,):
                self.dev.hostname = str(m.value)
                self.dev.engine.emit("device_changed", self.dev)
            elif m.oid == NAMES["sysLocation"] + (0,):
                self.location = str(m.value)
            elif m.oid == NAMES["sysContact"] + (0,):
                self.contact = str(m.value)
            else:
                err = "notWritable"
            typ, value = "STRING", m.value
        self.dev.send_udp(pkt.src, SNMP_PORT, pkt.payload.sport,
                          SnmpMsg("response", m.community, m.ident, oid, (typ, value) if typ else None, err), src=pkt.dst)


def clock_text_synced(dev) -> bool:
    m = getattr(dev, "mgmt", None)
    return bool(m and m.synced)


# ------------------------------------------------------------------------------------ server side
class SyslogServer:
    def __init__(self, owner, enabled: bool = True):
        self.dev = owner
        self.enabled = enabled
        self.messages: list[tuple[str, str, str]] = []      # (time, host ip, text)

    def handle(self, pkt, port) -> None:
        m = pkt.payload.data
        if self.enabled and isinstance(m, SyslogMsg):
            self.messages.append((m.stamp.lstrip("*"), str(pkt.src), m.text))
            self.dev.engine.emit("device_changed", self.dev)


class NtpServer:
    def __init__(self, owner, enabled: bool = True):
        self.dev = owner
        self.enabled = enabled
        self.served = 0

    def handle(self, pkt, port) -> None:
        m = pkt.payload.data
        if self.enabled and isinstance(m, NtpMsg) and m.mode == "client":
            self.served += 1
            self.dev.send_udp(pkt.src, NTP_PORT, NTP_PORT, NtpMsg("server", 1, self.dev.engine.now), src=pkt.dst)


# ------------------------------------------------------------------------------------ manager (hosts)
class SnmpRequest(Job):
    """snmpget / snmpset / snmpwalk from a host; on_row(oid, typ, value) per answer, on_done(error)."""

    TIMEOUT_MS = 1000.0
    RETRIES = 5

    def __init__(self, host, agent: IPv4Address, community: str, op: str, oid: tuple, on_row, on_done, value=None):
        super().__init__()
        self.host, self.agent, self.community, self.op = host, agent, community, op
        self.root, self.oid, self.value = oid, oid, value
        self.on_row, self.on_done = on_row, on_done
        self.sport = 50000 + next(_sports) % 10000
        self.tries = 0
        self.ident = next(_ids)
        self._timer = None

    def start(self) -> "SnmpRequest":
        self.host.bind_udp(self.sport, self._receive)
        self._send()
        return self

    def _send(self) -> None:
        if self.done:
            return
        self.tries += 1
        if self.tries > self.RETRIES + 1:
            self._finish(f"Timeout: No Response from {self.agent}")
            return
        op = "getnext" if self.op == "walk" else self.op
        if not self.host.send_udp(self.agent, self.sport, SNMP_PORT, SnmpMsg(op, self.community, self.ident, self.oid,
                                                                             self.value)):
            self._finish(f"Timeout: No Response from {self.agent}")
            return
        self._timer = self.host.engine.schedule(self.TIMEOUT_MS, self._send)

    def _receive(self, pkt, port) -> None:
        m = pkt.payload.data
        if not isinstance(m, SnmpMsg) or m.op != "response" or m.ident != self.ident:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        if self.op == "walk":
            if m.error or m.oid[:len(self.root)] != self.root:
                self._finish("")
                return
            self.on_row(m.oid, *m.value)
            self.oid, self.tries, self.ident = m.oid, 0, next(_ids)
            self._send()
            return
        if m.error:
            self._finish(m.error)
            return
        self.on_row(m.oid, *m.value)
        self._finish("")

    def _finish(self, err) -> None:
        if self.done:
            return
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_udp(self.sport)
        self.on_done(err)
        self.finish()

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancelled = True
        self.host.unbind_udp(self.sport)
        super().cancel()


def format_row(oid, typ, value) -> str:
    if typ == "Timeticks":
        t = int(value)
        s, cs = divmod(t, 100)
        h, s = divmod(s, 3600)
        mi, s = divmod(s, 60)
        return f"DISMAN-EVENT-MIB::sysUpTimeInstance = Timeticks: ({t}) {h}:{mi:02d}:{s:02d}.{cs:02d}"
    v = f'"{value}"' if typ == "STRING" else value
    return f"{oid_name(oid)} = {typ}: {v}"
