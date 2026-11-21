"""Cisco ASA (ASA 5506-X) CLI dialect.

The ASA's own rules, not IOS ones: an interface passes traffic only once it has a ``nameif``;
traffic from a higher ``security-level`` to a lower one is allowed and tracked (replies come back),
lower to higher is denied unless an ACL applied with ``access-group`` permits it.  ICMP is not
inspected by default, so a ping from inside to outside gets no reply until ``inspect icmp`` (in
``policy-map global_policy / class inspection_default``) or ``fixup protocol icmp``.  NAT is object
NAT: ``object network N`` / ``subnet`` / ``nat (inside,outside) dynamic interface``.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from ..addresses import make_interface, mask_to_prefix
from . import parser as P
from .base import TreeSession, make_ping

PROMPTS = {"user": ">", "priv": "#", "config": "(config)#", "config-if": "(config-if)#",
           "config-network-object": "(config-network-object)#", "config-pmap": "(config-pmap)#",
           "config-pmap-c": "(config-pmap-c)#"}


class AsaSession(TreeSession):
    negate_word = "no"
    negate_modes = ("config", "config-if", "config-network-object", "config-pmap", "config-pmap-c")
    parent_modes = {"config-if": "config", "config-network-object": "config", "config-pmap": "config",
                    "config-pmap-c": "config"}

    @property
    def prompt(self) -> str:
        return self.device.hostname + PROMPTS[self.mode]

    def banner(self) -> str:
        return "\nType help or '?' for a list of available commands.\n"

    def set_mode(self, mode):
        self.mode = mode
        if mode in ("config", "priv", "user"):
            self.iface = None
        return None

    @property
    def asa(self) -> dict:
        return self.device.extra.setdefault("asa", {"ifaces": {}, "objects": {}, "access_groups": {},
                                                    "inspect_icmp": False, "same_security": False})

    # -- trees -----------------------------------------------------------------------------------
    def build_trees(self) -> None:
        end = P.kw("end", "Exit from configure mode", handler=lambda s, a, n: s.set_mode("priv"))
        exit_cfg = P.kw("exit", "Exit from the current mode", handler=lambda s, a, n: s.exit_mode())
        show = P.kw("show", "Show running system information",
                    P.kw("running-config", "Show the current operating configuration",
                         handler=lambda s, a, n: s.show_run()),
                    P.kw("interface", "Show interface status", P.kw("ip", "IP", P.kw("brief", "Brief",
                         handler=lambda s, a, n: s.show_ip_brief()))),
                    P.kw("nameif", "Show interface names and security levels", handler=lambda s, a, n: s.show_nameif()),
                    P.kw("route", "Show the routing table", handler=lambda s, a, n: s.show_route()),
                    P.kw("conn", "Display all active connections", handler=lambda s, a, n: s.show_conn()),
                    P.kw("xlate", "Show current translation information", handler=lambda s, a, n: s.show_xlate()),
                    P.kw("access-list", "Show access lists", handler=lambda s, a, n: s.show_acl()),
                    P.kw("version", "Display the system software version", handler=lambda s, a, n: s.show_version()))
        ping = P.kw("ping", "Send echo messages", P.arg(P.WordArg("if_name|host"), "Interface name or host",
                    P.arg(P.IpArg(), "Destination", handler=lambda s, a, n: s.ping(a[1])),
                    handler=lambda s, a, n: s.ping(a[0])))
        user = [P.kw("enable", "Turn on privileged commands", handler=lambda s, a, n: s.set_mode("priv")),
                P.kw("exit", "Exit from the EXEC", handler=lambda s, a, n: None), ping, show]
        priv = [P.kw("configure", "Configure from the terminal", P.kw("terminal", "Configure using terminal",
                     handler=lambda s, a, n: s.set_mode("config"))),
                P.kw("disable", "Exit from privileged mode", handler=lambda s, a, n: s.set_mode("user")),
                P.kw("write", "Write config", P.kw("memory", "Save the configuration",
                     handler=lambda s, a, n: s.write_mem()), handler=lambda s, a, n: s.write_mem()),
                P.kw("copy", "Copy", P.kw("running-config", "running", P.kw("startup-config", "startup",
                     handler=lambda s, a, n: s.write_mem()))),
                P.kw("exit", "Exit", handler=lambda s, a, n: None), ping, show]
        config = [
            end, exit_cfg, show,
            P.kw("hostname", "Change host name of the system", P.arg(P.WordArg("WORD"), "Hostname",
                 handler=lambda s, a, n: s.set_hostname(a[0], n), negatable=True), negatable=True),
            P.kw("interface", "Select an interface to configure", P.arg(P.IfaceArg(), "Interface",
                 handler=lambda s, a, n: s.enter_if(a[0]))),
            P.kw("route", "Configure a static route for an interface",
                 P.arg(P.WordArg("if_name"), "Interface name", P.arg(P.IpArg(), "Destination network",
                       P.arg(P.MaskArg(), "Netmask", P.arg(P.IpArg(), "Gateway",
                             P.arg(P.NumArg(1, 255, "<1-255>"), "Distance",
                                   handler=lambda s, a, n: s.route(a[0], a[1], a[2], a[3], a[4], n), negatable=True),
                             handler=lambda s, a, n: s.route(a[0], a[1], a[2], a[3], 1, n), negatable=True)))),
                 negatable=True),
            P.kw("object", "Configure an object", P.kw("network", "Configure a network object",
                 P.arg(P.WordArg("WORD"), "Object name", handler=lambda s, a, n: s.obj(a[0], n), negatable=True),
                 negatable=True), negatable=True),
            P.kw("access-list", "Configure an access control element",
                 P.arg(P.WordArg("WORD"), "Access list name",
                       P.kw("extended", "Extended access list",
                            P.arg(P.LineArg("permit|deny protocol source destination [eq port]"), "Entry",
                                  handler=lambda s, a, n: s.acl(a[0], a[1], n), negatable=True))), negatable=True),
            P.kw("access-group", "Configure an access group",
                 P.arg(P.WordArg("WORD"), "Access list", P.kw("in", "Filter incoming traffic",
                       P.kw("interface", "Interface", P.arg(P.WordArg("if_name"), "Interface name",
                            handler=lambda s, a, n: s.access_group(a[0], a[1], n), negatable=True)))),
                 negatable=True),
            P.kw("same-security-traffic", "Enable same security level interfaces to communicate",
                 P.kw("permit", "Permit communication", P.kw("inter-interface", "Between interfaces",
                      handler=lambda s, a, n: s.flag("same_security", not n), negatable=True)), negatable=True),
            P.kw("fixup", "Legacy inspection", P.kw("protocol", "Protocol", P.kw("icmp", "ICMP",
                 handler=lambda s, a, n: s.flag("inspect_icmp", not n), negatable=True)), negatable=True),
            P.kw("policy-map", "Configure MPF policy map", P.arg(P.WordArg("WORD"), "Policy map name",
                 handler=lambda s, a, n: s.set_mode("config-pmap"))),
            P.kw("service-policy", "Configure MPF service policy", P.arg(P.LineArg("global_policy global"), "",
                 handler=lambda s, a, n: None)),
            P.kw("write", "Write config", P.kw("memory", "Save", handler=lambda s, a, n: s.write_mem())),
            ping]
        cif = [end, exit_cfg, show,
               P.kw("nameif", "Assign name to interface", P.arg(P.WordArg("WORD"), "Interface name",
                    handler=lambda s, a, n: s.nameif(a[0], n), negatable=True),
                    handler=lambda s, a, n: s.nameif(None, True) if n else s.err_incomplete("", 0), negatable=True),
               P.kw("security-level", "Specify security level of this interface",
                    P.arg(P.NumArg(0, 100, "<0-100>"), "Security level", handler=lambda s, a, n: s.level(a[0], n),
                          negatable=True), negatable=True),
               P.kw("ip", "Configure IP parameters", P.kw("address", "Configure the IP address",
                    P.arg(P.IpArg(), "IP address", P.arg(P.MaskArg(), "Network mask",
                          handler=lambda s, a, n: s.ip_address(a[0], a[1], n), negatable=True)),
                    handler=lambda s, a, n: s.ip_address(None, None, True) if n else s.err_incomplete("", 0),
                    negatable=True), negatable=True),
               P.kw("shutdown", "Shutdown the selected interface", handler=lambda s, a, n: s.shutdown(not n),
                    negatable=True),
               P.kw("description", "Interface specific description", P.arg(P.LineArg(), "Description",
                    handler=lambda s, a, n: s.describe(a[0])))]
        obj = [end, exit_cfg,
               P.kw("subnet", "Enter an IPv4 network", P.arg(P.IpArg(), "Network", P.arg(P.MaskArg(), "Mask",
                    handler=lambda s, a, n: s.obj_subnet(a[0], a[1])))),
               P.kw("host", "Enter a host IPv4 address", P.arg(P.IpArg(), "Address",
                    handler=lambda s, a, n: s.obj_subnet(a[0], "255.255.255.255"))),
               P.kw("nat", "Enable NAT on a singleton object", P.arg(P.WordArg("(real_ifc,mapped_ifc)"), "Interfaces",
                    P.kw("dynamic", "Dynamic NAT/PAT", P.kw("interface", "Use the interface address",
                         handler=lambda s, a, n: s.obj_nat(a[0], n), negatable=True))), negatable=True)]
        pmap = [end, exit_cfg, P.kw("class", "Policy criteria", P.arg(P.WordArg("WORD"), "Class map name",
                                                                       handler=lambda s, a, n: s.set_mode("config-pmap-c")))]
        pmap_c = [end, P.kw("exit", "Exit", handler=lambda s, a, n: s.set_mode("config-pmap")),
                  P.kw("inspect", "Protocol inspection", P.arg(P.WordArg("icmp|dns|ftp|http|..."), "Protocol",
                       handler=lambda s, a, n: s.flag("inspect_icmp", not n) if a[0].lower() == "icmp" else None,
                       negatable=True), negatable=True)]
        srt = lambda lst: sorted(lst, key=lambda x: x.word or "")
        self._trees = {"user": srt(user), "priv": srt(priv), "config": srt(config), "config-if": srt(cif),
                       "config-network-object": srt(obj), "config-pmap": srt(pmap), "config-pmap-c": srt(pmap_c)}

    # -- handlers ----------------------------------------------------------------------------------
    def exit_mode(self):
        self.set_mode({"config": "priv", "config-pmap-c": "config-pmap"}.get(self.mode, "config"))
        return None

    def set_hostname(self, name, negate):
        self.device.hostname = "ciscoasa" if negate else name
        self.device.engine.emit("device_changed", self.device)
        return None

    def enter_if(self, port):
        self.iface, self.mode = port, "config-if"
        return None

    def _ifs(self):
        return self.asa["ifaces"]

    def nameif(self, name, negate):
        cfg = self._ifs().setdefault(self.iface.name, {"nameif": None, "level": 0})
        if negate:
            cfg["nameif"] = None
            return None
        cfg["nameif"] = name
        if name == "inside":
            cfg["level"] = 100
            out = "INFO: Security level for \"inside\" set to 100 by default."
        else:
            cfg["level"] = cfg.get("level", 0) if name != "outside" else 0
            out = f"INFO: Security level for \"{name}\" set to 0 by default." if name == "outside" else None
        self._sync()
        return out

    def level(self, lvl, negate):
        self._ifs().setdefault(self.iface.name, {"nameif": None, "level": 0})["level"] = 0 if negate else int(lvl)
        self._sync()
        return None

    def ip_address(self, ip, mask, negate):
        p = self.iface
        p.ip = None if negate else make_interface(ip, mask)
        self.device.link_state_changed(p)
        self.device.routes_changed()
        return None

    def shutdown(self, down):
        self.iface.set_admin(not down)
        return None

    def describe(self, text):
        self.iface.description = text
        return None

    def _port_by_nameif(self, name):
        for pname, c in self._ifs().items():
            if c.get("nameif") == name:
                return self.device.ports.get(pname)
        return None

    def route(self, ifn, net, mask, gw, dist, negate):
        p = self._port_by_nameif(ifn)
        if p is None:
            return f"ERROR: Interface name \"{ifn}\" not found"
        network = IPv4Network(f"{net}/{mask_to_prefix(str(mask))}", strict=False)
        if negate:
            self.device.remove_static_route(network, IPv4Address(gw))
        else:
            self.device.add_static_route(network, IPv4Address(gw), None, int(dist))
        self.device.routes_changed()
        return None

    def obj(self, name, negate):
        objs = self.asa["objects"]
        if negate:
            objs.pop(name, None)
            self._sync()
            return None
        objs.setdefault(name, {"subnet": None, "nat": None})
        self._obj, self.mode = name, "config-network-object"
        return None

    def obj_subnet(self, ip, mask):
        self.asa["objects"][self._obj]["subnet"] = str(IPv4Network(f"{ip}/{mask_to_prefix(str(mask))}", strict=False))
        self._sync()
        return None

    def obj_nat(self, pair, negate):
        o = self.asa["objects"][self._obj]
        if negate:
            o["nat"] = None
        else:
            inner = pair.strip("()").split(",")
            if len(inner) != 2:
                return self.err_invalid(self.prompt, 0)
            o["nat"] = [inner[0].strip(), inner[1].strip()]
        self._sync()
        return None

    def acl(self, name, text, negate):
        from ..security.acl import Acl, AclRule
        w = text.split()
        try:
            action, proto = w[0], w[1]
            if action not in ("permit", "deny"):
                raise ValueError
            rest = w[2:]

            def addr(i):
                if rest[i] == "any":
                    return IPv4Network("0.0.0.0/0"), i + 1
                if rest[i] == "host":
                    return IPv4Network(rest[i + 1] + "/32"), i + 2
                return IPv4Network(f"{rest[i]}/{mask_to_prefix(rest[i + 1])}", strict=False), i + 2
            src, i = addr(0)
            dst, i = addr(i)
            dport = ("eq", int(rest[i + 1]) if rest[i + 1].isdigit() else {"www": 80, "http": 80, "https": 443,
                                                                             "ssh": 22, "telnet": 23, "domain": 53}[rest[i + 1]]) \
                if len(rest) > i + 1 and rest[i] == "eq" else None
        except (ValueError, IndexError, KeyError):
            return self.err_invalid(self.prompt, 0)
        acls = self.device.acls
        if negate:
            acls.pop(name, None)
            return None
        a = acls.setdefault(name, Acl(name, "extended"))
        a.add(AclRule(action, proto if proto != "ip" else "ip", src, dst, None, dport, text=text))
        return None

    def access_group(self, acl, ifn, negate):
        groups = self.asa["access_groups"]
        if negate:
            groups.pop(ifn, None)
        else:
            if acl not in self.device.acls:
                return f"ERROR: access-list <{acl}> does not exist"
            groups[ifn] = acl
        return None

    def flag(self, key, on):
        self.asa[key] = on
        return None

    def _sync(self):
        """Object NAT -> the shared NAT engine."""
        from ..security.nat import NatConfig, NatRule
        rules, inside, outside = [], set(), set()
        for name, o in self.asa["objects"].items():
            if not (o.get("subnet") and o.get("nat")):
                continue
            real, mapped = (self._port_by_nameif(x) for x in o["nat"])
            if real is None or mapped is None:
                continue
            inside.add(real.name)
            outside.add(mapped.name)
            rules.append(NatRule("dynamic", prefix=o["subnet"], interface=mapped.name, overload=True,
                                 text=f"object {name}"))
        if rules:
            self.device.nat_engine().cfg = NatConfig(sorted(inside), sorted(outside), rules, {})
        elif self.device.nat is not None:
            self.device.nat.cfg = NatConfig()
        return None

    def write_mem(self):
        self.device.save_startup()
        return "Building configuration...\nCryptochecksum: 1d2c3b4a 5e6f7a8b 9c0d1e2f 3a4b5c6d\n[OK]"

    def ping(self, ip):
        from ..stack.icmp import PingListener

        out = self

        class L(PingListener):
            def on_start(self, s):
                out.write(f"Type escape sequence to abort.\nSending 5, 100-byte ICMP Echos to {ip}, timeout is 2 seconds:\n")

            def on_reply(self, *a):
                out.write("!")

            def on_error(self, *a):
                out.write("?")

            def on_send_fail(self, *a):
                out.write(".")

            def on_timeout(self, *a):
                out.write(".")

            def on_finish(self, st):
                pct = round(100 * st.received / max(1, st.sent))
                out.write(f"\nSuccess rate is {pct} percent ({st.received}/{st.sent})\n")
        try:
            return make_ping(self.device, ip, L(), count=5, size=100).start()
        except ValueError:
            return self.err_invalid(self.prompt, 0)

    # -- show ------------------------------------------------------------------------------------
    def show_ip_brief(self):
        lines = ["Interface                  IP-Address      OK? Method Status                Protocol"]
        for p in self.device.ports.values():
            st = "up" if p.is_up else ("administratively down" if not p.admin_up else "down")
            lines.append(f"{p.name:<27}{str(p.ip.ip) if p.ip else 'unassigned':<16}YES {'CONFIG' if p.ip else 'unset ':<7}"
                         f"{st:<22}{'up' if p.is_up else 'down'}")
        return "\n".join(lines)

    def show_nameif(self):
        lines = ["Interface                Name                     Security"]
        for pname, c in self._ifs().items():
            if c.get("nameif"):
                lines.append(f"{pname:<25}{c['nameif']:<25}{c.get('level', 0)}")
        return "\n".join(lines)

    def show_route(self):
        names = {pname: c.get("nameif") for pname, c in self._ifs().items()}
        lines = ["Codes: L - local, C - connected, S - static, * - candidate default", ""]
        for r in self.device.routing_table():
            ifn = names.get(r.port.name) or r.port.name if r.port else ""
            star = "*" if r.network.prefixlen == 0 else " "
            if r.code in ("C", "L"):
                lines.append(f"{r.code}{star}       {r.network.network_address} {r.network.netmask} is directly connected, {ifn}")
            else:
                lines.append(f"{r.code[:1]}{star}       {r.network.network_address} {r.network.netmask} "
                             f"[{r.distance}/{r.metric}] via {r.next_hop}, {ifn}")
        return "\n".join(lines)

    def show_conn(self):
        flows = self.device.conntrack.active()
        lines = [f"{len(flows)} in use, {len(flows)} most used"]
        for f in flows:
            proto, src, dst, sp, dp = f.key
            name = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, str(proto))
            lines.append(f"{name} outside {dst}:{dp} inside {src}:{sp}, idle 0:00:01, bytes {f.packets * 64}, flags "
                         f"{'U' if proto != 6 else 'UIO'}")
        return "\n".join(lines)

    def show_xlate(self):
        nat = self.device.nat
        entries = list(getattr(nat, "table", {}).values()) if nat is not None else []
        lines = [f"{len(entries)} in use, {len(entries)} most used",
                 "Flags: D - DNS, e - extended, I - identity, i - dynamic, r - portmap,", ""]
        for e in entries:
            lines.append(f"{getattr(e, 'proto', 'ICMP')} PAT from inside:{getattr(e, 'local', e)} to outside:"
                         f"{getattr(e, 'glob', '')} flags ri idle 0:00:05 timeout 0:00:30")
        return "\n".join(lines)

    def show_acl(self):
        out = []
        groups = self.asa["access_groups"]
        for name, a in self.device.acls.items():
            out.append(f"access-list {name}; {len(a.rules)} elements")
            for i, r in enumerate(a.rules, 1):
                out.append(f"access-list {name} line {i} extended {r.text} (hitcnt={getattr(r, 'hits', 0)})")
        for ifn, acl in groups.items():
            out.append(f"access-group {acl} in interface {ifn}")
        return "\n".join(out)

    def show_version(self):
        return ("Cisco Adaptive Security Appliance Software Version 9.16(4)\n\nHardware:   ASA5506, 4096 MB RAM\n"
                f"{self.device.hostname} up 0 mins")

    def show_run(self):
        d = self.device
        a = self.asa
        lines = [": Saved", ":", "ASA Version 9.16(4)", "!", f"hostname {d.hostname}", "!"]
        for p in d.ports.values():
            c = a["ifaces"].get(p.name, {})
            lines.append(f"interface {p.name}")
            if p.description:
                lines.append(f" description {p.description}")
            if not p.admin_up:
                lines.append(" shutdown")
            if c.get("nameif"):
                lines.append(f" nameif {c['nameif']}")
                lines.append(f" security-level {c.get('level', 0)}")
            else:
                lines.append(" no nameif")
                lines.append(" no security-level")
            lines.append(f" ip address {p.ip.ip} {p.ip.netmask}" if p.ip else " no ip address")
            lines.append("!")
        if a.get("same_security"):
            lines.append("same-security-traffic permit inter-interface")
        for name, o in a["objects"].items():
            lines.append(f"object network {name}")
            if o.get("subnet"):
                n = IPv4Network(o["subnet"])
                lines.append(f" subnet {n.network_address} {n.netmask}")
        for name, acl in d.acls.items():
            for r in acl.rules:
                lines.append(f"access-list {name} extended {r.text}")
        for name, o in a["objects"].items():
            if o.get("nat"):
                lines += [f"object network {name}", f" nat ({o['nat'][0]},{o['nat'][1]}) dynamic interface"]
        for ifn, acl in a["access_groups"].items():
            lines.append(f"access-group {acl} in interface {ifn}")
        names = {pn: c.get("nameif") for pn, c in a["ifaces"].items()}
        for s in d.static_routes:
            port = d.route_lookup(s.next_hop)
            ifn = names.get(port[0].name) if port else "?"
            lines.append(f"route {ifn} {s.network.network_address} {s.network.netmask} {s.next_hop} {s.distance}")
        if a.get("inspect_icmp"):
            lines += ["policy-map global_policy", " class inspection_default", "  inspect icmp"]
        lines += ["service-policy global_policy global", ": end"]
        return "\n".join(lines)
