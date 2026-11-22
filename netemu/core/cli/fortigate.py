"""Fortinet FortiGate (FortiOS 7.x) CLI dialect: config / edit / set / next / end.

Supported tables: ``system global`` (hostname), ``system interface`` (ip, allowaccess, status,
alias), ``router static`` (dst, gateway, device), ``firewall address`` (subnet) and ``firewall
policy`` (srcintf, dstintf, srcaddr, dstaddr, service, action, nat, schedule, name).
FortiGate semantics: traffic between interfaces needs an accept policy; policies are stateful
(replies pass) and ``set nat enable`` translates the source to the outgoing interface address.
"""
from __future__ import annotations

import shlex
from ipaddress import IPv4Address, IPv4Network

from ..addresses import make_interface, mask_to_prefix
from .base import CliSession, make_ping

TABLES = ("system global", "system interface", "router static", "firewall address", "firewall policy",
          "ips sensor")
SERVICES = {"ALL": "any", "HTTP": "http", "HTTPS": "https", "SSH": "ssh", "DNS": "dns", "PING": "icmp",
            "ALL_ICMP": "icmp", "TELNET": "telnet"}


class FortiSession(CliSession):
    def __init__(self, device):
        super().__init__(device)
        self.table: str | None = None
        self.entry: str | None = None
        self.work: dict | None = None             # the entry being edited (committed on next/end)

    @property
    def fo(self) -> dict:
        return self.device.extra.setdefault("forti", {"interface": {}, "static": {}, "address": {}, "policy": {}, "ips": {}})

    @property
    def prompt(self) -> str:
        h = self.device.hostname
        if self.entry is not None:
            return f"{h} ({self.entry}) # "
        if self.table is not None:
            return f"{h} ({self.table.split()[-1]}) # "
        return f"{h} # "

    def banner(self) -> str:
        return ""

    # -- parsing ----------------------------------------------------------------------------------
    def _execute(self, line: str):
        try:
            w = shlex.split(line)
        except ValueError:
            return "command parse error before 'quote'\nCommand fail. Return code -61"
        if not w:
            return None
        cmd, args = w[0], w[1:]
        if self.table is None:
            return self._top(cmd, args, line)
        if self.entry is None and self.table != "system global":
            return self._in_table(cmd, args)
        return self._in_entry(cmd, args)

    def _fail(self, what="command parse error"):
        return f"{what}\nCommand fail. Return code -61"

    def _sync(self) -> None:
        """Pull state changed outside FortiOS (the Config form, a loaded project) into the FortiOS tables,
        so that show prints it and the next CLI change does not wipe it."""
        d, fo = self.device, self.fo
        for p in d.ports.values():
            e = fo["interface"].get(p.name)
            want_ip = [str(p.ip.ip), str(p.ip.netmask)] if p.ip is not None else None
            if e is None and want_ip is None and p.admin_up and not p.description:
                continue
            e = fo["interface"].setdefault(p.name, {})
            if want_ip is None:
                e.pop("ip", None)
            else:
                e["ip"] = want_ip
            if p.description:
                e["alias"] = p.description
            else:
                e.pop("alias", None)
            if p.admin_up:
                e.pop("status", None)
            else:
                e["status"] = "down"
        cur = [(str(r.network.network_address), str(r.network.netmask), str(r.next_hop) if r.next_hop else None,
                r.interface) for r in d.static_routes]
        mine = [(e.get("dst", ["0.0.0.0", "0.0.0.0"])[0], e.get("dst", ["0.0.0.0", "0.0.0.0"])[1], e.get("gateway"),
                 e.get("device")) for _, e in sorted(fo["static"].items(), key=lambda x: int(x[0]))]
        if cur != mine:
            fo["static"] = {}
            for i, (net, mask, gw, dev) in enumerate(cur, 1):
                e = {"dst": [net, mask]}
                if gw:
                    e["gateway"] = gw
                if dev:
                    e["device"] = dev
                fo["static"][str(i)] = e

    def _top(self, cmd, args, line):
        if cmd in ("config", "show"):
            self._sync()
        if cmd == "config":
            path = " ".join(args)
            if path not in TABLES:
                return self._fail(f"command parse error before '{args[-1] if args else ''}'")
            self.table = path
            if path == "system global":
                self.work = {}
            return None
        if cmd == "show":
            return self.show(" ".join(args))
        if cmd == "get":
            text = " ".join(args)
            if text.startswith("router info routing-table"):
                return self.routing_table()
            if text.startswith("system interface"):
                return self.show_interfaces()
            if text.startswith("system status"):
                return f"Version: FortiGate-60F v7.4.3,build2573,240201 (GA.F)\nHostname: {self.device.hostname}"
            return self._fail()
        if cmd == "execute" and args[:1] == ["ping"] and len(args) == 2:
            return self.ping(args[1])
        if cmd == "diagnose" and args[:3] == ["sys", "session", "list"]:
            return self.sessions()
        if cmd in ("exit", "quit"):
            return None
        return self._fail("Unknown action 0")

    def _in_table(self, cmd, args):
        tkey = {"system interface": "interface", "router static": "static", "firewall address": "address",
                "firewall policy": "policy", "ips sensor": "ips"}[self.table]
        t = self.fo[tkey]
        if cmd == "edit" and args:
            name = args[0]
            if self.table in ("router static", "firewall policy") and not name.isdigit():
                return self._fail("entry not found in datasource")
            if self.table == "system interface" and self.device.get_port(name) is None:
                return self._fail("entry not found in datasource")
            self.entry = name
            self.work = dict(t.get(name, {}))
            if name not in t:
                self.write(f"new entry '{name}' added\n")
            return None
        if cmd == "delete" and args:
            t.pop(args[0], None)
            self._apply()
            return None
        if cmd in ("end", "abort"):
            self.table = None
            return None
        if cmd == "show":
            return self.show(self.table)
        return self._fail()

    def _in_entry(self, cmd, args):
        if cmd == "set" and args:
            key, vals = args[0], args[1:]
            err = self._check(key, vals)
            if err:
                return err
            self.work[key] = vals if len(vals) != 1 else vals[0]
            return None
        if cmd == "unset" and args:
            self.work.pop(args[0], None)
            return None
        if cmd in ("next", "end"):
            self._commit()
            if cmd == "end":
                self.table = None
            return None
        if cmd == "abort":
            self.entry, self.work = None, None
            if self.table == "system global":
                self.table = None
            return None
        if cmd == "show":
            return "\n".join(f"    set {k} {v if isinstance(v, str) else ' '.join(v)}" for k, v in self.work.items())
        return self._fail()

    def _check(self, key, vals):
        try:
            if key == "ip" and self.table == "system interface":
                make_interface(vals[0], vals[1])
            elif key == "dst":
                IPv4Network(f"{vals[0]}/{mask_to_prefix(vals[1])}", strict=False)
            elif key in ("gateway",):
                IPv4Address(vals[0])
            elif key == "subnet":
                IPv4Network(f"{vals[0]}/{mask_to_prefix(vals[1])}", strict=False)
            elif key in ("srcintf", "dstintf", "device"):
                for v in vals:
                    if self.device.get_port(v) is None:
                        return self._fail("entry not found in datasource")
            elif key == "action" and vals[0] not in ("accept", "deny"):
                return self._fail()
            elif key == "nat" and vals[0] not in ("enable", "disable"):
                return self._fail()
            elif key == "service":
                for v in vals:
                    if v not in SERVICES:
                        return self._fail("entry not found in datasource")
        except (ValueError, IndexError):
            return self._fail("value parse error")
        return None

    def _commit(self):
        if self.table == "system global":
            if "hostname" in self.work:
                self.device.hostname = self.work["hostname"]
                self.device.engine.emit("device_changed", self.device)
            self.work = None
            return
        key = {"system interface": "interface", "router static": "static", "firewall address": "address",
               "firewall policy": "policy", "ips sensor": "ips"}[self.table]
        self.fo[key][self.entry] = self.work
        self.entry, self.work = None, None
        self._apply()

    # -- apply to the device -----------------------------------------------------------------------
    def _apply(self):
        from ..security.nat import NatConfig, NatRule
        from ..security.stateful import ZoneFirewall, ZoneRule
        d = self.device
        fo = self.fo
        for name, e in fo["interface"].items():
            p = d.get_port(name)
            if p is None:
                continue
            ip = e.get("ip")
            p.ip = make_interface(ip[0], ip[1]) if isinstance(ip, list) else None
            p.set_admin(e.get("status", "up") != "down")
            p.description = e.get("alias", "")
        d.static_routes = []
        for idx, e in fo["static"].items():
            dst = e.get("dst", ["0.0.0.0", "0.0.0.0"])
            net = IPv4Network(f"{dst[0]}/{mask_to_prefix(dst[1])}", strict=False)
            d.add_static_route(net, IPv4Address(e["gateway"]) if e.get("gateway") else None, e.get("device"))

        def addr(name):
            if name == "all":
                return "0.0.0.0/0"
            a = fo["address"].get(name, {}).get("subnet")
            return str(IPv4Network(f"{a[0]}/{mask_to_prefix(a[1])}", strict=False)) if a else "0.0.0.0/0"

        def lst(v):
            return v if isinstance(v, list) else [v]
        zf = ZoneFirewall({p.name: [p.name] for p in d.ports.values()})
        nat_rules, inside, outside = [], set(), set()
        for idx in sorted(fo["policy"], key=int):
            e = fo["policy"][idx]
            if e.get("status") == "disable":
                continue
            for si in lst(e.get("srcintf", [])):
                for di in lst(e.get("dstintf", [])):
                    sp, dp = d.get_port(si), d.get_port(di)
                    if sp is None or dp is None:
                        continue
                    for sa in lst(e.get("srcaddr", "all")):
                        for da in lst(e.get("dstaddr", "all")):
                            for sv in lst(e.get("service", "ALL")):
                                zf.pairs.setdefault(f"{sp.name}>{dp.name}", []).append(ZoneRule(
                                    e.get("name", idx), "permit" if e.get("action") == "accept" else "drop",
                                    SERVICES.get(sv, "any"), addr(sa), addr(da)))
                    if e.get("nat") == "enable" and e.get("action") == "accept":
                        inside.add(sp.name)
                        outside.add(dp.name)
                        for sa in lst(e.get("srcaddr", "all")):
                            nat_rules.append(NatRule("dynamic", prefix=addr(sa), interface=dp.name, out_iface=dp.name,
                                                     overload=True, text=f"policy {idx}"))
        d.zfw = zf
        ips_ifaces = {}
        for idx in sorted(fo["policy"], key=int):
            e = fo["policy"][idx]
            if e.get("status") == "disable" or e.get("action") != "accept":
                continue
            sensor = e.get("ips-sensor")
            if sensor and sensor in fo.get("ips", {}):
                for si in lst(e.get("srcintf", [])):
                    if d.get_port(si) is not None:
                        ips_ifaces[si] = sensor
        if ips_ifaces:
            d.extra["ips"] = {"names": sorted(set(ips_ifaces.values())), "ifaces": ips_ifaces,
                              "drops": (d.extra.get("ips") or {}).get("drops", 0)}
        else:
            d.extra.pop("ips", None)
        if nat_rules:
            d.nat_engine().cfg = NatConfig(sorted(inside), sorted(outside), nat_rules, {})
        elif d.nat is not None:
            d.nat.cfg = NatConfig()
        d.routes_changed()

    # -- show / get / execute ---------------------------------------------------------------------
    def show(self, path):
        if not path:
            return "\n".join(self.show(t) for t in TABLES)
        if path not in TABLES:
            return self._fail()
        if path == "system global":
            return f'config system global\n    set hostname "{self.device.hostname}"\nend'
        key = {"system interface": "interface", "router static": "static", "firewall address": "address",
               "firewall policy": "policy", "ips sensor": "ips"}[path]
        out = [f"config {path}"]
        for name, e in self.fo[key].items():
            out.append(f'    edit "{name}"' if not name.isdigit() else f"    edit {name}")
            for k, v in e.items():
                vv = v if isinstance(v, str) else " ".join(v)
                if k in ("srcintf", "dstintf", "srcaddr", "dstaddr", "service", "device", "name", "alias"):
                    vv = " ".join(f'"{x}"' for x in (v if isinstance(v, list) else [v]))
                out.append(f"        set {k} {vv}")
            out.append("    next")
        out.append("end")
        return "\n".join(out)

    def show_interfaces(self):
        out = []
        for p in self.device.ports.values():
            out.append(f"== [ {p.name} ]\nname: {p.name}   mode: static    ip: "
                       f"{(str(p.ip.ip) + ' ' + str(p.ip.netmask)) if p.ip else '0.0.0.0 0.0.0.0'}   "
                       f"status: {'up' if p.admin_up else 'down'}    link: {'up' if p.is_up else 'down'}")
        return "\n".join(out)

    def routing_table(self):
        lines = ["Codes: K - kernel, C - connected, S - static, R - RIP, B - BGP, O - OSPF,", "",
                 "Routing table for VRF=0"]
        for r in self.device.routing_table():
            if r.code == "L":
                continue
            if r.code == "C":
                lines.append(f"C       {r.network} is directly connected, {r.port.name}")
            else:
                lines.append(f"{r.code[:1]}*      {r.network} [{r.distance}/{r.metric}] via {r.next_hop}, {r.port.name}")
        return "\n".join(lines)

    def sessions(self):
        out = []
        for f in self.device.conntrack.active():
            proto, src, dst, sp, dp = f.key
            out += [f"session info: proto={proto} proto_state=01 duration=1 expire=59 timeout=3600",
                    f"policy_dir=0 tunnel=/ {f.zone_pair}", f"hook=pre dir=org act=noop {src}:{sp}->{dst}:{dp}", ""]
        out.append(f"total session {len(self.device.conntrack.active())}")
        return "\n".join(out)

    def ping(self, target):
        from ..stack.icmp import PingListener
        out = self

        class L(PingListener):
            def on_start(self, s):
                out.write(f"PING {target} ({target}): 56 data bytes\n")

            def on_reply(self, seq, src, rtt, ttl, size):
                out.write(f"64 bytes from {src}: icmp_seq={seq} ttl={ttl} time={rtt:.1f} ms\n")

            def on_finish(self, st):
                loss = round(100 * (st.sent - st.received) / max(1, st.sent))
                out.write(f"\n--- {target} ping statistics ---\n{st.sent} packets transmitted, {st.received} packets "
                          f"received, {loss}% packet loss\n")
        try:
            return make_ping(self.device, target, L(), count=5, size=56).start()
        except ValueError:
            return self._fail("value parse error")

    def help(self, line: str) -> str:
        if self.table is None:
            return "config    Configure object.\nget       Get dynamic and system information.\nshow      Show configuration.\n" \
                   "execute   Execute static commands.\ndiagnose  Diagnose facility.\nexit      Exit the CLI."
        if self.entry is None:
            return "edit      Add/edit a table value.\ndelete    Delete a table value.\nend       End and save.\n" \
                   "show      Show configuration."
        return "set       Modify value.\nunset     Set to default value.\nnext      Configure next table entry.\n" \
               "end       End and save last config.\nabort     End and discard last config."
