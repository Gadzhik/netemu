"""MikroTik RouterOS (v7-style) dialect.

RouterOS has a menu hierarchy (/ip address, /interface, ...) with commands
like ``print``, ``add key=value``, ``remove <number>`` -- quite different
from IOS-like trees, so it has its own small parser.
"""
from __future__ import annotations

import re
from typing import Callable

from ..addresses import IPv4Address, IPv4Interface, IPv4Network, is_valid_ip
from ..stack.icmp import PingListener, TraceListener
from .parser import is_ipv6 as P_is_ipv6
from .base import CliSession, make_ping, overlapping_port, v6_route_cmd, make_trace
from .render import sim_clock, table, uptime_text

_TOK = re.compile(r'(?:[^\s"]+|"[^"]*")+')


def _mac(m) -> str:
    h = f"{m.value:012X}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _unquote(v: str) -> str:
    return v.replace('"', "")


class MtPing(PingListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.dst = s.dst
        self.w("  SEQ HOST                                     SIZE TTL TIME       STATUS\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w(f"  {seq:>3} {str(src):<40} {size:>4} {ttl:>3} {str(round(rtt)) + 'ms':<10}\n")

    def on_timeout(self, seq):
        self.w(f"  {seq:>3} {str(self.dst):<40} {'':>4} {'':>3} {'':<10} timeout\n")

    def on_send_fail(self, seq):
        self.w(f"  {seq:>3} {'':<40} {'':>4} {'':>3} {'':<10} no route to host\n")

    def on_error(self, seq, kind, src):
        status = "host unreachable" if kind == "unreachable" else "TTL exceeded"
        self.w(f"  {seq:>3} {str(src):<40} {'':>4} {'':>3} {'':<10} {status}\n")

    def on_finish(self, st):
        text = f"    sent={st.sent} received={st.received} packet-loss={st.loss_percent}%"
        if st.received:
            text += f" min-rtt={st.rtt('min')}ms avg-rtt={st.rtt('avg')}ms max-rtt={st.rtt('max')}ms"
        self.w(text + " \n")


class MtTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w("Columns: ADDRESS, LOSS, SENT, LAST, AVG, BEST, WORST, STD-DEV\n"
               f"{'#':<3}{'ADDRESS':<17}{'LOSS':<6}{'SENT':<6}{'LAST':<9}{'AVG':<6}{'BEST':<6}{'WORST':<7}STD-DEV\n")

    def on_hop(self, hop):
        ok = [r for r in hop.probes if r is not None]
        loss = round(100 * (len(hop.probes) - len(ok)) / len(hop.probes))
        addr = str(hop.address) if hop.address else ""
        if ok:
            last = f"{round(hop.probes[-1])}ms" if hop.probes[-1] is not None else "timeout"
            avg, best, worst = round(sum(ok) / len(ok)), round(min(ok)), round(max(ok))
            self.w(f"{hop.ttl:<3}{addr:<17}{str(loss) + '%':<6}{len(hop.probes):<6}{last:<9}"
                   f"{avg:<6}{best:<6}{worst:<7}0\n")
        else:
            self.w(f"{hop.ttl:<3}{addr:<17}{'100%':<6}{len(hop.probes):<6}timeout\n")


class MikrotikSession(CliSession):
    def __init__(self, device):
        super().__init__(device)
        self.cwd: tuple[str, ...] = ()
        self._menus = self._build()

    @property
    def is_router(self) -> bool:
        return self.device.category == "router"

    @property
    def prompt(self) -> str:
        path = ("/" + " ".join(self.cwd)) if self.cwd else ""
        return f"[admin@{self.device.hostname}] {path}> " if path else f"[admin@{self.device.hostname}] > "

    def banner(self) -> str:
        return ("\n\n  MMM      MMM       KKK                          TTTTTTTTTTT      KKK\n"
                "  MMMM    MMMM       KKK                          TTTTTTTTTTT      KKK\n"
                "  MMM MMMM MMM  III  KKK  KKK  RRRRRR     OOOOOO      TTT     III  KKK  KKK\n"
                "  MMM  MM  MMM  III  KKKKK     RRR  RRR  OOO  OOO     TTT     III  KKKKK\n"
                "  MMM      MMM  III  KKK KKK   RRRRRR    OOO  OOO     TTT     III  KKK KKK\n"
                "  MMM      MMM  III  KKK  KKK  RRR  RRR   OOOOOO      TTT     III  KKK  KKK\n\n"
                "  MikroTik RouterOS 7.16.1 (c) 1999-2024       https://www.mikrotik.com/\n\n"
                "Press F1 for help\n\n")

    # -- menu definition -----------------------------------------------------------
    def _build(self) -> dict[tuple[str, ...], dict[str, tuple[str, Callable]]]:
        m: dict[tuple[str, ...], dict[str, tuple[str, Callable]]] = {
            (): {"export": ("Print or save an export script", lambda a: self.export()),
                 "ping": ("Send ICMP Echo packets", self.ping),
                 "quit": ("Quit console", lambda a: None)},
            ("interface",): {"print": ("Print values of item properties", lambda a: self.if_print()),
                             "enable": ("Enable items", lambda a: self.if_enable(a, True)),
                             "disable": ("Disable items", lambda a: self.if_enable(a, False)),
                             "set": ("Change item properties", self.if_set),
                             "comment": ("Set comment for items", self.if_comment)},
            ("interface", "ethernet"): {"print": ("Print values of item properties", lambda a: self.if_print()),
                                        "set": ("Change item properties", self.if_set),
                                        "enable": ("Enable items", lambda a: self.if_enable(a, True)),
                                        "disable": ("Disable items", lambda a: self.if_enable(a, False))},
            ("system",): {},
            ("system", "identity"): {"print": ("Print values of item properties",
                                               lambda a: f"  name: {self.device.hostname}"),
                                     "set": ("Change item properties", self.identity_set)},
            ("system", "resource"): {"print": ("Print values of item properties", lambda a: self.resource())},
            ("system", "clock"): {"print": ("Print values of item properties", lambda a: self.clock())},
            ("tool",): {"traceroute": ("Traceroute", self.traceroute)},
        }
        m[("system",)]["reset-configuration"] = ("Reset configuration", lambda a: self.reset())
        m[("system",)]["ssh"] = ("Open an SSH session", lambda a: self.remote_cmd("ssh", a))
        m[("system",)]["telnet"] = ("Open a Telnet session", lambda a: self.remote_cmd("telnet", a))
        m[("ip", "service")] = {"print": ("Print values of item properties", lambda a: self.service_print()),
                                "set": ("Change item properties", self.service_set),
                                "enable": ("Enable items", lambda a: self.service_onoff(a, True)),
                                "disable": ("Disable items", lambda a: self.service_onoff(a, False))}
        if not self.is_router:
            m[("interface", "bonding")] = {"print": ("Print values of item properties", lambda a: self.bond_print()),
                                           "add": ("Create a new item", self.bond_add),
                                           "remove": ("Remove item", self.bond_remove)}
        m[("ipv6", "pool")] = {"print": ("Print values of item properties",
                                         lambda a: "\n".join(f"{k} {v}" for k, v in self.device.extra.get("mt_v6pools", {}).items())),
                               "add": ("Create a new item", self.v6pool_add)}
        m[("ipv6", "dhcp-server")] = {"print": ("Print values of item properties", lambda a: self.dhcp6s_print()),
                                      "add": ("Create a new item", self.dhcp6s_add)}
        m[("ipv6", "dhcp-server", "binding")] = {"print": ("Print values of item properties",
                                                           lambda a: self.dhcp6_binding_print())}
        m[("ip", "neighbor")] = {"print": ("Print values of item properties", lambda a: self.mt_neighbors())}
        m[("ip", "neighbor", "discovery-settings")] = {
            "set": ("Change item properties", self.mt_discovery_set),
            "print": ("Print values of item properties", lambda a: self.mt_discovery_print())}
        m[("queue",)] = {}
        m[("queue", "simple")] = {"add": ("Create a new item", self.mt_queue_add),
                                  "print": ("Print values of item properties", lambda a: self.mt_queue_print()),
                                  "remove": ("Remove item", self.mt_queue_remove)}
        m[("ip", "firewall", "mangle")] = {"add": ("Create a new item", self.mt_mangle_add),
                                           "print": ("Print values of item properties", lambda a: self.mt_mangle_print()),
                                           "remove": ("Remove item", self.mt_mangle_remove)}
        m[("system", "logging")] = {"print": ("Print values of item properties", lambda a: self.mt_logging_print())}
        m[("system", "logging", "action")] = {"add": ("Create a new item", self.mt_log_action_add),
                                              "print": ("Print values of item properties", lambda a: self.mt_log_actions())}
        m[("system", "ntp")] = {}
        m[("system", "ntp", "client")] = {"set": ("Change item properties", self.mt_ntp_set),
                                          "print": ("Print values of item properties", lambda a: self.mt_ntp_print())}
        m[("snmp",)] = {"set": ("Change item properties", self.mt_snmp_set),
                        "print": ("Print values of item properties", lambda a: self.mt_snmp_print())}
        m[("snmp", "community")] = {"add": ("Create a new item", self.mt_comm_add),
                                    "print": ("Print values of item properties", lambda a: self.mt_comm_print())}
        m[("log",)] = {"print": ("Print values of item properties", lambda a: "\n".join(
            x.lstrip("*") for x in self.device.mgmt.buffer))}
        m[("interface", "gre")] = {"print": ("Print values of item properties", lambda a: self.gre_print()),
                                   "add": ("Create a new item", self.gre_add),
                                   "remove": ("Remove item", self.gre_remove)}
        m[("interface", "vrrp")] = {"print": ("Print values of item properties", lambda a: self.vrrp_print()),
                                    "add": ("Create a new item", self.vrrp_add),
                                    "remove": ("Remove item", self.vrrp_remove)}
        m[("user",)] = {"print": ("Print values of item properties", lambda a: self.user_print()),
                        "add": ("Create a new item", self.user_add),
                        "set": ("Change item properties", self.user_set),
                        "remove": ("Remove item", self.user_remove)}
        m.setdefault(("ip",), {})
        m[("system",)]["reboot"] = ("Reboot the system", lambda a: self.reboot())
        if True:
            m.update({
                ("ip",): {},
                ("ip", "address"): {"print": ("Print values of item properties", lambda a: self.addr_print()),
                                    "add": ("Create a new item", self.addr_add),
                                    "remove": ("Remove item", self.addr_remove)},
                ("ip", "settings"): {"set": ("Change item properties", self.ip_settings_set),
                                     "print": ("Print values of item properties", lambda a: self.ip_settings_print())},
                ("ip", "route"): {"print": ("Print values of item properties", lambda a: self.route_print()),
                                  "add": ("Create a new item", self.route_add),
                                  "remove": ("Remove item", self.route_remove)},
                ("ip", "arp"): {"print": ("Print values of item properties", lambda a: self.arp_print())},
                ("ipv6",): {},
                ("ipv6", "address"): {"print": ("Print values of item properties", lambda a: self.v6_addr_print()),
                                      "add": ("Create a new item", self.v6_addr_add),
                                      "remove": ("Remove item", self.v6_addr_remove)},
                ("ipv6", "route"): {"print": ("Print values of item properties", lambda a: self.v6_route_print()),
                                    "add": ("Create a new item", self.v6_route_add)},
                ("ipv6", "neighbor"): {"print": ("Print values of item properties", lambda a: self.v6_nd_print())},
                ("ipv6", "nd"): {"set": ("Change item properties", self.v6_nd_set)},
                ("ip", "firewall"): {},
                ("ip", "firewall", "filter"): {"print": ("Print values of item properties", self.fw_print),
                                               "add": ("Create a new item", lambda a: self.fw_add(a, "filter")),
                                               "remove": ("Remove item", lambda a: self.fw_remove(a, "filter"))},
                ("ip", "firewall", "nat"): {"print": ("Print values of item properties", lambda a: self.nat_print()),
                                            "add": ("Create a new item", lambda a: self.fw_add(a, "nat")),
                                            "remove": ("Remove item", lambda a: self.fw_remove(a, "nat"))},
                ("ip", "firewall", "connection"): {"print": ("Print values of item properties",
                                                             lambda a: self.conn_print() + "\n" + self.conn_tracking())},
                ("ip", "firewall", "address-list"): {"print": ("Print values of item properties",
                                                               lambda a: self.addrlist_print()),
                                                     "add": ("Create a new item", self.addrlist_add),
                                                     "remove": ("Remove item", self.addrlist_remove)},
                ("ip", "pool"): {"print": ("Print values of item properties", lambda a: self.pool_print()),
                                 "add": ("Create a new item", self.pool_add),
                                 "remove": ("Remove item", lambda a: self._mt_remove(a, "mt_pools"))},
                ("ip", "dhcp-server"): {"print": ("Print values of item properties", lambda a: self.dhcps_print()),
                                        "add": ("Create a new item", self.dhcps_add),
                                        "remove": ("Remove item", lambda a: self._mt_remove(a, "mt_dhcps"))},
                ("ip", "dhcp-server", "network"): {"print": ("Print values of item properties",
                                                             lambda a: self.dhcpnet_print()),
                                                   "add": ("Create a new item", self.dhcpnet_add),
                                                   "remove": ("Remove item", lambda a: self._mt_remove(a, "mt_dhcpnets"))},
                ("ip", "dhcp-server", "lease"): {"print": ("Print values of item properties",
                                                           lambda a: self.lease_print())},
                ("ip", "dhcp-relay"): {"print": ("Print values of item properties", lambda a: self.relay_print()),
                                       "add": ("Create a new item", self.relay_add),
                                       "remove": ("Remove item", lambda a: self._mt_remove(a, "mt_relays"))},
                ("routing", "rip"): {},
                ("routing", "rip", "instance"): {"print": ("Print values of item properties", lambda a: self.rip_inst_print()),
                                                 "add": ("Create a new item", self.rip_inst_add),
                                                 "remove": ("Remove item", lambda a: self._rip_remove(a, "mt_rip"))},
                ("routing", "rip", "interface-template"): {
                    "print": ("Print values of item properties", lambda a: self.rip_tpl_print()),
                    "add": ("Create a new item", self.rip_tpl_add),
                    "remove": ("Remove item", lambda a: self._rip_remove(a, "mt_rip_tpl"))},
                ("routing", "bgp"): {},
                ("routing", "bgp", "connection"): {"print": ("Print values of item properties",
                                                             lambda a: self.bgpconn_print()),
                                                   "add": ("Create a new item", self.bgpconn_add),
                                                   "remove": ("Remove item", self.bgpconn_remove)},
                ("routing", "bgp", "session"): {"print": ("Print values of item properties",
                                                          lambda a: self.bgpsess_print())},
                ("routing", "bgp", "advertisements"): {"print": ("Print values of item properties",
                                                                 lambda a: self.bgpadv_print())},
                ("routing",): {},
                ("routing", "ospf"): {},
                ("routing", "ospf", "instance"): {"print": ("Print values of item properties", lambda a: self.ospf_inst_print()),
                                                  "add": ("Create a new item", self.ospf_inst_add),
                                                  "set": ("Change item properties", self.ospf_inst_add),
                                                  "remove": ("Remove item", self.ospf_inst_remove)},
                ("routing", "ospf", "area"): {"print": ("Print values of item properties", lambda a: self.ospf_area_print()),
                                              "add": ("Create a new item", self.ospf_area_add),
                                              "remove": ("Remove item", self.ospf_area_remove)},
                ("routing", "ospf", "interface-template"): {
                    "print": ("Print values of item properties", lambda a: self.ospf_tpl_print()),
                    "add": ("Create a new item", self.ospf_tpl_add),
                    "remove": ("Remove item", self.ospf_tpl_remove)},
                ("routing", "ospf", "interface"): {"print": ("Print values of item properties",
                                                             lambda a: self.ospf_if_print())},
                ("routing", "ospf", "neighbor"): {"print": ("Print values of item properties",
                                                            lambda a: self.ospf_nb_print())},
                ("routing", "ospf", "lsa"): {"print": ("Print values of item properties",
                                                       lambda a: self.ospf_lsa_print())},
                ("interface", "vlan"): {"print": ("Print values of item properties", lambda a: self.vlan_print()),
                                        "add": ("Create a new item", self.vlan_add),
                                        "remove": ("Remove item", self.vlan_remove)},
            })
        if self.is_router:
            m.update({
                ("interface", "bridge"): {"print": ("Print values of item properties", lambda a: self.lo_print()),
                                          "add": ("Create a new item", self.lo_add),
                                          "remove": ("Remove item", self.lo_remove)},
            })
        else:
            m.update({
                ("interface", "bridge"): {"print": ("Print values of item properties",
                                                    lambda a: self.bridge_print()),
                                          "set": ("Change item properties", self.bridge_set),
                                          "monitor": ("Monitor bridge status", lambda a: self.bridge_monitor())},
                ("interface", "bridge", "vlan"): {"print": ("Print values of item properties",
                                                            lambda a: self.bridge_vlan_print()),
                                                  "add": ("Create a new item", self.bridge_vlan_add),
                                                  "remove": ("Remove item", self.bridge_vlan_remove)},
                ("interface", "bridge", "host"): {"print": ("Print values of item properties",
                                                            lambda a: self.host_print())},
                ("interface", "bridge", "port"): {"print": ("Print values of item properties",
                                                            lambda a: self.bridge_port_print()),
                                                  "set": ("Change item properties", self.bridge_port_set)},
            })
        m[()]["traceroute"] = ("Traceroute", self.traceroute)
        return m

    def _submenus(self, path: tuple[str, ...]) -> list[str]:
        n = len(path)
        return sorted({p[n] for p in self._menus if len(p) > n and p[:n] == path})

    def _commands(self, path: tuple[str, ...]) -> dict[str, tuple[str, Callable]]:
        cmds = dict(self._menus.get(path, {}))
        for g in ("ping", "quit", "export"):  # handy everywhere
            cmds.setdefault(g, self._menus[()][g])
        return cmds

    @staticmethod
    def _pick(word: str, options) -> list[str]:
        if word in options:
            return [word]
        return [o for o in options if o.startswith(word)]

    # -- execution ---------------------------------------------------------------------
    def _execute(self, line: str):
        toks = [(m.group(0), m.start()) for m in _TOK.finditer(line)]
        if not toks:
            return None
        # Expand "/ip/address/print" style and leading "/".
        expanded: list[tuple[str, int]] = []
        absolute = False
        for i, (t, pos) in enumerate(toks):
            if i == 0 and t.startswith("/"):
                absolute = True
                t, pos = t[1:], pos + 1
                if not t:
                    continue
            if "/" in t and "=" not in t and not is_valid_ip(t.split("/")[0]):
                off = pos
                for part in t.split("/"):
                    if part:
                        expanded.append((part, off))
                    off += len(part) + 1
            else:
                expanded.append((t, pos))
        path = () if absolute else self.cwd
        i = 0
        while i < len(expanded):
            word, pos = expanded[i]
            if word == "..":
                path = path[:-1]
                i += 1
                continue
            subs = self._pick(word, self._submenus(path))
            cmds = self._pick(word, self._commands(path))
            if len(subs) == 1 and not (word in cmds):
                path = path + (subs[0],)
                i += 1
                continue
            if len(cmds) == 1:
                args = expanded[i + 1:]
                desc, fn = self._commands(path)[cmds[0]]
                try:
                    return fn(args)
                except _MtError as e:
                    return str(e)
            if len(subs) + len(cmds) > 1:
                return f"ambiguous value of command, more than one possible value (line 1 column {pos + 1})"
            return f"bad command name {word} (line 1 column {pos + 1})"
        if path in self._menus or self._submenus(path):
            self.cwd = path
            return None
        return None

    def help(self, line: str) -> str:
        path = self.cwd
        for w in line.split():
            if w.startswith("/"):
                path = ()
                w = w[1:]
            subs = self._pick(w, self._submenus(path)) if w else []
            if len(subs) == 1:
                path = path + (subs[0],)
        out = []
        for c, (d, _) in sorted(self._commands(path).items()):
            out.append((c, d))
        for s in self._submenus(path):
            out.append((s + "/", ""))
        width = max(len(a) for a, _ in out)
        return "\n".join(f"{a:<{width}} -- {b}" if b else a for a, b in out) + "\n"

    def complete(self, line: str) -> str | None:
        if not line or line.endswith(" "):
            return None
        head, _, last = line.rpartition(" ")
        path = self.cwd
        for w in head.split():
            if w.startswith("/"):
                path, w = (), w[1:]
            subs = self._pick(w, self._submenus(path))
            if len(subs) == 1:
                path = path + (subs[0],)
        word = last.lstrip("/")
        lead = last[: len(last) - len(word)]
        options = self._submenus(path) + list(self._commands(path))
        match = [o for o in options if o.startswith(word)]
        if len(match) != 1:
            return None
        return (head + " " if head else "") + lead + match[0] + " "

    # -- argument helpers ----------------------------------------------------------------
    def _kv(self, args, allowed: set[str], positional: int = 0):
        kv: dict[str, str] = {}
        pos: list[str] = []
        for tok, col in args:
            if "=" in tok:
                k, _, v = tok.partition("=")
                ks = self._pick(k, allowed)
                if len(ks) != 1:
                    raise _MtError(f"expected end of command (line 1 column {col + 1})")
                kv[ks[0]] = _unquote(v)
            else:
                if len(pos) >= positional:
                    raise _MtError(f"expected end of command (line 1 column {col + 1})")
                pos.append(_unquote(tok))
        return kv, pos

    @staticmethod
    def _target(args):
        """Split '[find interface=ether1] key=value ...' or 'ether1 key=value' into (name, rest)."""
        if args and args[0][0].startswith("["):
            words, i = [], 0
            while i < len(args):
                words.append(args[i][0])
                i += 1
                if words[-1].endswith("]"):
                    break
            inner = " ".join(words).strip("[]").split()
            name = next((w.split("=", 1)[1] for w in inner if "=" in w), None)
            return name, args[i:]
        if args and "=" not in args[0][0]:
            return _unquote(args[0][0]), args[1:]
        return None, args

    def _port(self, name: str):
        dev = self.device
        if name == "bridge" and not self.is_router:
            return dev.ports.get("bridge") or dev.add_svi(1, "bridge", "bridge")
        if name.isdigit():
            ports = list(dev.ports.values())
            if int(name) < len(ports):
                return ports[int(name)]
        p = dev.ports.get(name)
        if p is None:
            raise _MtError("input does not match any value of interface")
        return p

    # -- /interface ------------------------------------------------------------------------
    def if_print(self):
        lines = ["Flags: R - RUNNING; X - DISABLED", "Columns: NAME, TYPE, ACTUAL-MTU, L2MTU, MAC-ADDRESS"]
        body = []
        for i, p in enumerate(self.device.ports.values()):
            flag = ("X" if not p.admin_up else " ") + ("R" if p.is_up else " ")
            if p.description:
                body.append((None, f";;; {p.description}"))
            body.append((i, flag, p.name, "vlan" if p.is_sub else "ether", "1500", "1568", _mac(p.mac)))
        t = table(["#", "  ", "NAME", "TYPE", "ACTUAL-MTU", "L2MTU", "MAC-ADDRESS"],
                  [[str(b[0]), b[1], b[2], b[3], b[4], b[5], b[6]] for b in body if b[0] is not None], gap=1)
        out = t.split("\n")
        result = lines + [out[0]]
        k = 1
        for b in body:
            if b[0] is None:
                result.append("   " + b[1])
            else:
                result.append(out[k])
                k += 1
        return "\n".join(result)

    def if_enable(self, args, up: bool):
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        for tok, col in args:
            for name in tok.split(","):
                self._port(name).set_admin(up)
        return None

    def if_set(self, args):
        kv, pos = self._kv(args, {"comment", "disabled", "name"}, positional=1)
        if not pos:
            raise _MtError("missing value(s) of argument(s) numbers")
        p = self._port(pos[0])
        if "comment" in kv:
            p.description = kv["comment"]
        if "disabled" in kv:
            p.set_admin(kv["disabled"] not in ("yes", "true"))
        if "name" in kv and kv["name"] != p.name:
            raise _MtError("failure: renaming interfaces is not supported in this simulator")
        return None

    def if_comment(self, args):
        if len(args) < 2:
            raise _MtError("missing value(s) of argument(s) comment")
        p = self._port(_unquote(args[0][0]))
        p.description = " ".join(_unquote(t) for t, _ in args[1:])
        return None

    # -- /ip address -----------------------------------------------------------------------
    def _addresses(self):
        """[(port, address)] - RouterOS allows several addresses per interface."""
        return [(p, a) for p in self.device.ports.values() if p.ip is not None for a in [p.ip] + list(p.ip2)]

    def addr_print(self):
        rows = [[str(i), " ", a.with_prefixlen, str(a.network.network_address), p.name]
                for i, (p, a) in enumerate(self._addresses())]
        return "Flags: D - DYNAMIC; I - INVALID\nColumns: ADDRESS, NETWORK, INTERFACE\n" + table(
            ["#", " ", "ADDRESS", "NETWORK", "INTERFACE"], rows, gap=1)

    def addr_add(self, args):
        kv, _ = self._kv(args, {"address", "interface", "network", "comment", "disabled"})
        if "address" not in kv or "interface" not in kv:
            raise _MtError("failure: address and interface must be specified")
        vr = self._vrrp_named(kv["interface"])
        if vr is not None:
            from .base import fhrp_set
            fhrp_set(self.device, self.device.ports[vr["interface"]], "vrrp", vr["vrid"], "vip",
                     kv["address"].split("/")[0])
            vr["address"] = kv["address"]
            return None
        p = self._port(kv["interface"])
        text = kv["address"] if "/" in kv["address"] else kv["address"] + "/32"
        try:
            iface = IPv4Interface(text)
        except ValueError:
            raise _MtError("invalid value for argument address")
        if p.ip == iface or iface in p.ip2:
            raise _MtError("failure: already have such address")
        other = overlapping_port(self.device, p, iface)
        if other is not None and other.ip.network == iface.network:
            raise _MtError("failure: already have address with such network on another interface")
        if p.ip is not None and p.ip.network != iface.network:
            p.ip2.append(iface)                     # another subnet on the same interface
            self.device.routes_changed()
            return None
        p.ip = iface
        self.device.arp.flush_port(p)
        return None

    def addr_remove(self, args):
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        addrs = self._addresses()
        targets = []
        for tok, col in args:
            for n in tok.split(","):
                if not n.isdigit() or int(n) >= len(addrs):
                    raise _MtError("no such item")
                targets.append(addrs[int(n)])
        for p, a in targets:
            if a in p.ip2:
                p.ip2.remove(a)
            elif p.ip2:
                p.ip = p.ip2.pop(0)
            else:
                p.ip = None
            self.device.arp.flush_port(p)
        return None

    # -- /ipv6 -------------------------------------------------------------------------------
    def _v6_list(self):
        return [(p, a) for p in self.device.ports.values() for a in p.ipv6]

    def v6_addr_print(self):
        rows = []
        i = 0
        for p in self.device.ports.values():
            ll = self.device.link_local(p)
            for a in p.ipv6:
                rows.append([str(i), " G", str(a), p.name, "yes"])
                i += 1
            if ll is not None and p.ipv6:
                rows.append(["", "DL", str(ll), p.name, "no"])
        return ("Flags: D - DYNAMIC; G - GLOBAL, L - LINK-LOCAL\nColumns: ADDRESS, INTERFACE, ADVERTISE\n"
                + table(["#", "  ", "ADDRESS", "INTERFACE", "ADVERTISE"], rows, gap=1))

    def v6_addr_add(self, args):
        from ipaddress import IPv6Interface
        kv, _ = self._kv(args, {"address", "interface", "advertise", "comment", "disabled", "eui-64"})
        if "address" not in kv or "interface" not in kv:
            raise _MtError("failure: address and interface must be specified")
        p = self._port(kv["interface"])
        try:
            a = IPv6Interface(kv["address"] if "/" in kv["address"] else kv["address"] + "/64")
        except ValueError:
            raise _MtError("invalid value for argument address")
        if kv.get("eui-64") == "yes":
            from ..stack.ipv6 import eui64_address
            a = eui64_address(a.network, p.mac)
        if a in p.ipv6:
            raise _MtError("failure: already have such address")
        p.ipv6.append(a)
        p.ipv6_enabled = True
        if "advertise" in kv:
            p.ra = kv["advertise"] == "yes"
        self.device.ipv6_changed()
        return None

    def v6_addr_remove(self, args):
        lst = self._v6_list()
        for tok, col in args:
            for n in tok.split(","):
                if not n.isdigit() or int(n) >= len(lst):
                    raise _MtError("no such item")
        for tok, col in args:
            for n in sorted((int(x) for x in tok.split(",")), reverse=True):
                p, a = lst[n]
                p.ipv6.remove(a)
        return None

    def v6_route_print(self):
        rows = []
        for i, r in enumerate(self.device.routing_table6()):
            flag = {"C": "DAc", "L": "DAc", "S": "As", "ND": "DAd"}.get(r.code, "DA")
            if r.code == "L":
                continue
            gw = f"{r.next_hop}%{r.port.name}" if r.next_hop and r.code != "C" else r.port.name
            rows.append([str(i), flag, str(r.network), gw, str(r.distance)])
        return ("Flags: D - DYNAMIC; A - ACTIVE; c - CONNECT, s - STATIC, d - DHCP\n"
                "Columns: DST-ADDRESS, GATEWAY, DISTANCE\n"
                + table(["#", "   ", "DST-ADDRESS", "GATEWAY", "DISTANCE"], rows, gap=1))

    def v6_route_add(self, args):
        kv, _ = self._kv(args, {"dst-address", "gateway", "distance", "comment", "disabled"})
        if "gateway" not in kv:
            raise _MtError("failure: gateway must be specified")
        gw = kv["gateway"]
        iface = None
        if "%" in gw:
            gw, iface = gw.split("%", 1)
        try:
            v6_route_cmd(self.device, kv.get("dst-address", "::/0"), gw, iface, False, int(kv.get("distance", 1)))
        except ValueError:
            raise _MtError("invalid value for argument gateway")
        return None

    def v6_nd_print(self):
        rows = [[str(i), str(n.ip), n.port.name, _mac(n.mac), "reachable"]
                for i, n in enumerate(self.device.nd_cache.values())]
        return "Columns: ADDRESS, INTERFACE, MAC-ADDRESS, STATUS\n" + table(
            ["#", "ADDRESS", "INTERFACE", "MAC-ADDRESS", "STATUS"], rows, gap=1)

    def v6_nd_set(self, args):
        kv, pos = self._kv(args, {"interface", "advertise-mac-address", "disabled", "ra-interval"}, positional=1)
        disabled = kv.get("disabled") == "yes"
        for p in self.device.ports.values():
            p.ra = not disabled
        self.device.ipv6_changed()
        return None

    # -- /ip route ---------------------------------------------------------------------------
    def route_print(self):
        d = self.device
        active = {(r.network, r.next_hop) for r in d.routing_table() if r.code == "S"}
        rows = []
        for i, s in enumerate(d.static_routes):
            flag = ("A" if (s.network, s.next_hop) in active else " ") + "s"
            gw = str(s.next_hop) if s.next_hop else (s.interface or "")
            rows.append([str(i), " " + flag, str(s.network), gw, str(s.distance)])
        for r in d.connected_routes():
            if r.code == "C":
                rows.append(["", "DAc", str(r.network), r.port.name, "0"])
        table_now = d.routing_table()
        best = {(r.network, r.next_hop) for r in table_now if r.code.startswith(("O", "B"))}
        for r in d.dynamic_routes():
            if r.code.startswith("O"):
                flag = "DAo" if (r.network, r.next_hop) in best else "D o"
                rows.append(["", flag, str(r.network), f"{r.next_hop}%{r.port.name}", str(r.distance)])
            elif r.code == "R":
                rows.append(["", "DAr", str(r.network), f"{r.next_hop}%{r.port.name}", str(r.distance)])
            elif r.code == "B":
                flag = "DAb" if (r.network, r.next_hop) in best else "D b"
                rows.append(["", flag, str(r.network), str(r.next_hop), str(r.distance)])
        return ("Flags: D - DYNAMIC; A - ACTIVE; c - CONNECT, s - STATIC, r - RIP, o - OSPF, b - BGP\n"
                "Columns: DST-ADDRESS, GATEWAY, DISTANCE\n"
                + table(["#", "   ", "DST-ADDRESS", "GATEWAY", "DISTANCE"], rows, gap=1))

    def ip_settings_set(self, args):
        kv, _ = self._kv(args, {"rp-filter", "ip-forward", "tcp-syncookies"})
        if "rp-filter" in kv:
            mode = kv["rp-filter"]
            if mode not in ("strict", "loose", "no"):
                raise _MtError("invalid value for argument rp-filter")
            for p in self.device.ports.values():
                p.urpf = None if mode == "no" else mode
        if "tcp-syncookies" in kv:
            self.device.extra["mt_syncookies"] = kv["tcp-syncookies"] == "yes"
        return None

    def ip_settings_print(self):
        modes = {p.urpf for p in self.device.ports.values() if getattr(p, "urpf", None)}
        rows = [("ip-forward", "yes"), ("send-redirects", "yes"), ("accept-source-route", "no"),
                ("accept-redirects", "no"), ("secure-redirects", "yes"), ("rp-filter", modes.pop() if modes else "no"),
                ("tcp-syncookies", "yes" if self.device.extra.get("mt_syncookies") else "no")]
        return "\n".join(f"{k:>24}: {v}" for k, v in rows)

    def route_add(self, args):
        flags = [a for a in args if a[0] == "blackhole"]
        args = [a for a in args if a[0] != "blackhole"]
        kv, _ = self._kv(args, {"dst-address", "gateway", "distance", "comment", "disabled", "blackhole"})
        if flags or kv.get("blackhole") in ("yes", "true"):
            dst = kv.get("dst-address", "0.0.0.0/0")
            try:
                net = IPv4Network(dst if "/" in dst else dst + "/32", strict=False)
            except ValueError:
                raise _MtError("invalid value for argument dst-address")
            self.device.add_static_route(net, None, "Null0", int(kv.get("distance", 1)))
            self.device.routes_changed()
            return None
        if "gateway" not in kv:
            raise _MtError("failure: gateway must be specified")
        dst = kv.get("dst-address", "0.0.0.0/0")
        try:
            net = IPv4Network(dst if "/" in dst else dst + "/32", strict=False)
        except ValueError:
            raise _MtError("invalid value for argument dst-address")
        gw = kv["gateway"]
        dist = int(kv.get("distance", 1))
        if is_valid_ip(gw):
            self.device.add_static_route(net, IPv4Address(gw), None, dist)
        else:
            self._port(gw)
            self.device.add_static_route(net, None, gw, dist)
        return None

    def route_remove(self, args):
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        routes = self.device.static_routes
        idx = set()
        for tok, col in args:
            for n in tok.split(","):
                if not n.isdigit() or int(n) >= len(routes):
                    raise _MtError("no such item")
                idx.add(int(n))
        self.device.static_routes = [r for i, r in enumerate(routes) if i not in idx]
        return None

    # -- /ip arp -----------------------------------------------------------------------------
    def arp_print(self):
        d = self.device
        rows = [[str(i), "DC", str(e.ip), _mac(e.mac), e.port.name]
                for i, e in enumerate(d.arp.entries(d.engine.now))]
        return ("Flags: D - DYNAMIC; C - COMPLETE\nColumns: ADDRESS, MAC-ADDRESS, INTERFACE\n"
                + table(["#", "  ", "ADDRESS", "MAC-ADDRESS", "INTERFACE"], rows, gap=1))

    # -- /interface bridge (switch) ------------------------------------------------------------
    def bridge_print(self):
        return ("Flags: X - disabled, R - running\n"
                f" 0  R name=\"bridge\" mtu=auto actual-mtu=1500 l2mtu=1592 arp=enabled "
                f"mac-address={_mac(self.device.ports['ether1'].mac)} protocol-mode=rstp")

    # -- OSPFv3 / RIPng (RouterOS v7: instance version=3, rip instance afi=ipv6) ----------------
    def _v6state(self):
        return self.device.extra.setdefault("mt_v6igp", {"ospf_instance": None, "areas": {}, "ripng": [],
                                                          "templates": []})

    def ospf_inst_add(self, args):
        if not any(t == "version=3" for t, _ in args):
            return self._ospf_inst_add_v2(args)
        kv, _ = self._kv(args, {"name", "router-id", "originate-default", "redistribute", "version", "comment",
                                "disabled"})
        from ..routing.v6igp import ensure
        st = self._v6state()
        st["ospf_instance"] = kv.get("name", "default-v3")
        proc = ensure(self.device, "ospf6")
        proc.cfg.router_id = kv.get("router-id")
        proc.kick()
        return None

    def ospf_area_add(self, args):
        from ..routing.ospf import area_id
        kv, _ = self._kv(args, {"name", "area-id", "instance", "type", "comment", "disabled"})
        st = self._v6state()
        if kv.get("instance") is None or kv.get("instance") != st.get("ospf_instance"):
            return self._ospf_area_add_v2(args)
        try:
            st["areas"][kv.get("name", "backbone-v3")] = area_id(kv.get("area-id", "0.0.0.0"))
        except ValueError:
            raise _MtError("invalid value for argument area-id")
        return None

    def ospf_tpl_add(self, args):
        kv, _ = self._kv(args, {"networks", "area", "interfaces", "passive", "cost", "hello-interval",
                                "dead-interval", "priority", "type", "comment", "disabled"}, positional=1)
        st = self._v6state()
        if kv.get("area") not in st["areas"]:
            return self._ospf_tpl_add_v2(args)
        from ..routing.v6igp import enable_iface
        for x in filter(None, kv.get("interfaces", "").split(",")):
            enable_iface(self.device, "ospf6", self._port(x), st["areas"][kv["area"]],
                         cost=int(kv["cost"]) if "cost" in kv else None)
        st["templates"].append({"kind": "ospf6", "area": kv["area"], "interfaces": kv.get("interfaces", "")})
        return None

    def rip_inst_add(self, args):
        kv, _ = self._kv(args, {"name", "redistribute", "originate-default", "afi", "comment", "disabled"})
        if kv.get("afi") != "ipv6":
            return self._rip_inst_add_v2([(t, c) for t, c in args if not t.startswith("afi=")])
        from ..routing.v6igp import ensure
        self._v6state()["ripng"].append(kv.get("name", "ripng"))
        ensure(self.device, "ripng").kick()
        return None

    def rip_tpl_add(self, args):
        kv, _ = self._kv(args, {"interfaces", "instance", "passive", "comment", "disabled"}, positional=1)
        st = self._v6state()
        if kv.get("instance") not in st["ripng"]:
            return self._rip_tpl_add_v2(args)
        from ..routing.v6igp import enable_iface
        for x in filter(None, kv.get("interfaces", "").split(",")):
            enable_iface(self.device, "ripng", self._port(x))
        st["templates"].append({"kind": "ripng", "instance": kv["instance"], "interfaces": kv.get("interfaces", "")})
        return None

    # -- /ipv6 pool, /ipv6 dhcp-server ----------------------------------------------------------
    def v6pool_add(self, args):
        from ipaddress import IPv6Network
        kv, _ = self._kv(args, {"name", "prefix", "prefix-length", "comment"})
        if "name" not in kv or "prefix" not in kv:
            raise _MtError("failure: name and prefix must be specified")
        try:
            IPv6Network(kv["prefix"], strict=False)
        except ValueError:
            raise _MtError("invalid value for argument prefix")
        self.device.extra.setdefault("mt_v6pools", {})[kv["name"]] = kv["prefix"]
        return None

    def dhcp6s_add(self, args):
        from .base import dhcp6_pool_cmd
        kv, _ = self._kv(args, {"name", "interface", "address-pool", "lease-time", "comment", "disabled"})
        pools = self.device.extra.get("mt_v6pools", {})
        if kv.get("address-pool") not in pools:
            raise _MtError("input does not match any value of address-pool")
        p = self._port(kv.get("interface", ""))
        name = kv.get("name", f"dhcp6-{p.name}")
        dhcp6_pool_cmd(self.device, name, "prefix", pools[kv["address-pool"]])
        self.device.dhcp6.ifaces[p.name] = name
        self.device.extra.setdefault("mt_dhcp6s", []).append(kv)
        return None

    def dhcp6s_print(self):
        rows = [[str(i), s.get("name", ""), s.get("interface", ""), s.get("address-pool", "")]
                for i, s in enumerate(self.device.extra.get("mt_dhcp6s", []))]
        return "Columns: NAME, INTERFACE, ADDRESS-POOL\n" + table(["#", "NAME", "INTERFACE", "ADDRESS-POOL"], rows, gap=1)

    def dhcp6_binding_print(self):
        rows = []
        for pool in self.device.dhcp6.pools.values():
            for duid, addr in pool.bindings.items():
                rows.append([str(len(rows)), "D", addr, duid, pool.name])
        return "Flags: D - DYNAMIC\nColumns: ADDRESS, DUID, SERVER\n" + table(["#", " ", "ADDRESS", "DUID", "SERVER"],
                                                                                 rows, gap=1)

    # -- LLDP-MED network policy: the voice VLAN IP phones learn -----------------------------------
    def mt_discovery_set(self, args):
        kv, _ = self._kv(args, {"discover-interface-list", "protocol", "lldp-med-net-policy-vlan", "mode"})
        if "lldp-med-net-policy-vlan" in kv:
            v = kv["lldp-med-net-policy-vlan"]
            if v in ("disabled", ""):
                self.device.extra.pop("lldp_med_vlan", None)
            elif v.isdigit() and 1 <= int(v) <= 4094:
                self.device.extra["lldp_med_vlan"] = int(v)
            else:
                raise _MtError("invalid value for argument lldp-med-net-policy-vlan")
            from .base import set_voice_vlan
            for p in self.device.ports.values():                # phones already connected re-learn it
                if p.peer is not None and hasattr(p.peer.device, "_discover"):
                    set_voice_vlan(p, p.voice_vlan)
        return None

    def mt_discovery_print(self):
        v = self.device.extra.get("lldp_med_vlan")
        return (" discover-interface-list: all\n                protocol: cdp,lldp,mndp\n"
                f"  lldp-med-net-policy-vlan: {v if v else 'disabled'}")

    def mt_neighbors(self):
        rows = []
        for p in self.device.ports.values():
            peer = p.peer
            if peer is not None and peer.device.category in ("phone", "router", "switch"):
                ip = getattr(getattr(peer.device, "nic", None), "ip", None) or peer.ip
                rows.append([str(len(rows)), p.name, str(ip.ip) if ip else "", peer.device.hostname,
                             getattr(peer.device, "model", "")])
        return "Columns: INTERFACE, ADDRESS, IDENTITY, PLATFORM\n" + table(
            ["#", "INTERFACE", "ADDRESS", "IDENTITY", "PLATFORM"], rows, gap=1)

    # -- /queue simple, /ip firewall mangle (change-dscp) -> global QoS policies --------------------
    def _mt_qos_sync(self):
        """Rebuild the device QoS from the RouterOS lists kept in extra."""
        from ..security.qos import QosClass, QosPolicy, parse_dscp, parse_rate
        d = self.device
        x = d.extra
        d.qos = {k: v for k, v in d.qos.items() if not k.startswith("mt-")}
        d.qos_global = [g for g in d.qos_global if not g.startswith("mt-")]
        mangle = QosPolicy("mt-mangle")
        for i, r in enumerate(x.get("mt_mangle", [])):
            if r.get("disabled") == "yes":
                continue
            m = {}
            for k, key in (("src-address", "src"), ("dst-address", "dst"), ("protocol", "protocol")):
                if r.get(k):
                    m[key] = r[k]
            mangle.classes.append(QosClass(f"rule{i}", m or {"any": True}, set_dscp=parse_dscp(r["new-dscp"])))
        if mangle.classes:
            d.qos[mangle.name] = mangle
            d.qos_global.append(mangle.name)
        for q in x.get("mt_queues", []):
            up, _, down = q["max-limit"].partition("/")
            pol = QosPolicy(f"mt-q-{q['name']}", [
                QosClass("upload", {"src": q["target"]}, police_bps=parse_rate(up)),
                QosClass("download", {"dst": q["target"]}, police_bps=parse_rate(down or up))])
            d.qos[pol.name] = pol
            d.qos_global.append(pol.name)

    def mt_queue_add(self, args):
        from ipaddress import IPv4Network
        from ..security.qos import parse_rate
        kv, _ = self._kv(args, {"name", "target", "max-limit", "limit-at", "comment", "disabled", "priority"})
        if "target" not in kv or "max-limit" not in kv:
            raise _MtError("failure: target and max-limit must be specified")
        try:
            kv["target"] = str(IPv4Network(kv["target"] if "/" in kv["target"] else kv["target"] + "/32", strict=False))
            for part in kv["max-limit"].split("/"):
                parse_rate(part)
        except ValueError:
            raise _MtError("invalid value for argument max-limit")
        qs = self.device.extra.setdefault("mt_queues", [])
        kv.setdefault("name", f"queue{len(qs) + 1}")
        if any(q["name"] == kv["name"] for q in qs):
            raise _MtError("failure: queue with such name already exists")
        qs.append({k: kv[k] for k in ("name", "target", "max-limit")})
        self._mt_qos_sync()
        return None

    def mt_queue_remove(self, args):
        qs = self.device.extra.get("mt_queues", [])
        for tok, _ in args:
            for x in tok.split(","):
                idx = int(x) if x.isdigit() else next((i for i, q in enumerate(qs) if q["name"] == x), None)
                if idx is None or idx >= len(qs):
                    raise _MtError("no such item")
                qs.pop(idx)
        self._mt_qos_sync()
        return None

    def mt_queue_print(self):
        pols = self.device.qos
        rows = []
        for i, q in enumerate(self.device.extra.get("mt_queues", [])):
            p = pols.get(f"mt-q-{q['name']}")
            drops = sum(c.dropped for c in p.classes) if p else 0
            rows.append([str(i), q["name"], q["target"], q["max-limit"], str(drops)])
        return "Columns: NAME, TARGET, MAX-LIMIT, DROPPED\n" + table(["#", "NAME", "TARGET", "MAX-LIMIT", "DROPPED"],
                                                                     rows, gap=1)

    def mt_mangle_add(self, args):
        from ..security.qos import parse_dscp
        kv, _ = self._kv(args, {"chain", "action", "new-dscp", "src-address", "dst-address", "protocol", "comment",
                                "passthrough", "disabled"})
        if kv.get("chain") not in ("prerouting", "forward", "postrouting", "input", "output"):
            raise _MtError("input does not match any value of chain")
        if kv.get("action") != "change-dscp" or "new-dscp" not in kv:
            raise _MtError("failure: NetEmu supports action=change-dscp new-dscp=N in mangle")
        try:
            parse_dscp(kv["new-dscp"])
        except ValueError:
            raise _MtError("invalid value for argument new-dscp")
        self.device.extra.setdefault("mt_mangle", []).append(dict(kv))
        self._mt_qos_sync()
        return None

    def mt_mangle_remove(self, args):
        rules = self.device.extra.get("mt_mangle", [])
        for tok, _ in args:
            for x in sorted((int(v) for v in tok.split(",") if v.isdigit()), reverse=True):
                if x >= len(rules):
                    raise _MtError("no such item")
                rules.pop(x)
        self._mt_qos_sync()
        return None

    def mt_mangle_print(self):
        lines = ["Flags: X - disabled, I - invalid; D - dynamic"]
        pol = self.device.qos.get("mt-mangle")
        for i, r in enumerate(self.device.extra.get("mt_mangle", [])):
            hits = pol.classes[i].matched if pol and i < len(pol.classes) else 0
            parts = " ".join(f"{k}={v}" for k, v in r.items() if k not in ("chain", "action"))
            lines.append(f" {i}    chain={r['chain']} action={r['action']} {parts} packets={hits}")
        return "\n".join(lines)

    # -- /system logging, /system ntp client, /snmp ---------------------------------------------
    def mt_log_action_add(self, args):
        from .base import mgmt_add
        kv, _ = self._kv(args, {"name", "target", "remote", "remote-port", "bsd-syslog", "syslog-facility"})
        if kv.get("target") != "remote" or not is_valid_ip(kv.get("remote", "")):
            raise _MtError("failure: target=remote and remote=<ip> are required in NetEmu")
        self.device.extra.setdefault("mt_log_actions", {})[kv.get("name", f"remote{len(self.device.mgmt.syslog_hosts)}")] = kv["remote"]
        return mgmt_add(self.device, "syslog", kv["remote"])

    def mt_log_actions(self):
        rows = [[str(i), n, "remote", ip] for i, (n, ip) in enumerate(self.device.extra.get("mt_log_actions", {}).items())]
        return "Columns: NAME, TARGET, REMOTE\n" + table(["#", "NAME", "TARGET", "REMOTE"], rows, gap=1)

    def mt_logging_print(self):
        rows = [["0", "info", "memory"], ["1", "error", "memory"], ["2", "warning", "memory"]]
        rows += [[str(3 + i), "info", n] for i, n in enumerate(self.device.extra.get("mt_log_actions", {}))]
        return "Columns: TOPICS, ACTION\n" + table(["#", "TOPICS", "ACTION"], rows, gap=1)

    def mt_ntp_set(self, args):
        kv, _ = self._kv(args, {"enabled", "servers", "mode"})
        m = self.device.mgmt
        servers = [x for x in kv.get("servers", ",".join(m.ntp_servers)).split(",") if x]
        if any(not is_valid_ip(x) for x in servers):
            raise _MtError("invalid value for argument servers")
        on = kv.get("enabled", "yes" if m.ntp_servers else "no") in ("yes", "true")
        m.set_ntp_servers(servers if on else [])
        self.device.extra["mt_ntp_servers"] = servers
        return None

    def mt_ntp_print(self):
        m = self.device.mgmt
        servers = m.ntp_servers or self.device.extra.get("mt_ntp_servers", [])
        st = "synchronized" if m.synced else ("waiting" if m.ntp_servers else "stopped")
        return (f"           enabled: {'yes' if m.ntp_servers else 'no'}\n              mode: unicast\n"
                f"           servers: {','.join(servers)}\n            status: {st}"
                + (f"\n   synced-server: {m.synced}\n    synced-stratum: {m.stratum}" if m.synced else ""))

    def mt_snmp_set(self, args):
        kv, _ = self._kv(args, {"enabled", "contact", "location", "trap-version", "trap-community"})
        m = self.device.mgmt
        if "enabled" in kv:
            on = kv["enabled"] in ("yes", "true")
            self.device.extra["mt_snmp"] = on
            if on and not m.communities:
                m.communities["public"] = "ro"
            if not on:
                m.communities.clear()
        for k in ("contact", "location"):
            if k in kv:
                setattr(m, k, kv[k])
        return None

    def mt_snmp_print(self):
        m = self.device.mgmt
        return (f"         enabled: {'yes' if m.communities else 'no'}\n         contact: {m.contact}\n"
                f"        location: {m.location}\n    engine-id-suffix:\n    trap-version: 1")

    def mt_comm_add(self, args):
        from .base import mgmt_add
        kv, _ = self._kv(args, {"name", "write-access", "read-access", "addresses", "security"})
        if "name" not in kv:
            raise _MtError("missing value(s) of argument(s) name")
        return mgmt_add(self.device, "rw" if kv.get("write-access") in ("yes", "true") else "ro", kv["name"])

    def mt_comm_print(self):
        rows = [[str(i), c, "yes", "yes" if v == "rw" else "no"] for i, (c, v) in enumerate(self.device.mgmt.communities.items())]
        return "Columns: NAME, READ-ACCESS, WRITE-ACCESS\n" + table(["#", "NAME", "READ-ACCESS", "WRITE-ACCESS"], rows, gap=1)

    # -- /interface gre ------------------------------------------------------------------------
    def gre_add(self, args):
        kv, _ = self._kv(args, {"name", "remote-address", "local-address", "ipsec-secret", "comment", "disabled",
                                "keepalive", "allow-fast-path"})
        if "remote-address" not in kv:
            raise _MtError("failure: remote-address must be specified")
        if not is_valid_ip(kv["remote-address"]) or ("local-address" in kv and not is_valid_ip(kv["local-address"])):
            raise _MtError("invalid value for argument remote-address")
        d = self.device
        name = kv.get("name") or f"gre-tunnel{sum(1 for p in d.ports.values() if p.is_tunnel) + 1}"
        if name in d.ports:
            raise _MtError("failure: interface with such name exists")
        t = d.add_tunnel(name, name)
        from .base import tunnel_set
        tunnel_set(t, "destination", kv["remote-address"])
        if "local-address" in kv:
            tunnel_set(t, "source", kv["local-address"])
        else:
            r = d.route_lookup(t.destination)
            if r is not None and r[0].ip is not None:
                tunnel_set(t, "source", str(r[0].ip.ip))
        t.ipsec_key = kv.get("ipsec-secret")
        return None

    def gre_remove(self, args):
        tuns = [p for p in self.device.ports.values() if p.is_tunnel]
        for tok, _ in args:
            for x in tok.split(","):
                t = tuns[int(x)] if x.isdigit() and int(x) < len(tuns) else self.device.ports.get(x)
                if t is None or not t.is_tunnel:
                    raise _MtError("no such item")
                self.device.remove_tunnel(t)
        return None

    def gre_print(self):
        rows = [[str(i), "R" if t.is_up else " ", t.name, str(t.source_ip() or ""), str(t.destination or ""),
                 "yes" if t.ipsec_key else "no"]
                for i, t in enumerate(p for p in self.device.ports.values() if p.is_tunnel)]
        return ("Flags: R - RUNNING\nColumns: NAME, LOCAL-ADDRESS, REMOTE-ADDRESS, IPSEC\n"
                + table(["#", " ", "NAME", "LOCAL-ADDRESS", "REMOTE-ADDRESS", "IPSEC"], rows, gap=1))

    # -- /interface vrrp ----------------------------------------------------------------------
    def _vrrps(self):
        return self.device.extra.setdefault("mt_vrrp", [])

    def vrrp_add(self, args):
        kv, _ = self._kv(args, {"name", "interface", "vrid", "priority", "preemption-mode", "comment"})
        if "interface" not in kv:
            raise _MtError("failure: interface must be specified")
        p = self._port(kv["interface"])
        vrid = int(kv.get("vrid", 1))
        name = kv.get("name") or f"vrrp{len(self._vrrps()) + 1}"
        if name in self.device.ports or any(v["name"] == name for v in self._vrrps()):
            raise _MtError("failure: interface with such name exists")
        self._vrrps().append({"name": name, "interface": p.name, "vrid": vrid})
        from .base import fhrp_set
        fhrp_set(self.device, p, "vrrp", vrid, "priority", int(kv.get("priority", 100)))
        if kv.get("preemption-mode") == "no":
            fhrp_set(self.device, p, "vrrp", vrid, "preempt", False)
        return None

    def vrrp_remove(self, args):
        lst = self._vrrps()
        for tok, _ in args:
            for x in tok.split(","):
                v = lst[int(x)] if x.isdigit() and int(x) < len(lst) else next((y for y in lst if y["name"] == x), None)
                if v is None:
                    raise _MtError("no such item")
                lst.remove(v)
                self.device.fhrp.remove(v["interface"], "vrrp", v["vrid"])
        return None

    def vrrp_print(self):
        rows = []
        for i, v in enumerate(self._vrrps()):
            g = self.device.fhrp.group(v["interface"], "vrrp", v["vrid"], create=False)
            flag = "RM" if g is not None and g.state == "master" else ("R " if g is not None and g.state == "backup" else "  ")
            rows.append([str(i), flag, v["name"], v["interface"], str(v["vrid"]), str(g.priority if g else 100)])
        return ("Flags: R - RUNNING; M - MASTER\nColumns: NAME, INTERFACE, VRID, PRIORITY\n"
                + table(["#", "  ", "NAME", "INTERFACE", "VRID", "PRIORITY"], rows, gap=1))

    def _vrrp_named(self, name):
        return next((v for v in self._vrrps() if v["name"] == name), None)

    # -- /interface bonding -------------------------------------------------------------------
    def bond_print(self):
        rows = []
        for i, po in enumerate(self.device.channels()):
            mode = "802.3ad" if any(m.channel_mode == "active" for m in po.members) else "balance-rr"
            rows.append([str(i), "R" if po.is_up else " ", po.name, mode, ",".join(m.name for m in po.members)])
        return ("Flags: R - RUNNING\nColumns: NAME, MODE, SLAVES\n"
                + table(["#", " ", "NAME", "MODE", "SLAVES"], rows, gap=1))

    def bond_add(self, args):
        kv, _ = self._kv(args, {"name", "slaves", "mode", "comment", "lacp-rate", "transmit-hash-policy"})
        if "slaves" not in kv:
            raise _MtError("failure: slaves must be specified")
        d = self.device
        name = kv.get("name") or f"bonding{len(d.channels()) + 1}"
        if name in d.ports:
            raise _MtError("failure: interface with such name exists")
        mode = kv.get("mode", "balance-rr")
        if mode not in ("802.3ad", "balance-rr", "active-backup", "balance-xor"):
            raise _MtError("input does not match any value of mode")
        slaves = [self._port(x) for x in kv["slaves"].split(",")]
        group = max([po.group for po in d.channels()] + [0]) + 1
        po = d.add_channel(group, name, name)
        po.pvid = po.access_vlan = slaves[0].access_vlan
        for p in slaves:
            d.set_channel_member(p, group, "active" if mode == "802.3ad" else "on")
        return None

    def bond_remove(self, args):
        chans = self.device.channels()
        for tok, _ in args:
            for x in tok.split(","):
                po = chans[int(x)] if x.isdigit() and int(x) < len(chans) else self.device.ports.get(x)
                if po is None or not po.is_channel:
                    raise _MtError("no such item")
                self.device.remove_channel(po)
        return None

    def bridge_port_print(self):
        stp = self.device.stp
        roles = {"root": "root-port", "designated": "designated-port", "alternate": "alternate-port",
                 "edge": "designated-port", "disabled": "disabled-port"}
        rows = [[str(i), "" if p.is_up else "I", p.name, "bridge", str(p.access_vlan),
                 roles.get(stp.roles.get(p.name), "disabled-port") if stp.enabled else "",
                 str(stp.port_cost(p))]
                for i, p in enumerate(self.device.l2_ports())]
        return ("Flags: I - INACTIVE\nColumns: INTERFACE, BRIDGE, PVID, ROLE, PATH-COST\n"
                + table(["#", " ", "INTERFACE", "BRIDGE", "PVID", "ROLE", "PATH-COST"], rows, gap=1))

    def bridge_monitor(self):
        from ..addresses import MacAddress
        stp = self.device.stp
        root = stp.root_id or stp.bridge_id
        fmt = lambda b: f"0x{b[0]:X}.{_mac(MacAddress(b[1]))}"
        return "\n".join(["                  state: enabled", f"      current-mac-address: {_mac(MacAddress(stp.bridge_id[1]))}",
                          f"            root-bridge: {'yes' if root == stp.bridge_id else 'no'}",
                          f"         root-bridge-id: {fmt(root)}", f"         root-path-cost: {stp.root_cost}",
                          f"              root-port: {stp.root_port or 'none'}",
                          f"     regional-root-bridge-id: {fmt(stp.bridge_id)}",
                          f"    port-count: {sum(1 for p in self.device.ports.values() if p.is_up)}",
                          f"  designated-port-count: {sum(1 for r in stp.roles.values() if r in ('designated', 'edge'))}",
                          f"        protocol-mode: {stp.mode if stp.enabled else 'none'}"])

    # -- bridge VLAN filtering (CRS) --------------------------------------------------------
    def _mt_recompute(self):
        """Derive the neutral switchport model from pvids + the bridge VLAN table."""
        sw = self.device
        ids = set()
        for e in sw.bridge_vlans:
            ids.update(e["vlan_ids"])
        for p in sw.ports.values():
            tagged = {v for e in sw.bridge_vlans if p.name in e["tagged"] for v in e["vlan_ids"]}
            pvid = p.access_vlan
            p.native_vlan = pvid
            if tagged:
                p.mode, p.pvid, p.tagged = "trunk", pvid, frozenset(tagged | {pvid})
            else:
                p.mode, p.pvid, p.tagged = "access", pvid, None
            ids.add(pvid)
        sw.vlans = {v: sw.vlans.get(v, "default" if v == 1 else f"vlan{v}") for v in sorted(ids | {1})}
        sw.clear_mac_table()
        sw.engine.emit("device_changed", sw)

    def bridge_set(self, args):
        name, rest = self._target(args)
        if name not in (None, "bridge", "0"):
            raise _MtError("no such item")
        kv, _ = self._kv(rest, {"vlan-filtering", "protocol-mode", "comment", "name", "pvid", "priority",
                                "dhcp-snooping"})
        if "dhcp-snooping" in kv:
            self.device.dhcp_snooping["enabled"] = kv["dhcp-snooping"] in ("yes", "true")
        stp = self.device.stp
        if "protocol-mode" in kv:
            m = kv["protocol-mode"]
            if m not in ("none", "stp", "rstp", "mstp"):
                raise _MtError("input does not match any value of protocol-mode")
            stp.enabled = m != "none"
            if stp.enabled:
                stp.mode = m
            else:
                stp.states.clear()
            stp.schedule()
        if "priority" in kv:
            v = kv["priority"]
            prio = int(v, 16) if v.lower().startswith("0x") else int(v)
            if prio % 4096 or not 0 <= prio <= 61440:
                raise _MtError("priority must be a multiple of 0x1000 (4096)")
            stp.priority = prio
            stp.schedule()
        if "vlan-filtering" in kv:
            self.device.vlan_filtering = kv["vlan-filtering"] in ("yes", "true")
            self.device.clear_mac_table()
        return None

    def bridge_port_set(self, args):
        name, rest = self._target(args)
        if name is None:
            raise _MtError("missing value(s) of argument(s) numbers")
        if name.isdigit():
            p = list(self.device.ports.values())[int(name)] if int(name) < len(self.device.ports) else None
        else:
            p = self.device.ports.get(name)
        if p is None:
            raise _MtError("no such item")
        kv, _ = self._kv(rest, {"pvid", "comment", "disabled", "frame-types", "ingress-filtering", "edge",
                                "path-cost", "trusted", "learn"})
        if "trusted" in kv:
            p.dhcp_trust = kv["trusted"] in ("yes", "true")
        stp = self.device.stp
        if "edge" in kv:
            (stp.edge.add if kv["edge"] in ("yes", "yes-discover") else stp.edge.discard)(p.name)
            stp.schedule()
        if "path-cost" in kv:
            stp.costs[p.name] = int(kv["path-cost"])
            stp.schedule()
        if "pvid" in kv:
            if not kv["pvid"].isdigit() or not 1 <= int(kv["pvid"]) <= 4094:
                raise _MtError("value of pvid out of range (1..4094)")
            p.access_vlan = int(kv["pvid"])
        if "comment" in kv:
            p.description = kv["comment"]
        self._mt_recompute()
        return None

    @staticmethod
    def _vlan_ids(text):
        from .parser import parse_vlan_list
        ids = parse_vlan_list(text)
        if not ids:
            raise _MtError("invalid value for argument vlan-ids")
        return sorted(ids)

    def bridge_vlan_add(self, args):
        kv, _ = self._kv(args, {"bridge", "vlan-ids", "tagged", "untagged", "comment", "disabled"})
        if "vlan-ids" not in kv:
            raise _MtError("failure: vlan-ids must be specified")
        if kv.get("bridge", "bridge") != "bridge":
            raise _MtError("input does not match any value of bridge")
        entry = {"vlan_ids": self._vlan_ids(kv["vlan-ids"]), "tagged": [], "untagged": []}
        for key in ("tagged", "untagged"):
            for nm in filter(None, kv.get(key, "").split(",")):
                entry[key].append(self._port(nm).name)
        self.device.bridge_vlans.append(entry)
        self._mt_recompute()
        return None

    def bridge_vlan_remove(self, args):
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        idx = sorted({int(x) for t, _ in args for x in t.split(",") if x.isdigit()}, reverse=True)
        for i in idx:
            if i >= len(self.device.bridge_vlans):
                raise _MtError("no such item")
            del self.device.bridge_vlans[i]
        self._mt_recompute()
        return None

    def bridge_vlan_print(self):
        rows = []
        for i, e in enumerate(self.device.bridge_vlans):
            ids = ",".join(map(str, e["vlan_ids"]))
            rows.append([str(i), "bridge", ids, ",".join(e["tagged"]), ",".join(e["untagged"])])
        return ("Columns: BRIDGE, VLAN-IDS, CURRENT-TAGGED, CURRENT-UNTAGGED\n"
                + table(["#", "BRIDGE", "VLAN-IDS", "CURRENT-TAGGED", "CURRENT-UNTAGGED"], rows, gap=1))

    # -- /ip firewall filter | nat -------------------------------------------------------------
    FW_KEYS = {"chain", "action", "src-address", "dst-address", "protocol", "src-port", "dst-port",
               "in-interface", "out-interface", "to-addresses", "to-ports", "icmp-options", "comment",
               "disabled", "log", "connection-state"}

    def _fw_raw(self, table):
        return self.device.extra.setdefault("mt_" + table, [])

    def fw_add(self, args, table):
        kv, _ = self._kv(args, self.FW_KEYS)
        chains = ("input", "forward", "output") if table == "filter" else ("srcnat", "dstnat")
        actions = {"filter": ("accept", "drop", "reject", "passthrough", "log"),
                   "nat": ("masquerade", "src-nat", "dst-nat", "accept", "redirect", "netmap")}[table]
        if kv.get("chain") not in chains:
            raise _MtError("input does not match any value of chain")
        kv.setdefault("action", "accept")
        if kv["action"] not in actions:
            raise _MtError("input does not match any value of action")
        for key in ("in-interface", "out-interface"):
            if key in kv:
                self._port(kv[key])
        for key in ("src-address", "dst-address"):
            if key in kv:
                try:
                    IPv4Network(kv[key] if "/" in kv[key] else kv[key] + "/32", strict=False)
                except ValueError:
                    raise _MtError(f"invalid value for argument {key}")
        if kv.get("protocol") not in (None, "icmp", "tcp", "udp", "ospf"):
            raise _MtError("input does not match any value of protocol")
        if table == "nat" and kv["action"] in ("src-nat", "dst-nat") and "to-addresses" not in kv:
            raise _MtError("failure: to-addresses must be set for " + kv["action"])
        self._fw_raw(table).append(kv)
        self._fw_apply()
        return None

    def fw_remove(self, args, table):
        raw = self._fw_raw(table)
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        idx = sorted({int(x) for t, _ in args for x in t.split(",") if x.isdigit()}, reverse=True)
        for i in idx:
            if i >= len(raw):
                raise _MtError("no such item")
            del raw[i]
        self._fw_apply()
        return None

    def _fw_apply(self):
        """Translate the RouterOS tables into the neutral firewall rules and NAT config."""
        from ..security.acl import AclRule
        from ..security.nat import NatConfig, NatRule
        d = self.device

        def net(v):
            return IPv4Network(v if "/" in v else v + "/32", strict=False) if v else IPv4Network("0.0.0.0/0")

        def port_cond(v):
            if not v:
                return None
            if "-" in v:
                a, b = v.split("-", 1)
                return ("range", int(a), int(b))
            return ("eq", int(v))
        rules = []
        for kv in self._fw_raw("filter"):
            if kv.get("disabled") in ("yes", "true") or kv["action"] in ("passthrough", "log"):
                continue
            itype = None
            if kv.get("icmp-options"):
                itype = int(kv["icmp-options"].split(":")[0])
            rules.append(AclRule("permit" if kv["action"] == "accept" else "deny", kv.get("protocol", "ip"),
                                 net(kv.get("src-address")), net(kv.get("dst-address")),
                                 port_cond(kv.get("src-port")), port_cond(kv.get("dst-port")), itype,
                                 extra={"chain": kv["chain"], "in": self._pname(kv.get("in-interface")),
                                        "out": self._pname(kv.get("out-interface")),
                                        "reject": kv["action"] == "reject",
                                        "state": [x for x in kv.get("connection-state", "").split(",") if x]}))
        d.firewall = rules
        nat_rules = []
        for kv in self._fw_raw("nat"):
            if kv.get("disabled") in ("yes", "true"):
                continue
            act = kv["action"]
            if kv["chain"] == "srcnat" and act in ("masquerade", "src-nat"):
                out = self._pname(kv.get("out-interface"))
                pool = None
                if act == "src-nat":
                    a, _, b = kv["to-addresses"].partition("-")
                    pool = (a, b or a)
                nat_rules.append(NatRule("dynamic", prefix=kv.get("src-address") and str(net(kv["src-address"])),
                                         out_iface=out, interface=out if act == "masquerade" else None,
                                         pool=pool, overload=True))
            elif kv["chain"] == "dstnat" and act == "dst-nat" and kv.get("dst-address"):
                nat_rules.append(NatRule("dnat", local=kv["to-addresses"].split("-")[0],
                                         glob=str(net(kv["dst-address"]).network_address),
                                         proto=kv.get("protocol"), in_iface=self._pname(kv.get("in-interface")),
                                         global_port=int(kv["dst-port"]) if kv.get("dst-port") else None,
                                         local_port=int(kv["to-ports"]) if kv.get("to-ports") else None))
        if nat_rules:
            d.nat_engine().cfg = NatConfig(rules=nat_rules)
        elif d.nat is not None:
            d.nat = None

    def _pname(self, name):
        return self._port(name).name if name else None

    def fw_print(self, args):
        stats = any(t == "stats" for t, _ in args)
        raw = self._fw_raw("filter")
        lines = ["Flags: X - disabled, I - invalid; D - dynamic"]
        hits = [r.hits for r in self.device.firewall]
        for i, kv in enumerate(raw):
            if stats:
                h = hits[i] if i < len(hits) else 0
                lines.append(f" {i}    chain={kv['chain']} action={kv['action']} packets={h} bytes={h * 84}")
            else:
                parts = [f"{k}={v}" for k, v in kv.items() if k not in ("chain", "action")]
                lines.append(f" {i}    chain={kv['chain']} action={kv['action']} " + " ".join(parts))
        return "\n".join(lines)

    def nat_print(self):
        lines = ["Flags: X - disabled, I - invalid; D - dynamic"]
        for i, kv in enumerate(self._fw_raw("nat")):
            parts = [f"{k}={v}" for k, v in kv.items() if k not in ("chain", "action")]
            lines.append(f" {i}    chain={kv['chain']} action={kv['action']} " + " ".join(parts))
        return "\n".join(lines)

    def conn_print(self):
        nat = self.device.nat
        lines = ["Flags: E - expected, S - seen-reply, A - assured, C - confirmed, D - dying, F - fasttrack, "
                 "s - srcnat, d - dstnat",
                 "Columns: PROTOCOL, SRC-ADDRESS, DST-ADDRESS, REPLY-SRC-ADDRESS, REPLY-DST-ADDRESS, TIMEOUT"]
        rows = []
        if nat is not None:
            nat._expire()
            names = {1: "icmp", 6: "tcp", 17: "udp"}
            for i, t in enumerate(nat.table):
                flag = "SACs" if t.in_global[0] != t.in_local[0] else "SAC"
                rows.append([str(i), flag, names.get(t.proto, str(t.proto)), str(t.in_local[0]), str(t.outside[0]),
                             str(t.outside[0]), str(t.in_global[0]), "9s"])
        return "\n".join(lines) + "\n" + table(["#", "    ", "PROTOCOL", "SRC-ADDRESS", "DST-ADDRESS",
                                                 "REPLY-SRC-ADDRESS", "REPLY-DST-ADDRESS", "TIMEOUT"], rows, gap=1)

    # -- /routing rip (v7) ---------------------------------------------------------------------

    def conn_tracking(self):
        rows = []
        for i, f in enumerate(self.device.conntrack.active()):
            proto, src, dst, sp, dp = f.key
            name = {1: "icmp", 6: "tcp", 17: "udp"}.get(proto, str(proto))
            rows.append([str(i), name, f"{src}:{sp}" if proto != 1 else str(src), f"{dst}:{dp}" if proto != 1 else str(dst),
                         "established" if f.packets > 1 else "new"])
        return "Columns: PROTOCOL, SRC-ADDRESS, DST-ADDRESS, TCP-STATE\n" + table(
            ["#", "PROTOCOL", "SRC-ADDRESS", "DST-ADDRESS", "STATE"], rows, gap=1)
    def _rip_inst_add_v2(self, args):
        kv, _ = self._kv(args, {"name", "redistribute", "originate-default", "comment", "disabled"})
        kv.setdefault("name", "rip1")
        for w in filter(None, kv.get("redistribute", "").split(",")):
            if w not in ("connected", "static", "ospf"):
                raise _MtError(f"invalid value {w} for argument redistribute")
        self._mt_list("mt_rip").append(kv)
        self._rip_apply()
        return None

    def _rip_tpl_add_v2(self, args):
        kv, pos = self._kv(args, {"interfaces", "instance", "passive", "comment", "disabled"}, positional=1)
        if "interfaces" not in kv or "instance" not in kv:
            raise _MtError("failure: interfaces and instance must be specified")
        if not any(i["name"] == kv["instance"] for i in self._mt_list("mt_rip")):
            raise _MtError("input does not match any value of instance")
        for x in kv["interfaces"].split(","):
            self._port(x)
        if "passive" in pos:
            kv["passive"] = "yes"
        self._mt_list("mt_rip_tpl").append(kv)
        self._rip_apply()
        return None

    def _rip_remove(self, args, key):
        lst = self._mt_list(key)
        for tok, _ in args:
            for x in sorted((int(v) for v in tok.split(",") if v.isdigit()), reverse=True):
                if x >= len(lst):
                    raise _MtError("no such item")
                del lst[x]
        self._rip_apply()
        return None

    def _rip_apply(self):
        from ..routing.rip import RipConfig
        d = self.device
        insts = [i for i in self._mt_list("mt_rip") if i.get("disabled") not in ("yes", "true")]
        if not insts:
            d.disable_rip()
            return
        inst = insts[0]
        ifs, passive = [], []
        for t in self._mt_list("mt_rip_tpl"):
            if t["instance"] != inst["name"]:
                continue
            for x in t["interfaces"].split(","):
                ifs.append(self._port(x).name)
                if t.get("passive") in ("yes", "true", ""):
                    passive.append(self._port(x).name)
        cfg = RipConfig(interfaces=ifs, passive=passive,
                        redistribute=[w for w in inst.get("redistribute", "").split(",") if w],
                        default_originate=inst.get("originate-default") in ("always", "if-installed"),
                        send_connected=False)
        d.enable_rip(cfg)

    def rip_inst_print(self):
        rows = [[str(i), e["name"], e.get("redistribute", ""), e.get("originate-default", "never")]
                for i, e in enumerate(self._mt_list("mt_rip"))]
        return "Columns: NAME, REDISTRIBUTE, ORIGINATE-DEFAULT\n" + table(["#", "NAME", "REDISTRIBUTE",
                                                                         "ORIGINATE-DEFAULT"], rows, gap=1)

    def rip_tpl_print(self):
        rows = [[str(i), e["interfaces"], e["instance"], "yes" if e.get("passive") else ""]
                for i, e in enumerate(self._mt_list("mt_rip_tpl"))]
        return "Columns: INTERFACES, INSTANCE, PASSIVE\n" + table(["#", "INTERFACES", "INSTANCE", "PASSIVE"], rows, gap=1)

    # -- DHCP server / relay ---------------------------------------------------------------------
    def _mt_list(self, key):
        return self.device.extra.setdefault(key, [])

    def _mt_remove(self, args, key):
        lst = self._mt_list(key)
        for tok, _ in args:
            for x in tok.split(","):
                idx = int(x) if x.isdigit() else next((i for i, e in enumerate(lst) if e.get("name") == x), None)
                if idx is None or idx >= len(lst):
                    raise _MtError("no such item")
                del lst[idx]
        self._dhcp_apply()
        return None

    def pool_add(self, args):
        kv, _ = self._kv(args, {"name", "ranges", "comment"})
        if "ranges" not in kv:
            raise _MtError("failure: ranges must be specified")
        a, _, b = kv["ranges"].partition("-")
        try:
            IPv4Address(a), IPv4Address(b or a)
        except ValueError:
            raise _MtError("invalid value for argument ranges")
        kv.setdefault("name", f"pool{len(self._mt_list('mt_pools')) + 1}")
        self._mt_list("mt_pools").append(kv)
        self._dhcp_apply()
        return None

    def pool_print(self):
        rows = [[str(i), e["name"], e["ranges"]] for i, e in enumerate(self._mt_list("mt_pools"))]
        return "Columns: NAME, RANGES\n" + table(["#", "NAME", "RANGES"], rows, gap=1)

    def dhcps_add(self, args):
        kv, _ = self._kv(args, {"name", "interface", "address-pool", "lease-time", "disabled", "comment"})
        if "interface" not in kv:
            raise _MtError("failure: interface must be specified")
        self._port(kv["interface"])
        if kv.get("address-pool") and not any(p["name"] == kv["address-pool"] for p in self._mt_list("mt_pools")):
            raise _MtError("input does not match any value of address-pool")
        kv.setdefault("name", f"dhcp{len(self._mt_list('mt_dhcps')) + 1}")
        self._mt_list("mt_dhcps").append(kv)
        self._dhcp_apply()
        return None

    def dhcps_print(self):
        rows = [[str(i), "X" if e.get("disabled") in ("yes", "true") else " ", e["name"], e["interface"],
                 e.get("address-pool", "static-only")] for i, e in enumerate(self._mt_list("mt_dhcps"))]
        return "Flags: X - DISABLED, I - INVALID\nColumns: NAME, INTERFACE, ADDRESS-POOL\n" + table(
            ["#", " ", "NAME", "INTERFACE", "ADDRESS-POOL"], rows, gap=1)

    def dhcpnet_add(self, args):
        kv, _ = self._kv(args, {"address", "gateway", "dns-server", "domain", "comment"})
        try:
            kv["address"] = str(IPv4Network(kv["address"], strict=False))
        except (KeyError, ValueError):
            raise _MtError("failure: address must be specified")
        self._mt_list("mt_dhcpnets").append(kv)
        self._dhcp_apply()
        return None

    def dhcpnet_print(self):
        rows = [[str(i), e["address"], e.get("gateway", ""), e.get("dns-server", "")]
                for i, e in enumerate(self._mt_list("mt_dhcpnets"))]
        return "Columns: ADDRESS, GATEWAY, DNS-SERVER\n" + table(["#", "ADDRESS", "GATEWAY", "DNS-SERVER"], rows, gap=1)

    def lease_print(self):
        srv = self.device.dhcp_server
        rows = []
        if srv is not None:
            for i, b in enumerate(srv.bindings.values()):
                rows.append([str(i), "D", str(b.ip), _mac(b.mac), b.hostname, b.pool, "bound"])
        return "Flags: D - DYNAMIC\nColumns: ADDRESS, MAC-ADDRESS, HOST-NAME, SERVER, STATUS\n" + table(
            ["#", " ", "ADDRESS", "MAC-ADDRESS", "HOST-NAME", "SERVER", "STATUS"], rows, gap=1)

    def relay_add(self, args):
        kv, _ = self._kv(args, {"name", "interface", "dhcp-server", "local-address", "disabled", "comment"})
        if "interface" not in kv or "dhcp-server" not in kv:
            raise _MtError("failure: interface and dhcp-server must be specified")
        self._port(kv["interface"])
        kv.setdefault("name", f"relay{len(self._mt_list('mt_relays')) + 1}")
        self._mt_list("mt_relays").append(kv)
        self._dhcp_apply()
        return None

    def relay_print(self):
        rows = [[str(i), e["name"], e["interface"], e["dhcp-server"], e.get("local-address", "")]
                for i, e in enumerate(self._mt_list("mt_relays"))]
        return "Columns: NAME, INTERFACE, DHCP-SERVER, LOCAL-ADDRESS\n" + table(
            ["#", "NAME", "INTERFACE", "DHCP-SERVER", "LOCAL-ADDRESS"], rows, gap=1)

    def _dhcp_apply(self):
        from ..services.dhcp import DhcpPool, DhcpServerConfig
        d = self.device
        pools_by_name = {p["name"]: p for p in self._mt_list("mt_pools")}
        nets = self._mt_list("mt_dhcpnets")
        pools, ifs = [], []
        for srv in self._mt_list("mt_dhcps"):
            if srv.get("disabled") in ("yes", "true"):
                continue
            port = self._port(srv["interface"])
            ifs.append(port.name)
            rng = pools_by_name.get(srv.get("address-pool"))
            lo, _, hi = (rng["ranges"] if rng else "").partition("-")
            net = next((n for n in nets if lo and IPv4Address(lo) in IPv4Network(n["address"])), None)
            if net is None and port.ip is not None:
                net = next((n for n in nets if IPv4Network(n["address"]) == port.ip.network), None)
            if net is None or not lo:
                continue
            pools.append(DhcpPool(srv["name"], net["address"], net.get("gateway"), net.get("dns-server"),
                                  net.get("domain", ""), lo, hi or lo,
                                  lease=int(srv.get("lease-time", "1800").rstrip("s").rstrip("m") or 1800)))
        if ifs:
            d.dhcp().cfg = DhcpServerConfig(True, pools, [], ifs)
        else:
            d.dhcp_server = None
        relay_ifs = {}
        for r in self._mt_list("mt_relays"):
            if r.get("disabled") not in ("yes", "true"):
                relay_ifs.setdefault(self._port(r["interface"]).name, []).append(r["dhcp-server"])
        for p in d.ports.values():
            p.helpers = list(relay_ifs.get(p.name, []))

    # -- /ip firewall address-list -----------------------------------------------------------
    def addrlist_add(self, args):
        kv, _ = self._kv(args, {"list", "address", "comment", "disabled"})
        if "list" not in kv or "address" not in kv:
            raise _MtError("failure: list and address must be specified")
        try:
            net = IPv4Network(kv["address"] if "/" in kv["address"] else kv["address"] + "/32", strict=False)
        except ValueError:
            raise _MtError("invalid value for argument address")
        self.device.extra.setdefault("mt_addrlist", []).append({"list": kv["list"], "address": str(net)})
        self._bgp_apply()
        return None

    def addrlist_remove(self, args):
        lst = self.device.extra.setdefault("mt_addrlist", [])
        for i in sorted({int(x) for t, _ in args for x in t.split(",") if x.isdigit()}, reverse=True):
            if i >= len(lst):
                raise _MtError("no such item")
            del lst[i]
        self._bgp_apply()
        return None

    def addrlist_print(self):
        rows = [[str(i), e["list"], e["address"]] for i, e in enumerate(self.device.extra.get("mt_addrlist", []))]
        return "Columns: LIST, ADDRESS\n" + table(["#", "LIST", "ADDRESS"], rows, gap=1)

    # -- /routing bgp (v7) -----------------------------------------------------------------------
    BGP_KEYS = {"name", "remote.address", "remote.as", "as", "local.role", "local.address", "router-id",
                "multihop", "nexthop-choice", "output.network", "output.redistribute",
                "output.default-originate", "comment", "disabled", "templates", "connect", "listen"}

    def bgpconn_add(self, args):
        kv, _ = self._kv(args, self.BGP_KEYS)
        for k in ("remote.address", "remote.as", "as"):
            if k not in kv:
                raise _MtError(f"failure: {k} must be set")
        try:
            IPv4Address(kv["remote.address"].split("/")[0])
            int(kv["remote.as"]), int(kv["as"])
        except ValueError:
            raise _MtError("invalid value for argument remote.address or AS")
        conns = self.device.extra.setdefault("mt_bgp", [])
        if any(int(c["as"]) != int(kv["as"]) for c in conns):
            raise _MtError("failure: all connections must use the same local AS in this simulator")
        kv.setdefault("name", f"bgp{len(conns) + 1}")
        kv["remote.address"] = kv["remote.address"].split("/")[0]
        conns.append(kv)
        self._bgp_apply()
        return None

    def bgpconn_remove(self, args):
        conns = self.device.extra.setdefault("mt_bgp", [])
        for tok, _ in args:
            for x in tok.split(","):
                idx = int(x) if x.isdigit() else next((i for i, c in enumerate(conns) if c["name"] == x), None)
                if idx is None or idx >= len(conns):
                    raise _MtError("no such item")
                del conns[idx]
        self._bgp_apply()
        return None

    def _bgp_apply(self):
        from ..routing.bgp import BgpConfig
        d = self.device
        conns = [c for c in d.extra.get("mt_bgp", []) if c.get("disabled") not in ("yes", "true")]
        if not conns:
            d.disable_bgp()
            return
        lists = d.extra.get("mt_addrlist", [])
        neighbors, networks, redistribute, rid = {}, [], [], None
        for c in conns:
            nb = {"remote_as": int(c["remote.as"]), "mt_name": c["name"]}
            if c.get("local.address"):
                owner = next((p for p in d.ports.values() if p.ip and str(p.ip.ip) == c["local.address"]), None)
                if owner is not None:
                    nb["update_source"] = owner.name
            if c.get("multihop") in ("yes", "true"):
                nb["multihop"] = 64
            if c.get("nexthop-choice") == "force-self":
                nb["next_hop_self"] = True
            if c.get("output.default-originate") in ("always", "if-installed"):
                nb["default_originate"] = True
            if c.get("local.role", "").endswith("rr-client") or c.get("local.role") == "ibgp-rr":
                nb["rr_client"] = True
            neighbors[c["remote.address"]] = nb
            if c.get("output.network"):
                networks += [e["address"] for e in lists if e["list"] == c["output.network"]]
            for w in filter(None, c.get("output.redistribute", "").split(",")):
                redistribute.append(w)
            rid = rid or c.get("router-id")
        cfg = BgpConfig(asn=int(conns[0]["as"]), router_id=rid, neighbors=neighbors,
                        networks=sorted(set(networks)), redistribute=sorted(set(redistribute)))
        if d.bgp is None:
            d.enable_bgp(cfg)
        else:
            old = d.bgp.cfg.neighbors
            for ip in old:
                if ip in neighbors and old[ip].get("remote_as") != neighbors[ip]["remote_as"]:
                    d.bgp.reset_peer(ip, "Remote AS changed")
            d.bgp.cfg = cfg

    def bgpconn_print(self):
        out = ["Flags: D - dynamic, X - disabled, I - inactive"]
        for i, c in enumerate(self.device.extra.get("mt_bgp", [])):
            out.append(f" {i}   name=\"{c['name']}\"")
            out.append(f"      remote.address={c['remote.address']} .as={c['remote.as']}")
            out.append(f"      local.role={c.get('local.role', 'ebgp')}" +
                       (f" .address={c['local.address']}" if c.get("local.address") else ""))
            extra = [f"{k}={v}" for k, v in c.items() if k.startswith("output.") or k in ("multihop", "nexthop-choice")]
            out.append(f"      as={c['as']}" + (" " + " ".join(extra) if extra else ""))
        return "\n".join(out)

    def bgpsess_print(self):
        b = self.device.bgp
        out = ["Flags: E - established"]
        if b is not None and b.running:
            for i, ip in enumerate(sorted(b.peers, key=int)):
                p = b.peers[ip]
                nc = b.ncfg(ip)
                flag = "E" if p.state == "Established" else " "
                up = int((self.device.engine.now - p.up_since) / 1000) if p.state == "Established" else 0
                out += [f" {i} {flag} name=\"{nc.get('mt_name', ip)}-1\"",
                        f"     remote.address={ip} .as={nc.get('remote_as')} .id={p.remote_rid or ''} "
                        f".messages={p.msgs_rx}",
                        f"     local.address={p.local_ip or ''} .as={b.cfg.asn} .id={b.router_id} .messages={p.msgs_tx}",
                        f"     prefix-count={len(p.adj_in) if p.state == 'Established' else 0} uptime={up}s"
                        + (f" last-error=\"{p.last_error}\"" if p.last_error else "")]
        return "\n".join(out)

    def bgpadv_print(self):
        b = self.device.bgp
        rows = []
        if b is not None and b.running:
            for ip in sorted(b.peers, key=int):
                for net, a in b.peers[ip].adj_out.items():
                    rows.append([str(ip), str(net), str(a.next_hop), " ".join(map(str, a.as_path)), a.origin])
        return "Columns: PEER, PREFIX, NEXTHOP, AS-PATH, ORIGIN\n" + table(
            ["PEER", "PREFIX", "NEXTHOP", "AS-PATH", "ORIGIN"], rows, gap=1)

    # -- loopbacks (/interface bridge on a router) --------------------------------------------
    def lo_add(self, args):
        kv, _ = self._kv(args, {"name", "comment", "protocol-mode", "disabled"})
        name = kv.get("name") or f"bridge{sum(1 for p in self.device.ports.values() if p.is_loopback) + 1}"
        if name in self.device.ports:
            raise _MtError("failure: interface with such name exists")
        self.device.add_loopback(name)
        return None

    def lo_remove(self, args):
        los = [p for p in self.device.ports.values() if p.is_loopback]
        for tok, _ in args:
            for x in tok.split(","):
                p = los[int(x)] if x.isdigit() and int(x) < len(los) else self.device.ports.get(x)
                if p is None or not p.is_loopback:
                    raise _MtError("no such item")
                self.device.remove_loopback(p)
        return None

    def lo_print(self):
        rows = [[str(i), "R" if p.is_up else " ", f'name="{p.name}"']
                for i, p in enumerate(p for p in self.device.ports.values() if p.is_loopback)]
        return "Flags: X - disabled, R - running\n" + "\n".join(f" {r[0]}  {r[1]} {r[2]} mtu=auto" for r in rows)

    # -- /routing ospf (v7) ---------------------------------------------------------------------
    def _ospf(self, create=False):
        from ..routing.ospf import OspfConfig
        d = self.device
        if d.ospf is None or not d.ospf.cfg.enabled:
            if not create:
                return None
            cfg = d.ospf.cfg if d.ospf is not None else OspfConfig()
            cfg.enabled = True
            d.enable_ospf(cfg)
        return d.ospf.cfg

    def _ospf_changed(self):
        if self.device.ospf is not None and self.device.ospf.running:
            self.device.ospf.reset_router_id()
            self.device.ospf.reconfigure()
        return None

    def _ospf_inst_add_v2(self, args):
        name, rest = self._target(args)
        kv, _ = self._kv(rest if name else args, {"name", "router-id", "originate-default", "redistribute",
                                                  "version", "comment", "disabled"})
        c = self._ospf(create=True)
        c.mt_instance = kv.get("name", c.mt_instance)
        if "router-id" in kv:
            from ..addresses import is_valid_ip
            if not is_valid_ip(kv["router-id"]):
                raise _MtError("invalid value of router-id, IP address expected")
            c.router_id = kv["router-id"]
        if "originate-default" in kv:
            v = kv["originate-default"]
            c.default_originate = {"always": "always", "if-installed": "yes", "never": None}.get(v)
            if v not in ("always", "if-installed", "never"):
                raise _MtError("invalid value for argument originate-default")
        if "redistribute" in kv:
            out = []
            for w in filter(None, kv["redistribute"].split(",")):
                if w not in ("static", "connected"):
                    raise _MtError(f"invalid value {w} for argument redistribute")
                out.append(w)
            c.redistribute = out
        return self._ospf_changed()

    def ospf_inst_remove(self, args):
        self.device.disable_ospf()
        return None

    def ospf_inst_print(self):
        c = self._ospf()
        if c is None:
            return "Flags: X - disabled, I - inactive"
        rid = c.router_id or "main"
        extra = []
        if c.default_originate:
            extra.append(f"originate-default={'always' if c.default_originate == 'always' else 'if-installed'}")
        if c.redistribute:
            extra.append("redistribute=" + ",".join(c.redistribute))
        return ("Flags: X - disabled, I - inactive\n"
                f" 0    name=\"{c.mt_instance}\" version=2 vrf=main router-id={rid} " + " ".join(extra))

    def _ospf_area_add_v2(self, args):
        from ..routing.ospf import area_id
        kv, _ = self._kv(args, {"name", "area-id", "instance", "type", "comment", "disabled"})
        c = self._ospf()
        if c is None or kv.get("instance", c.mt_instance) != c.mt_instance:
            raise _MtError("input does not match any value of instance")
        try:
            aid = area_id(kv.get("area-id", "0.0.0.0"))
        except ValueError:
            raise _MtError("invalid value of area-id")
        name = kv.get("name") or f"area{len(c.mt_areas)}"
        c.mt_areas[name] = aid
        for t in c.templates:
            if t.get("area_name") == name:
                t["area"] = aid
        return self._ospf_changed()

    def ospf_area_remove(self, args):
        c = self._ospf()
        names = list(c.mt_areas) if c else []
        for tok, _ in args:
            for x in tok.split(","):
                n = names[int(x)] if x.isdigit() and int(x) < len(names) else x
                if c is None or n not in c.mt_areas:
                    raise _MtError("no such item")
                del c.mt_areas[n]
                for t in c.templates:
                    if t.get("area_name") == n:
                        t["area"] = None
        return self._ospf_changed()

    def ospf_area_print(self):
        c = self._ospf()
        lines = ["Flags: X - disabled, I - inactive, D - default", "Columns: NAME, INSTANCE, AREA-ID, TYPE"]
        rows = [[str(i), n, c.mt_instance, a, "default"] for i, (n, a) in enumerate((c.mt_areas if c else {}).items())]
        return "\n".join(lines) + "\n" + table(["#", "NAME", "INSTANCE", "AREA-ID", "TYPE"], rows, gap=1)

    def _ospf_tpl_add_v2(self, args):
        kv, pos = self._kv(args, {"networks", "area", "interfaces", "passive", "cost", "hello-interval",
                                  "dead-interval", "priority", "type", "comment", "disabled"}, positional=1)
        c = self._ospf()
        if c is None or kv.get("area") not in c.mt_areas:
            raise _MtError("input does not match any value of area")
        t = {"area_name": kv["area"], "area": c.mt_areas[kv["area"]], "networks": [], "interfaces": []}
        for n in filter(None, kv.get("networks", "").split(",")):
            try:
                t["networks"].append(str(IPv4Network(n, strict=False)))
            except ValueError:
                raise _MtError(f"invalid value {n} for argument networks")
        for n in filter(None, kv.get("interfaces", "").split(",")):
            t["interfaces"].append(self._port(n).name)
        if "passive" in pos or kv.get("passive") in ("yes", "true", ""):
            t["passive"] = True
        for key, name in (("cost", "cost"), ("priority", "priority")):
            if key in kv:
                t[name] = int(kv[key])
        for key, name in (("hello-interval", "hello"), ("dead-interval", "dead")):
            if key in kv:
                t[name] = float(kv[key].rstrip("s"))
        c.templates.append(t)
        return self._ospf_changed()

    def ospf_tpl_remove(self, args):
        c = self._ospf()
        for tok, _ in args:
            for x in sorted((int(v) for v in tok.split(",") if v.isdigit()), reverse=True):
                if c is None or x >= len(c.templates):
                    raise _MtError("no such item")
                del c.templates[x]
        return self._ospf_changed()

    def ospf_tpl_print(self):
        c = self._ospf()
        lines = ["Flags: X - disabled, I - inactive", "Columns: INTERFACES, AREA, NETWORKS, PASSIVE, COST"]
        rows = []
        for i, t in enumerate(c.templates if c else []):
            rows.append([str(i), ",".join(t["interfaces"]), t["area_name"], ",".join(t["networks"]),
                         "yes" if t.get("passive") else "", str(t.get("cost", 1))])
        return "\n".join(lines) + "\n" + table(["#", "INTERFACES", "AREA", "NETWORKS", "PASSIVE", "COST"],
                                                rows, gap=1)

    def _ospf_running(self):
        o = self.device.ospf
        return o if o is not None and o.running else None

    def ospf_if_print(self):
        o = self._ospf_running()
        lines = ["Flags: D - DYNAMIC", "Columns: ADDRESS, AREA, STATE, NETWORK-TYPE, COST, PRIORITY"]
        rows = []
        if o:
            names = {v: k for k, v in o.cfg.mt_areas.items()}
            for p in self.device.ports.values():
                area = o.iface_area(p)
                if area is None:
                    continue
                dr, bdr = o.dr_bdr(p)
                st = "passive" if o.is_passive(p) else ("dr" if dr == p.ip.ip else "bdr" if bdr == p.ip.ip else "other")
                rows.append([str(len(rows)), "D", f"{p.ip.ip}%{p.name}", names.get(area, area), st,
                             "ptp" if p.is_loopback else "broadcast", str(o.cost(p)), str(o.priority(p))])
        return "\n".join(lines) + "\n" + table(["#", " ", "ADDRESS", "AREA", "STATE", "NETWORK-TYPE", "COST",
                                                 "PRIORITY"], rows, gap=1)

    def ospf_nb_print(self):
        o = self._ospf_running()
        out = ["Flags: V - virtual; D - dynamic"]
        if o:
            names = {v: k for k, v in o.cfg.mt_areas.items()}
            for i, (n, state, dead) in enumerate(o.neighbor_rows()):
                dr, bdr = o.dr_bdr(n.port)
                st = "Full" if n.two_way and state.startswith("FULL") else ("2-Way" if n.two_way else "Init")
                up = int((self.device.engine.now - n.full_since) / 1000)
                out.append(f" {i}  D instance={o.cfg.mt_instance} area={names.get(o.iface_area(n.port), '?')} "
                           f"address={n.ip} priority={n.priority} router-id={n.rid} dr={dr or '0.0.0.0'} "
                           f"bdr={bdr or '0.0.0.0'} state=\"{st}\" state-changes=6 adjacency={up}s timeout={int(dead)}s")
        return "\n".join(out)

    def ospf_lsa_print(self):
        o = self._ospf_running()
        out = ["Flags: S - self-originated, F - flushing, W - wraparound; D - dynamic"]
        if o:
            names = {v: k for k, v in o.cfg.mt_areas.items()}
            kinds = {1: "router", 3: "inter-area-prefix", 4: "inter-area-router", 5: "external"}
            for i, l in enumerate(x for x in o.database() if x.body):
                flag = "SD" if l.adv == o.router_id else " D"
                area = names.get(l.area, l.area) if l.area else "external"
                out.append(f" {i} {flag} instance={o.cfg.mt_instance} area={area} type=\"{kinds[l.type]}\" "
                           f"originator={l.adv} id={l.ls_id} sequence=0x{l.seq:X}")
        return "\n".join(out)

    # -- /interface vlan (router subinterfaces) ----------------------------------------------
    def _vlan_ifaces(self):
        return [p for p in self.device.ports.values() if p.is_sub or (p.is_svi and p.name != "bridge")]

    def vlan_add(self, args):
        kv, _ = self._kv(args, {"name", "vlan-id", "interface", "comment", "disabled", "mtu"})
        if "vlan-id" not in kv or "interface" not in kv:
            raise _MtError("failure: vlan-id and interface must be specified")
        if not kv["vlan-id"].isdigit() or not 1 <= int(kv["vlan-id"]) <= 4094:
            raise _MtError("value of vlan-id out of range (1..4094)")
        parent = self._port(kv["interface"])
        vid = int(kv["vlan-id"])
        if not self.is_router:
            if parent.name != "bridge":
                raise _MtError("failure: on a switch create VLAN interfaces on the bridge (interface=bridge)")
            name = kv.get("name") or f"vlan{len(self._vlan_ifaces()) + 1}"
            if name in self.device.ports:
                raise _MtError("failure: interface with such name exists")
            if self.device.svi_for_vlan(vid) is not None:
                raise _MtError("failure: vlan with such vlan-id already exists on interface")
            self.device.add_vlan(vid)
            svi = self.device.add_svi(vid, name, name)
            svi.description = kv.get("comment", "")
            self.device.engine.emit("device_changed", self.device)
            return None
        if parent.is_sub:
            raise _MtError("failure: can not create VLAN on top of VLAN in this simulator")
        name = kv.get("name") or f"vlan{len(self._vlan_ifaces()) + 1}"
        if name in self.device.ports:
            raise _MtError("failure: interface with such name exists")
        if any(s.parent is parent and s.vlan == vid for s in self._vlan_ifaces()):
            raise _MtError("failure: vlan with such vlan-id already exists on interface")
        sub = self.device.add_subinterface(parent, vid, name=name, vlan=vid)
        sub.description = kv.get("comment", "")
        if kv.get("disabled") in ("yes", "true"):
            sub.set_admin(False)
        self.device.engine.emit("device_changed", self.device)
        return None

    def vlan_remove(self, args):
        if not args:
            raise _MtError("missing value(s) of argument(s) numbers")
        subs = self._vlan_ifaces()
        for tok, _ in args:
            for x in tok.split(","):
                sub = subs[int(x)] if x.isdigit() and int(x) < len(subs) else self.device.ports.get(x)
                if sub is None or not (sub.is_sub or sub.is_svi):
                    raise _MtError("no such item")
                if sub.is_svi:
                    self.device.remove_svi(sub)
                else:
                    self.device.remove_subinterface(sub)
        self.device.engine.emit("device_changed", self.device)
        return None

    def vlan_print(self):
        rows = [[str(i), "R" if s.is_up else " ", s.name, "1500", "enabled", str(s.vlan),
                 "bridge" if s.is_svi else s.parent.name]
                for i, s in enumerate(self._vlan_ifaces())]
        return ("Flags: R - RUNNING\nColumns: NAME, MTU, ARP, VLAN-ID, INTERFACE\n"
                + table(["#", " ", "NAME", "MTU", "ARP", "VLAN-ID", "INTERFACE"], rows, gap=1))

    def host_print(self):
        rows = [[str(i), "D", _mac(e.mac), e.port.name, "bridge"]
                for i, e in enumerate(self.device.mac_entries())]
        return ("Flags: D - DYNAMIC; L - LOCAL\nColumns: MAC-ADDRESS, ON-INTERFACE, BRIDGE\n"
                + table(["#", " ", "MAC-ADDRESS", "ON-INTERFACE", "BRIDGE"], rows, gap=1))

    # -- /system -------------------------------------------------------------------------------
    # -- /ip service, /user, /system ssh|telnet ------------------------------------------------
    def _acc(self):
        from ..services.remote import access
        return access(self.device)

    def service_print(self):
        a = self._acc()
        rows = [[str(i), "" if a.get(n, False) or n not in ("telnet", "ssh") else "X", n, str(port)]
                for i, (n, port) in enumerate((("telnet", 23), ("ftp", 21), ("www", 80), ("ssh", 22),
                                               ("www-ssl", 443), ("api", 8728), ("winbox", 8291), ("api-ssl", 8729)))]
        return "Flags: X - DISABLED\nColumns: NAME, PORT\n" + table(["#", " ", "NAME", "PORT"], rows, gap=1)

    def _service_names(self, args):
        names = []
        for tok, _ in args:
            if "=" in tok:
                continue
            for x in tok.split(","):
                if x.isdigit():
                    x = ("telnet", "ftp", "www", "ssh", "www-ssl", "api", "winbox", "api-ssl")[int(x)] if int(x) < 8 else ""
                names.append(x)
        return names

    def service_set(self, args):
        kv, _ = self._kv(args, {"disabled", "port", "address"}, positional=1)
        for n in self._service_names(args):
            if n in ("telnet", "ssh") and "disabled" in kv:
                self._acc()[n] = kv["disabled"] in ("no", "false")
        return None

    def service_onoff(self, args, on):
        for n in self._service_names(args):
            if n in ("telnet", "ssh"):
                self._acc()[n] = on
        return None

    def user_print(self):
        rows = [[str(i), "", u, "full"] for i, u in enumerate(self._acc().get("users", {}))]
        return "Flags: X - DISABLED\nColumns: NAME, GROUP\n" + table(["#", " ", "NAME", "GROUP"], rows, gap=1)

    def user_add(self, args):
        kv, _ = self._kv(args, {"name", "password", "group", "comment", "disabled"})
        if "name" not in kv:
            raise _MtError("failure: name must be specified")
        if "group" not in kv:
            raise _MtError("failure: group must be specified")
        users = self._acc().setdefault("users", {})
        if kv["name"] in users:
            raise _MtError("failure: user with the same name already exists")
        users[kv["name"]] = kv.get("password", "")
        return None

    def _user_names(self, args):
        users = list(self._acc().get("users", {}))
        out = []
        for tok, _ in args:
            if "=" in tok:
                continue
            for x in tok.split(","):
                if x.isdigit() and int(x) < len(users):
                    out.append(users[int(x)])
                elif x in users:
                    out.append(x)
                else:
                    raise _MtError("no such item")
        return out

    def user_set(self, args):
        kv, _ = self._kv(args, {"password", "group", "name", "comment", "disabled"}, positional=1)
        users = self._acc().setdefault("users", {})
        for u in self._user_names(args):
            if "password" in kv:
                users[u] = kv["password"]
        return None

    def user_remove(self, args):
        users = self._acc().setdefault("users", {})
        for u in self._user_names(args):
            users.pop(u, None)
        return None

    def remote_cmd(self, proto, args):
        from ..services.remote import connect
        kv, pos = self._kv(args, {"address", "user", "port"}, positional=1)
        target = kv.get("address") or (pos[0] if pos else None)
        if not target or not is_valid_ip(target):
            raise _MtError("invalid value for argument address")
        user = kv.get("user", "admin") if proto == "ssh" else ""
        return connect(self, target, proto, user, "mikrotik", int(kv["port"]) if "port" in kv else None)

    def identity_set(self, args):
        kv, _ = self._kv(args, {"name"})
        if "name" not in kv:
            raise _MtError("missing value(s) of argument(s) name")
        self.device.hostname = kv["name"]
        self.device.engine.emit("device_changed", self.device)
        return None

    def resource(self):
        d = self.device
        board = d.model.replace("MikroTik ", "")
        return (f"                   uptime: {uptime_text(d.engine.now)}\n"
                f"                  version: 7.16.1 (stable)\n"
                f"               board-name: {board}\n"
                f"         architecture-name: arm\n"
                f"                 cpu-load: 1%")

    def clock(self):
        t = sim_clock(self.device.engine.now)
        return f"                  time: {t:%H:%M:%S}\n                  date: {t:%Y-%m-%d}\n  time-zone-name: UTC"

    def reset(self):
        self.device.factory_reset()
        self.device.startup_config = None
        self.cwd = ()
        return "Dangerous! Reset anyway? [y/N]: y\nsystem configuration will be reset"

    def reboot(self):
        self.cwd = ()
        return "Reboot, yes? [y/N]: y\nsystem will reboot shortly"

    # -- export ----------------------------------------------------------------------------------
    def export(self):
        d = self.device
        t = sim_clock(d.engine.now)
        lines = [f"# {t:%Y-%m-%d %H:%M:%S} by RouterOS 7.16.1",
                 f"# model = {d.model.replace('MikroTik ', '')}"]
        eth = []
        for p in d.ports.values():
            parts = []
            if p.description:
                parts.append(f'comment="{p.description}"' if " " in p.description else f"comment={p.description}")
            if not p.admin_up:
                parts.append("disabled=yes")
            if parts:
                eth.append(f"set [ find default-name={p.name} ] " + " ".join(parts))
        if eth:
            lines += ["/interface ethernet"] + eth
        if not self.is_router:
            vf = " vlan-filtering=yes" if d.vlan_filtering else ""
            lines += ["/interface bridge", f"add name=bridge{vf}", "/interface bridge port"]
            lines += [f"add bridge=bridge interface={p.name}"
                      + (f" pvid={p.access_vlan}" if p.access_vlan != 1 else "") for p in d.l2_ports()]
            bonds = d.channels()
            if bonds:
                lines.append("/interface bonding")
                for po in bonds:
                    mode = "802.3ad" if any(m.channel_mode == "active" for m in po.members) else "balance-rr"
                    lines.append(f"add mode={mode} name={po.name} slaves={','.join(m.name for m in po.members)}")
            if d.bridge_vlans:
                lines.append("/interface bridge vlan")
                for e in d.bridge_vlans:
                    parts = [f"add bridge=bridge vlan-ids={','.join(map(str, e['vlan_ids']))}"]
                    if e["tagged"]:
                        parts.append("tagged=" + ",".join(e["tagged"]))
                    if e["untagged"]:
                        parts.append("untagged=" + ",".join(e["untagged"]))
                    lines.append(" ".join(parts))
        if True:
            vl = self._vlan_ifaces()
            if vl:
                lines.append("/interface vlan")
                lines += [f"add interface={'bridge' if s.is_svi else s.parent.name} name={s.name} vlan-id={s.vlan}"
                          for s in vl]
            addrs = self._addresses()
            if addrs:
                lines.append("/ip address")
                lines += [f"add address={a.with_prefixlen} interface={p.name} "
                          f"network={a.network.network_address}" for p, a in addrs]
            if d.static_routes:
                lines.append("/ip route")
                for s in d.static_routes:
                    extra = f" distance={s.distance}" if s.distance != 1 else ""
                    if s.interface == "Null0":
                        lines.append(f"add blackhole dst-address={s.network}{extra}")
                        continue
                    gw = str(s.next_hop) if s.next_hop else s.interface
                    lines.append(f"add dst-address={s.network} gateway={gw}{extra}")
            rpf = {p.urpf for p in d.ports.values() if getattr(p, "urpf", None)}
            if rpf:
                lines += ["/ip settings", f"set rp-filter={rpf.pop()}"]
            tuns = [p for p in d.ports.values() if p.is_tunnel]
            if tuns:
                lines.append("/interface gre")
                for t in tuns:
                    sec = f" ipsec-secret={t.ipsec_key}" if t.ipsec_key else ""
                    lines.append(f"add local-address={t.source} name={t.name} remote-address={t.destination}{sec}")
            vr = self._vrrps()
            if vr:
                lines.append("/interface vrrp")
                for v in vr:
                    g = d.fhrp.group(v["interface"], "vrrp", v["vrid"], create=False)
                    pr = f" priority={g.priority}" if g is not None and g.priority != 100 else ""
                    lines.append(f"add interface={v['interface']} name={v['name']}{pr} vrid={v['vrid']}")
                lines.append("/ip address")
                lines += [f"add address={v['address']} interface={v['name']}" for v in vr if v.get("address")]
            x = d.extra
            if x.get("mt_v6pools"):
                lines.append("/ipv6 pool")
                lines += [f"add name={k} prefix={v} prefix-length=64" for k, v in x["mt_v6pools"].items()]
            if x.get("mt_dhcp6s"):
                lines.append("/ipv6 dhcp-server")
                lines += ["add " + " ".join(f"{k}={v}" for k, v in kv.items()) for kv in x["mt_dhcp6s"]]
            st6 = x.get("mt_v6igp")
            if st6:
                if st6.get("ospf_instance"):
                    proc = d.v6igp.get("ospf6")
                    rid = f" router-id={proc.cfg.router_id}" if proc is not None and proc.cfg.router_id else ""
                    lines += ["/routing ospf instance", f"add name={st6['ospf_instance']}{rid} version=3"]
                    lines += ["/routing ospf area"] + [f"add area-id={a} instance={st6['ospf_instance']} name={n}"
                                                       for n, a in st6["areas"].items()]
                for n in st6.get("ripng", []):
                    lines += ["/routing rip instance", f"add afi=ipv6 name={n}"]
                for tpl in st6.get("templates", []):
                    if tpl["kind"] == "ospf6":
                        lines += ["/routing ospf interface-template", f"add area={tpl['area']} interfaces={tpl['interfaces']}"]
                    else:
                        lines += ["/routing rip interface-template",
                                  f"add instance={tpl['instance']} interfaces={tpl['interfaces']}"]
            v6 = self._v6_list()
            if v6:
                lines.append("/ipv6 address")
                lines += [f"add address={a} interface={p.name}" + (" advertise=no" if p.ra is False else "")
                          for p, a in v6]
            if d.static_routes6:
                lines.append("/ipv6 route")
                lines += [f"add dst-address={s.network} gateway={s.next_hop}" for s in d.static_routes6]
        if self.is_router:
            for table_name in ("filter", "nat"):
                raw = d.extra.get("mt_" + table_name, [])
                if raw:
                    lines.append(f"/ip firewall {table_name}")
                    for kv in raw:
                        lines.append("add " + " ".join(f"{k}={v}" for k, v in kv.items()))
            for key, menu in (("mt_rip", "/routing rip instance"), ("mt_rip_tpl", "/routing rip interface-template"),
                              ("mt_pools", "/ip pool"), ("mt_dhcps", "/ip dhcp-server"),
                              ("mt_dhcpnets", "/ip dhcp-server network"), ("mt_relays", "/ip dhcp-relay"),
                              ("mt_addrlist", "/ip firewall address-list"), ("mt_bgp", "/routing bgp connection")):
                items = d.extra.get(key, [])
                if items:
                    lines.append(menu)
                    lines += ["add " + " ".join(f"{k}={v}" for k, v in it.items()) for it in items]
            los = [p for p in d.ports.values() if p.is_loopback]
            if los:
                lines[0:0] = ["/interface bridge"] + [f"add name={p.name}" for p in los]
            c = self._ospf()
            if c is not None:
                inst = f"add name={c.mt_instance} version=2"
                if c.router_id:
                    inst += f" router-id={c.router_id}"
                if c.default_originate:
                    inst += f" originate-default={'always' if c.default_originate == 'always' else 'if-installed'}"
                if c.redistribute:
                    inst += " redistribute=" + ",".join(c.redistribute)
                lines += ["/routing ospf instance", inst, "/routing ospf area"]
                lines += [f"add area-id={a} instance={c.mt_instance} name={n}" for n, a in c.mt_areas.items()]
                if c.templates:
                    lines.append("/routing ospf interface-template")
                    for t in c.templates:
                        parts = [f"add area={t['area_name']}"]
                        if t["interfaces"]:
                            parts.append("interfaces=" + ",".join(t["interfaces"]))
                        if t["networks"]:
                            parts.append("networks=" + ",".join(t["networks"]))
                        if t.get("passive"):
                            parts.append("passive")
                        if t.get("cost"):
                            parts.append(f"cost={t['cost']}")
                        lines.append(" ".join(parts))
        lines += ["/system identity", f"set name={d.hostname}"]
        return "\n".join(lines)

    # -- ping / traceroute -------------------------------------------------------------------------
    def ping(self, args):
        kv, pos = self._kv(args, {"count", "size", "address", "ttl", "interval"}, positional=1)
        target = kv.get("address") or (pos[0] if pos else None)
        if not target or not (is_valid_ip(target) or P_is_ipv6(target)):
            raise _MtError("invalid value for argument address")
        count = int(kv.get("count", 4))
        return make_ping(self.device, target, MtPing(self.write), count=count,
                           size=int(kv.get("size", 56)), timeout=1000.0, interval=1000.0,
                           ttl=int(kv["ttl"]) if "ttl" in kv else None).start()

    def traceroute(self, args):
        kv, pos = self._kv(args, {"address", "count", "max-hops"}, positional=1)
        target = kv.get("address") or (pos[0] if pos else None)
        if not target or not (is_valid_ip(target) or P_is_ipv6(target)):
            raise _MtError("invalid value for argument address")
        return make_trace(self.device, target, MtTrace(self.write),
                                 max_hops=int(kv.get("max-hops", 30)), timeout=1000.0).start()


class _MtError(Exception):
    pass
