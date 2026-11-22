"""Juniper Junos dialect (SRX routers, EX switches).

Configuration mode edits a *candidate* configuration which only takes effect
after ``commit`` -- just like the real thing.
"""
from __future__ import annotations

import copy

from ..addresses import IPv4Address, IPv4Interface, IPv4Network
from ..stack.icmp import PingListener, TraceListener
from . import parser as P
from .base import TreeSession, make_ping, make_trace
from .render import uptime_text

JUNOS_STATIC_PREF = 5


def _mac(m) -> str:
    h = f"{m.value:012x}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


class JunosPing(PingListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.dst = s.dst
        self.w(f"PING {s.dst} ({s.dst}): {s.size} data bytes\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w(f"{size + 8} bytes from {src}: icmp_seq={seq} ttl={ttl} time={rtt:.3f} ms\n")

    def on_timeout(self, seq):
        self.w(f"Request timeout for icmp_seq {seq}\n")

    def on_send_fail(self, seq):
        self.w("ping: sendto: No route to host\n")

    def on_error(self, seq, kind, src):
        text = "Destination Host Unreachable" if kind == "unreachable" else "Time to live exceeded"
        self.w(f"36 bytes from {src}: {text}\n")

    def on_finish(self, st):
        self.w(f"\n--- {self.dst} ping statistics ---\n"
               f"{st.sent} packets transmitted, {st.received} packets received, "
               f"{st.loss_percent}% packet loss\n")
        if st.received:
            mn, mx = min(st.rtts), max(st.rtts)
            avg = sum(st.rtts) / len(st.rtts)
            dev = (sum((r - avg) ** 2 for r in st.rtts) / len(st.rtts)) ** 0.5
            self.w(f"round-trip min/avg/max/stddev = {mn:.3f}/{avg:.3f}/{mx:.3f}/{dev:.3f} ms\n")


class JunosTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f"traceroute to {s.dst} ({s.dst}), {s.max_hops} hops max, 40 byte packets\n")

    def on_hop(self, hop):
        head = f"{hop.ttl:>2}  "
        if hop.address:
            head += f"{hop.address} ({hop.address})  "
        parts = ["*" if r is None else f"{r:.3f} ms" + (" !H" if hop.unreachable else "")
                 for r in hop.probes]
        self.w(head + " ".join(parts) + "\n")


class JunosSession(TreeSession):
    @property
    def is_router(self) -> bool:
        return self.device.category == "router"

    def __init__(self, device):
        self.candidate: dict | None = None
        super().__init__(device)
        self.mode = "oper"

    @property
    def prompt(self) -> str:
        h = self.device.hostname
        if self.mode == "config":
            return f"\n[edit]\nroot@{h}# "
        return f"root@{h}> "

    def banner(self) -> str:
        return "--- JUNOS 21.4R3.15 Kernel 64-bit  JNPR-12.1-20220124.e1f8ac5_buil\n"

    def _col(self, prompt: str) -> int:
        return len(prompt.split("\n")[-1])

    def err_invalid(self, prompt, pos):
        return " " * (self._col(prompt) + pos) + "^\nsyntax error.\n"

    def err_ambiguous(self, prompt, pos, token):
        return " " * (self._col(prompt) + pos) + f"^\n'{token}' is ambiguous.\n"

    def err_incomplete(self, prompt, pos):
        return " " * (self._col(prompt) + pos) + "^\nsyntax error, expecting <command>.\n"

    def ctrl_z(self) -> str:
        return ""

    # -- trees ----------------------------------------------------------------------
    def build_trees(self) -> None:
        self._build_trees_base()
        t = self._trees
        t["oper"] = t["oper"] + [
            P.kw("ssh", "Start secure shell on another host",
                 P.arg(P.IpArg("<host>"), "Hostname or address of remote host",
                       P.kw("user", "Username to use on the remote host",
                            P.arg(P.WordArg("<user>"), "", handler=lambda s, a, n: s.remote("ssh", a[0], a[1]))),
                       handler=lambda s, a, n: s.remote("ssh", a[0], "root"))),
            P.kw("telnet", "Telnet to another host",
                 P.arg(P.IpArg("<host>"), "Hostname or address of remote host",
                       handler=lambda s, a, n: s.remote("telnet", a[0], ""))),
        ]
        set_root = next(x for x in t["config"] if x.word == "set")
        if self.is_router:
            unit_arg = next(x for x in next(y for y in set_root.children if y.word == "interfaces").children[0].children
                            if x.word == "unit").children[0]
            unit_arg.children.append(P.kw("tunnel", "Tunnel parameters",
                P.arg(P.LineArg("source A.B.C.D destination A.B.C.D"), "",
                      handler=lambda s, a, n: s.cfg_gre(a[0], a[2]))))
            system = next(x for x in set_root.children if x.word == "system")
            services = next(x for x in system.children if x.word == "services")
            dls = next((x for x in services.children if x.word == "dhcp-local-server"), None)
            if dls is not None:
                dls.children.append(P.kw("dhcpv6", "DHCPv6 local server",
                                         P.kw("group", "Group", P.arg(P.WordArg("<group-name>"), "Group name",
                                              P.kw("interface", "Interface", P.arg(P.WordArg("<interface-name>"), "",
                                                   handler=lambda s, a, n: s.cfg_dhcp6_if(a[1])))))))
            acc = next((x for x in set_root.children if x.word == "access"), None)
            if acc is not None:
                pool_arg = acc.children[0].children[0].children[0]
                fam = next(x for x in pool_arg.children if x.word == "family")
                fam.children.append(P.kw("inet6", "IPv6", P.arg(P.LineArg("prefix X:X::/64 | dhcp-attributes dns-server X"),
                                                               "", handler=lambda s, a, n: s.cfg_dhcp6_pool(a[0], a[1]))))
        else:
            set_root.children.append(P.kw("switch-options", "Options for default routing-instance of type virtual-switch",
                P.kw("interface", "Interface options", P.arg(P.WordArg("<interface-name>"), "Interface",
                     P.kw("interface-mac-limit", "Maximum number of MAC addresses learned on the interface",
                          P.arg(P.NumArg(1, 65535, "<limit>"), "Limit",
                                P.kw("packet-action", "Action when the limit is reached",
                                     *[P.kw(w, w, handler=lambda s, a, n, w=w: s.cfg_mac_limit(a[0], a[1], w))
                                       for w in ("drop", "drop-and-log", "shutdown", "log")]),
                                handler=lambda s, a, n: s.cfg_mac_limit(a[0], a[1], "drop")))))))
            vl = next(x for x in set_root.children if x.word == "vlans")
            vl.children[0].children.append(P.kw("forwarding-options", "Forwarding options",
                P.kw("dhcp-security", "DHCP snooping",
                     P.arg(P.LineArg("group G overrides trusted interface ge-0/0/23"), "",
                           handler=lambda s, a, n: s.cfg_dhcp_security(a[0], a[1])),
                     handler=lambda s, a, n: s.cfg_dhcp_security(a[0], ""))))
        for verb in ("set", "delete"):
            root = next(x for x in t["config"] if x.word == verb)
            protos = next((x for x in root.children if x.word == "protocols"), None)
            if protos is None:
                protos = P.kw("protocols", "Routing protocol configuration")
                root.children.append(protos)
            dl = verb == "delete"
            if self.is_router:
                protos.children.append(P.kw("isis", "IS-IS configuration",
                    *([] if dl else [P.arg(P.LineArg("interface <name> [passive] | interface <name> level 2 metric N | "
                                                     "level 1 disable"), "IS-IS statement",
                                           handler=lambda s, a, n: s.cfg_isis(a[0], False))]),
                    handler=(lambda s, a, n: s.cfg_isis("", True)) if dl else None))
                unit_arg = next(x for x in next(y for y in root.children if y.word == "interfaces").children[0].children
                                if x.word == "unit").children[0]
                fam = next((x for x in unit_arg.children if x.word == "family"), None)
                if fam is not None and not any(c.word == "iso" for c in fam.children):
                    fam.children.append(P.kw("iso", "OSI ISO protocol parameters",
                        *([] if dl else [P.kw("address", "Interface address",
                                              P.arg(P.WordArg("<address>"), "NET, e.g. 49.0001.0000.0000.0001.00",
                                                    handler=lambda s, a, n: s.cfg_iso(a[0], a[-1])))]),
                        handler=(lambda s, a, n: s.cfg_iso(a[0], None)) if dl else (lambda s, a, n: None)))
            protos.children += [
                P.kw("ospf3", "OSPFv3 configuration",
                     *([] if dl else [P.kw("area", "Configure an OSPF area",
                                           P.arg(P.WordArg("<area_id>"), "Area ID",
                                                 P.kw("interface", "Include an interface in this area",
                                                      P.arg(P.WordArg("<interface_name>"), "Interface name",
                                                            handler=lambda s, a, n: s.cfg_v6igp("ospf6", a[1], a[0])))))]),
                     handler=(lambda s, a, n: s.cfg_v6igp_delete("ospf6")) if dl else None),
                P.kw("ripng", "RIPng configuration",
                     *([] if dl else [P.kw("group", "RIPng group",
                                           P.arg(P.WordArg("<group_name>"), "Group name",
                                                 P.kw("neighbor", "Neighbor interface",
                                                      P.arg(P.WordArg("<interface_name>"), "Interface name",
                                                            handler=lambda s, a, n: s.cfg_v6igp("ripng", a[1], None)))))]),
                     handler=(lambda s, a, n: s.cfg_v6igp_delete("ripng")) if dl else None)]
        oshow = next(x for x in t["oper"] if x.word == "show")
        oshow.children += [
            P.kw("ospf3", "Show OSPFv3 information",
                 P.kw("neighbor", "Show neighbor status", handler=lambda s, a, n: s.show_v6igp("ospf6")),
                 P.kw("database", "Show the OSPFv3 link-state database", handler=lambda s, a, n: __import__(
                     "netemu.core.cli.base", fromlist=["x"]).ospf6_database_text(s.device))),
            P.kw("ripng", "Show RIPng information",
                 P.kw("neighbor", "Show neighbor status", handler=lambda s, a, n: s.show_v6igp("ripng")))]
        ifs = next(x for x in set_root.children if x.word == "interfaces")
        node = ifs.children[0]                                    # <interface>
        unit = next(x for x in node.children if x.word == "unit")
        fam = next(x for x in unit.children[0].children if x.word == "family")
        inet = next(x for x in fam.children if x.word == "inet")
        addr = next(x for x in inet.children if x.word == "address")
        addr.children[0].children.append(
            P.kw("vrrp-group", "VRRP group",
                 P.arg(P.NumArg(0, 255, "<group_id>"), "VRRP group identifier",
                       P.arg(P.LineArg("virtual-address A | priority N | preempt | no-preempt"), "",
                             handler=lambda s, a, n: s.cfg_address(a[0], a[2], False, unit=a[1]) or s.cfg_vrrp(a[0], a[3], a[4])))))
        show = next(x for x in t["oper"] if x.word == "show")
        if self.is_router:
            sec = next((x for x in show.children if x.word == "security"), None)
            nodes = [P.kw("zones", "Show security zone information", handler=lambda s, a, n: s.show_security("zones")),
                     P.kw("policies", "Show security policies", handler=lambda s, a, n: s.show_security("policies")),
                     P.kw("flow", "Show flow information", P.kw("session", "Show session information",
                          handler=lambda s, a, n: s.show_security("flow"))),
                     P.kw("idp", "Show IDP information",
                          P.kw("status", "Show IDP status", handler=lambda s, a, n: s.show_security("idp")),
                          P.kw("counters", "Show IDP counters", P.kw("application-identification",
                               "", handler=lambda s, a, n: s.show_security("idp")),
                               handler=lambda s, a, n: s.show_security("idp")),
                          handler=lambda s, a, n: s.show_security("idp"))]
            if sec is None:
                show.children.append(P.kw("security", "Show security information", *nodes))
            else:
                words = {x.word for x in nodes}
                sec.children[:] = [c for c in sec.children if c.word not in words] + nodes
            show.children.append(P.kw("isis", "Show IS-IS information",
                P.kw("adjacency", "Show IS-IS adjacencies", handler=lambda s, a, n: s.show_isis("adjacency")),
                P.kw("database", "Show IS-IS link-state database", handler=lambda s, a, n: s.show_isis("database"))))
        from .base import mgmt_ntp_assoc, mgmt_ntp_status
        show.children += [
            P.kw("ntp", "Show Network Time Protocol information",
                 P.kw("associations", "Show NTP peers", handler=lambda s, a, n: mgmt_ntp_assoc(s.device)),
                 P.kw("status", "Show NTP status", handler=lambda s, a, n: mgmt_ntp_status(s.device))),
            P.kw("log", "Show contents of log file",
                 P.kw("messages", "System messages", handler=lambda s, a, n: "\n".join(
                     x.lstrip("*") for x in s.device.mgmt.buffer)))]
        show.children.append(P.kw("vrrp", "Show VRRP information",
                                  P.kw("summary", "Summary", handler=lambda s, a, n: s.show_vrrp()),
                                  handler=lambda s, a, n: s.show_vrrp()))
        for verb in ("set", "delete"):
            root = next(x for x in t["config"] if x.word == verb)
            system = next(x for x in root.children if x.word == "system")
            delete = verb == "delete"
            jm = lambda what, d=delete: (lambda s, a, n: s.jmg(what, a[0] if a else "", d))
            system.children += [
                P.kw("syslog", "System logging facility",
                     P.kw("host", "Host to be notified", P.arg(P.IpArg("<hostname>"), "",
                          P.arg(P.LineArg("any any"), "Facility and level", handler=jm("syslog")),
                          handler=jm("syslog")))),
                P.kw("ntp", "Network Time Protocol services",
                     P.kw("server", "NTP server", P.arg(P.IpArg("<address>"), "", handler=jm("ntp"))))]
            if not self.is_router:
                so = next((x for x in root.children if x.word == "switch-options"), None)
                voip = P.kw("voip", "Voice over IP", P.kw("interface", "Interface", P.arg(P.WordArg("<interface-name>"), "",
                    P.kw("vlan", "Voice VLAN", P.arg(P.WordArg("<vlan-name>"), "",
                         handler=lambda s, a, n, d=delete: s.cfg_voip(a[0], a[1], d))))))
                if so is None:
                    root.children.append(P.kw("switch-options", "Options for default routing-instance of type virtual-switch", voip))
                else:
                    so.children.append(voip)
            root.children.append(P.kw("snmp", "Simple Network Management Protocol configuration",
                P.kw("community", "Configure a community string", P.arg(P.WordArg("<community-name>"), "",
                     P.kw("authorization", "Authorization type",
                          P.kw("read-only", "Read-only access", handler=lambda s, a, n, d=delete: s.jmg("ro", a[0], d)),
                          P.kw("read-write", "Read-write access", handler=lambda s, a, n, d=delete: s.jmg("rw", a[0], d))),
                     handler=lambda s, a, n, d=delete: s.jmg("ro", a[0], d))),
                P.kw("location", "Physical location of system", P.arg(P.LineArg("<location>"), "", handler=jm("location")),
                     handler=jm("location")),
                P.kw("contact", "Contact information for administrator",
                     P.arg(P.LineArg("<contact>"), "", handler=jm("contact")), handler=jm("contact"))))
            services = next((x for x in system.children if x.word == "services"), None)
            svc_nodes = [P.kw(w, f"Allow {w} login", handler=lambda s, a, n, w=w, d=delete: s.cfg_service(w, d))
                         for w in ("ssh", "telnet")]
            if services is None:
                system.children.append(P.kw("services", "System services", *svc_nodes))
            else:
                services.children += svc_nodes
            if delete:
                system.children += [
                    P.kw("login", "Names, login classes, and passwords for users",
                         P.kw("user", "Username", P.arg(P.WordArg("<name>"), "",
                                                        handler=lambda s, a, n: s.cfg_login_user(a[0], None, True))))]
            else:
                system.children += [
                    P.kw("login", "Names, login classes, and passwords for users",
                         P.kw("user", "Username",
                              P.arg(P.WordArg("<name>"), "",
                                    P.arg(P.LineArg("class super-user authentication plain-text-password"), "",
                                          handler=lambda s, a, n: s.cfg_login_user(a[0], a[1], False))))),
                    P.kw("root-authentication", "Authentication information for the root login",
                         P.kw("plain-text-password", "Prompt for plain text password",
                              handler=lambda s, a, n: s.cfg_login_user("root", "authentication plain-text-password", False)))]

    def _build_trees_base(self) -> None:
        iface = P.IfaceArg("<interface_name>")
        show_items = [
            P.kw("arp", "Show system Address Resolution Protocol table entries",
                 P.kw("no-resolve", "Don't attempt to print addresses symbolically",
                      handler=lambda s, a, n: s.show_arp()),
                 handler=lambda s, a, n: s.show_arp()),
            P.kw("configuration", "Show current configuration",
                 handler=lambda s, a, n: render_config(s.device, s.device.to_config())),
            P.kw("interfaces", "Show interface information",
                 P.kw("terse", "Display terse output", handler=lambda s, a, n: s.show_terse()),
                 P.arg(iface, "Name of physical or logical interface",
                       P.kw("terse", "Display terse output", handler=lambda s, a, n: s.show_terse(a[0])),
                       handler=lambda s, a, n: s.show_interface(a[0])),
                 handler=lambda s, a, n: s.show_interface(None)),
            P.kw("version", "Show software process revision levels",
                 handler=lambda s, a, n: s.show_version()),
            P.kw("system", "Show system information",
                 P.kw("uptime", "Show time since system and processes started",
                      handler=lambda s, a, n: f"System booted: {uptime_text(s.device.engine.now)} ago")),
        ]
        if True:
            show_items.append(P.kw("ipv6", "Show IP version 6 information",
                                   P.kw("neighbors", "Show IPv6 neighbor cache", handler=lambda s, a, n: s.show_v6_neighbors())))
            show_items.append(P.kw("route", "Show routing table information",
                                   P.kw("table", "Name of routing table",
                                        P.kw("inet6.0", "IPv6 unicast table", handler=lambda s, a, n: s.show_route6()),
                                        P.kw("inet.0", "IPv4 unicast table", handler=lambda s, a, n: s.show_route())),
                                   P.kw("protocol", "Name of protocol",
                                        P.arg(P.WordArg("ospf|static|direct"), "",
                                              handler=lambda s, a, n: s.show_route(a[0]))),
                                   handler=lambda s, a, n: s.show_route()))
            show_items.append(P.kw("firewall", "Show firewall information", handler=lambda s, a, n: s.show_firewall()))
            show_items.append(P.kw("rip", "Show RIP information",
                                   P.kw("neighbor", "Show RIP neighbor information",
                                        handler=lambda s, a, n: s.show_rip_neighbor())))
            show_items.append(P.kw("dhcp", "Show Dynamic Host Configuration Protocol information",
                                   P.kw("server", "Show DHCP server information",
                                        P.kw("binding", "Show DHCP server client binding information",
                                             handler=lambda s, a, n: s.show_dhcp_binding()))))
            show_items.append(P.kw("bgp", "Show BGP information",
                                   P.kw("summary", "Show overall BGP information", handler=lambda s, a, n: s.show_bgp_summary()),
                                   P.kw("neighbor", "Show BGP neighbors",
                                        P.arg(P.IpArg(), "IP address of BGP peer", handler=lambda s, a, n: s.show_bgp_neighbor(a[0])),
                                        handler=lambda s, a, n: s.show_bgp_neighbor(None))))
            show_items.append(P.kw("security", "Show security information",
                                   P.kw("nat", "Show Network Address Translation information",
                                        P.kw("source", "Show source NAT information",
                                             P.kw("summary", "Display source NAT summary",
                                                  handler=lambda s, a, n: s.show_snat_summary())),
                                        P.kw("static", "Show static NAT information",
                                             P.kw("rule", "Show static NAT rules",
                                                  P.kw("all", "All rules", handler=lambda s, a, n: s.show_static_nat()))),
                                        P.kw("destination", "Show destination NAT information",
                                             P.kw("summary", "Display destination NAT summary",
                                                  handler=lambda s, a, n: s.show_static_nat("dnat")))),
                                   P.kw("flow", "Show flow information",
                                        P.kw("session", "Show session information",
                                             handler=lambda s, a, n: s.show_flow_sessions()))))
            show_items.append(P.kw("ospf", "Show information about OSPF",
                                   P.kw("neighbor", "Show neighbor status", handler=lambda s, a, n: s.show_ospf_neighbor()),
                                   P.kw("interface", "Show interface status", handler=lambda s, a, n: s.show_ospf_interface()),
                                   P.kw("database", "Show the OSPF link-state database",
                                        handler=lambda s, a, n: s.show_ospf_database()),
                                   P.kw("route", "Show OSPF routing table", handler=lambda s, a, n: s.show_route("ospf"))))
        if not self.is_router:
            show_items = [x for x in show_items if x.word != "security"]
            show_items.append(P.kw("lacp", "Show LACP information",
                                   P.kw("interfaces", "Show LACP aggregated interface information",
                                        handler=lambda s, a, n: s.show_lacp())))
            show_items.append(P.kw("ethernet-switching", "Show Ethernet-switching information",
                                   P.kw("table", "Show media access control table",
                                        handler=lambda s, a, n: s.show_switching_table()),
                                   P.kw("interface", "Show Ethernet-switching interface information",
                                        handler=lambda s, a, n: s.show_esw_interface())))
            show_items.append(P.kw("spanning-tree", "Show spanning tree information",
                                   P.kw("bridge", "Show STP bridge information", handler=lambda s, a, n: s.show_stp_bridge()),
                                   P.kw("interface", "Show STP interface information",
                                        handler=lambda s, a, n: s.show_stp_interface())))
            show_items.append(P.kw("vlans", "Show VLAN information",
                                   handler=lambda s, a, n: s.show_vlans()))
        show_items.sort(key=lambda x: x.word)
        show = P.kw("show", "Show system information", *show_items)
        ping = P.kw("ping", "Ping remote target",
                    P.arg(P.AnyIpArg("<host>"), "Hostname or IP address of remote host",
                          P.kw("count", "Number of ping requests to send",
                               P.arg(P.NumArg(1, 2000000000, "<count>"), "",
                                     P.kw("rapid", "Send requests rapidly",
                                          handler=lambda s, a, n: s.ping(a[0], a[1])),
                                     handler=lambda s, a, n: s.ping(a[0], a[1]))),
                          P.kw("rapid", "Send requests rapidly", handler=lambda s, a, n: s.ping(a[0])),
                          handler=lambda s, a, n: s.ping(a[0])))
        trace = P.kw("traceroute", "Trace route to remote host",
                     P.arg(P.AnyIpArg("<host>"), "IP address of remote host",
                           handler=lambda s, a, n: s.traceroute(a[0])))
        oper = [
            P.kw("clear", "Clear information in the system",
                 P.kw("arp", "Clear address resolution information",
                      handler=lambda s, a, n: s.device.arp.clear()),
                 P.kw("ethernet-switching", "Clear Ethernet switching information",
                      P.kw("table", "Clear MAC table",
                           handler=lambda s, a, n: s.device.clear_mac_table() if not s.is_router else None))),
            P.kw("configure", "Manipulate software configuration information",
                 handler=lambda s, a, n: s.configure()),
            P.kw("exit", "Exit the management session", handler=lambda s, a, n: None),
            ping,
            P.kw("quit", "Exit the management session", handler=lambda s, a, n: None),
            P.kw("request", "Make system-level requests",
                 P.kw("system", "Perform system-level operations",
                      P.kw("zeroize", "Erase all data, including configuration and log files",
                           handler=lambda s, a, n: s.zeroize()))),
            show, trace,
        ]

        def cfg_paths(delete: bool) -> list[P.Node]:
            def family(vlan_path: bool):
                # args: [iface, unit] (+ [vlan-id] when reached through "vlan-id N")
                def addr(s, a, n):
                    vid = a[2] if vlan_path else None
                    prefix = a[3] if vlan_path else a[2]
                    return s.cfg_address(a[0], prefix, delete, unit=a[1], vlan=vid)

                def addr_del(s, a, n):
                    return s.cfg_address(a[0], None, True, unit=a[1])
                esw = []
                if not self.is_router:
                    esw = [P.kw("ethernet-switching", "Ethernet switching parameters",
                                P.kw("interface-mode", "Interface mode",
                                     P.kw("access", "Access mode", handler=lambda s, a, n: s.cfg_esw_mode(a[0], "access", delete)),
                                     P.kw("trunk", "Trunk mode", handler=lambda s, a, n: s.cfg_esw_mode(a[0], "trunk", delete)),
                                     handler=(lambda s, a, n: s.cfg_esw_mode(a[0], "access", True)) if delete else None),
                                P.kw("vlan", "Virtual LAN",
                                     P.kw("members", "VLAN membership",
                                          P.arg(P.LineArg("<vlan-name>"), "VLAN name or ID, or [ list ]",
                                                handler=lambda s, a, n: s.cfg_esw_members(a[0], a[-1], delete)),
                                          handler=(lambda s, a, n: s.cfg_esw_members(a[0], None, True)) if delete else None)),
                                handler=(lambda s, a, n: s.cfg_esw_reset(a[0])) if delete else lambda s, a, n: None)]
                return P.kw("family", "Protocol family",
                            P.kw("inet", "IPv4 parameters",
                                 P.kw("rpf-check", "Reverse-path-forwarding checks (anti-spoofing)",
                                      P.kw("mode", "Reverse-path-forwarding mode",
                                           P.kw("loose", "Reverse-path-forwarding loose mode",
                                                handler=lambda s, a, n: s.cfg_rpf(a[0], "loose", delete))),
                                      handler=lambda s, a, n: s.cfg_rpf(a[0], "strict", delete)),
                                 P.kw("address", "Interface address/destination prefix",
                                      P.arg(P.PrefixArg("<name>"), "Interface address/destination prefix",
                                            handler=addr),
                                      handler=addr_del if delete else None),
                                 *([P.kw("filter", "Filters to apply to this logical interface",
                                         *[P.kw(d, f"Name of filter applied to {'received' if d == 'input' else 'transmitted'} packets",
                                                P.arg(P.WordArg("<filter-name>"), "Filter name",
                                                      handler=lambda s, a, n, d=d: s.cfg_if_filter(a[0], d, a[-1], delete)),
                                                handler=(lambda s, a, n, d=d: s.cfg_if_filter(a[0], d, None, True)) if delete else None)
                                           for d in ("input", "output")])] if self.is_router else []),
                                 handler=addr_del if delete else None),
                            *([P.kw("inet6", "IPv6 parameters",
                                    P.kw("address", "Interface address/destination prefix",
                                         P.arg(P.Ipv6PrefixArg(), "Interface address/destination prefix",
                                               handler=lambda s, a, n: s.cfg_address6(a[0], a[-1], delete)),
                                         handler=(lambda s, a, n: s.cfg_address6(a[0], None, True)) if delete else None),
                                    handler=(lambda s, a, n: s.cfg_address6(a[0], None, True)) if delete else None)]
                              if self.is_router and not vlan_path else []),
                            *esw)
            unit_children = [family(False)]
            if self.is_router:
                unit_children.append(
                    P.kw("vlan-id", "Virtual LAN identifier value for 802.1q VLAN tags",
                         P.arg(P.NumArg(1, 4094, "<vlan-id>"), "VLAN ID",
                               family(True),
                               handler=lambda s, a, n: s.cfg_unit_vlan(a[0], a[1], a[2], delete)),
                         handler=(lambda s, a, n: s.cfg_unit_vlan(a[0], a[1], None, True)) if delete else None))
            unit = P.kw("unit", "Logical interface",
                        P.arg(P.NumArg(0, 16385, "<interface_unit_number>"), "Interface unit number",
                              *unit_children,
                              handler=(lambda s, a, n: s.cfg_unit_delete(a[0], a[1])) if delete else None))
            iface_node = P.arg(iface, "Interface name",
                               P.kw("description", "Text description of interface",
                                    *([] if delete else [P.arg(P.LineArg("<description>"), "",
                                                               handler=lambda s, a, n: s.cfg_desc(a[0], a[1]))]),
                                    handler=(lambda s, a, n: s.cfg_desc(a[0], "")) if delete else None),
                               P.kw("disable", "Disable this interface",
                                    handler=lambda s, a, n: s.cfg_disable(a[0], not delete)),
                               *([P.kw("vlan-tagging", "Enable 802.1q VLAN tagging",
                                       handler=lambda s, a, n: None)] if self.is_router else
                                 [P.kw("native-vlan-id", "Virtual LAN identifier for untagged frames",
                                       *([] if delete else [P.arg(P.NumArg(1, 4094, "<vlan-id>"), "VLAN ID",
                                                                  handler=lambda s, a, n: s.cfg_native(a[0], a[1]))]),
                                       handler=(lambda s, a, n: s.cfg_native(a[0], None)) if delete else None)]),
                               unit,
                               *([] if self.is_router else [
                                   P.kw("ether-options", "Ethernet interface options",
                                        P.kw("802.3ad", "Aggregated Ethernet bundle membership",
                                             *([] if delete else [P.arg(P.WordArg("<ae-interface>"), "aeN",
                                                                        handler=lambda s, a, n: s.cfg_ae_member(a[0], a[1]))]),
                                             handler=(lambda s, a, n: s.cfg_ae_member(a[0], None)) if delete else None)),
                                   P.kw("aggregated-ether-options", "Aggregated Ethernet options",
                                        P.kw("lacp", "LACP options",
                                             *([] if delete else [
                                                 P.kw("active", "Transmit LACP packets", handler=lambda s, a, n: s.cfg_ae_lacp(a[0], "active")),
                                                 P.kw("passive", "Respond to LACP packets", handler=lambda s, a, n: s.cfg_ae_lacp(a[0], "passive"))]),
                                             handler=(lambda s, a, n: s.cfg_ae_lacp(a[0], None)) if delete else None))]),
                               handler=(lambda s, a, n: s.cfg_iface_reset(a[0])) if delete else None)
            route_node = P.arg(P.PrefixArg("<destination>"), "Destination prefix",
                               P.kw("discard", "Drop packets to destination; send no ICMP unreachables",
                                    handler=lambda s, a, n: s.cfg_route_discard(a[0], delete)),
                               P.kw("next-hop", "Next hop to destination",
                                    P.arg(P.IpArg("<next-hop>"), "",
                                          handler=lambda s, a, n: s.cfg_route(a[0], a[1], delete))),
                               P.kw("qualified-next-hop", "Next hop with qualifiers",
                                    P.arg(P.IpArg("<next-hop>"), "",
                                          *([] if delete else [
                                              P.kw("preference", "Preference of this next hop",
                                                   P.arg(P.NumArg(0, 4294967295, "<preference>"), "",
                                                         handler=lambda s, a, n: s.cfg_route(
                                                             a[0], a[1], False, int(a[2]))))]),
                                          handler=lambda s, a, n: s.cfg_route(a[0], a[1], delete))),
                               handler=(lambda s, a, n: s.cfg_route(a[0], None, True)) if delete else None)
            irb = P.kw("irb", "Integrated routing and bridging interface",
                       P.kw("unit", "Logical interface",
                            P.arg(P.NumArg(0, 16385, "<interface_unit_number>"), "Interface unit number",
                                  P.kw("family", "Protocol family",
                                       P.kw("inet", "IPv4 parameters",
                                            P.kw("address", "Interface address/destination prefix",
                                                 *([] if delete else [P.arg(P.PrefixArg("<name>"), "Interface address",
                                                                            handler=lambda s, a, n: s.cfg_irb(a[0], a[1], False))]),
                                                 handler=(lambda s, a, n: s.cfg_irb(a[0], None, True)) if delete else None),
                                            handler=(lambda s, a, n: s.cfg_irb(a[0], None, True)) if delete else None)),
                                  handler=(lambda s, a, n: s.cfg_irb(a[0], None, True, remove=True)) if delete else None)),
                       handler=(lambda s, a, n: s.cfg_irb(None, None, True, remove=True)) if delete else None)
            paths = [
                *([] if self.is_router or delete else [
                    P.kw("chassis", "Chassis configuration",
                         P.kw("aggregated-devices", "Aggregated devices configuration",
                              P.kw("ethernet", "Aggregated Ethernet configuration",
                                   P.kw("device-count", "Number of aggregated Ethernet devices",
                                        P.arg(P.NumArg(1, 64, "<device-count>"), "",
                                              handler=lambda s, a, n: s.cfg_ae_count(a[0]))))))]),
                P.kw("interfaces", "Interface configuration", iface_node, *([] if self.is_router else [irb])),
                P.kw("system", "System parameters",
                     P.kw("host-name", "Hostname for this router",
                          *([] if delete else [P.arg(P.WordArg("<host-name>"), "",
                                                     handler=lambda s, a, n: s.cfg_hostname(a[0]))]),
                          handler=(lambda s, a, n: s.cfg_hostname(None)) if delete else None),
                     *([P.kw("services", "System services",
                             P.kw("dhcp-local-server", "Dynamic Host Configuration Protocol server",
                                  P.kw("group", "Group of interfaces",
                                       P.arg(P.WordArg("<group-name>"), "Group name",
                                             P.kw("interface", "Interface name",
                                                  P.arg(P.WordArg("<interface-name>"), "",
                                                        handler=lambda s, a, n: s.cfg_dhcp_group(a[0], a[1], delete))),
                                             handler=(lambda s, a, n: s.cfg_dhcp_group(a[0], None, True)) if delete else None)),
                                  handler=(lambda s, a, n: s.cfg_dhcp_group(None, None, True)) if delete else None))]
                       if self.is_router else [])),
            ]
            stp_nodes = []
            if not self.is_router:
                stp_nodes = [P.kw(proto, f"{proto.upper()} options",
                                  *([] if delete else [P.arg(P.LineArg("bridge-priority|interface ..."), "",
                                                             handler=lambda s, a, n, proto=proto: s.cfg_stp(proto, a[0]))]),
                                  handler=(lambda s, a, n, proto=proto: s.cfg_stp(proto, None, True)) if delete
                                  else (lambda s, a, n, proto=proto: s.cfg_stp(proto, "")))
                             for proto in ("rstp", "stp")]
                paths.append(P.kw("vlans", "VLAN configuration",
                                  P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                        P.kw("l3-interface", "Layer 3 interface for this VLAN",
                                             *([] if delete else [P.arg(P.WordArg("<interface_name>"), "irb.N",
                                                                        handler=lambda s, a, n: s.cfg_vlan_l3(a[0], a[1]))]),
                                             handler=(lambda s, a, n: s.cfg_vlan_l3(a[0], None)) if delete else None),
                                        *([] if delete else [
                                            P.kw("vlan-id", "802.1q VLAN tag identifier",
                                                 P.arg(P.NumArg(1, 4094, "<vlan-id>"), "VLAN ID",
                                                       handler=lambda s, a, n: s.cfg_vlan(a[0], a[1])))]),
                                        handler=(lambda s, a, n: s.cfg_vlan(a[0], None)) if delete else None)))
            if True:
                paths.append(P.kw("routing-options", "Protocol-independent routing option configuration",
                                  P.kw("rib", "Routing table options",
                                       P.kw("inet6.0", "IPv6 unicast routing table",
                                            P.kw("static", "Static routing information",
                                                 P.kw("route", "Static route",
                                                      P.arg(P.Ipv6PrefixArg(), "Destination prefix",
                                                            *([] if delete else [
                                                                P.kw("next-hop", "Next hop to destination",
                                                                     P.arg(P.Ipv6Arg(), "Next hop",
                                                                           handler=lambda s, a, n: s.cfg_route6(a[0], a[1], False)))]),
                                                            handler=(lambda s, a, n: s.cfg_route6(a[0], None, True)) if delete else None))))),
                                  P.kw("static", "Static routing information",
                                       P.kw("route", "Static route", route_node),
                                       handler=(lambda s, a, n: s.cfg_route(None, None, True)) if delete else None),
                                  P.kw("router-id", "Router identifier",
                                       *([] if delete else [P.arg(P.IpArg("<router-id>"), "Router identifier",
                                                                  handler=lambda s, a, n: s.cfg_router_id(a[0]))]),
                                       handler=(lambda s, a, n: s.cfg_router_id(None)) if delete else None),
                                  P.kw("autonomous-system", "Autonomous system number",
                                       *([] if delete else [P.arg(P.NumArg(1, 4294967295, "<as-number>"), "",
                                                                  handler=lambda s, a, n: s.cfg_asn(a[0]))]),
                                       handler=(lambda s, a, n: s.cfg_asn(None)) if delete else None)))
                num = lambda name, lo, hi: P.NumArg(lo, hi, name)
                if_opts = [] if delete else [
                    P.kw("passive", "Do not run OSPF, but advertise it",
                         handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], "passive", True)),
                    P.kw("metric", "Interface metric",
                         P.arg(num("<metric>", 1, 65535), "", handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], "cost", a[2]))),
                    P.kw("hello-interval", "Hello interval",
                         P.arg(num("<hello-interval>", 1, 255), "", handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], "hello", a[2]))),
                    P.kw("dead-interval", "Dead interval",
                         P.arg(num("<dead-interval>", 1, 65535), "", handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], "dead", a[2]))),
                    P.kw("priority", "Designated router priority",
                         P.arg(num("<priority>", 0, 255), "", handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], "priority", a[2]))),
                ]
                bgp_group = P.arg(P.WordArg("<group-name>"), "Group name",
                                  *([] if delete else [P.arg(P.LineArg("type|peer-as|neighbor|local-address|multihop|export ..."),
                                                             "Group statement",
                                                             handler=lambda s, a, n: s.cfg_bgp_group(a[0], a[1]))]),
                                  handler=(lambda s, a, n: s.cfg_bgp_group_delete(a[0])) if delete else None)
                bgp_node = P.kw("bgp", "BGP options", P.kw("group", "Define a peer group", bgp_group),
                                handler=(lambda s, a, n: s.cfg_bgp_delete()) if delete else None)
                rip_node = P.kw("rip", "RIP options",
                                P.kw("group", "Define a RIP group",
                                     P.arg(P.WordArg("<group-name>"), "Group name",
                                           *([] if delete else [P.arg(P.LineArg("neighbor|export ..."), "",
                                                                      handler=lambda s, a, n: s.cfg_rip_group(a[0], a[1]))]),
                                           handler=(lambda s, a, n: s.cfg_rip_delete(a[0])) if delete else None)),
                                handler=(lambda s, a, n: s.cfg_rip_delete(None)) if delete else None)
                paths.append(P.kw("protocols", "Routing protocol configuration",
                                  bgp_node, rip_node, *stp_nodes,
                                  P.kw("ospf", "OSPF configuration",
                                       P.kw("area", "Configure an OSPF area",
                                            P.arg(P.WordArg("<area_id>"), "Area ID",
                                                  P.kw("interface", "Include an interface in this area",
                                                       P.arg(P.WordArg("<interface_name>"), "Interface name",
                                                             *if_opts,
                                                             handler=lambda s, a, n: s.cfg_ospf_if(a[0], a[1], None, None, delete)),
                                                       handler=(lambda s, a, n: s.cfg_ospf_area_delete(a[0])) if delete else None),
                                                  handler=(lambda s, a, n: s.cfg_ospf_area_delete(a[0])) if delete else None)),
                                       P.kw("export", "Export policy",
                                            P.arg(P.WordArg("<policy-name>"), "Policy name",
                                                  handler=lambda s, a, n: s.cfg_ospf_export(a[0], delete)),
                                            handler=(lambda s, a, n: s.cfg_ospf_export(None, True)) if delete else None),
                                       handler=(lambda s, a, n: s.cfg_ospf_delete()) if delete else None)))
                paths.append(P.kw("access", "Network access configuration",
                                  P.kw("address-assignment", "Address assignment configuration",
                                       P.kw("pool", "Address pool",
                                            P.arg(P.WordArg("<pool-name>"), "Pool name",
                                                  P.kw("family", "Protocol family",
                                                       P.kw("inet", "IPv4",
                                                            *([] if delete else [P.arg(P.LineArg("network|range|dhcp-attributes ..."), "",
                                                                                       handler=lambda s, a, n: s.cfg_dhcp_pool(a[0], a[1]))]))),
                                                  handler=(lambda s, a, n: s.cfg_dhcp_pool_delete(a[0])) if delete else None)))))
                paths.append(P.kw("forwarding-options", "Configure options to control packet forwarding",
                                  P.kw("dhcp-relay", "DHCP relay agent configuration",
                                       *([] if delete else [P.arg(P.LineArg("server-group|active-server-group|group ..."), "",
                                                                  handler=lambda s, a, n: s.cfg_dhcp_relay(a[0]))]),
                                       handler=(lambda s, a, n: s.cfg_dhcp_relay(None)) if delete else None)))
                paths.append(P.kw("firewall", "Define a firewall configuration",
                                  P.kw("policer", "Policer template definition",
                                       P.arg(P.WordArg("<policer-name>"), "Policer name",
                                             *([] if delete else [P.arg(P.LineArg("if-exceeding bandwidth-limit 1m burst-size-limit 15k | then discard"),
                                                                        "", handler=lambda s, a, n: s.cfg_policer(a[0], a[1], False))]),
                                             handler=(lambda s, a, n: s.cfg_policer(a[0], "", True)) if delete else None)),
                                  P.kw("family", "Protocol family",
                                       P.kw("inet", "Protocol family IPv4 for firewall filter",
                                            P.kw("filter", "Define an IPv4 firewall filter",
                                                 P.arg(P.WordArg("<filter-name>"), "Filter name",
                                                       P.kw("term", "Define a firewall term",
                                                            P.arg(P.WordArg("<term-name>"), "Term name",
                                                                  *([] if delete else [P.arg(P.LineArg("from|then ..."), "Match conditions or actions",
                                                                                             handler=lambda s, a, n: s.cfg_fw_term(a[0], a[1], a[2]))]),
                                                                  handler=(lambda s, a, n: s.cfg_fw_delete(a[0], a[1])) if delete else None)),
                                                       handler=(lambda s, a, n: s.cfg_fw_delete(a[0], None)) if delete else None))))))
                if self.is_router:
                    paths.append(P.kw("security", "Security configuration",
                                      P.kw("zones", "Zone configuration",
                                           *([] if delete else [P.arg(P.LineArg("security-zone <name> interfaces <ifl> ..."), "",
                                                                      handler=lambda s, a, n: s.cfg_srx("zones", a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_srx("zones", "", True)) if delete else None),
                                      P.kw("policies", "Configure Network Security Policies",
                                           *([] if delete else [P.arg(P.LineArg("from-zone A to-zone B policy P match|then ..."), "",
                                                                      handler=lambda s, a, n: s.cfg_srx("policies", a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_srx("policies", "", True)) if delete else None),
                                      P.kw("ike", "IKE configuration",
                                           *([] if delete else [P.arg(P.LineArg("policy|gateway|proposal ..."), "IKE statement",
                                                                      handler=lambda s, a, n: s.cfg_vpn("ike", a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_vpn("ike", "", True)) if delete else None),
                                      P.kw("ipsec", "IPsec configuration",
                                           *([] if delete else [P.arg(P.LineArg("vpn|policy|proposal ..."), "IPsec statement",
                                                                      handler=lambda s, a, n: s.cfg_vpn("ipsec", a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_vpn("ipsec", "", True)) if delete else None),
                                      P.kw("nat", "Configure Network Address Translation",
                                           *([] if delete else [P.arg(P.LineArg("source|static|destination ..."), "NAT statement",
                                                                      handler=lambda s, a, n: s.cfg_jnat(a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_jnat("", True)) if delete else None),
                                      P.kw("idp", "Intrusion Detection and Prevention",
                                           *([] if delete else [P.arg(P.LineArg("active-policy <name> | idp-policy <name> ..."), "IDP statement",
                                                                      handler=lambda s, a, n: s.cfg_idp(a[0], False))]),
                                           handler=(lambda s, a, n: s.cfg_idp("", True)) if delete else None)))
                paths.append(P.kw("policy-options", "Policy option configuration",
                                  P.kw("policy-statement", "Routing policy",
                                       P.arg(P.WordArg("<policy-name>"), "Name to identify a policy filter",
                                             P.kw("term", "Policy term",
                                                  P.arg(P.WordArg("<term-name>"), "Name to identify a policy term",
                                                        P.kw("from", "Conditions to match the source of a route",
                                                             P.kw("protocol", "Protocol name",
                                                                  P.arg(P.WordArg("static|direct|ospf|bgp"), "",
                                                                        handler=lambda s, a, n: s.cfg_policy(a[0], "from", a[2], delete))),
                                                             P.kw("route-filter", "List of routes to match",
                                                                  P.arg(P.PrefixArg("<address>"), "IP address or hostname",
                                                                        *[P.kw(m, f"{m} match",
                                                                               handler=lambda s, a, n, m=m: s.cfg_policy(a[0], "filter", (a[2], m), delete))
                                                                          for m in ("exact", "orlonger")]))),
                                                        P.kw("then", "Actions to take if 'from' and 'to' conditions match",
                                                             P.kw("next-hop", "Set the address of the next-hop router",
                                                                  P.kw("self", "Use a local address as the next-hop address",
                                                                       handler=lambda s, a, n: s.cfg_policy(a[0], "nhs", True, delete))),
                                                             P.kw("accept", "Accept a route",
                                                                  handler=lambda s, a, n: s.cfg_policy(a[0], "accept", True, delete)),
                                                             P.kw("reject", "Reject a route",
                                                                  handler=lambda s, a, n: s.cfg_policy(a[0], "accept", False, delete))))),
                                             handler=(lambda s, a, n: s.cfg_policy(a[0], None, None, True)) if delete else None))))
            paths.sort(key=lambda x: x.word)
            return paths

        config = [
            P.kw("commit", "Commit current set of changes",
                 P.kw("and-quit", "Quit configuration mode if commit succeeds",
                      handler=lambda s, a, n: s.commit(and_quit=True)),
                 P.kw("check", "Check correctness of syntax; do not apply changes",
                      handler=lambda s, a, n: s.commit(check=True)),
                 handler=lambda s, a, n: s.commit()),
            P.kw("delete", "Delete a data element", *cfg_paths(True)),
            P.kw("exit", "Exit from this level", handler=lambda s, a, n: s.exit_config()),
            P.kw("quit", "Quit from this level", handler=lambda s, a, n: s.exit_config()),
            P.kw("rollback", "Roll back to previous committed configuration",
                 P.arg(P.NumArg(0, 49), "", handler=lambda s, a, n: s.rollback()),
                 handler=lambda s, a, n: s.rollback()),
            P.kw("run", "Run an operational-mode command",
                 P.arg(P.LineArg(), "", handler=lambda s, a, n: s.run_in_mode("oper", a[0]))),
            P.kw("set", "Set a parameter", *cfg_paths(False)),
            P.kw("show", "Show a parameter",
                 handler=lambda s, a, n: render_config(s.device, s.candidate)),
            P.kw("top", "Exit to top level of configuration", handler=lambda s, a, n: None),
        ]
        self._trees = {"oper": oper, "config": config}

    # -- configuration mode ----------------------------------------------------------
    def configure(self):
        self.mode = "config"
        if self.candidate is None:
            self.candidate = copy.deepcopy(self.device.to_config())
        return "Entering configuration mode"

    @property
    def dirty(self) -> bool:
        return self.candidate is not None and self.candidate != self.device.to_config()

    def exit_config(self):
        msg = ""
        if self.dirty:
            msg = ("The configuration has been changed but not committed\n"
                   "Exit with uncommitted changes? [yes,no] (yes) yes\n\n")
        self.mode = "oper"
        return msg + "Exiting configuration mode"

    def rollback(self):
        self.candidate = copy.deepcopy(self.device.to_config())
        return "load complete"

    def _icfg(self, port) -> dict:
        return self.candidate["interfaces"].setdefault(
            port.name, {"admin_up": True, "ip": None, "description": ""})

    def cfg_hostname(self, name):
        self.candidate["hostname"] = name or self.device.default_hostname
        return None

    def cfg_desc(self, port, text):
        self._icfg(port)["description"] = text
        return None

    def cfg_disable(self, port, disabled: bool):
        self._icfg(port)["admin_up"] = not disabled
        return None

    def cfg_iface_reset(self, port):
        self.candidate["interfaces"][port.name] = {"admin_up": True, "ip": None, "description": ""}
        return None

    def _sub_cfg(self, port, unit, create=True):
        name = f"{port.name}.{unit}"
        ifs = self.candidate["interfaces"]
        if name not in ifs and create:
            ifs[name] = {"admin_up": True, "ip": None, "description": "", "parent": port.name,
                         "vlan": None, "native": False, "arp_broadcast": True}
        return ifs.get(name)

    def cfg_unit_vlan(self, port, unit, vid, delete):
        if port.is_sub:
            return self.err_invalid(self.prompt, 0)
        if delete:
            ic = self._sub_cfg(port, unit, create=False)
            if ic is None:
                return "warning: statement not found"
            ic["vlan"] = None
            return None
        self._sub_cfg(port, unit)["vlan"] = vid
        return None

    def cfg_unit_delete(self, port, unit):
        name = f"{port.name}.{unit}"
        if name in self.candidate["interfaces"]:
            del self.candidate["interfaces"][name]
            return None
        return self.cfg_address(port, None, True)

    def cfg_address(self, port, prefix, delete, unit=0, vlan=None):
        if self.is_router and (unit != 0 or vlan is not None or
                               f"{port.name}.{unit}" in self.candidate["interfaces"]):
            if vlan is not None:
                self.cfg_unit_vlan(port, unit, vlan, False)
            ic = self._sub_cfg(port, unit, create=not delete)
            if ic is None:
                return "warning: statement not found"
            if delete:
                ic["ip"] = None
            else:
                ic["ip"] = str(IPv4Interface(prefix))
            return None
        ic = self._icfg(port)
        extra = ic.setdefault("ip2", [])
        if delete:
            if prefix is None:
                ic["ip"], ic["ip2"] = None, []
                return None
            a = str(IPv4Interface(prefix))
            if a in extra:
                extra.remove(a)
                return None
            if ic.get("ip") and IPv4Interface(ic["ip"]) == IPv4Interface(prefix):
                ic["ip"] = extra.pop(0) if extra else None          # the next address becomes primary
                return None
            return "warning: statement not found"
        if not self.is_router:
            return "error: family inet is not supported on switch ports of this platform"
        a = str(IPv4Interface(prefix))
        if ic.get("ip") and a != ic["ip"]:
            if IPv4Interface(a).network == IPv4Interface(ic["ip"]).network:
                ic["ip"] = a                                         # same subnet: replace
            elif a not in extra:
                extra.append(a)                                      # another subnet: additional address
            return None
        ic["ip"] = a
        return None

    # -- IPv6 ---------------------------------------------------------------------------
    def cfg_address6(self, port, prefix, delete):
        from ipaddress import IPv6Interface
        ic = self._icfg(port)
        lst = ic.setdefault("ipv6", [])
        if delete:
            if prefix is None:
                lst.clear()
            elif str(IPv6Interface(prefix)) in lst:
                lst.remove(str(IPv6Interface(prefix)))
            else:
                return "warning: statement not found"
        elif str(IPv6Interface(prefix)) not in lst:
            lst.append(str(IPv6Interface(prefix)))
        ic["ipv6_enabled"] = bool(lst)
        return None

    def cfg_route6(self, prefix, nh, delete):
        from ipaddress import IPv6Network
        net = str(IPv6Network(prefix, strict=False))
        routes = self.candidate.setdefault("static_routes6", [])
        routes[:] = [r for r in routes if not (r["network"] == net and (delete or r.get("next_hop") == nh))]
        if not delete:
            routes.append({"network": net, "next_hop": nh, "interface": None, "distance": JUNOS_STATIC_PREF})
        return None

    def show_route6(self):
        d = self.device
        table = d.routing_table6()
        lines = ["", f"inet6.0: {len(table)} destinations, {len(table)} routes ({len(table)} active, 0 holddown, 0 hidden)",
                 "+ = Active Route, - = Last Active, * = Both", ""]
        for r in table:
            proto = {"C": "Direct", "L": "Local", "S": "Static", "O": "OSPF3", "OI": "OSPF3", "R": "RIPng"}.get(r.code, "Access")
            pref = {"C": 0, "L": 0}.get(r.code, r.distance)
            lines.append(f"{str(r.network):<20}*[{proto}/{pref}] 00:00:01")
            if r.code == "L":
                lines.append(f"{'':<22}Local via {r.port.name}.0")
            elif r.next_hop is not None and r.code != "C":
                lines.append(f"{'':<22}>  to {r.next_hop} via {r.port.name}.0")
            else:
                lines.append(f"{'':<22}>  via {r.port.name}.0")
        return "\n".join(lines)

    def show_v6_neighbors(self):
        d = self.device
        lines = [f"{'IPv6 Address':<30}{'Linklayer Address':<20}{'State':<11}{'Exp':<5}{'Rtr':<5}Interface"]
        for n in d.nd_cache.values():
            lines.append(f"{str(n.ip):<30}{n.mac.cisco():<20}{'reachable':<11}{'28':<5}{'yes' if n.router else 'no':<5}"
                         f"{n.port.name}.0")
        return "\n".join(lines)

    # -- switching (EX, ELS syntax) ------------------------------------------------------
    # -- remote access ---------------------------------------------------------------------
    def _cand_access(self):
        from ..services.remote import default_access
        acc = self.candidate.get("access")
        if acc is None:
            acc = self.candidate["access"] = default_access(self.device)
        return acc

    def cfg_service(self, proto, delete):
        self._cand_access()[proto] = not delete
        return None

    def cfg_login_user(self, name, text, delete):
        acc = self._cand_access()
        users = acc.setdefault("users", {})
        if delete:
            users.pop(name, None)
            return None
        if "plain-text-password" not in (text or ""):
            return None
        first = {}

        def second(pw):
            if pw != first["pw"]:
                return "error: passwords do not match"
            users[name] = pw
            return None

        def one(pw):
            if len(pw) < 6:
                return "error: minimum password length is 6"
            first["pw"] = pw
            self.ask("Retype new password: ", second)
            return None
        self.ask("New password: ", one)
        return None

    def remote(self, proto, ip, user):
        from ..services.remote import connect
        if proto == "telnet":
            self.write(f"Trying {ip}...\n")
        return connect(self, ip, proto, user, "juniper")

    # -- OSPFv3 / RIPng ------------------------------------------------------------------------
    def cfg_v6igp(self, kind, ifname, area):
        from ..routing.ospf import area_id
        port = self._port_for_logical(ifname)
        if port is None:
            return f"error: interface {ifname} is not configured"
        lst = self.candidate.setdefault("v6igp", [])
        c = next((x for x in lst if x["kind"] == kind), None)
        if c is None:
            c = {"kind": kind, "tag": "1", "router_id": None, "interfaces": {}, "enabled": True}
            lst.append(c)
        try:
            c["interfaces"][port.name] = {"area": area_id(area) if area is not None else "0.0.0.0"}
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_v6igp_delete(self, kind):
        lst = self.candidate.get("v6igp", [])
        self.candidate["v6igp"] = [x for x in lst if x["kind"] != kind]
        return None

    def show_v6igp(self, kind):
        from .base import v6igp_neighbors
        if kind == "ripng":
            lines = [f"{'Neighbor':<16}{'State':<7}{'Source Address':<28}{'Dest Address':<16}Send   Recv  In Met"]
            for rid, p, ll in v6igp_neighbors(self.device, kind):
                lines.append(f"{(p.name + '.0') if p else '':<16}{'Up':<7}{ll.compressed:<28}{'ff02::9':<16}yes    yes      1")
            return "\n".join(lines)
        lines = [f"{'ID':<16}{'Interface':<16}{'State':<8}{'Pri':<5}Dead"]
        for rid, p, ll in v6igp_neighbors(self.device, kind):
            lines += [f"{rid:<16}{(p.name + '.0') if p else '':<16}{'Full':<8}{128:<5}35", f"  Neighbor-address {ll.compressed}"]
        return "\n".join(lines)

    def cfg_voip(self, ifname, vlan_name, delete):
        port = self.device.get_port(ifname.split(".")[0])
        if port is None:
            return f"error: interface {ifname} not found"
        vlans = self.candidate.get("vlans", {})
        vid = next((int(k) for k, v in vlans.items() if v == vlan_name), None)
        if vid is None and not delete:
            return f"error: vlan {vlan_name} is not defined"
        ic = self._icfg(port)
        if delete:
            ic.pop("voice_vlan", None)
        else:
            ic["voice_vlan"] = vid
        return None

    # -- syslog / ntp / snmp (candidate) --------------------------------------------------------
    def jmg(self, what, value, delete):
        m = self.candidate.setdefault("mgmt", {})
        if what in ("syslog", "ntp"):
            lst = [x for x in m.get(what, []) if x != value]
            m[what] = lst if delete else lst + [value]
        elif what in ("ro", "rw"):
            snmp = m.setdefault("snmp", {})
            if delete:
                snmp.pop(value, None)
            else:
                snmp[value] = what
        else:
            m[what] = "" if delete else value.strip('"')
        return None

    # -- GRE (gr-0/0/0) -------------------------------------------------------------------------
    def cfg_gre(self, port, text):
        from ipaddress import IPv4Address
        if not getattr(port, "is_tunnel", False):
            return "error: tunnel parameters are only valid on gr- interfaces"
        w = text.split()
        ic = self._icfg(port)
        try:
            for i in range(0, len(w), 2):
                if w[i] in ("source", "destination"):
                    IPv4Address(w[i + 1])
                    ic[w[i]] = w[i + 1]
                else:
                    raise ValueError
        except (ValueError, IndexError):
            return self.err_invalid(self.prompt, 0)
        ic["tunnel"] = True
        return None

    # -- DHCPv6 server (SRX), port security and DHCP snooping (EX) ----------------------------------
    def cfg_dhcp6_pool(self, name, text):
        w = text.split()
        d6 = self.candidate.setdefault("dhcp6", {"pools": [], "ifaces": {}})
        pool = next((x for x in d6["pools"] if x["name"] == name), None)
        if pool is None:
            pool = {"name": name, "prefix": None, "dns": None}
            d6["pools"].append(pool)
        try:
            if w[0] == "prefix":
                from ipaddress import IPv6Network
                pool["prefix"] = str(IPv6Network(w[1], strict=False))
            elif w[:2] == ["dhcp-attributes", "dns-server"]:
                pool["dns"] = w[2]
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_dhcp6_if(self, ifname):
        port = self._port_for_logical(ifname)
        if port is None:
            return f"error: interface {ifname} is not configured"
        self.candidate.setdefault("dhcp6", {"pools": [], "ifaces": {}})["ifaces"][port.name] = None
        return None

    def _resolve_dhcp6(self, cfg) -> str | None:
        from ipaddress import IPv6Interface, IPv6Network
        d6 = cfg.get("dhcp6")
        if not d6:
            return None
        for pname in list(d6["ifaces"]):
            ic = cfg["interfaces"].get(pname, {})
            nets = [IPv6Interface(a).network for a in ic.get("ipv6", [])]
            pool = next((x for x in d6["pools"] if x.get("prefix") and IPv6Network(x["prefix"]) in nets), None)
            if pool is None:
                return (f"[edit system services dhcp-local-server dhcpv6]\n  No address pool matches interface {pname}\n"
                        "error: configuration check-out failed")
            d6["ifaces"][pname] = pool["name"]
        return None

    def cfg_mac_limit(self, ifname, limit, action):
        port = self._port_for_logical(ifname)
        if port is None:
            return f"error: interface {ifname} is not configured"
        self._icfg(port)["port_security"] = {"max": limit, "violation": "shutdown" if action == "shutdown" else "restrict",
                                             "sticky": False, "macs": []}
        return None

    def cfg_dhcp_security(self, vlan, text):
        vid = next((int(k) for k, v in self.candidate.get("vlans", {}).items() if v == vlan), None)
        if vid is None:
            return f"error: vlan {vlan} is not configured"
        ds = self.candidate.setdefault("dhcp_snooping", {"enabled": True, "vlans": []})
        ds["enabled"] = True
        if vid not in ds["vlans"]:
            ds["vlans"].append(vid)
        w = text.split()
        if "trusted" in w and "interface" in w:
            ifname = w[w.index("interface") + 1] if w.index("interface") + 1 < len(w) else ""
            port = self._port_for_logical(ifname)
            if port is None:
                return f"error: interface {ifname} is not configured"
            self._icfg(port)["dhcp_trust"] = True
        return None

    # -- VRRP ----------------------------------------------------------------------------------
    def cfg_vrrp(self, port, group, text):
        w = text.split()
        lst = self.candidate.setdefault("fhrp", [])
        g = next((x for x in lst if x["port"] == port.name and x["group"] == group), None)
        if g is None:
            g = {"port": port.name, "proto": "vrrp", "group": group, "vip": None, "priority": 100, "preempt": None}
            lst.append(g)
        try:
            if w[0] == "virtual-address" and len(w) == 2:
                __import__("ipaddress").IPv4Address(w[1])
                g["vip"] = w[1]
            elif w[0] == "priority" and len(w) == 2 and w[1].isdigit():
                g["priority"] = int(w[1])
            elif w == ["preempt"]:
                g["preempt"] = True
            elif w == ["no-preempt"]:
                g["preempt"] = False
            elif w == ["accept-data"]:
                pass
            else:
                raise ValueError
        except (ValueError, IndexError):
            return self.err_invalid(self.prompt, 0)
        return None

    def show_vrrp(self):
        from .base import fhrp_rows
        lines = [f"{'Interface':<14}{'State':<8}{'Group':<8}{'VR state':<10}{'VR Mode':<10}{'Timer':<9}{'Type':<6}Address"]
        for g, p, act, stb in fhrp_rows(self.device):
            vr = {"master": "master", "backup": "backup"}.get(g.state, "init")
            lines.append(f"{p.name + '.0':<14}{'up' if p.is_up else 'down':<8}{g.group:<8}{vr:<10}{'Active':<10}"
                         f"{'A  0.716':<9}{'lcl':<6}{p.ip.ip if p.ip else ''}")
            lines.append(f"{'':<65}{'vip':<6}{g.vip}")
            if act and g.state != "master":
                lines.append(f"{'':<65}{'mas':<6}{act}")
        return "\n".join(lines)

    # -- aggregated Ethernet (EX) -------------------------------------------------------------
    def cfg_ae_count(self, n):
        d = self.device
        for i in range(n):
            if d.channel_for_group(i) is None:
                d.add_channel(i, f"ae{i}", f"ae{i}")
            self.candidate["interfaces"].setdefault(f"ae{i}", {"admin_up": True, "ip": None, "description": "",
                                                               "channel": True, "group": i, "short": f"ae{i}"})
        return None

    def cfg_ae_member(self, port, ae):
        ic = self._icfg(port)
        if ae is None:
            ic.pop("channel_group", None)
            ic.pop("channel_mode", None)
            return None
        if not (ae.startswith("ae") and ae[2:].isdigit()) or self.device.channel_for_group(int(ae[2:])) is None:
            return (f"error: device {ae} not found\n"
                    "(set chassis aggregated-devices ethernet device-count first)")
        ic["channel_group"] = int(ae[2:])
        ic["channel_mode"] = "on"
        return None

    def cfg_ae_lacp(self, port, mode):
        if not getattr(port, "is_channel", False):
            return "error: aggregated-ether-options apply to aeN interfaces"
        ic = self._icfg(port)
        if mode is None:
            ic.pop("lacp", None)
        else:
            ic["lacp"] = mode
        return None

    def _resolve_lag(self, cfg) -> str | None:
        ifs = cfg["interfaces"]
        for name, ic in ifs.items():
            g = ic.get("channel_group")
            if g is not None:
                ae = ifs.get(f"ae{g}", {})
                ic["channel_mode"] = ae.get("lacp") or "on"
        return None

    def show_lacp(self):
        from .base import lag_summary_rows
        lines = ["Aggregated interface: " + ", ".join(po.name for po in self.device.channels()) if self.device.channels()
                 else "warning: lacp subsystem not running - not needed by configuration."]
        for po, proto, mem in lag_summary_rows(self.device):
            lines += [f"Aggregated interface: {po.name}",
                      "    LACP state:       Role   Exp   Def  Dist  Col  Syn  Aggr  Timeout  Activity"]
            for m, f in mem:
                yes = "Yes" if f == "P" else "No"
                act = "Active" if m.channel_mode == "active" else "Passive"
                lines.append(f"      {m.name:<16}Actor    No    No   {yes:<5}{yes:<5}{yes:<5}Yes   Fast    {act}")
            lines += ["    LACP protocol:        Receive State  Transmit State          Mux State"]
            for m, f in mem:
                lines.append(f"      {m.name:<20}{'Current' if f == 'P' else 'Defaulted':<15}Fast periodic           "
                             f"{'Collecting distributing' if f == 'P' else 'Detached'}")
        return "\n".join(lines[1:] if self.device.channels() else lines)

    def cfg_irb(self, unit, prefix, delete, remove=False):
        ifs = self.candidate["interfaces"]
        if unit is None:
            for k in [k for k, v in ifs.items() if v.get("svi")]:
                del ifs[k]
            return None
        name = f"irb.{unit}"
        if delete and remove:
            if ifs.pop(name, None) is None:
                return "warning: statement not found"
            return None
        ic = ifs.setdefault(name, {"admin_up": True, "ip": None, "description": "", "svi": True, "vlan": 0,
                                   "short": name})
        ic["ip"] = None if delete else str(IPv4Interface(prefix))
        return None

    def cfg_vlan_l3(self, vlan_name, ifname):
        m = self.candidate.setdefault("extra", {}).setdefault("l3_ifs", {})
        if ifname is None:
            m.pop(vlan_name, None)
        else:
            if not ifname.startswith("irb."):
                return f"error: l3-interface must be an irb unit (irb.N), not {ifname}"
            m[vlan_name] = ifname
        return None

    def _resolve_irb(self, cfg) -> str | None:
        if self.is_router:
            return None
        names = {v: int(k) for k, v in cfg.get("vlans", {}).items()}
        names.setdefault("default", 1)
        mapping = cfg.get("extra", {}).get("l3_ifs", {})
        for ic in cfg["interfaces"].values():
            if ic.get("svi"):
                ic["vlan"] = 0
        for vname, ifname in mapping.items():
            if vname not in names:
                return (f"[edit vlans {vname}]\n  'l3-interface {ifname}'\n    VLAN {vname} has no vlan-id\n"
                        "error: configuration check-out failed")
            ic = cfg["interfaces"].get(ifname)
            if ic is None:
                return (f"[edit vlans {vname}]\n  'l3-interface {ifname}'\n    Interface {ifname} is not configured\n"
                        "error: configuration check-out failed")
            ic["vlan"] = names[vname]
        return None

    def cfg_vlan(self, name, vid):
        vlans = self.candidate.setdefault("vlans", {})
        for k, v in list(vlans.items()):
            if v == name:
                del vlans[k]
        if vid is not None:
            vlans[str(vid)] = name
        elif name not in ("default",):
            return None
        return None

    def _esw(self, port):
        ic = self._icfg(port)
        if "members" not in ic:      # derive from the committed state
            if ic.get("mode") == "trunk":
                tagged = ic.get("tagged")
                ic["members"] = ["all"] if tagged is None else [str(v) for v in tagged]
            else:
                av = ic.get("access_vlan", 1)
                ic["members"] = [] if av == 1 else [str(av)]
            ic.setdefault("mode", "access")
        return ic

    def cfg_esw_mode(self, port, mode, delete):
        self._esw(port)["mode"] = "access" if delete else mode
        return None

    def cfg_esw_members(self, port, text, delete):
        ic = self._esw(port)
        names = [] if text is None else [w for w in text.replace("[", " ").replace("]", " ").split()]
        if delete:
            ic["members"] = [m for m in ic["members"] if names and m not in names]
        else:
            ic["members"] = list(dict.fromkeys(ic["members"] + names))
        return None

    def cfg_esw_reset(self, port):
        ic = self._icfg(port)
        for k in ("mode", "members", "pvid", "tagged", "access_vlan"):
            ic.pop(k, None)
        return None

    def cfg_native(self, port, vid):
        ic = self._esw(port)
        ic["native_vlan"] = vid or 1
        return None

    def _resolve_switching(self, cfg) -> str | None:
        """Turn ELS 'members' lists into the neutral mode/pvid/tagged fields."""
        by_name = {"default": 1}
        by_name.update({v: int(k) for k, v in cfg.get("vlans", {}).items()})
        for name, ic in cfg["interfaces"].items():
            if "members" not in ic:
                continue
            ids = []
            for m in ic.pop("members"):
                if m == "all":
                    ids = None
                    break
                vid = int(m) if m.isdigit() else by_name.get(m)
                if vid is None:
                    return (f"[edit interfaces {name} unit 0 family ethernet-switching vlan]\n"
                            f"  'members {m}'\n    Vlan {m} is not configured\n"
                            "error: configuration check-out failed")
                if m.isdigit() and vid not in by_name.values():
                    return (f"[edit interfaces {name} unit 0 family ethernet-switching vlan]\n"
                            f"  'members {m}'\n    No vlan matches vlan tag {m} for interface {name}.0\n"
                            "error: configuration check-out failed")
                ids.append(vid)
            native = ic.get("native_vlan", 1)
            if ic.get("mode", "access") == "access":
                if ids is None or len(ids) > 1:
                    return (f"[edit interfaces {name} unit 0 family ethernet-switching]\n"
                            "  Access interface can be part of only one vlan\n"
                            "error: configuration check-out failed")
                ic.update(mode="access", access_vlan=ids[0] if ids else 1, tagged=None)
                ic["pvid"] = ic["access_vlan"]
            else:
                ic.update(mode="trunk", native_vlan=native, pvid=native,
                          tagged=None if ids is None else sorted(set(ids)))
                ic.setdefault("access_vlan", 1)
        return None

    # -- OSPF ------------------------------------------------------------------------------------
    def cfg_router_id(self, rid):
        if rid is None:
            self.candidate.pop("router_id", None)
        else:
            self.candidate["router_id"] = rid
        return None

    def _ospf_cfg(self, create=True):
        o = self.candidate.get("ospf")
        if o is None and create:
            from ..routing.ospf import OspfConfig
            o = self.candidate["ospf"] = OspfConfig().to_dict()
        return o

    def _port_for_logical(self, name):
        """'ge-0/0/0.0' -> port ge-0/0/0; 'ge-0/0/0.10' -> subinterface; 'lo0.0' -> lo0."""
        d = self.device
        if name in d.ports:
            return d.ports[name]
        base, dot, unit = name.rpartition(".")
        if dot and unit == "0" and base in d.ports:
            return d.ports[base]
        if dot and base in d.ports:
            return None if f"{base}.{unit}" not in self.candidate["interfaces"] else _Pending(name)
        return None

    def cfg_ospf_if(self, area, ifname, attr, value, delete=False):
        from ..routing.ospf import area_id
        try:
            a = area_id(area)
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        port = self._port_for_logical(ifname)
        if port is None:
            return (f"error: interface {ifname} is not configured\n"
                    "error: configuration check-out failed") if not delete else "warning: statement not found"
        o = self._ospf_cfg(create=not delete)
        if o is None:
            return "warning: statement not found"
        ifs = o.setdefault("interfaces", {})
        if delete:
            if port.name not in ifs:
                return "warning: statement not found"
            if attr is None:
                del ifs[port.name]
            else:
                ifs[port.name].pop(attr, None)
            return None
        entry = ifs.setdefault(port.name, {"area": a})
        entry["area"] = a
        if attr is not None:
            entry[attr] = value
        return None

    def cfg_ospf_area_delete(self, area):
        from ..routing.ospf import area_id
        o = self._ospf_cfg(create=False)
        if o is None:
            return "warning: statement not found"
        a = area_id(area)
        o["interfaces"] = {k: v for k, v in o.get("interfaces", {}).items() if v.get("area") != a}
        return None

    def cfg_ospf_delete(self):
        self.candidate.pop("ospf", None)
        return None

    def cfg_ospf_export(self, name, delete):
        o = self._ospf_cfg(create=not delete)
        if o is None:
            return "warning: statement not found"
        exports = o.setdefault("export", [])
        if delete:
            o["export"] = [] if name is None else [x for x in exports if x != name]
        elif name not in exports:
            exports.append(name)
        return None

    def cfg_policy(self, name, what, value, delete):
        pols = self.candidate.setdefault("policies", {})
        if delete and what is None:
            pols.pop(name, None)
            return None
        pol = pols.setdefault(name, {"protocols": [], "accept": False})
        if what == "from":
            if delete:
                pol["protocols"] = [x for x in pol["protocols"] if x != value]
            elif value not in pol["protocols"]:
                pol["protocols"].append(value)
        elif what == "accept":
            pol["accept"] = False if delete else value
        elif what == "nhs":
            pol["next_hop_self"] = not delete
        elif what == "filter":
            prefix = str(IPv4Network(value[0], strict=False))
            flt = pol.setdefault("route_filters", [])
            flt[:] = [f for f in flt if f[0] != prefix]
            if not delete:
                flt.append([prefix, value[1]])
        return None

    # -- firewall filters / NAT (raw Junos form kept in cfg["extra"]) -------------------------
    def _extra(self):
        return self.candidate.setdefault("extra", {})

    def cfg_if_filter(self, port, direction, name, delete):
        ic = self._icfg(port)
        key = "acl_in" if direction == "input" else "acl_out"
        if delete:
            ic.pop(key, None)
        else:
            ic[key] = name
        return None

    def cfg_fw_term(self, filt, term, text):
        words = text.split()
        if len(words) < 2 or words[0] not in ("from", "then"):
            return self.err_invalid(self.prompt, 0)
        t = self._extra().setdefault("filters", {}).setdefault(filt, {}).setdefault(term, {"from": {}, "then": None})
        if words[0] == "then":
            if words[1] in ("policer", "dscp") and len(words) == 3:
                from ..security.qos import parse_dscp
                if words[1] == "dscp":
                    try:
                        parse_dscp(words[2])
                    except ValueError:
                        return self.err_invalid(self.prompt, 0)
                t[words[1]] = words[2]
                return None
            if words[1] not in ("accept", "discard", "reject") or len(words) > 2:
                return self.err_invalid(self.prompt, 0)
            t["then"] = words[1]
            return None
        key, vals = words[1], words[2:]
        if key not in ("source-address", "destination-address", "protocol", "destination-port", "source-port",
                       "icmp-type") or len(vals) != 1:
            return self.err_invalid(self.prompt, 0)
        v = vals[0]
        if key.endswith("-address"):
            try:
                v = str(IPv4Network(v if "/" in v else v + "/32", strict=False))
            except ValueError:
                return self.err_invalid(self.prompt, 0)
        t["from"].setdefault(key, [])
        if v not in t["from"][key]:
            t["from"][key].append(v)
        return None

    # -- IS-IS (candidate) ---------------------------------------------------------------------
    def _isis_cand(self):
        return self.candidate.setdefault("isis", {"tag": "1", "net": None, "interfaces": [], "passive": [],
                                                  "metrics": {}, "enabled": True, "ipv6_all": True})

    def cfg_isis(self, text, delete):
        if delete:
            self.candidate.pop("isis", None)
            return None
        w = text.split()
        c = self._isis_cand()
        try:
            if w[0] == "level" and w[2] in ("disable", "wide-metrics-only"):
                return None
            if w[0] != "interface":
                raise IndexError
            port = self.device.get_port(w[1].split(".")[0])
            if port is None:
                return f"error: interface {w[1]} not found"
            if port.name not in c["interfaces"]:
                c["interfaces"].append(port.name)
            rest = w[2:]
            if rest == ["passive"] and port.name not in c["passive"]:
                c["passive"].append(port.name)
            elif rest[:1] == ["level"] and rest[2:3] == ["metric"]:
                c["metrics"][port.name] = int(rest[3])
            elif rest and rest != ["passive"] and rest[:1] != ["point-to-point"]:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_iso(self, port, net):
        from ..routing.isis import parse_net
        if net is None:
            self._isis_cand()["net"] = None
            return None
        if parse_net(net) is None:
            return "error: invalid ISO address (format 49.0001.0000.0000.0001.00)"
        if port.is_loopback:
            self._isis_cand()["net"] = net
        return None

    def show_isis(self, what):
        from .base import isis_database_lines, isis_neighbors_rows
        if what == "adjacency":
            lines = ["Interface             System         L State         Hold (secs) SNPA"]
            for sid, host, port, ip, state, hold in isis_neighbors_rows(self.device):
                lines.append(f"{port.name + '.0':<22}{host:<15}2 {state.capitalize():<14}{hold:<12}")
            return "\n".join(lines)
        return "\n".join(["IS-IS level 2 link-state database:",
                          "LSP ID                      Sequence Checksum Lifetime Attributes"]
                         + isis_database_lines(self.device))

    def cfg_vpn(self, section, text, delete):
        """security ike policy P pre-shared-key ascii-text K / ike gateway G address A|ike-policy P|external-interface I
        / ipsec vpn V bind-interface st0.0 / ipsec vpn V ike gateway G (proposals and policies are accepted as-is)."""
        vpn = self._extra().setdefault("vpn", {"ike_policies": {}, "gateways": {}, "vpns": {}})
        if delete:
            if section == "ike":
                vpn["ike_policies"], vpn["gateways"] = {}, {}
            else:
                vpn["vpns"] = {}
            return None
        w = [x.strip('"') for x in text.split()]
        try:
            if section == "ike" and w[0] == "policy" and w[2] == "pre-shared-key":
                vpn["ike_policies"].setdefault(w[1], {})["key"] = w[4]
            elif section == "ike" and w[0] == "policy":
                vpn["ike_policies"].setdefault(w[1], {})
            elif section == "ike" and w[0] == "gateway":
                g = vpn["gateways"].setdefault(w[1], {})
                if w[2] == "address":
                    IPv4Network(w[3] + "/32")
                    g["address"] = w[3]
                elif w[2] == "ike-policy":
                    g["policy"] = w[3]
                elif w[2] == "external-interface":
                    g["interface"] = w[3].split(".")[0]
                elif w[2] not in ("version", "local-identity", "remote-identity", "dead-peer-detection"):
                    raise IndexError
            elif section == "ike" and w[0] == "proposal":
                pass
            elif section == "ipsec" and w[0] == "vpn":
                v = vpn["vpns"].setdefault(w[1], {})
                if w[2] == "bind-interface":
                    v["bind"] = w[3].split(".")[0]
                elif w[2:4] == ["ike", "gateway"]:
                    v["gateway"] = w[4]
                elif w[2:4] == ["ike", "ipsec-policy"] or w[2] == "establish-tunnels":
                    pass
                else:
                    raise IndexError
            elif section == "ipsec" and w[0] in ("policy", "proposal"):
                pass
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_idp(self, text, delete):
        ex = self._extra()
        if delete:
            ex.pop("idp_active", None)
            ex.pop("idp_policies", None)
            return None
        w = [x.strip('"') for x in text.split()]
        if w[:1] == ["active-policy"] and len(w) > 1:
            ex["idp_active"] = w[1]
        elif w[:1] == ["idp-policy"] and len(w) > 1:
            ex.setdefault("idp_policies", [])
            if w[1] not in ex["idp_policies"]:
                ex["idp_policies"].append(w[1])
        else:
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_srx(self, section, text, delete):
        ex = self._extra()
        if delete:
            ex.pop("srx_" + section, None)
            return None
        w = [x.strip('"') for x in text.split()]
        try:
            if section == "zones":
                if w[0] != "security-zone":
                    raise IndexError
                z = ex.setdefault("srx_zones", {}).setdefault(w[1], [])
                if len(w) > 2:
                    if w[2] != "interfaces":
                        raise IndexError
                    port = self.device.get_port(w[3].split(".")[0])
                    if port is None:
                        return f"error: interface {w[3]} not found"
                    if port.name not in z:
                        z.append(port.name)
                return None
            if w[0] != "from-zone" or w[2] != "to-zone" or w[4] != "policy":
                raise IndexError
            pol = ex.setdefault("srx_policies", {}).setdefault(f"{w[1]}>{w[3]}", {}).setdefault(
                w[5], {"src": "any", "dst": "any", "app": "any", "then": None})
            rest = w[6:]
            if rest[0] == "match":
                key = {"source-address": "src", "destination-address": "dst", "application": "app"}[rest[1]]
                pol[key] = rest[2]
            elif rest[0] == "then" and rest[1] in ("permit", "deny", "reject"):
                pol["then"] = rest[1]
            else:
                raise IndexError
        except (IndexError, KeyError):
            return self.err_invalid(self.prompt, 0)
        return None

    def _resolve_zones(self, cfg) -> str | None:
        from ..security.stateful import APPS, ZoneFirewall, ZoneRule
        ex = cfg.get("extra", {})
        zones = ex.get("srx_zones") or {}
        if not zones:
            cfg.pop("zfw", None)
            return None
        zf = ZoneFirewall({k: list(v) for k, v in zones.items()})
        for pair, pols in (ex.get("srx_policies") or {}).items():
            a, b = pair.split(">")
            for z in (a, b):
                if z not in zones:
                    return (f"[edit security policies from-zone {a} to-zone {b}]\n  Security zone '{z}' is not defined\n"
                            "error: configuration check-out failed")
            rules = []
            for name, pol in pols.items():
                if pol["then"] is None:
                    continue
                app = pol["app"]
                if app != "any" and app not in APPS:
                    return f"error: application '{app}' is not defined\nerror: configuration check-out failed"
                rules.append(ZoneRule(name, "permit" if pol["then"] == "permit" else "drop",
                                      "any" if app == "any" else app,
                                      "0.0.0.0/0" if pol["src"] == "any" else pol["src"],
                                      "0.0.0.0/0" if pol["dst"] == "any" else pol["dst"]))
            zf.pairs[pair] = rules
        cfg["zfw"] = zf.to_dict()
        return None

    def show_security(self, what):
        d = self.device
        ex = d.extra
        if what == "idp":
            ips = ex.get("ips") or {}
            active = ex.get("idp_active")
            if not active:
                return "node0:\n--------------------------------------------------------------------------\n" \
                       "IDP is not enabled"
            return (f"node0:\n{'-' * 70}\nState of IDP: enabled, Up since: 2026-11-22\n"
                    f"Packets/second: 0, KBits/second: 0\nActive policy: {active}\n"
                    f"Interfaces with IDP: {len(ips.get('ifaces', {}))}\n"
                    f"Sessions dropped by IDP: {ex.get('ips', {}).get('drops', 0)}")
        if what == "zones":
            out = []
            for z, ifs in (ex.get("srx_zones") or {}).items():
                out += ["", f"Security zone: {z}", "  Send reset for non-SYN session TCP packets: Off",
                        f"  Interfaces bound: {len(ifs)}", "  Interfaces:"] + [f"    {i}.0" for i in ifs]
            return "\n".join(out)
        if what == "policies":
            out = []
            for pair, pols in (ex.get("srx_policies") or {}).items():
                a, b = pair.split(">")
                out.append(f"From zone: {a}, To zone: {b}")
                for i, (name, pol) in enumerate(pols.items(), 4):
                    out += [f"  Policy: {name}, State: enabled, Index: {i}, Scope Policy: 0, Sequence number: {i - 3}",
                            f"    Source addresses: {pol['src']}", f"    Destination addresses: {pol['dst']}",
                            f"    Applications: {pol['app']}", f"    Action: {pol['then']}"]
            out.append("Default policy: deny-all")
            return "\n".join(out)
        if not (ex.get("srx_zones") or {}):
            return self.show_flow_sessions()                 # packet mode: the NAT sessions
        out = []
        for i, f in enumerate(d.conntrack.active(), 1):
            proto, src, dst, sp, dp = f.key
            name = {1: "icmp", 6: "tcp", 17: "udp"}.get(proto, str(proto))
            out += [f"Session ID: {1000 + i}, Policy name: {f.zone_pair}, Timeout: 60, Valid",
                    f"  In: {src}/{sp} --> {dst}/{dp};{name}, Pkts: {f.packets}",
                    f"  Out: {dst}/{dp} --> {src}/{sp};{name}"]
        out.append(f"Total sessions: {len(d.conntrack.active())}")
        return "\n".join(out)

    def _resolve_idp(self, cfg) -> str | None:
        ex = cfg.setdefault("extra", {})
        name = ex.get("idp_active")
        if not name:
            if (ex.get("ips") or {}).get("_idp"):
                ex.pop("ips", None)
            return None
        ifaces = {p.name: name for p in self.device.ports.values()
                  if p.ip is not None and not getattr(p, "is_loopback", False)}
        ex["ips"] = {"names": [name], "ifaces": ifaces, "_idp": True,
                     "drops": (ex.get("ips") or {}).get("drops", 0)}
        return None

    def _resolve_vpn(self, cfg) -> str | None:
        vpn = cfg.get("extra", {}).get("vpn") or {}
        for name, ic in cfg["interfaces"].items():
            if name.startswith("st") and ic.get("tunnel"):          # recomputed from the VPN statements below
                for k in ("st_vpn", "source", "destination", "ipsec_key"):
                    ic.pop(k, None)
        for vname, v in vpn.get("vpns", {}).items():
            bind, gname = v.get("bind"), v.get("gateway")
            if not bind or not gname:
                continue
            g = vpn.get("gateways", {}).get(gname)
            if g is None:
                return (f"[edit security ipsec vpn {vname} ike]\n  'gateway {gname}'\n    Referenced gateway is not defined\n"
                        "error: configuration check-out failed")
            key = vpn.get("ike_policies", {}).get(g.get("policy", ""), {}).get("key")
            if not (g.get("address") and g.get("interface") and key):
                return (f"[edit security ike gateway {gname}]\n  address, external-interface and an ike-policy with "
                        "pre-shared-key are required\nerror: configuration check-out failed")
            ic = cfg["interfaces"].setdefault(bind, {"admin_up": True, "ip": None, "description": ""})
            ic.update(tunnel=True, short=bind, source=g["interface"], destination=g["address"], ipsec_key=key,
                      st_vpn=vname)
        return None

    def cfg_policer(self, name, text, delete):
        from ..security.qos import parse_rate
        pol = self._extra().setdefault("policers", {})
        if delete:
            pol.pop(name, None)
            return None
        w = text.split()
        p = pol.setdefault(name, {"bps": None, "action": "discard"})
        try:
            if w[:2] == ["if-exceeding", "bandwidth-limit"]:
                p["bps"] = parse_rate(w[2])
                if w[3:4] == ["burst-size-limit"]:
                    parse_rate(w[4])
            elif w == ["then", "discard"]:
                p["action"] = "discard"
            else:
                raise ValueError
        except (ValueError, IndexError):
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_fw_delete(self, filt, term):
        flt = self._extra().get("filters", {})
        if filt not in flt or (term is not None and term not in flt[filt]):
            return "warning: statement not found"
        if term is None:
            del flt[filt]
        else:
            del flt[filt][term]
        return None

    def cfg_jnat(self, text, delete):
        ex = self._extra()
        if delete:
            ex.pop("nat", None)
            return None
        w = text.split()
        nat = ex.setdefault("nat", {"source": {"pools": {}, "rule_sets": {}},
                                    "static": {"rule_sets": {}},
                                    "destination": {"pools": {}, "rule_sets": {}}})
        try:
            kind = w[0]
            if kind not in ("source", "static", "destination"):
                raise IndexError
            sec = nat[kind]
            if w[1] == "pool" and kind != "static":
                pool = sec["pools"].setdefault(w[2], {})
                if w[3] == "address":
                    pool["address"] = str(IPv4Network(w[4] if "/" in w[4] else w[4] + "/32", strict=False))
                    rest = w[5:]
                    if rest[:1] == ["to"]:
                        pool["to"] = str(IPv4Network(rest[1] if "/" in rest[1] else rest[1] + "/32", strict=False))
                        rest = rest[2:]
                    if rest[:1] == ["port"]:
                        pool["port"] = int(rest[1])
                        rest = rest[2:]
                    if rest:
                        raise IndexError
                else:
                    raise IndexError
                return None
            if w[1] != "rule-set":
                raise IndexError
            rs = sec["rule_sets"].setdefault(w[2], {"from": None, "to": None, "rules": {}})
            if w[3] in ("from", "to"):
                if w[4] != "interface":
                    return ("error: only 'from/to interface <name>' rule-set contexts are supported "
                            "(security zones are not modelled)")
                rs[w[3]] = w[5]
                return None
            if w[3] != "rule":
                raise IndexError
            rule = rs["rules"].setdefault(w[4], {"match": {}, "then": None})
            if w[5] == "match":
                key, val = w[6], w[7]
                if key in ("source-address", "destination-address"):
                    val = str(IPv4Network(val if "/" in val else val + "/32", strict=False))
                elif key == "destination-port":
                    val = int(val)
                else:
                    raise IndexError
                rule["match"][key] = val
            elif w[5] == "then":
                if kind == "source" and w[6:8] == ["source-nat", "interface"]:
                    rule["then"] = {"interface": True}
                elif kind == "source" and w[6:8] == ["source-nat", "pool"]:
                    rule["then"] = {"pool": w[8]}
                elif kind == "static" and w[6:8] == ["static-nat", "prefix"]:
                    rule["then"] = {"prefix": str(IPv4Network(w[8] if "/" in w[8] else w[8] + "/32", strict=False))}
                elif kind == "destination" and w[6:8] == ["destination-nat", "pool"]:
                    rule["then"] = {"pool": w[8]}
                else:
                    raise IndexError
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def _phys_name(self, lname):
        p = self._port_for_logical(lname) if lname else None
        return p.name if p is not None else None

    def _resolve_security(self, cfg) -> str | None:
        from ..security.acl import ICMP_TYPES, Acl, AclRule
        from ..security.cisco_syntax import PORT_NAMES
        from ..security.nat import NatConfig, NatRule
        ex = cfg.get("extra", {})
        # firewall filters -> ACLs with an implicit silent discard
        acls = {}
        for fname, terms in ex.get("filters", {}).items():
            acl = Acl(fname, "filter", default="deny", silent_default=True)
            seq = 0
            for tname, term in terms.items():
                if term.get("then") is None:
                    continue
                f = term["from"]
                action = "permit" if term["then"] == "accept" else "deny"
                silent = term["then"] == "discard"
                def port_of(v):
                    return int(v) if str(v).isdigit() else PORT_NAMES.get(v)
                for src in f.get("source-address", ["0.0.0.0/0"]):
                    for dst in f.get("destination-address", ["0.0.0.0/0"]):
                        seq += 1
                        r = AclRule(action, (f.get("protocol") or ["ip"])[0], IPv4Network(src), IPv4Network(dst),
                                    seq=seq, text=tname, extra={"silent": silent, "term": tname})
                        if f.get("destination-port"):
                            r.dport = ("eq", port_of(f["destination-port"][0]))
                        if f.get("source-port"):
                            r.sport = ("eq", port_of(f["source-port"][0]))
                        if f.get("icmp-type"):
                            r.icmp_type = ICMP_TYPES.get(f["icmp-type"][0])
                        acl.rules.append(r)
            acls[fname] = acl
        for name, ic in cfg["interfaces"].items():
            for key in ("acl_in", "acl_out"):
                if ic.get(key) and ic[key] not in acls:
                    return (f"[edit interfaces {name} unit 0 family inet filter]\n  '{'input' if key == 'acl_in' else 'output'} "
                            f"{ic[key]}'\n    Referenced filter '{ic[key]}' is not defined\n"
                            "error: configuration check-out failed")
        cfg["acls"] = {k: a.to_dict() for k, a in acls.items()}
        # terms with "then policer" / "then dscp" -> QoS policies on the interfaces using the filter
        from ..security.qos import QosClass, QosPolicy, parse_dscp
        policers = ex.get("policers", {})
        qpols = []
        for fname, terms in ex.get("filters", {}).items():
            classes = []
            for tname, term in terms.items():
                if not (term.get("policer") or term.get("dscp")):
                    continue
                if term.get("policer") and term["policer"] not in policers:
                    return (f"[edit firewall family inet filter {fname} term {tname} then]\n  'policer {term['policer']}'\n"
                            f"    Referenced policer '{term['policer']}' is not defined\nerror: configuration check-out failed")
                f = term["from"]
                m = {}
                if f.get("source-address"):
                    m["src"] = f["source-address"][0]
                if f.get("destination-address"):
                    m["dst"] = f["destination-address"][0]
                if f.get("protocol"):
                    m["protocol"] = f["protocol"][0]
                bps = policers.get(term.get("policer"), {}).get("bps") if term.get("policer") else None
                classes.append(QosClass(tname, m or {"any": True},
                                        set_dscp=parse_dscp(term["dscp"]) if term.get("dscp") else None, police_bps=bps))
            if classes:
                qpols.append(QosPolicy(f"filter-{fname}", classes))
        qnames = {p.name for p in qpols}
        for name, ic in cfg["interfaces"].items():
            for key, qkey in (("acl_in", "qos_in"), ("acl_out", "qos_out")):
                q = f"filter-{ic[key]}" if ic.get(key) else None
                if q in qnames:
                    ic[qkey] = q
                else:
                    ic.pop(qkey, None)
        if qpols:
            cfg["qos"] = {"policies": [p.to_dict() for p in qpols], "classes": {}}
        else:
            cfg.pop("qos", None)
        # NAT
        nat = ex.get("nat")
        if not nat:
            cfg.pop("nat", None)
            return None
        rules = []
        pools = nat["source"]["pools"]
        for rsname, rs in nat["source"]["rule_sets"].items():
            out = self._phys_name(rs.get("to"))
            for rname, r in rs["rules"].items():
                then = r.get("then") or {}
                prefix = r["match"].get("source-address", "0.0.0.0/0")
                if then.get("interface"):
                    rules.append(NatRule("dynamic", prefix=prefix, out_iface=out, interface=out, overload=True,
                                         text=f"{rsname}/{rname}"))
                elif then.get("pool"):
                    pool = pools.get(then["pool"])
                    if pool is None:
                        return (f"[edit security nat source rule-set {rsname} rule {rname} then source-nat pool]\n"
                                f"  Pool {then['pool']} is not defined\nerror: configuration check-out failed")
                    first = IPv4Network(pool["address"]).network_address
                    last = IPv4Network(pool.get("to", pool["address"])).broadcast_address
                    rules.append(NatRule("dynamic", prefix=prefix, out_iface=out, pool=(str(first), str(last)),
                                         overload=True, text=f"{rsname}/{rname}"))
        for rsname, rs in nat["static"]["rule_sets"].items():
            frm = self._phys_name(rs.get("from"))
            for rname, r in rs["rules"].items():
                g = r["match"].get("destination-address")
                then = r.get("then") or {}
                if g and then.get("prefix"):
                    rules.append(NatRule("static", local=str(IPv4Network(then["prefix"]).network_address),
                                         glob=str(IPv4Network(g).network_address), out_iface=frm,
                                         text=f"{rsname}/{rname}"))
        dpools = nat["destination"]["pools"]
        for rsname, rs in nat["destination"]["rule_sets"].items():
            frm = self._phys_name(rs.get("from"))
            for rname, r in rs["rules"].items():
                then = r.get("then") or {}
                pool = dpools.get(then.get("pool"))
                g = r["match"].get("destination-address")
                if pool and g:
                    rules.append(NatRule("dnat", local=str(IPv4Network(pool["address"]).network_address),
                                         glob=str(IPv4Network(g).network_address), in_iface=frm,
                                         global_port=r["match"].get("destination-port"),
                                         local_port=pool.get("port"), text=f"{rsname}/{rname}"))
        cfg["nat"] = NatConfig(rules=rules).to_dict()
        return None

    # -- spanning tree (EX) ------------------------------------------------------------------------
    def _stp_raw(self):
        ex = self._extra()
        if "stp" not in ex:
            ex["stp"] = {"proto": "rstp", "priority": 32768, "interfaces": {}}
        return ex["stp"]

    def cfg_stp(self, proto, text, delete=False):
        st = self._stp_raw()
        if delete:
            if st.get("proto") == proto:
                st["proto"] = None
            return None
        st["proto"] = proto
        w = (text or "").split()
        if not w:
            return None
        try:
            if w[0] == "bridge-priority" and len(w) == 2:
                v = w[1].lower()
                prio = int(v[:-1]) * 1024 if v.endswith("k") else int(v)
                if prio % 4096 or not 0 <= prio <= 61440:
                    return "error: bridge-priority must be a multiple of 4k between 0 and 60k"
                st["priority"] = prio
            elif w[0] == "interface" and len(w) >= 3:
                port = self._port_for_logical(w[1])
                if port is None:
                    return f"error: interface {w[1]} is not configured"
                ic = st["interfaces"].setdefault(port.name, {})
                if w[2] == "edge" and len(w) == 3:
                    ic["edge"] = True
                elif w[2] == "cost" and len(w) == 4 and w[3].isdigit():
                    ic["cost"] = int(w[3])
                else:
                    raise IndexError
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def _resolve_stp(self, cfg) -> str | None:
        if self.is_router:
            return None
        st = cfg.get("extra", {}).get("stp")
        if st is None:
            return None
        cfg["stp"] = {"enabled": st.get("proto") is not None, "mode": st.get("proto") or "rstp",
                      "priority": st.get("priority", 32768),
                      "costs": {k: v["cost"] for k, v in st.get("interfaces", {}).items() if v.get("cost")},
                      "edge": sorted(k for k, v in st.get("interfaces", {}).items() if v.get("edge"))}
        return None

    def show_stp_bridge(self):
        from ..addresses import MacAddress
        stp = self.device.stp
        if not stp.enabled:
            return "STP is not enabled"
        root = stp.root_id or stp.bridge_id
        fmt = lambda b: f"{b[0]}.{MacAddress(b[1]).cisco().replace('.', '').lower()}"
        return "\n".join([
            "STP bridge parameters", "Routing instance name               : GLOBAL",
            "Context ID                          : 0", f"Enabled protocol                    : {stp.mode.upper()}",
            f"  Root ID                           : {fmt(root)}",
            f"  Root cost                         : {stp.root_cost}",
            f"  Root port                         : {(stp.root_port + '.0') if stp.root_port else '-'}",
            "  Hello time                        : 2 seconds", "  Maximum age                       : 20 seconds",
            "  Forward delay                     : 15 seconds",
            f"  Number of topology changes        : {stp.topology_changes}",
            "  Local parameters", f"    Bridge ID                       : {fmt(stp.bridge_id)}"])

    def show_stp_interface(self):
        sw = self.device
        stp = sw.stp
        roles = {"root": "ROOT", "designated": "DESG", "alternate": "ALT", "edge": "DESG", "disabled": "DIS"}
        states = {"forwarding": "FWD", "blocking": "BLK", "listening": "BLK", "learning": "LRN", "disabled": "DIS"}
        lines = ["Spanning tree interface parameters for instance 0", "",
                 f"{'Interface':<14}{'Port ID':<10}{'Designated':<12}{'Designated':<22}{'Port':<8}{'State':<7}Role",
                 f"{'':<14}{'':<10}{'port ID':<12}{'bridge ID':<22}{'Cost':<8}"]
        for p in sw.ports.values():
            if not p.is_up:
                continue
            pid = f"128:{list(sw.ports).index(p.name) + 1}"
            bid = f"{stp.bridge_id[0]}.{stp.bridge_id[1]:012x}"
            lines.append(f"{p.name + '.0':<14}{pid:<10}{pid:<12}{bid:<22}{stp.port_cost(p):<8}"
                         f"{states.get(stp.states.get(p.name, 'forwarding')):<7}{roles.get(stp.roles.get(p.name), 'DESG')}")
        return "\n".join(lines)

    # -- RIP ---------------------------------------------------------------------------------------
    def cfg_rip_group(self, name, text):
        w = text.split()
        g = self._extra().setdefault("rip", {}).setdefault(name, {"neighbors": [], "export": []})
        if len(w) == 2 and w[0] == "neighbor":
            if self._port_for_logical(w[1]) is None:
                return f"error: interface {w[1]} is not configured"
            if w[1] not in g["neighbors"]:
                g["neighbors"].append(w[1])
        elif len(w) == 2 and w[0] == "export":
            if w[1] not in g["export"]:
                g["export"].append(w[1])
        else:
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_rip_delete(self, name):
        rip = self._extra().get("rip", {})
        if name is None:
            self._extra().pop("rip", None)
        elif name in rip:
            del rip[name]
        else:
            return "warning: statement not found"
        return None

    def _resolve_rip(self, cfg) -> str | None:
        from ..routing.rip import RipConfig
        groups = cfg.get("extra", {}).get("rip")
        if not groups:
            cfg.pop("rip", None)
            return None
        ifs, redist = [], []
        for gname, g in groups.items():
            for lname in g.get("neighbors", []):
                port = self._port_for_logical(lname)
                if port is not None:
                    ifs.append(port.name)
            for pname in g.get("export", []):
                pol = cfg.get("policies", {}).get(pname)
                if pol is None:
                    return (f"[edit protocols rip group {gname}]\n  'export {pname}'\n    Policy error: Policy {pname} "
                            "referenced but not defined\nerror: configuration check-out failed")
                if pol.get("accept"):
                    redist += [{"direct": "connected"}.get(x, x) for x in pol.get("protocols", [])
                               if x in ("direct", "static", "ospf")]
        cfg["rip"] = RipConfig(interfaces=ifs, redistribute=sorted(set(redist)), send_connected=False).to_dict()
        return None

    def show_rip_neighbor(self):
        rip = self.device.rip
        lines = [f"{'Neighbor':<17}{'State':<6}{'Source Address':<16}{'Destination Address':<20}{'Send Mode':<10}"
                 f"{'Receive Mode':<13}In Met"]
        if rip is not None:
            for p in self.device.ports.values():
                if p.name in rip.cfg.interfaces:
                    st = "Up" if p.is_up else "Dn"
                    lines.append(f"{_lname(p):<17}{st:<6}{str(p.ip.ip) if p.ip else '-':<16}{'224.0.0.9':<20}"
                                 f"{'mcast':<10}{'both':<13}1")
        return "\n".join(lines)

    # -- DHCP (jdhcpd syntax, kept in cfg["extra"]) -------------------------------------------
    def cfg_dhcp_pool(self, name, text):
        w = text.split()
        pool = self._extra().setdefault("dhcp_pools", {}).setdefault(name, {"ranges": {}})
        try:
            if w[0] == "network" and len(w) == 2:
                pool["network"] = str(IPv4Network(w[1], strict=False))
            elif w[0] == "range" and w[2] == "low" and w[4] == "high":
                pool["ranges"][w[1]] = [str(IPv4Address(w[3])), str(IPv4Address(w[5]))]
            elif w[0] == "range" and w[2] in ("low", "high"):
                r = pool["ranges"].setdefault(w[1], [None, None])
                r[0 if w[2] == "low" else 1] = str(IPv4Address(w[3]))
            elif w[0] == "dhcp-attributes" and w[1] in ("router", "name-server", "domain-name") and len(w) == 3:
                if w[1] != "domain-name":
                    IPv4Address(w[2])
                pool[w[1]] = w[2]
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def cfg_dhcp_pool_delete(self, name):
        pools = self._extra().get("dhcp_pools", {})
        if name not in pools:
            return "warning: statement not found"
        del pools[name]
        return None

    def cfg_dhcp_group(self, group, ifname, delete):
        groups = self._extra().setdefault("dhcp_groups", {})
        if group is None:
            groups.clear()
            return None
        lst = groups.setdefault(group, [])
        if delete:
            if ifname is None:
                groups.pop(group, None)
            elif ifname in lst:
                lst.remove(ifname)
            return None
        if self._port_for_logical(ifname) is None:
            return f"error: interface {ifname} is not configured"
        if ifname not in lst:
            lst.append(ifname)
        return None

    def cfg_dhcp_relay(self, text):
        ex = self._extra()
        if text is None:
            ex.pop("dhcp_relay", None)
            return None
        w = text.split()
        relay = ex.setdefault("dhcp_relay", {"server_groups": {}, "active": None, "groups": {}})
        try:
            if w[0] == "server-group" and len(w) == 3:
                IPv4Address(w[2])
                lst = relay["server_groups"].setdefault(w[1], [])
                if w[2] not in lst:
                    lst.append(w[2])
            elif w[0] == "active-server-group" and len(w) == 2:
                relay["active"] = w[1]
            elif w[0] == "group" and w[2] == "interface" and len(w) == 4:
                if self._port_for_logical(w[3]) is None:
                    return f"error: interface {w[3]} is not configured"
                lst = relay["groups"].setdefault(w[1], [])
                if w[3] not in lst:
                    lst.append(w[3])
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def _resolve_dhcp(self, cfg) -> str | None:
        from ..services.dhcp import DhcpPool, DhcpServerConfig
        ex = cfg.get("extra", {})
        pools = []
        for name, p in ex.get("dhcp_pools", {}).items():
            if not p.get("network"):
                return (f"[edit access address-assignment pool {name} family inet]\n  'network' is mandatory\n"
                        "error: configuration check-out failed")
            rng = next(iter(p.get("ranges", {}).values()), [None, None])
            pools.append(DhcpPool(name, p["network"], p.get("router"), p.get("name-server"), p.get("domain-name", ""),
                                  rng[0], rng[1]))
        ifs = []
        for lst in ex.get("dhcp_groups", {}).values():
            for lname in lst:
                port = self._port_for_logical(lname)
                if port is not None:
                    ifs.append(port.name)
        if pools or ifs:
            cfg["dhcp_server"] = DhcpServerConfig(True, pools, [], ifs).to_dict()
        else:
            cfg.pop("dhcp_server", None)
        relay = ex.get("dhcp_relay") or {}
        servers = relay.get("server_groups", {}).get(relay.get("active"), [])
        relay_ifs = set()
        for lst in relay.get("groups", {}).values():
            for lname in lst:
                port = self._port_for_logical(lname)
                if port is not None:
                    relay_ifs.add(port.name)
        for name, ic in cfg["interfaces"].items():
            if name in relay_ifs and servers:
                ic["helpers"] = list(servers)
            else:
                ic.pop("helpers", None)
        return None

    def show_dhcp_binding(self):
        srv = self.device.dhcp_server
        lines = [f"{'IP address':<18}{'Session Id':<12}{'Hardware address':<19}{'Expires':<12}{'State':<11}Interface"]
        if srv is not None:
            now = self.device.engine.now
            for i, b in enumerate(srv.bindings.values(), 1):
                port = next((p for p in self.device.ports.values() if p.ip and b.ip in p.ip.network), None)
                lines.append(f"{str(b.ip):<18}{i:<12}{_mac(b.mac):<19}{int((b.expires - now) / 1000):<12}"
                             f"{'BOUND':<11}{_lname(port) if port else '-'}")
        return "\n".join(lines)

    # -- BGP (groups kept in cfg["extra"]["bgp"]) -------------------------------------------
    def cfg_asn(self, asn):
        if asn is None:
            self._extra().pop("asn", None)
        else:
            self._extra()["asn"] = asn
        return None

    def cfg_bgp_delete(self):
        self._extra().pop("bgp", None)
        return None

    def cfg_bgp_group_delete(self, name):
        groups = self._extra().get("bgp", {})
        if name not in groups:
            return "warning: statement not found"
        del groups[name]
        return None

    def cfg_bgp_group(self, name, text):
        w = text.split()
        g = self._extra().setdefault("bgp", {}).setdefault(name, {"neighbors": {}, "export": []})
        try:
            if w[0] == "type" and w[1] in ("external", "internal") and len(w) == 2:
                g["type"] = w[1]
            elif w[0] == "peer-as" and w[1].isdigit() and len(w) == 2:
                g["peer_as"] = int(w[1])
            elif w[0] == "neighbor":
                IPv4Address(w[1])
                nb = g["neighbors"].setdefault(w[1], {})
                if w[2:4] and w[2] == "peer-as" and w[3].isdigit():
                    nb["peer_as"] = int(w[3])
                elif len(w) > 2:
                    raise IndexError
            elif w[0] == "local-address" and len(w) == 2:
                g["local_address"] = str(IPv4Address(w[1]))
            elif w[0] == "multihop":
                g["multihop"] = int(w[2]) if w[1:2] == ["ttl"] else 64
            elif w[0] == "export" and len(w) == 2:
                if w[1] not in g["export"]:
                    g["export"].append(w[1])
            else:
                raise IndexError
        except (IndexError, ValueError):
            return self.err_invalid(self.prompt, 0)
        return None

    def _resolve_bgp(self, cfg) -> str | None:
        ex = cfg.get("extra", {})
        groups = ex.get("bgp")
        if not groups:
            cfg.pop("bgp", None)
            return None
        asn = ex.get("asn")
        if not asn:
            return ("[edit protocols bgp]\n  'group'\n    BGP: autonomous-system not configured in routing-options\n"
                    "error: configuration check-out failed")
        neighbors, networks, redistribute = {}, [], []
        for gname, g in groups.items():
            nhs = False
            for pname in g.get("export", []):
                pol = cfg.get("policies", {}).get(pname)
                if pol is None:
                    return (f"[edit protocols bgp group {gname}]\n  'export {pname}'\n    Policy error: Policy "
                            f"{pname} referenced but not defined\nerror: configuration check-out failed")
                if not pol.get("accept"):
                    continue
                nhs = nhs or bool(pol.get("next_hop_self"))
                if pol.get("route_filters"):
                    networks += [f[0] for f in pol["route_filters"]]
                else:
                    redistribute += [{"direct": "connected"}.get(x, x) for x in pol.get("protocols", [])
                                     if x in ("direct", "static", "ospf")]
            src = None
            if g.get("local_address"):
                owner = next((p for p in self.device.ports.values()
                              if p.ip is not None and str(p.ip.ip) == g["local_address"]), None)
                if owner is None:
                    for name, ic in cfg["interfaces"].items():
                        if ic.get("ip") and str(IPv4Interface(ic["ip"]).ip) == g["local_address"]:
                            owner = self._port_for_logical(name)
                src = owner.name if owner is not None else None
            for ip, nb in g.get("neighbors", {}).items():
                peer_as = nb.get("peer_as") or g.get("peer_as")
                if g.get("type") == "internal":
                    peer_as = asn
                if not peer_as:
                    return (f"[edit protocols bgp group {gname} neighbor {ip}]\n  peer-as is not configured\n"
                            "error: configuration check-out failed")
                entry = {"remote_as": peer_as}
                if src:
                    entry["update_source"] = src
                if g.get("multihop"):
                    entry["multihop"] = g["multihop"]
                if nhs:
                    entry["next_hop_self"] = True
                entry["group"] = gname
                neighbors[ip] = entry
        from ..routing.bgp import BgpConfig
        cfg["bgp"] = BgpConfig(asn=asn, neighbors=neighbors, networks=sorted(set(networks)),
                               redistribute=sorted(set(redistribute))).to_dict()
        return None

    def _resolve_ospf(self, cfg) -> str | None:
        o = cfg.get("ospf")
        if not o:
            return None
        o["enabled"] = True
        redistribute, default = [], None
        for name in o.get("export", []):
            pol = cfg.get("policies", {}).get(name)
            if pol is None:
                return (f"[edit protocols ospf]\n  'export {name}'\n    Policy error: Policy {name} "
                        "referenced but not defined\nerror: configuration check-out failed")
            if pol.get("accept"):
                for proto in pol.get("protocols", []):
                    if proto == "static":
                        redistribute.append("static")
                        default = "yes"          # Junos exports a static default like any static
                    elif proto == "direct":
                        redistribute.append("connected")
        o["redistribute"] = sorted(set(redistribute))
        o["default_originate"] = default
        return None

    def cfg_route_discard(self, prefix, delete):
        routes = self.candidate.setdefault("static_routes", [])
        net = str(IPv4Network(prefix, strict=False))
        routes[:] = [r for r in routes if not (r["network"] == net and r.get("interface") == "Null0")]
        if not delete:
            routes.append({"network": net, "next_hop": None, "interface": "Null0", "distance": JUNOS_STATIC_PREF})
        return None

    def cfg_rpf(self, port, mode, delete):
        ic = self._icfg(port)
        if delete:
            ic.pop("urpf", None)
        else:
            ic["urpf"] = mode
        return None

    def cfg_route(self, prefix, nh, delete, preference=None):
        routes = self.candidate.setdefault("static_routes", [])
        if prefix is None:
            routes.clear()
            return None
        net = str(IPv4Network(prefix, strict=False))
        if delete:
            before = len(routes)
            routes[:] = [r for r in routes
                         if not (r["network"] == net and (nh is None or r.get("next_hop") == nh))]
            return None if len(routes) != before else "warning: statement not found"
        for r in routes:
            if r["network"] == net and r.get("next_hop") == nh:
                if preference is not None:
                    r["distance"] = preference
                return None
        routes.append({"network": net, "next_hop": nh, "interface": None,
                       "distance": JUNOS_STATIC_PREF if preference is None else preference})
        return None

    def _validate(self) -> str | None:
        nets = []
        for name, ic in self.candidate["interfaces"].items():
            if ic.get("ip"):
                i = IPv4Interface(ic["ip"])
                for other, onet in nets:
                    if onet.overlaps(i.network):
                        return (f"[edit interfaces {name} unit 0 family inet]\n  'address {i}'\n"
                                f"    Overlapping subnet with {other}\nerror: configuration check-out failed")
                nets.append((name, i.network))
        return None

    def commit(self, and_quit=False, check=False):
        err = self._validate()
        if err:
            return err
        cfg = copy.deepcopy(self.candidate)
        err = (self._resolve_switching(cfg) or self._resolve_ospf(cfg) or self._resolve_vpn(cfg) or self._resolve_zones(cfg) or self._resolve_idp(cfg) or self._resolve_security(cfg)
               or self._resolve_bgp(cfg) or self._resolve_dhcp(cfg) or self._resolve_rip(cfg)
               or self._resolve_stp(cfg) or self._resolve_irb(cfg) or self._resolve_lag(cfg)
               or self._resolve_dhcp6(cfg))
        if err:
            return err
        if check:
            return "configuration check succeeds"
        self.device.apply_config(cfg)
        self.candidate = copy.deepcopy(self.device.to_config())
        self.device.save_startup()  # Junos commits are persistent
        self.device.engine.emit("device_changed", self.device)
        if and_quit:
            self.mode = "oper"
            return "commit complete\nExiting configuration mode"
        return "commit complete"

    def zeroize(self):
        self.device.factory_reset()
        self.device.startup_config = None
        self.candidate = None
        self.mode = "oper"
        return "warning: System will be rebooted and may not boot without configuration\nzeroizing..."

    # -- operational --------------------------------------------------------------------
    def ping(self, ip, count=5):
        return make_ping(self.device, ip, JunosPing(self.write), count=count,
                           size=56, interval=1000.0 if count < 100 else 0).start()

    def traceroute(self, ip):
        return make_trace(self.device, ip, JunosTrace(self.write)).start()

    def show_version(self):
        d = self.device
        model = d.model.replace("Juniper ", "").lower()
        return (f"Hostname: {d.hostname}\nModel: {model}\n"
                f"Junos: 21.4R3.15\nJUNOS Software Release [21.4R3.15]")

    def show_terse(self, port=None):
        ports = [port] if port else list(self.device.ports.values())
        lines = [f"{'Interface':<24}{'Admin':<6}{'Link':<5}{'Proto':<9}{'Local':<22}Remote"]
        for p in ports:
            admin = "up" if p.admin_up else "down"
            link = "up" if p.is_up else "down"
            if p.is_sub:
                lines.append(f"{p.name:<24}{admin:<6}{link:<5}{'inet':<9}"
                             f"{p.ip.with_prefixlen if p.ip else ''}")
                continue
            lines.append(f"{p.name:<24}{admin:<6}{link}")
            if p.ip:
                lines.append(f"{p.name + '.0':<24}{admin:<6}{link:<5}{'inet':<9}{p.ip.with_prefixlen}")
            elif self.device.category == "switch" and p.connected:
                lines.append(f"{p.name + '.0':<24}{admin:<6}{link:<5}eth-switch")
        return "\n".join(lines)

    def show_interface(self, port):
        ports = [port] if port else list(self.device.ports.values())
        out = []
        for p in ports:
            en = "Enabled" if p.admin_up else "Disabled"
            block = [f"Physical interface: {p.name}, {en}, Physical link is {'Up' if p.is_up else 'Down'}"]
            if p.description:
                block.append(f"  Description: {p.description}")
            block += ["  Link-level type: Ethernet, MTU: 1514, Speed: 1000mbps",
                      f"  Current address: {_mac(p.mac)}, Hardware address: {_mac(p.mac)}",
                      f"  Input packets : {p.rx_frames}", f"  Output packets: {p.tx_frames}"]
            if p.ip:
                block += [f"  Logical interface {p.name}.0",
                          "    Protocol inet, MTU: 1500",
                          "      Addresses, Flags: Is-Preferred Is-Primary",
                          f"        Destination: {p.ip.network}, Local: {p.ip.ip}"]
            out.append("\n".join(block))
        return "\n\n".join(out)

    def show_arp(self):
        d = self.device
        if not self.is_router:
            return "Total entries: 0"
        entries = d.arp.entries(d.engine.now)
        lines = [f"{'MAC Address':<18}{'Address':<16}{'Name':<26}{'Interface':<24}Flags"]
        for e in entries:
            lines.append(f"{_mac(e.mac):<18}{str(e.ip):<16}{str(e.ip):<26}{_lname(e.port):<24}none")
        lines.append(f"Total entries: {len(entries)}")
        return "\n".join(lines)

    def show_route(self, only=None):
        d = self.device
        table = d.routing_table()
        if only:
            code = {"ospf": "O", "static": "S", "direct": "C", "bgp": "B", "rip": "R"}.get(only, "?")
            table = [r for r in table if r.code.startswith(code)]
        lines = ["", f"inet.0: {len(table)} destinations, {len(table)} routes "
                     f"({len(table)} active, 0 holddown, 0 hidden)",
                 "+ = Active Route, - = Last Active, * = Both", ""]
        age = _age(d.engine.now)
        for r in table:
            proto = {"C": "Direct", "L": "Local", "S": "Static", "B": "BGP", "R": "RIP",
                     "i L2": "IS-IS"}.get(r.code, "OSPF")
            pref = 0 if r.code in ("C", "L") else r.distance
            metric = f", metric {r.metric}" if r.code.startswith(("O", "R")) else ""
            if r.code == "B":
                bp = next((x for x in d.bgp.paths.get(r.network, []) if x.best), None) if d.bgp else None
                lp = bp.attrs.local_pref if bp and bp.attrs.local_pref is not None else 100
                metric = f", localpref {lp}"
            lines.append(f"{str(r.network):<19}*[{proto}/{pref}] {age}{metric}")
            if r.code == "B" and bp is not None:
                path = " ".join(map(str, bp.attrs.as_path))
                lines.append(f"{'':<22}AS path: {(path + ' ') if path else ''}{bp.attrs.origin.upper()}, "
                             "validation-state: unverified")
            if r.code == "L":
                lines.append(f"{'':<22}Local via {_lname(r.port)}")
            elif r.next_hop:
                lines.append(f"{'':<20}> to {r.next_hop} via {_lname(r.port)}")
            else:
                lines.append(f"{'':<20}> via {_lname(r.port)}")
        return "\n".join(lines)

    def _ospf_on(self):
        o = self.device.ospf
        return o if o is not None and o.running else None

    def show_ospf_neighbor(self):
        o = self._ospf_on()
        if o is None:
            return "error: the ospf subsystem is not running"
        lines = [f"{'Address':<17}{'Interface':<23}{'State':<16}{'ID':<17}{'Pri':<5}Dead"]
        for n, state, dead in o.neighbor_rows():
            st = "Full" if n.two_way and state.startswith("FULL") else ("2Way" if n.two_way else "Init")
            lines.append(f"{str(n.ip):<17}{_lname(n.port):<23}{st:<16}{str(n.rid):<17}{n.priority:<5}{int(dead)}")
        return "\n".join(lines)

    def show_ospf_interface(self):
        o = self._ospf_on()
        if o is None:
            return "error: the ospf subsystem is not running"
        lines = [f"{'Interface':<20}{'State':<8}{'Area':<16}{'DR ID':<16}{'BDR ID':<16}Nbrs"]
        for p in self.device.ports.values():
            area = o.iface_area(p)
            if area is None:
                continue
            dr, bdr = o.dr_bdr(p) if not p.is_loopback else (None, None)
            rid_of = {n.ip: n.rid for n in o.neighbors.values()}
            rid_of[p.ip.ip] = o.router_id
            if p.is_loopback or o.is_passive(p):
                st = "DRother"
            elif not p.is_up:
                st = "Down"
            else:
                st = "DR" if dr == p.ip.ip else "BDR" if bdr == p.ip.ip else "DRother"
            nb = sum(1 for n in o.neighbors.values() if n.port is p)
            lines.append(f"{_lname(p):<20}{st:<8}{area:<16}{str(rid_of.get(dr, '0.0.0.0')):<16}"
                         f"{str(rid_of.get(bdr, '0.0.0.0')):<16}{nb:>4}")
        return "\n".join(lines)

    def show_ospf_database(self):
        o = self._ospf_on()
        if o is None:
            return "error: the ospf subsystem is not running"
        names = {1: "Router", 3: "Summary", 4: "ASBRSum", 5: "Extern"}
        lines, area = [], object()
        now = int(self.device.engine.now / 1000)
        for l in o.database():
            if not l.body:
                continue
            if l.area != area:
                area = l.area
                lines += ["", f"    OSPF database, Area {area}" if area else "    OSPF AS SCOPE link state database",
                          f" {'Type':<7}{'ID':<17}{'Adv Rtr':<17}{'Seq':<11}{'Age':>6}  Opt  Cksum  Len"]
            star = "*" if l.adv == o.router_id else " "
            lines.append(f"{names[l.type]:<7}{star}{str(l.ls_id):<16} {str(l.adv):<16} 0x{l.seq:08x} "
                         f"{min(now, 3600):>5}  0x22 0x{(l.seq * 40503) & 0xFFFF:04x}  {36 + 12 * len(l.body)}")
        return "\n".join(lines)

    def _bgp_on(self):
        b = self.device.bgp
        return b if b is not None and b.running else None

    def show_bgp_summary(self):
        b = self._bgp_on()
        if b is None:
            return "error: the bgp subsystem is not running"
        groups = {nc.get("group") for nc in b.cfg.neighbors.values()}
        down = sum(1 for p in b.peers.values() if p.state != "Established")
        tot = sum(len(v) for v in b.paths.values())
        act = sum(1 for v in b.paths.values() for bp in v if bp.best and bp.peer is not None)
        lines = [f"Groups: {len(groups)} Peers: {len(b.peers)} Down peers: {down}",
                 f"{'Table':<15}{'Tot Paths':>9}  {'Act Paths':>9} {'Suppressed':>10} {'History':>10} {'Damp State':>10} {'Pending':>10}",
                 f"{'inet.0':<15}{tot:>9}  {act:>9} {0:>10} {0:>10} {0:>10} {0:>10}",
                 f"{'Peer':<25}{'AS':>5}{'InPkt':>11}{'OutPkt':>11}{'OutQ':>8}{'Flaps':>8} Last Up/Dwn State|#Active/Received/Accepted/Damped..."]
        now = self.device.engine.now
        for ip in sorted(b.peers, key=int):
            p = b.peers[ip]
            nc = b.ncfg(ip)
            if p.state == "Established":
                rcv = len(p.adj_in)
                active = sum(1 for v in b.paths.values() for bp in v if bp.best and bp.peer == ip)
                st = f"{active}/{rcv}/{rcv}/0              0/0/0/0"
                t = (now - p.up_since) / 1000
            else:
                st = "Connect" if p.state in ("Active", "OpenSent") else p.state.split()[0]
                t = 0
            lines.append(f"{str(ip):<25}{nc.get('remote_as', 0):>5}{p.msgs_rx:>11}{p.msgs_tx:>11}{0:>8}{p.flaps:>8}"
                         f" {int(t) // 60:>6}:{int(t) % 60:02d} {st}")
        return "\n".join(lines)

    def show_bgp_neighbor(self, ip):
        b = self._bgp_on()
        if b is None:
            return "error: the bgp subsystem is not running"
        out = []
        for addr in sorted(b.peers, key=int):
            if ip is not None and str(addr) != ip:
                continue
            p = b.peers[addr]
            nc = b.ncfg(addr)
            out.append("\n".join([
                f"Peer: {addr}+179 AS {nc.get('remote_as')} Local: {p.local_ip or '0.0.0.0'} AS {b.cfg.asn}",
                f"  Group: {nc.get('group', '-')}  Routing-Instance: master",
                f"  Type: {'External' if b.is_ebgp(addr) else 'Internal'}    State: {p.state.split()[0]}",
                f"  Peer ID: {p.remote_rid or '0.0.0.0'}  Local ID: {b.router_id}",
                f"  Last Error: {p.last_error or 'None'}",
                f"  Input messages:  Total {p.msgs_rx}", f"  Output messages: Total {p.msgs_tx}",
                f"  Active prefixes: {sum(1 for v in b.paths.values() for bp in v if bp.best and bp.peer == addr)}",
                f"  Received prefixes: {len(p.adj_in)}", f"  Advertised prefixes: {len(p.adj_out)}"]))
        return "\n\n".join(out)

    def show_firewall(self):
        out = []
        for name, acl in self.device.acls.items():
            out += ["", f"Filter: {name}", "Counters:", f"{'Name':<52}{'Bytes':>12}{'Packets':>16}"]
            for r in acl.rules:
                out.append(f"{r.extra.get('term', r.seq)!s:<52}{r.hits * 84:>12}{r.hits:>16}")
        return "\n".join(out)

    def show_snat_summary(self):
        nat = self.device.nat
        lines = ["Total port number usage for port translation pool: 64512", "",
                 f"{'Rule-set':<20}{'From':<14}{'To':<14}{'Rules':<6}"]
        if nat is not None:
            for r in nat.cfg.rules:
                if r.kind == "dynamic":
                    to = r.out_iface or "-"
                    lines.append(f"{r.text.split('/')[0]:<20}{'interface':<14}{to:<14}1")
        return "\n".join(lines)

    def show_static_nat(self, kind="static"):
        nat = self.device.nat
        lines = []
        if nat is not None:
            for r in nat.cfg.rules:
                if r.kind != kind:
                    continue
                rs, rn = (r.text.split("/") + [""])[:2]
                lines += [f"Static NAT rule: {rn:<20}Rule-set: {rs}" if kind == "static"
                          else f"Destination NAT rule: {rn:<15}Rule-set: {rs}",
                          f"  From interface         : {r.out_iface or r.in_iface or '-'}",
                          f"  Destination addresses  : {r.glob}",
                          f"  Host addresses         : {r.local}" + (f"  port {r.local_port}" if r.local_port else ""), ""]
        return "\n".join(lines) or "Total static-nat rules: 0"

    def show_flow_sessions(self):
        nat = self.device.nat
        lines = []
        names = {1: "icmp", 6: "tcp", 17: "udp"}
        if nat is not None:
            nat._expire()
            for i, t in enumerate(nat.table, 1):
                pr = names.get(t.proto, str(t.proto))
                lid = f"/{t.in_local[1]}" if t.in_local[1] is not None else ""
                gid = f"/{t.in_global[1]}" if t.in_global[1] is not None else ""
                oid = f"/{t.outside[1]}" if t.outside[1] is not None else ""
                lines += [f"Session ID: {1000 + i}, Policy name: default-permit/4, Timeout: 60, Valid",
                          f"  In: {t.in_local[0]}{lid} --> {t.outside[0]}{oid};{pr}, Conn Tag: 0x0",
                          f"  Out: {t.outside[0]}{oid} --> {t.in_global[0]}{gid};{pr}, Conn Tag: 0x0", ""]
        lines.append(f"Total sessions: {len(nat.table) if nat else 0}")
        return "\n".join(lines)

    def show_switching_table(self):
        entries = self.device.mac_entries()
        lines = ["", "MAC flags (S - static MAC, D - dynamic MAC, L - locally learned, P - Persistent static",
                 "           SE - statistics enabled, NM - non configured MAC, R - remote PE MAC, O - ovsdb MAC)",
                 "", "",
                 f"Ethernet switching table : {len(entries)} entries, {len(entries)} learned",
                 "Routing instance : default-switch",
                 "   Vlan                MAC                 MAC         Age    Logical                NH        RTR",
                 "   name                address             flags              interface              Index     ID"]
        for e in entries:
            vname = self.device.vlans.get(e.vlan, str(e.vlan))
            lines.append(f"   {vname[:19]:<20}{_mac(e.mac):<20}{'D':<12}{'-':>3}    {e.port.name + '.0':<23}{'0':<10}0")
        return "\n".join(lines)

    def show_vlans(self):
        sw = self.device
        lines = ["", f"{'Routing instance':<24}{'VLAN name':<22}{'Tag':<13}Interfaces"]
        for vid, name in sorted(sw.vlans.items()):
            lines.append(f"{'default-switch':<24}{name[:21]:<22}{vid:<13}")
            for p in sw.ports.values():
                if p.vlan_out(vid)[0] and (p.mode == "trunk" or p.connected):
                    star = "*" if p.is_up else ""
                    lines.append(f"{'':<59}{p.name}.0{star}")
        return "\n".join(lines)

    def show_esw_interface(self):
        sw = self.device
        lines = ["Routing Instance Name : default-switch", "Logical Interface flags (DL - disable learning, "
                 "AD - packet action drop,", "                         LH - MAC limit hit, DN - interface down )",
                 "", f"{'Logical':<14}{'Vlan':<18}{'TAG':<7}{'MAC':<7}{'STP':<14}{'Logical':<9}Tagging",
                 f"{'interface':<14}{'members':<18}{'':<7}{'limit':<7}{'state':<14}{'interface':<9}",
                 f"{'':<14}{'':<18}{'':<7}{'':<7}{'':<14}{'flags':<9}"]
        for p in sw.ports.values():
            first = True
            for vid, name in sorted(sw.vlans.items()):
                member, tag = p.vlan_out(vid)
                if not member:
                    continue
                head = f"{p.name + '.0':<14}" if first else " " * 14
                state = "Forwarding" if p.is_up else "Discarding"
                lines.append(f"{head}{name[:17]:<18}{vid:<7}{'294912':<7}{state:<14}{'':<9}"
                             f"{'untagged' if tag is None else 'tagged'}")
                first = False
        return "\n".join(lines)


def _lname(port) -> str:
    """Junos logical interface name: ge-0/0/0.0, or the unit name of a subinterface."""
    return port.name if port.is_sub else port.name + ".0"


def _age(now_ms: float) -> str:
    s = int(now_ms // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def render_config(device, cfg: dict) -> str:
    """Junos curly-brace configuration."""
    lines = ["## Last commit: by root", "version 21.4R3.15;", "system {",
             f"    host-name {cfg.get('hostname')};"]
    acc = cfg.get("access") or {}
    users = {u: pw for u, pw in acc.get("users", {}).items()}
    if "root" in users:
        lines += ["    root-authentication {", f'        encrypted-password "$6$netemu${_hash(users.pop("root"))}";', "    }"]
    if users:
        lines.append("    login {")
        for u, pw in users.items():
            lines += [f"        user {u} {{", "            class super-user;", "            authentication {",
                      f'                encrypted-password "$6$netemu${_hash(pw)}";', "            }", "        }"]
        lines.append("    }")
    dgroups = cfg.get("extra", {}).get("dhcp_groups", {})
    svc = [w for w in ("ssh", "telnet") if acc.get(w)]
    if dgroups or svc:
        lines.append("    services {")
        lines += [f"        {w};" for w in svc]
        if dgroups:
            lines.append("        dhcp-local-server {")
            for g, ifs in dgroups.items():
                lines.append(f"            group {g} {{ " + " ".join(f"interface {i};" for i in ifs) + " }")
            lines.append("        }")
        lines.append("    }")
    lines.append("}")
    all_ifs = cfg.get("interfaces", {})
    vlan_names = {1: "default"}
    vlan_names.update({int(k): v for k, v in cfg.get("vlans", {}).items()})
    subs: dict[str, list] = {}
    for n, ic in all_ifs.items():
        if ic.get("parent"):
            subs.setdefault(ic["parent"], []).append((n, ic))

    def switching(ic) -> list[str]:
        if "members" in ic:
            members, mode = ic["members"], ic.get("mode", "access")
        elif "mode" in ic:
            mode = ic["mode"]
            if mode == "trunk":
                t = ic.get("tagged")
                members = ["all"] if t is None else [vlan_names.get(v, str(v)) for v in t]
            else:
                av = ic.get("access_vlan", 1)
                members = [] if av == 1 else [vlan_names.get(av, str(av))]
        else:
            return []
        out = ["        unit 0 {", "            family ethernet-switching {",
               f"                interface-mode {mode};"]
        if members:
            m = members[0] if len(members) == 1 else "[ " + " ".join(members) + " ]"
            out += ["                vlan {", f"                    members {m};", "                }"]
        return out + ["            }", "        }"]

    aes = sorted(ic["group"] for ic in all_ifs.values() if ic.get("channel"))
    if aes:
        lines += ["chassis {", "    aggregated-devices {", "        ethernet {",
                  f"            device-count {max(aes) + 1};", "        }", "    }", "}"]
    ifaces = [(n, ic) for n, ic in all_ifs.items() if not ic.get("parent") and not ic.get("svi") and (
        ic.get("channel_group") is not None or ic.get("lacp") or (
        ic.get("ip") or ic.get("ipv6") or ic.get("description") or not ic.get("admin_up", True) or n in subs
        or "mode" in ic or "members" in ic))]
    irbs = sorted(((int(n.split(".", 1)[1]), ic) for n, ic in all_ifs.items() if ic.get("svi")), key=lambda x: x[0])
    if ifaces or irbs:
        lines.append("interfaces {")
        for name, ic in ifaces:
            lines.append(f"    {name} {{")
            if ic.get("description"):
                lines.append(f'        description "{ic["description"]}";')
            if not ic.get("admin_up", True):
                lines.append("        disable;")
            if name in subs:
                lines.append("        vlan-tagging;")
            if ic.get("channel_group") is not None:
                lines += ["        ether-options {", f"            802.3ad ae{ic['channel_group']};", "        }"]
            if ic.get("lacp"):
                lines += ["        aggregated-ether-options {", "            lacp {", f"                {ic['lacp']};",
                          "            }", "        }"]
            if ic.get("native_vlan", 1) != 1 and ic.get("mode") == "trunk":
                lines.append(f"        native-vlan-id {ic['native_vlan']};")
            if ic.get("ip") or ic.get("ipv6"):
                lines += ["        unit 0 {"]
            if ic.get("ipv6"):
                lines += ["            family inet6 {"] + [f"                address {a};" for a in ic["ipv6"]] + ["            }"]
                if not ic.get("ip"):
                    lines.append("        }")
            if ic.get("ip"):
                lines += ["            family inet {"]
                if ic.get("acl_in") or ic.get("acl_out"):
                    lines.append("                filter {")
                    if ic.get("acl_in"):
                        lines.append(f"                    input {ic['acl_in']};")
                    if ic.get("acl_out"):
                        lines.append(f"                    output {ic['acl_out']};")
                    lines.append("                }")
                vr = [g for g in cfg.get("fhrp", []) if g["port"] == name and g["proto"] == "vrrp"]
                if vr:
                    lines.append(f"                address {IPv4Interface(ic['ip']).with_prefixlen} {{")
                    for g in vr:
                        lines.append(f"                    vrrp-group {g['group']} {{")
                        if g.get("vip"):
                            lines.append(f"                        virtual-address {g['vip']};")
                        if g.get("priority", 100) != 100:
                            lines.append(f"                        priority {g['priority']};")
                        if g.get("preempt") is False:
                            lines.append("                        no-preempt;")
                        lines.append("                    }")
                    lines.append("                }")
                if ic.get("urpf"):
                    lines.append("                rpf-check;" if ic["urpf"] == "strict" else
                                 "                rpf-check { mode loose; }")
                lines += ([] if vr else [f"                address {IPv4Interface(ic['ip']).with_prefixlen};"]) + [
                          f"                address {IPv4Interface(a2).with_prefixlen};" for a2 in ic.get("ip2", [])] + [
                          "            }", "        }"]
            lines += switching(ic)
            for sname, sic in subs.get(name, []):
                lines.append(f"        unit {sname.rsplit('.', 1)[-1]} {{")
                if sic.get("vlan") is not None:
                    lines.append(f"            vlan-id {sic['vlan']};")
                if sic.get("ip"):
                    lines += ["            family inet {",
                              f"                address {IPv4Interface(sic['ip']).with_prefixlen};",
                              "            }"]
                lines.append("        }")
            lines.append("    }")
        if irbs:
            lines.append("    irb {")
            for unit, ic in irbs:
                lines.append(f"        unit {unit} {{")
                if ic.get("ip"):
                    lines += ["            family inet {",
                              f"                address {IPv4Interface(ic['ip']).with_prefixlen};", "            }"]
                lines.append("        }")
            lines.append("    }")
        lines.append("}")
    if cfg.get("vlans"):
        l3 = cfg.get("extra", {}).get("l3_ifs", {})
        lines.append("vlans {")
        for vid, name in sorted((int(k), v) for k, v in cfg["vlans"].items()):
            lines += [f"    {name} {{", f"        vlan-id {vid};"]
            if name in l3:
                lines.append(f"        l3-interface {l3[name]};")
            lines.append("    }")
        lines.append("}")
    routes = cfg.get("static_routes", [])
    asn = cfg.get("extra", {}).get("asn")
    routes6 = cfg.get("static_routes6", [])
    if routes or cfg.get("router_id") or asn or routes6:
        lines.append("routing-options {")
        if routes6:
            lines += ["    rib inet6.0 {", "        static {"]
            lines += [f"            route {r['network']} next-hop {r['next_hop']};" for r in routes6]
            lines += ["        }", "    }"]
        if cfg.get("router_id"):
            lines.append(f"    router-id {cfg['router_id']};")
        if asn:
            lines.append(f"    autonomous-system {asn};")
        if routes:
            lines.append("    static {")
            for r in routes:
                if r.get("interface") == "Null0":
                    lines.append(f"        route {r['network']} discard;")
                elif r.get("distance", JUNOS_STATIC_PREF) == JUNOS_STATIC_PREF:
                    lines.append(f"        route {r['network']} next-hop {r['next_hop']};")
                else:
                    lines.append(f"        route {r['network']} qualified-next-hop {r['next_hop']} "
                                 f"preference {r['distance']};")
            lines.append("    }")
        lines.append("}")
    groups = cfg.get("extra", {}).get("bgp")
    ospf = cfg.get("ospf")
    has_ospf = bool(ospf and ospf.get("enabled", True) and (ospf.get("interfaces") or ospf.get("export")))
    if groups or has_ospf:
        lines.append("protocols {")
    rip_groups = cfg.get("extra", {}).get("rip")
    if rip_groups and not (groups or has_ospf):
        lines.append("protocols {")
    if rip_groups:
        lines.append("    rip {")
        for gname, g in rip_groups.items():
            lines.append(f"        group {gname} {{")
            lines += [f"            export {e};" for e in g.get("export", [])]
            lines += [f"            neighbor {nb};" for nb in g.get("neighbors", [])]
            lines.append("        }")
        lines.append("    }")
        if not (groups or has_ospf):
            lines.append("}")
    if groups:
        lines.append("    bgp {")
        for gname, g in groups.items():
            lines.append(f"        group {gname} {{")
            if g.get("type"):
                lines.append(f"            type {g['type']};")
            if g.get("local_address"):
                lines.append(f"            local-address {g['local_address']};")
            if g.get("multihop"):
                lines.append(f"            multihop {{ ttl {g['multihop']}; }}")
            for e in g.get("export", []):
                lines.append(f"            export {e};")
            if g.get("peer_as"):
                lines.append(f"            peer-as {g['peer_as']};")
            for ip, nb in g.get("neighbors", {}).items():
                lines.append(f"            neighbor {ip}" + (f" {{ peer-as {nb['peer_as']}; }}" if nb.get("peer_as") else ";"))
            lines.append("        }")
        lines.append("    }")
    if not has_ospf and groups:
        lines.append("}")
    if has_ospf:
        lines += ["    ospf {"]
        for name in ospf.get("export", []):
            lines.append(f"        export {name};")
        areas: dict[str, list] = {}
        for ifname, ic in ospf.get("interfaces", {}).items():
            areas.setdefault(ic.get("area", "0.0.0.0"), []).append((ifname, ic))
        for area, ifs in sorted(areas.items()):
            lines.append(f"        area {area} {{")
            for ifname, ic in ifs:
                lname = ifname if "." in ifname else ifname + ".0"
                opts = []
                if ic.get("passive"):
                    opts.append("passive;")
                for key, word in (("cost", "metric"), ("hello", "hello-interval"), ("dead", "dead-interval"),
                                  ("priority", "priority")):
                    if ic.get(key) is not None:
                        opts.append(f"{word} {ic[key]};")
                if opts:
                    lines.append(f"            interface {lname} {{")
                    lines += [f"                {o}" for o in opts]
                    lines.append("            }")
                else:
                    lines.append(f"            interface {lname};")
            lines.append("        }")
        lines += ["    }", "}"]
    isis_c = cfg.get("isis")
    if isis_c and isis_c.get("interfaces"):
        block = ["    isis {", "        level 1 disable;"]
        for ifname in isis_c["interfaces"]:
            lname = ifname if "." in ifname else ifname + ".0"
            if ifname in isis_c.get("passive", []):
                block.append(f"        interface {lname} {{ passive; }}")
            elif ifname in isis_c.get("metrics", {}):
                block.append(f"        interface {lname} {{ level 2 metric {isis_c['metrics'][ifname]}; }}")
            else:
                block.append(f"        interface {lname};")
        block.append("    }")
        if "protocols {" in lines:
            depth, i = 0, lines.index("protocols {")
            for j in range(i, len(lines)):
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0:
                    lines[j:j] = block
                    break
        else:
            lines += ["protocols {"] + block + ["}"]
    ex = cfg.get("extra", {})
    if ex.get("filters"):
        lines += ["firewall {", "    family inet {"]
        for fname, terms in ex["filters"].items():
            lines.append(f"        filter {fname} {{")
            for tname, t in terms.items():
                lines.append(f"            term {tname} {{")
                if t["from"]:
                    lines.append("                from {")
                    for k, vals in t["from"].items():
                        v = vals[0] if len(vals) == 1 else "[ " + " ".join(vals) + " ]"
                        lines.append(f"                    {k} {v};")
                    lines.append("                }")
                if t.get("then"):
                    lines.append(f"                then {t['then']};")
                lines.append("            }")
            lines.append("        }")
        lines += ["    }", "}"]
    if ex.get("nat"):
        nat = ex["nat"]
        lines += ["security {", "    nat {"]
        for kind in ("source", "static", "destination"):
            sec = nat[kind]
            if not sec.get("rule_sets") and not sec.get("pools"):
                continue
            lines.append(f"        {kind} {{")
            for pname, pool in sec.get("pools", {}).items():
                addr = pool.get("address", "")
                extra = (f" to {pool['to']}" if pool.get("to") else "") + (f" port {pool['port']}" if pool.get("port") else "")
                lines += [f"            pool {pname} {{", f"                address {{ {addr}{extra}; }}", "            }"]
            for rsname, rs in sec.get("rule_sets", {}).items():
                lines.append(f"            rule-set {rsname} {{")
                if rs.get("from"):
                    lines.append(f"                from interface {rs['from']};")
                if rs.get("to"):
                    lines.append(f"                to interface {rs['to']};")
                for rname, r in rs["rules"].items():
                    lines.append(f"                rule {rname} {{")
                    m = " ".join(f"{k} {v};" for k, v in r["match"].items())
                    lines.append(f"                    match {{ {m} }}")
                    th = r.get("then") or {}
                    if th.get("interface"):
                        lines.append("                    then { source-nat { interface; } }")
                    elif th.get("pool") and kind == "source":
                        lines.append(f"                    then {{ source-nat {{ pool {{ {th['pool']}; }} }} }}")
                    elif th.get("prefix"):
                        lines.append(f"                    then {{ static-nat {{ prefix {{ {th['prefix']}; }} }} }}")
                    elif th.get("pool"):
                        lines.append(f"                    then {{ destination-nat {{ pool {{ {th['pool']}; }} }} }}")
                    lines.append("                }")
                lines.append("            }")
            lines.append("        }")
        lines += ["    }", "}"]
    if ex.get("idp_active") or ex.get("idp_policies"):
        lines.append("security {")
        lines.append("    idp {")
        for name in ex.get("idp_policies", []):
            lines += [f"        idp-policy {name} {{", "        }"]
        if ex.get("idp_active"):
            lines.append(f"        active-policy {ex['idp_active']};")
        lines += ["    }", "}"]
    pools = ex.get("dhcp_pools", {})
    if pools:
        lines += ["access {", "    address-assignment {"]
        for name, p in pools.items():
            lines += [f"        pool {name} {{", "            family inet {", f"                network {p.get('network')};"]
            for rname, (lo, hi) in p.get("ranges", {}).items():
                lines += [f"                range {rname} {{", f"                    low {lo};", f"                    high {hi};",
                          "                }"]
            attrs = [(k, p[k]) for k in ("router", "name-server", "domain-name") if p.get(k)]
            if attrs:
                lines.append("                dhcp-attributes {")
                lines += [f"                    {k} {{ {v}; }}" if k != "domain-name" else f"                    {k} {v};"
                          for k, v in attrs]
                lines.append("                }")
            lines += ["            }", "        }"]
        lines += ["    }", "}"]
    relay = ex.get("dhcp_relay")
    if relay:
        lines += ["forwarding-options {", "    dhcp-relay {"]
        for sg, ips in relay.get("server_groups", {}).items():
            lines += [f"        server-group {{ {sg} {{ " + " ".join(f"{ip};" for ip in ips) + " } }"]
        if relay.get("active"):
            lines.append(f"        active-server-group {relay['active']};")
        for g, ifs in relay.get("groups", {}).items():
            lines.append(f"        group {g} {{ " + " ".join(f"interface {i};" for i in ifs) + " }")
        lines += ["    }", "}"]
    pols = cfg.get("policies", {})
    if pols:
        lines += ["policy-options {"]
        for name, pol in pols.items():
            lines.append(f"    policy-statement {name} {{")
            lines.append("        term 1 {")
            for proto in pol.get("protocols", []):
                lines.append(f"            from protocol {proto};")
            for pfx, how in pol.get("route_filters", []):
                lines.append(f"            from route-filter {pfx} {how};")
            if pol.get("next_hop_self"):
                lines.append("            then next-hop self;")
            lines.append(f"            then {'accept' if pol.get('accept') else 'reject'};")
            lines += ["        }", "    }"]
        lines.append("}")
    return "\n".join(lines)


class _Pending:
    """A logical interface that exists only in the candidate configuration (not committed yet)."""

    def __init__(self, name):
        self.name = name


def _hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:43]
