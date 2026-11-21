"""Cisco IOS dialect (routers and Catalyst switches)."""
from __future__ import annotations

from ..addresses import IPv4Address, IPv4Interface, IPv4Network, make_interface
from ..routing.ospf import OspfConfig, area_id, wildcard_to_prefix
from ..security import cisco_syntax as ACLX
from ..security.acl import Acl
from ..security.nat import NatRule
from ..stack.icmp import PingListener, TraceListener
from . import parser as P
from .base import TreeSession, make_ping, overlapping_port, v6_addr_cmd, v6_route_cmd, make_trace
from .render import sim_clock, uptime_text

PROMPTS = {"user": ">", "priv": "#", "config": "(config)#", "config-if": "(config-if)#",
           "config-subif": "(config-subif)#", "config-vlan": "(config-vlan)#",
           "config-router": "(config-router)#", "config-router-bgp": "(config-router)#",
           "config-router-af": "(config-router-af)#", "dhcp-config": "(dhcp-config)#",
           "config-router-rip": "(config-router)#", "config-router-eigrp": "(config-router)#",
           "config-vrf": "(config-vrf)#", "config-router-isis": "(config-router)#",
           "config-server-tacacs": "(config-server-tacacs)#", "config-radius-server": "(config-radius-server)#",
           "config-sec-zone": "(config-sec-zone)#", "config-sec-zone-pair": "(config-sec-zone-pair)#",
           "config-cmap-insp": "(config-cmap)#", "config-pmap-insp": "(config-pmap)#",
           "config-pmap-c-insp": "(config-pmap-c)#",
           "config-std-nacl": "(config-std-nacl)#", "config-ext-nacl": "(config-ext-nacl)#",
           "config-line": "(config-line)#", "config-rtr6": "(config-rtr)#",
           "config-dhcpv6": "(config-dhcpv6)#", "config-isakmp": "(config-isakmp)#",
           "cfg-crypto-trans": "(cfg-crypto-trans)#", "ipsec-profile": "(ipsec-profile)#",
           "config-telephony": "(config-telephony)#", "config-ephone-dn": "(config-ephone-dn)#",
           "config-ephone": "(config-ephone)#", "config-cmap": "(config-cmap)#", "config-pmap": "(config-pmap)#", "config-pmap-c": "(config-pmap-c)#"}


# -- ping / traceroute output ----------------------------------------------------
class CiscoPing(PingListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w("Type escape sequence to abort.\n"
               f"Sending {s.count}, {s.size}-byte ICMP Echos to {s.dst}, timeout is "
               f"{int(s.timeout / 1000)} seconds:\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w("!")

    def on_timeout(self, seq):
        self.w(".")

    def on_send_fail(self, seq):
        self.w(".")

    def on_error(self, seq, kind, src):
        self.w("U" if kind == "unreachable" else "&")

    def on_finish(self, st):
        text = f"\nSuccess rate is {st.success_percent} percent ({st.received}/{st.sent})"
        if st.received:
            text += f", round-trip min/avg/max = {st.rtt('min')}/{st.rtt('avg')}/{st.rtt('max')} ms"
        self.w(text + "\n")


class CiscoTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f"Type escape sequence to abort.\nTracing the route to {s.dst}\n\n")

    def on_hop(self, hop):
        parts = []
        for r in hop.probes:
            parts.append("*" if r is None else ("!H" if hop.unreachable else f"{round(r)} msec"))
        addr = str(hop.address) if hop.address else ""
        line = f"{hop.ttl:>3}   {addr:<16}" if addr else f"{hop.ttl:>3}   "
        self.w(line + "  ".join(parts) + "\n")


# -- the session -------------------------------------------------------------------
class CiscoSession(TreeSession):
    negate_word = "no"
    shows_console = True        # %OSPF-5-ADJCHG and similar messages appear on the console
    negate_modes = ("config-line", "config", "config-if", "config-subif", "config-vlan", "config-router",
                    "config-router-bgp", "config-router-af", "dhcp-config", "config-router-rip",
                    "config-std-nacl", "config-ext-nacl", "config-router-eigrp", "config-cmap", "config-pmap",
                    "config-pmap-c", "config-telephony", "config-ephone-dn", "config-ephone", "config-isakmp",
                    "cfg-crypto-trans", "ipsec-profile", "config-rtr6", "config-dhcpv6", "config-vrf",
                    "config-router-isis", "config-sec-zone", "config-sec-zone-pair", "config-cmap-insp",
                    "config-pmap-insp", "config-pmap-c-insp", "config-server-tacacs", "config-radius-server")
    parent_modes = {"config-if": "config", "config-subif": "config", "config-vlan": "config",
                    "config-router": "config", "config-std-nacl": "config", "config-ext-nacl": "config",
                    "config-router-bgp": "config", "config-router-af": "config-router-bgp",
                    "dhcp-config": "config", "config-router-rip": "config", "config-line": "config",
                    "config-router-eigrp": "config", "config-vrf": "config", "config-router-isis": "config",
                    "config-server-tacacs": "config", "config-radius-server": "config",
                    "config-sec-zone": "config", "config-sec-zone-pair": "config", "config-cmap-insp": "config",
                    "config-pmap-insp": "config", "config-pmap-c-insp": "config-pmap-insp",
                    "config-rtr6": "config", "config-dhcpv6": "config", "config-isakmp": "config",
                    "cfg-crypto-trans": "config", "ipsec-profile": "config", "config-cmap": "config",
                    "config-telephony": "config", "config-ephone-dn": "config", "config-ephone": "config",
                    "config-pmap": "config", "config-pmap-c": "config-pmap"}

    @property
    def prompt(self) -> str:
        return self.device.hostname + PROMPTS[self.mode]

    @property
    def is_router(self) -> bool:
        return self.device.category == "router"

    def ctrl_z(self) -> str:
        if self.mode not in ("user", "priv"):
            self.mode = "priv"
            self.iface = None
            if getattr(self.device, "mgmt", None) is not None:
                self.device.mgmt.log(5, "%SYS-5-CONFIG_I: Configured from console by console")
            return "\n%SYS-5-CONFIG_I: Configured from console by console\n"
        return ""

    # -- trees -------------------------------------------------------------------
    def build_trees(self) -> None:
        self._build_trees_base()
        self._add_remote_trees()

    def _add_remote_trees(self) -> None:
        """telnet/ssh client, line vty, local users, enable secret, SSH keys."""
        t = self._trees
        clients = [
            P.kw("telnet", "Open a telnet connection",
                 P.arg(P.IpArg(), "IP address of a remote system",
                       P.arg(P.NumArg(1, 65535, "<1-65535>"), "Port number", handler=lambda s, a, n: s.remote("telnet", a[0], "", a[1])),
                       handler=lambda s, a, n: s.remote("telnet", a[0], ""))),
            P.kw("ssh", "Open a secure shell client connection",
                 P.kw("-l", "Log in using this user name",
                      P.arg(P.WordArg("WORD"), "Login name",
                            P.arg(P.IpArg(), "IP address of a remote system",
                                  handler=lambda s, a, n: s.remote("ssh", a[1], a[0]))))),
        ]
        for mode in ("user", "priv"):
            t[mode] = sorted(t[mode] + clients, key=lambda x: x.word or "")
        word = lambda h: P.WordArg(h)
        t["config"] = sorted(t["config"] + [
            P.kw("line", "Configure a terminal line",
                 P.kw("vty", "Virtual terminal",
                      P.arg(P.NumArg(0, 15, "<0-15>"), "First Line number",
                            P.arg(P.NumArg(1, 15, "<1-15>"), "Last Line number", handler=lambda s, a, n: s.enter_vty()),
                            handler=lambda s, a, n: s.enter_vty())),
                 P.kw("console", "Primary terminal line",
                      P.arg(P.NumArg(0, 0, "<0-0>"), "First Line number", handler=lambda s, a, n: s.enter_console()))),
            P.kw("username", "Establish User Name Authentication",
                 P.arg(word("WORD"), "User name",
                       P.kw("privilege", "Set user privilege level",
                            P.arg(P.NumArg(0, 15, "<0-15>"), "User privilege level",
                                  P.kw("secret", "Specify the secret for the user",
                                       P.arg(word("LINE"), "The UNENCRYPTED (cleartext) user secret",
                                             handler=lambda s, a, n: s.username(a[0], a[2], "secret", a[1]))),
                                  P.kw("password", "Specify the password for the user",
                                       P.arg(word("LINE"), "The UNENCRYPTED (cleartext) user password",
                                             handler=lambda s, a, n: s.username(a[0], a[2], "password", a[1]))))),
                       P.kw("secret", "Specify the secret for the user",
                            P.arg(word("LINE"), "The UNENCRYPTED (cleartext) user secret",
                                  handler=lambda s, a, n: s.username(a[0], a[1], "secret"))),
                       P.kw("password", "Specify the password for the user",
                            P.arg(word("LINE"), "The UNENCRYPTED (cleartext) user password",
                                  handler=lambda s, a, n: s.username(a[0], a[1], "password"))),
                       handler=lambda s, a, n: s.username(a[0], None, None) if n else s.err_incomplete("", 0),
                       negatable=True),
                 negatable=True),
            P.kw("enable", "Modify enable password parameters",
                 P.kw("secret", "Assign the privileged level secret",
                      P.arg(word("LINE"), "The UNENCRYPTED (cleartext) 'enable' secret",
                            handler=lambda s, a, n: s.enable_secret(a[0], n), negatable=True),
                      handler=lambda s, a, n: s.enable_secret(None, n) if n else s.err_incomplete("", 0), negatable=True),
                 P.kw("password", "Assign the privileged level password",
                      P.arg(word("LINE"), "The UNENCRYPTED (cleartext) 'enable' password",
                            handler=lambda s, a, n: s.enable_secret(a[0], n), negatable=True),
                      negatable=True),
                 negatable=True),
            P.kw("crypto", "Encryption module",
                 P.kw("key", "Long term key operations",
                      P.kw("generate", "Generate new keys",
                           P.kw("rsa", "Generate RSA keys",
                                P.kw("general-keys", "Generate a general purpose RSA key pair",
                                     P.kw("modulus", "Provide number of modulus bits on the command line",
                                          P.arg(P.NumArg(360, 4096, "<360-4096>"), "size of the key modulus",
                                                handler=lambda s, a, n: s.crypto_rsa(a[0]))),
                                     handler=lambda s, a, n: s.crypto_rsa(None)),
                                P.kw("modulus", "Provide number of modulus bits on the command line",
                                     P.arg(P.NumArg(360, 4096, "<360-4096>"), "size of the key modulus",
                                           handler=lambda s, a, n: s.crypto_rsa(a[0]))),
                                handler=lambda s, a, n: s.crypto_rsa(None))),
                      P.kw("zeroize", "Remove keys",
                           P.kw("rsa", "Remove RSA keys", handler=lambda s, a, n: s.crypto_zeroize())))),
        ], key=lambda x: x.word or "")
        ip = next(x for x in t["config"] if x.word == "ip")
        ip.children += [
            P.kw("domain-name", "Define the default domain name",
                 P.arg(word("WORD"), "Default domain name", handler=lambda s, a, n: s.domain_name(a[0], n), negatable=True),
                 negatable=True),
            P.kw("ssh", "Configure ssh options",
                 P.kw("version", "Specify protocol version supported",
                      P.arg(P.NumArg(1, 2, "<1-2>"), "Protocol version", handler=lambda s, a, n: None, negatable=True),
                      negatable=True),
                 negatable=True)]
        line = [
            P.kw("password", "Set a password",
                 P.arg(word("LINE"), "The UNENCRYPTED (cleartext) line password",
                       handler=lambda s, a, n: s.line_password(a[0], n), negatable=True),
                 handler=lambda s, a, n: s.line_password(None, n) if n else s.err_incomplete("", 0), negatable=True),
            P.kw("login", "Enable password checking",
                 P.kw("local", "Local password checking", handler=lambda s, a, n: s.line_login("local", n), negatable=True),
                 handler=lambda s, a, n: s.line_login("line", n), negatable=True),
            P.kw("transport", "Define transport protocols for line",
                 P.kw("input", "Define which protocols to use when connecting to the terminal server",
                      *[P.kw(w, h, handler=lambda s, a, n, w=w: s.line_transport(w))
                        for w, h in (("all", "All protocols"), ("none", "No protocols"))],
                      P.kw("ssh", "TCP/IP SSH protocol", P.kw("telnet", "TCP/IP Telnet protocol",
                                                                handler=lambda s, a, n: s.line_transport("all")),
                           handler=lambda s, a, n: s.line_transport("ssh")),
                      P.kw("telnet", "TCP/IP Telnet protocol", P.kw("ssh", "TCP/IP SSH protocol",
                                                                     handler=lambda s, a, n: s.line_transport("all")),
                           handler=lambda s, a, n: s.line_transport("telnet")))),
            P.kw("exec-timeout", "Set the EXEC timeout",
                 P.arg(P.NumArg(0, 35791, "<0-35791>"), "Timeout in minutes",
                       P.arg(P.NumArg(0, 2147483, "<0-2147483>"), "Timeout in seconds", handler=lambda s, a, n: None),
                       handler=lambda s, a, n: None)),
            P.kw("logging", "Modify message logging facilities",
                 P.kw("synchronous", "Synchronized message output", handler=lambda s, a, n: None, negatable=True),
                 negatable=True),
            P.kw("exit", "Exit from line configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            next(x for x in t["config"] if x.word == "do"), next(x for x in t["config"] if x.word == "end"),
        ]
        t["config-line"] = sorted(line, key=lambda x: x.word or "")
        show = next(x for x in t["priv"] if x.word == "show")
        ipk = next(x for x in show.children if x.word == "ip")
        ipk.children.append(P.kw("ssh", "Information on SSH", handler=lambda s, a, n: s.show_ip_ssh()))
        show.children.append(P.kw("users", "Display information about terminal lines",
                                  handler=lambda s, a, n: s.show_users()))
        for mode in ("user",):
            for x in t[mode]:
                if x.word == "enable":
                    x.handler = lambda s, a, n: s.enable_cmd()
        grp = lambda proto, lo: P.arg(P.NumArg(lo, 255, f"<{lo}-255>"), "group number",
                                      P.kw("ip", "Enable group and set virtual IP address",
                                           P.arg(P.IpArg(), "Virtual IP address",
                                                 handler=lambda s, a, n: s.fhrp(proto, a[0], "vip", a[1], n), negatable=True),
                                           negatable=True),
                                      P.kw("priority", "Priority level",
                                           P.arg(P.NumArg(1 if proto == "vrrp" else 0, 254 if proto == "vrrp" else 255, "<0-255>"),
                                                 "Priority value", handler=lambda s, a, n: s.fhrp(proto, a[0], "priority", a[1], n),
                                                 negatable=True),
                                           negatable=True),
                                      P.kw("preempt", "Overthrow lower priority Active routers",
                                           handler=lambda s, a, n: s.fhrp(proto, a[0], "preempt", True, n), negatable=True),
                                      handler=lambda s, a, n: s.fhrp(proto, a[0], None, None, True) if n else s.err_incomplete("", 0),
                                      negatable=True)
        v6if = next((x for x in t["config-if"] if x.word == "ipv6"), None)
        if v6if is not None:
            v6if.children += [
                P.kw("ospf", "OSPF interface commands",
                     P.arg(P.NumArg(1, 65535, "<1-65535>"), "Process ID",
                           P.kw("area", "Set the OSPF area ID",
                                P.arg(P.WordArg("<0-4294967295>|A.B.C.D"), "OSPF area ID",
                                      handler=lambda s, a, n: s.v6igp_if("ospf6", a[0], a[1], n), negatable=True)),
                           negatable=True),
                     P.kw("cost", "Interface cost", P.arg(P.NumArg(1, 65535, "<1-65535>"), "Cost",
                                                          handler=lambda s, a, n: s.v6igp_cost(a[0], n), negatable=True),
                          negatable=True),
                     negatable=True),
                P.kw("rip", "Configure RIP routing protocol",
                     P.arg(P.WordArg("WORD"), "User selected string identifying this process",
                           P.kw("enable", "Enable RIP routing on interface",
                                handler=lambda s, a, n: s.v6igp_if("ripng", a[0], None, n), negatable=True),
                           negatable=True),
                     negatable=True),
                P.kw("eigrp", "Configure EIGRP IPv6 on interface",
                     P.arg(P.NumArg(1, 65535, "<1-65535>"), "Autonomous system number",
                           handler=lambda s, a, n: s.v6igp_if("eigrp6", a[0], None, n), negatable=True),
                     negatable=True),
                P.kw("router", "IPv6 Router interface commands",
                     P.kw("isis", "IS-IS Routing for IPv6",
                          P.arg(P.WordArg("WORD"), "Routing process tag", handler=lambda s, a, n: s.if_isis6(n),
                                negatable=True),
                          handler=lambda s, a, n: s.if_isis6(n), negatable=True),
                     negatable=True)]
        v6g = next((x for x in t["config"] if x.word == "ipv6"), None)
        if v6g is not None:
            v6g.children.append(P.kw("router", "Enable an IPV6 routing process",
                                     P.kw("ospf", "Open Shortest Path First (OSPF)",
                                          P.arg(P.NumArg(1, 65535, "<1-65535>"), "Process ID",
                                                handler=lambda s, a, n: s.v6igp_router("ospf6", a[0], n), negatable=True),
                                          negatable=True),
                                     P.kw("rip", "IPv6 Routing Information Protocol (RIPng)",
                                          P.arg(P.WordArg("WORD"), "User selected string identifying this process",
                                                handler=lambda s, a, n: s.v6igp_router("ripng", a[0], n), negatable=True),
                                          negatable=True),
                                     P.kw("eigrp", "Enhanced Interior Gateway Routing Protocol (EIGRP)",
                                          P.arg(P.NumArg(1, 65535, "<1-65535>"), "Autonomous System",
                                                handler=lambda s, a, n: s.v6igp_router("eigrp6", a[0], n), negatable=True),
                                          negatable=True),
                                     negatable=True))
        if v6g is not None:
            dh = next((x for x in v6g.children if x.word == "dhcp"), None)
            node = P.kw("pool", "Configure IPv6 DHCP pool",
                        P.arg(P.WordArg("WORD"), "DHCP pool name", handler=lambda s, a, n: s.dhcp6_pool(a[0], n),
                              negatable=True), negatable=True)
            if dh is None:
                v6g.children.append(P.kw("dhcp", "Configure IPv6 DHCP", node, negatable=True))
            else:
                dh.children.append(node)
        if v6if is not None:
            v6if.children.append(P.kw("dhcp", "IPv6 DHCP interface subcommands",
                                      P.kw("server", "Configure IPv6 DHCP server on this interface",
                                           P.arg(P.WordArg("WORD"), "Name of IPv6 DHCP pool",
                                                 handler=lambda s, a, n: s.dhcp6_if(a[0], n), negatable=True),
                                           negatable=True), negatable=True))
        for mode in ("config", "config-if"):
            ik = next(x for x in t[mode] if x.word == "interface")
            if any(c.word == "range" for c in ik.children):
                continue
            ik.children.insert(0, P.kw("range", "interface range command",
                P.arg(P.LineArg("fa0/1 - 4 , gi0/1"), "Interface list", handler=lambda s, a, n: s.enter_range(a[0]))))
        if self.is_router:
            ipg2 = next(x for x in t["config"] if x.word == "ip")
            dh2 = next(x for x in ipg2.children if x.word == "dhcp")
            dh2.children.append(P.kw("relay", "DHCP relay agent parameters", P.kw("information",
                "DHCP relay agent information option", P.kw("trust-all",
                    "Received DHCP packets may contain relay info option with zero giaddr",
                    handler=lambda s, a, n: s.relay_trust_all(n), negatable=True), negatable=True), negatable=True))
            self._add_vrf_trees(t)
            self._add_isis_trees(t)
            self._add_mpls_trees(t)
            de3 = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
            t["config-router-eigrp"] = sorted(de3 + [
                P.kw("exit", "Exit from routing protocol configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("network", "Enable routing on an IP network",
                     P.arg(P.IpArg(), "Network number",
                           P.arg(P.IpArg("A.B.C.D"), "EIGRP wild card bits",
                                 handler=lambda s, a, n: s.eigrp_network(a[0], a[1], n), negatable=True),
                           handler=lambda s, a, n: s.eigrp_network(a[0], None, n), negatable=True), negatable=True),
                P.kw("passive-interface", "Suppress routing updates on an interface",
                     P.kw("default", "Suppress routing updates on all interfaces",
                          handler=lambda s, a, n: s.eigrp_set("passive", "default", n), negatable=True),
                     P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.eigrp_set("passive", a[0].name, n),
                           negatable=True), negatable=True),
                P.kw("eigrp", "EIGRP specific commands", P.kw("router-id", "router-id for this EIGRP process",
                     P.arg(P.IpArg(), "EIGRP Router-ID in IP address format",
                           handler=lambda s, a, n: s.eigrp_set("router_id", a[0], n), negatable=True), negatable=True)),
                P.kw("auto-summary", "Enable automatic network number summarization",
                     handler=lambda s, a, n: None, negatable=True),
                P.kw("redistribute", "Redistribute information from another routing protocol",
                     *[P.kw(w, h, P.arg(P.LineArg(), "options", handler=lambda s, a, n, w=w: s.eigrp_set("redistribute", w, n)),
                            handler=lambda s, a, n, w=w: s.eigrp_set("redistribute", w, n), negatable=True)
                       for w, h in (("static", "Static routes"), ("connected", "Connected"), ("ospf", "Open Shortest Path First"),
                                    ("rip", "Routing Information Protocol"))], negatable=True),
            ], key=lambda x: x.word or "")
            t["config-if"] = sorted(t["config-if"] + [
                P.kw("bandwidth", "Set bandwidth informational parameter",
                     P.arg(P.NumArg(1, 10000000, "<1-10000000>"), "Bandwidth in kilobits",
                           handler=lambda s, a, n: s.if_metric("eigrp_bw", a[0], n), negatable=True),
                     handler=lambda s, a, n: s.if_metric("eigrp_bw", None, True) if n else s.err_incomplete("", 0),
                     negatable=True),
                P.kw("delay", "Specify interface throughput delay",
                     P.arg(P.NumArg(1, 16777215, "<1-16777215>"), "Throughput delay (tens of microseconds)",
                           handler=lambda s, a, n: s.if_metric("eigrp_delay", a[0], n), negatable=True),
                     handler=lambda s, a, n: s.if_metric("eigrp_delay", None, True) if n else s.err_incomplete("", 0),
                     negatable=True)], key=lambda x: x.word or "")
            show_e = next(x for x in t["priv"] if x.word == "show")
            ipn = next(x for x in show_e.children if x.word == "ip")
            ipn.children.append(P.kw("eigrp", "IP-EIGRP show commands",
                P.kw("neighbors", "IP-EIGRP neighbors", handler=lambda s, a, n: s.show_eigrp("neighbors")),
                P.kw("topology", "IP-EIGRP Topology Table", handler=lambda s, a, n: s.show_eigrp("topology")),
                P.kw("interfaces", "IP-EIGRP interfaces", handler=lambda s, a, n: s.show_eigrp("interfaces"))))
            route_n = next((x for x in ipn.children if x.word == "route"), None)
            if route_n is not None:
                route_n.children.append(P.kw("eigrp", "Enhanced Interior Gateway Routing Protocol (EIGRP)",
                                             handler=lambda s, a, n: s.show_ip_route("D")))
        if not self.is_router:
            sp = next(x for x in t["config-if"] if x.word == "switchport")
            sp.children.append(P.kw("voice", "Voice appliance attributes",
                P.kw("vlan", "Vlan for voice traffic", P.arg(P.NumArg(1, 4094, "<1-4094>"), "Vlan for voice traffic",
                     handler=lambda s, a, n: s.voice_vlan(a[0], n), negatable=True),
                     handler=lambda s, a, n: s.voice_vlan(None, True) if n else s.err_incomplete("", 0),
                     negatable=True), negatable=True))
        if self.is_router:
            dhcp_mode = next((m for m in ("dhcp-config", "config-dhcp") if m in t), None)
            if dhcp_mode:
                t[dhcp_mode] = sorted(t[dhcp_mode] + [P.kw("option", "Raw DHCP options",
                    P.kw("150", "TFTP server (call manager)", P.kw("ip", "Data type IP address",
                         P.arg(P.IpArg(), "IP address", handler=lambda s, a, n: s.dhcp_attr("tftp", a[0], n),
                               negatable=True))), negatable=True)], key=lambda x: x.word or "")
            de2 = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
            t["config"] = sorted(t["config"] + [
                P.kw("telephony-service", "Configure Cisco Unified Communications Manager Express",
                     handler=lambda s, a, n: s.cme_mode(n), negatable=True),
                P.kw("ephone-dn", "Configure ephone-dn", P.arg(P.NumArg(1, 288, "<1-288>"), "ephone-dn tag",
                     handler=lambda s, a, n: s.cme_dn(a[0], n), negatable=True), negatable=True),
                P.kw("ephone", "Configure ephone", P.arg(P.NumArg(1, 240, "<1-240>"), "ephone tag",
                     handler=lambda s, a, n: s.cme_ephone(a[0], n), negatable=True), negatable=True)],
                key=lambda x: x.word or "")
            t["config-telephony"] = sorted(de2 + [
                P.kw("exit", "Exit from telephony-service configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("max-ephones", "Define max number of IP phones", P.arg(P.NumArg(1, 240, "<1-240>"), "Maximum phones",
                     handler=lambda s, a, n: s.cme_set("max_ephones", a[0]))),
                P.kw("max-dn", "Maximum number of directory numbers", P.arg(P.NumArg(1, 288, "<1-288>"), "DN",
                     P.arg(P.LineArg(), "options", handler=lambda s, a, n: s.cme_set("max_dn", a[0])),
                     handler=lambda s, a, n: s.cme_set("max_dn", a[0]))),
                P.kw("ip", "Define IP address and port for SCCP", P.kw("source-address", "Define source address",
                     P.arg(P.IpArg(), "IP address", P.kw("port", "Define port", P.arg(P.NumArg(2000, 9999, "<2000-9999>"),
                           "Port", handler=lambda s, a, n: s.cme_set("source", a[0]))),
                           handler=lambda s, a, n: s.cme_set("source", a[0])))),
                P.kw("auto", "Define auto", P.kw("assign", "Define dn range for auto assignment",
                     P.arg(P.NumArg(1, 288, "<1-288>"), "start", P.kw("to", "to", P.arg(P.NumArg(1, 288, "<1-288>"), "end",
                           handler=lambda s, a, n: s.cme_set("auto_assign", (a[0], a[1])))))), negatable=True),
                P.kw("create", "create cnf-files", P.kw("cnf-files", "Build XML configuration files",
                     handler=lambda s, a, n: "Creating CNF files")),
            ], key=lambda x: x.word or "")
            t["config-ephone-dn"] = sorted(de2 + [
                P.kw("exit", "Exit from ephone-dn configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("number", "Define DN number", P.arg(P.WordArg("WORD"), "A sequence of digits",
                     handler=lambda s, a, n: s.cme_number(a[0], n), negatable=True), negatable=True)],
                key=lambda x: x.word or "")
            t["config-ephone"] = sorted(de2 + [
                P.kw("exit", "Exit from ephone configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("mac-address", "define ethernet MAC address", P.arg(P.WordArg("H.H.H"), "ethernet MAC address",
                     handler=lambda s, a, n: s.cme_mac(a[0]))),
                P.kw("button", "define button", P.arg(P.WordArg("1:N"), "button and DN, e.g. 1:1",
                     handler=lambda s, a, n: s.cme_button(a[0])))], key=lambda x: x.word or "")
            show_v = next(x for x in t["priv"] if x.word == "show")
            show_v.children += [
                P.kw("ephone", "Show ephone status",
                     P.kw("registered", "Registered ephones", handler=lambda s, a, n: s.show_ephone(True)),
                     handler=lambda s, a, n: s.show_ephone(False)),
                P.kw("ephone-dn", "Show ephone-dn status", P.kw("summary", "Summary",
                     handler=lambda s, a, n: s.show_ephone_dn())),
                P.kw("telephony-service", "Show telephony-service", handler=lambda s, a, n: s.show_telephony())]
        if self.is_router:
            for mode in ("config", "config-if"):
                iface_kw = next(x for x in t[mode] if x.word == "interface")
                iface_kw.children.append(P.arg(P.VlanIfArg("Tunnel <0-2147483647>", word="tunnel", min_prefix=2, lo=0, hi=2147483647),
                                               "Tunnel interface", handler=lambda s, a, n: s.enter_tunnel(a[0], n),
                                               negatable=True))
            t["config-if"] = sorted(t["config-if"] + [P.kw("tunnel", "protocol-over-protocol tunneling",
                P.kw("source", "source of tunnel packets",
                     P.arg(P.IpArg(), "ip address", handler=lambda s, a, n: s.tun("source", a[0])),
                     P.arg(P.IfaceArg(), "interface", handler=lambda s, a, n: s.tun("source", a[0].name))),
                P.kw("destination", "destination of tunnel",
                     P.arg(P.IpArg(), "ip address", handler=lambda s, a, n: s.tun("destination", a[0]))),
                P.kw("mode", "tunnel encapsulation method",
                     P.kw("gre", "generic route encapsulation protocol",
                          P.kw("ip", "over IP", handler=lambda s, a, n: None), handler=lambda s, a, n: None)),
                P.kw("protection", "Enable tunnel protection",
                     P.kw("ipsec", "IPsec", P.kw("profile", "Determine the ipsec policy profile to use",
                          P.arg(P.WordArg("WORD"), "Name", handler=lambda s, a, n: s.tun_protect(a[0], n),
                                negatable=True), negatable=True), negatable=True), negatable=True),
                negatable=True)], key=lambda x: x.word or "")
            noop = lambda h: P.arg(P.LineArg(), h, handler=lambda s, a, n: None)
            t["config-isakmp"] = sorted([next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))] + [
                P.kw("exit", "Exit from ISAKMP policy mode", handler=lambda s, a, n: s.set_mode("config")),
                *[P.kw(w, h, noop(h)) for w, h in (("encryption", "Set encryption algorithm"), ("hash", "Set hash algorithm"),
                                                   ("authentication", "Set authentication method"),
                                                   ("group", "Set the Diffie-Hellman group"), ("lifetime", "Set lifetime"))]],
                key=lambda x: x.word or "")
            t["cfg-crypto-trans"] = sorted([next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))] + [
                P.kw("exit", "Exit from crypto transform configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("mode", "encapsulation mode (transport/tunnel)", noop("mode"))], key=lambda x: x.word or "")
            t["ipsec-profile"] = sorted([next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))] + [
                P.kw("exit", "Exit from crypto ipsec profile mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("set", "Specify an optional parameter", P.kw("transform-set", "Specify list of transform sets",
                     P.arg(P.WordArg("WORD"), "Transform set name", handler=lambda s, a, n: None)))],
                key=lambda x: x.word or "")
            crypto_new = P.kw("crypto", "Encryption module",
                P.kw("isakmp", "Configure ISAKMP policy",
                     P.kw("policy", "Set policy for an ISAKMP protection suite",
                          P.arg(P.NumArg(1, 10000, "<1-10000>"), "Priority", handler=lambda s, a, n: s.set_mode("config-isakmp"))),
                     P.kw("key", "Set pre-shared key for remote peer",
                          P.arg(P.WordArg("WORD"), "pre-shared key",
                                P.kw("address", "define shared key with IP address",
                                     P.arg(P.IpArg(), "Peer IP address", handler=lambda s, a, n: s.isakmp_key(a[1], a[0], n),
                                           negatable=True)), negatable=True), negatable=True)),
                P.kw("ipsec", "Configure IPSEC policy",
                     P.kw("transform-set", "Define transform and settings",
                          P.arg(P.WordArg("WORD"), "Transform set tag",
                                P.arg(P.LineArg("esp-aes 256 esp-sha-hmac"), "Transforms",
                                      handler=lambda s, a, n: s.set_mode("cfg-crypto-trans")))),
                     P.kw("profile", "Configure an ipsec policy profile",
                          P.arg(P.WordArg("WORD"), "Profile name", handler=lambda s, a, n: s.set_mode("ipsec-profile")))),
                negatable=True)
            crypto_old = next((x for x in t["config"] if x.word == "crypto"), None)
            if crypto_old is None:
                t["config"] = sorted(t["config"] + [crypto_new], key=lambda x: x.word or "")
            else:
                crypto_old.children += crypto_new.children
                crypto_old.negatable = True
            showc = next(x for x in t["priv"] if x.word == "show")
            showc.children.append(P.kw("crypto", "Encryption module",
                P.kw("ipsec", "IPSEC information", P.kw("sa", "IPSEC SA table", handler=lambda s, a, n: s.show_ipsec_sa())),
                P.kw("isakmp", "ISAKMP information", P.kw("sa", "ISAKMP SA table", handler=lambda s, a, n: s.show_isakmp_sa()))))
            de = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
            cname = lambda: P.arg(P.WordArg("WORD"), "class-map name", handler=lambda s, a, n: s.cmap(a[-1], n), negatable=True)
            t["config"] = sorted(t["config"] + [
                P.kw("class-map", "Configure Class Map",
                     P.kw("match-any", "Logical-OR all matching statements under this classmap", cname()),
                     P.kw("match-all", "Logical-AND all matching statements under this classmap", cname()),
                     cname(), negatable=True),
                P.kw("policy-map", "Configure QoS Policy Map",
                     P.arg(P.WordArg("WORD"), "policy-map name", handler=lambda s, a, n: s.pmap(a[0], n), negatable=True),
                     negatable=True)], key=lambda x: x.word or "")
            dscp_arg = lambda h: P.arg(P.WordArg("<0-63>|ef|af41|cs3|default"), "DSCP value", handler=h, negatable=True)
            t["config-cmap"] = sorted(de + [
                P.kw("exit", "Exit from class-map configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("match", "classification criteria",
                     P.kw("access-group", "Access group",
                          P.kw("name", "Named Access List", P.arg(P.WordArg("WORD"), "Access List name",
                               handler=lambda s, a, n: s.cmatch("acl", a[0], n), negatable=True)),
                          P.arg(P.WordArg("<1-2799>"), "Access list index", handler=lambda s, a, n: s.cmatch("acl", a[0], n),
                                negatable=True)),
                     P.kw("protocol", "Protocol", *[P.kw(w, w.upper(), handler=lambda s, a, n, w=w: s.cmatch("protocol", w, n),
                                                         negatable=True) for w in ("icmp", "tcp", "udp", "ospf")]),
                     P.kw("dscp", "Match DSCP in IP(v4) and IPv6 packets",
                          dscp_arg(lambda s, a, n: s.cmatch("dscp", a[0], n))),
                     P.kw("ip", "IP specific values", P.kw("dscp", "Match IP DSCP",
                          dscp_arg(lambda s, a, n: s.cmatch("dscp", a[0], n)))),
                     P.kw("any", "Any packets", handler=lambda s, a, n: s.cmatch("any", True, n), negatable=True),
                     negatable=True)], key=lambda x: x.word or "")
            t["config-pmap"] = sorted(de + [
                P.kw("exit", "Exit from policy-map configuration mode", handler=lambda s, a, n: s.set_mode("config")),
                P.kw("class", "policy criteria",
                     P.arg(P.WordArg("WORD|class-default"), "class-map name", handler=lambda s, a, n: s.pclass(a[0], n),
                           negatable=True), negatable=True)], key=lambda x: x.word or "")
            t["config-pmap-c"] = sorted(de + [
                P.kw("exit", "Exit from QoS class action configuration mode", handler=lambda s, a, n: s.set_mode("config-pmap")),
                P.kw("set", "Set QoS values",
                     P.kw("dscp", "Set DSCP in IP(v4) and IPv6 packets", dscp_arg(lambda s, a, n: s.pset("dscp", a[0], n))),
                     P.kw("ip", "Set IP specific values", P.kw("dscp", "Set IP DSCP",
                          dscp_arg(lambda s, a, n: s.pset("dscp", a[0], n)))), negatable=True),
                P.kw("police", "Police",
                     P.kw("cir", "Committed information rate", P.arg(P.NumArg(8000, 10000000000, "<8000-10000000000>"),
                          "Bits per second", handler=lambda s, a, n: s.pset("police", a[0], n), negatable=True)),
                     P.arg(P.NumArg(8000, 10000000000, "<8000-10000000000>"), "Bits per second",
                           P.arg(P.LineArg(), "burst / actions", handler=lambda s, a, n: s.pset("police", a[0], n)),
                           handler=lambda s, a, n: s.pset("police", a[0], n), negatable=True),
                     negatable=True)], key=lambda x: x.word or "")
            t["config-if"] = sorted(t["config-if"] + [P.kw("service-policy", "Configure CPL Service Policy",
                P.kw("input", "Assign policy-map to the input of an interface",
                     P.arg(P.WordArg("WORD"), "policy-map name", handler=lambda s, a, n: s.svc_policy("in", a[0], n), negatable=True)),
                P.kw("output", "Assign policy-map to the output of an interface",
                     P.arg(P.WordArg("WORD"), "policy-map name", handler=lambda s, a, n: s.svc_policy("out", a[0], n), negatable=True)),
                negatable=True)], key=lambda x: x.word or "")
            showq = next(x for x in t["priv"] if x.word == "show")
            showq.children.append(P.kw("policy-map", "Show QoS Policy Map",
                P.kw("interface", "Show Qos Policy Map on interfaces",
                     P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.show_pmap_if(a[0])),
                     handler=lambda s, a, n: s.show_pmap_if(None)),
                handler=lambda s, a, n: s.show_pmaps()))
            t["config"] = sorted(t["config"] + [P.kw("nat64", "NAT64 configuration",
                P.kw("prefix", "Prefix configuration",
                     P.kw("stateful", "Stateful prefix", P.arg(P.Ipv6PrefixArg(), "Stateful prefix (/96)",
                          handler=lambda s, a, n: s.nat64_prefix(a[0], n), negatable=True), negatable=True),
                     negatable=True),
                P.kw("v4", "IPv4 configuration", P.kw("pool", "IPv4 address pool",
                     P.arg(P.WordArg("WORD"), "Pool name", P.arg(P.IpArg(), "Start", P.arg(P.IpArg(), "End",
                           handler=lambda s, a, n: None))))),
                P.kw("v6v4", "IPv6 to IPv4 mapping", P.arg(P.LineArg("list ACL pool POOL overload"), "",
                                                           handler=lambda s, a, n: None)),
                negatable=True)], key=lambda x: x.word or "")
            t["config-if"] = sorted(t["config-if"] + [P.kw("nat64", "NAT64 interface configuration",
                P.kw("enable", "Enable NAT64 on the interface", handler=lambda s, a, n: s.nat64_if(n), negatable=True),
                negatable=True)], key=lambda x: x.word or "")
            show = next(x for x in t["priv"] if x.word == "show")
            show.children.append(P.kw("nat64", "NAT64 information",
                                      P.kw("translations", "Translations", handler=lambda s, a, n: s.show_nat64())))
        from .base import mgmt_add, mgmt_logging_text, mgmt_ntp_assoc, mgmt_ntp_status
        from ..services.mgmt import SEVERITY
        mg = lambda what: (lambda s, a, n: mgmt_add(s.device, what, a[0] if a else None, n))
        t["config"] = sorted(t["config"] + [
            P.kw("logging", "Modify message logging facilities",
                 P.kw("host", "Set syslog server IP address and parameters",
                      P.arg(P.IpArg(), "IP address of the syslog server", handler=mg("syslog"), negatable=True),
                      negatable=True),
                 P.kw("trap", "Set syslog server logging level",
                      *[P.kw(w, f"{w.capitalize()} conditions ({i})", handler=lambda s, a, n, i=i: mgmt_add(
                          s.device, "trap", i, n), negatable=True) for i, w in enumerate(SEVERITY)], negatable=True),
                 P.kw("buffered", "Set buffered logging parameters", handler=lambda s, a, n: None, negatable=True),
                 P.arg(P.IpArg(), "IP address of the logging host", handler=mg("syslog"), negatable=True),
                 negatable=True),
            P.kw("snmp-server", "Modify SNMP engine parameters",
                 P.kw("community", "Enable SNMP; set community string and access privs",
                      P.arg(P.WordArg("WORD"), "SNMP community string",
                            P.kw("RO", "Read-only access with this community string",
                                 handler=lambda s, a, n: mgmt_add(s.device, "ro", a[0], n), negatable=True),
                            P.kw("RW", "Read-write access with this community string",
                                 handler=lambda s, a, n: mgmt_add(s.device, "rw", a[0], n), negatable=True),
                            handler=lambda s, a, n: mgmt_add(s.device, "ro", a[0], n), negatable=True), negatable=True),
                 P.kw("location", "Text for mib object sysLocation",
                      P.arg(P.LineArg(), "The physical location of this node", handler=mg("location"), negatable=True),
                      handler=mg("location"), negatable=True),
                 P.kw("contact", "Text for mib object sysContact",
                      P.arg(P.LineArg(), "identification of the contact person", handler=mg("contact"), negatable=True),
                      handler=mg("contact"), negatable=True),
                 P.kw("enable", "Enable traps", P.arg(P.LineArg(), "traps", handler=lambda s, a, n: None, negatable=True)),
                 negatable=True),
            P.kw("ntp", "Configure NTP",
                 P.kw("server", "Configure NTP server",
                      P.arg(P.IpArg(), "IP address of peer", handler=mg("ntp"), negatable=True), negatable=True),
                 P.kw("master", "Act as NTP master clock",
                      P.arg(P.NumArg(1, 15, "<1-15>"), "Stratum number", handler=mg("ntp_master"), negatable=True),
                      handler=lambda s, a, n: mgmt_add(s.device, "ntp_master", None, n), negatable=True),
                 negatable=True)], key=lambda x: x.word or "")
        show_m = next(x for x in t["priv"] if x.word == "show")
        show_m.children += [
            P.kw("logging", "Show the contents of logging buffers", handler=lambda s, a, n: mgmt_logging_text(s.device)),
            P.kw("ntp", "Network time protocol",
                 P.kw("status", "NTP status", handler=lambda s, a, n: mgmt_ntp_status(s.device)),
                 P.kw("associations", "NTP associations", handler=lambda s, a, n: mgmt_ntp_assoc(s.device))),
            P.kw("snmp", "snmp statistics",
                 P.kw("community", "show snmp communities", handler=lambda s, a, n: "\n\n".join(
                     f"Community name: {c}\nCommunity Index: {c}\nCommunity SecurityName: {c}\nstorage-type: "
                     f"nonvolatile\t active\naccess: {'read-write' if v == 'rw' else 'read-only'}"
                     for c, v in s.device.mgmt.communities.items())),
                 handler=lambda s, a, n: f"Chassis: NETEMU0001\nContact: {s.device.mgmt.contact}\n"
                                         f"Location: {s.device.mgmt.location}\nSNMP agent enabled"
                 if s.device.mgmt.communities else "%SNMP agent not enabled")]
        t["config-dhcpv6"] = sorted([
            next(x for x in t["config"] if x.word == "do"), next(x for x in t["config"] if x.word == "end"),
            P.kw("exit", "Exit from DHCPv6 configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("address", "Address prefix for address assignment",
                 P.kw("prefix", "IPv6 address prefix",
                      P.arg(P.Ipv6PrefixArg(), "IPv6 x:x::y/<z>", handler=lambda s, a, n: s.dhcp6_attr("prefix", a[0])))),
            P.kw("dns-server", "DNS servers", P.arg(P.Ipv6Arg(), "DNS server address",
                                                   handler=lambda s, a, n: s.dhcp6_attr("dns", a[0]))),
        ], key=lambda x: x.word or "")
        show6 = next((x for x in next(y for y in t["priv"] if y.word == "show").children if x.word == "ipv6"), None)
        if show6 is not None:
            show6.children.append(P.kw("dhcp", "IPv6 DHCP",
                                       P.kw("binding", "Show DHCPv6 bindings", handler=lambda s, a, n: s.show_dhcp6())))
        t["config-rtr6"] = sorted([
            next(x for x in t["config"] if x.word == "do"), next(x for x in t["config"] if x.word == "end"),
            P.kw("exit", "Exit from IPv6 routing protocol configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("router-id", "router-id for this OSPF process",
                 P.arg(P.IpArg(), "OSPF router-id in IP address format", handler=lambda s, a, n: s.v6igp_rid(a[0]))),
            P.kw("eigrp", "EIGRP specific commands",
                 P.kw("router-id", "router id for this EIGRP process",
                      P.arg(P.IpArg(), "EIGRP Router-ID in IP address format",
                            handler=lambda s, a, n: s.v6igp_rid(a[0])))),
            P.kw("shutdown", "Shutdown this instance of EIGRP", handler=lambda s, a, n: None, negatable=True),
        ], key=lambda x: x.word or "")
        show6 = next((x for x in next(y for y in t["priv"] if y.word == "show").children if x.word == "ipv6"), None)
        if show6 is not None:
            show6.children += [
                P.kw("ospf", "OSPF information",
                     P.kw("neighbor", "Neighbor list", handler=lambda s, a, n: s.show_v6igp_nb("ospf6")),
                     P.kw("database", "Database summary", handler=lambda s, a, n: __import__(
                         "netemu.core.cli.base", fromlist=["x"]).ospf6_database_text(s.device))),
                P.kw("rip", "RIP routing protocol status", handler=lambda s, a, n: s.show_v6igp_nb("ripng")),
                P.kw("eigrp", "Show IPv6 EIGRP",
                     P.kw("neighbors", "IPv6 EIGRP neighbors", handler=lambda s, a, n: s.show_v6igp_nb("eigrp6")))]
            r6 = next((x for x in show6.children if x.word == "route"), None)
            if r6 is not None:
                r6.children += [P.kw(w, h, handler=lambda s, a, n, c=c: s.show_ipv6_route(c)) for w, h, c in
                                (("eigrp", "EIGRP routes", "D"), ("isis", "IS-IS routes", "I"),
                                 ("ospf", "OSPF routes", "O"), ("static", "Static routes", "S"),
                                 ("connected", "Connected routes", "C"))
                                if not any(x.word == w for x in r6.children)]
        t["config-if"] = sorted(t["config-if"] + [
            P.kw("standby", "HSRP interface configuration commands", grp("hsrp", 0), negatable=True),
            P.kw("vrrp", "VRRP Interface configuration commands", grp("vrrp", 1), negatable=True),
        ], key=lambda x: x.word or "")
        show = next(x for x in t["priv"] if x.word == "show")
        show.children += [
            P.kw("standby", "Hot standby protocol information",
                 P.kw("brief", "Brief output", handler=lambda s, a, n: s.show_fhrp("hsrp", True)),
                 handler=lambda s, a, n: s.show_fhrp("hsrp", False)),
            P.kw("vrrp", "VRRP information",
                 P.kw("brief", "Brief output", handler=lambda s, a, n: s.show_fhrp("vrrp", True)),
                 handler=lambda s, a, n: s.show_fhrp("vrrp", False))]
        if not self.is_router:
            swp = next(x for x in t["config-if"] if x.word == "switchport")
            swp.children.append(P.kw(
                "port-security", "Security related command",
                P.kw("maximum", "Max secure addresses",
                     P.arg(P.NumArg(1, 132, "<1-132>"), "Maximum addresses",
                           handler=lambda s, a, n: s.psec("max", a[0], n), negatable=True), negatable=True),
                P.kw("violation", "Security violation mode",
                     *[P.kw(m, h, handler=lambda s, a, n, m=m: s.psec("violation", m, n), negatable=True)
                       for m, h in (("protect", "Security violation protect mode"),
                                    ("restrict", "Security violation restrict mode"),
                                    ("shutdown", "Security violation shutdown mode"))],
                     negatable=True),
                P.kw("mac-address", "Secure mac address",
                     P.kw("sticky", "Configure dynamic secure addresses as sticky",
                          handler=lambda s, a, n: s.psec("sticky", None, n), negatable=True),
                     P.arg(P.WordArg("H.H.H"), "48 bit mac address",
                           handler=lambda s, a, n: s.psec("mac", a[0], n), negatable=True),
                     negatable=True),
                handler=lambda s, a, n: s.psec("enable", None, n), negatable=True))
            ipif = next(x for x in t["config-if"] if x.word == "ip")
            ipif.children.append(P.kw("dhcp", "DHCP", P.kw("snooping", "DHCP Snooping",
                                      P.kw("trust", "DHCP Snooping trust config",
                                           handler=lambda s, a, n: s.snoop_trust(n), negatable=True),
                                      negatable=True), negatable=True))
            ipg = next(x for x in t["config"] if x.word == "ip")
            dh = next(x for x in ipg.children if x.word == "dhcp")
            dh.children.append(P.kw("snooping", "DHCP Snooping",
                                    P.kw("information", "DHCP Snooping information", P.kw("option",
                                         "DHCP Snooping information option (option 82)",
                                         handler=lambda s, a, n: s.snoop_opt82(not n), negatable=True),
                                         negatable=True),
                                    P.kw("vlan", "DHCP Snooping vlan",
                                         P.arg(P.VlanListArg(), "DHCP Snooping vlan range",
                                               handler=lambda s, a, n: s.snoop_vlan(a[0], n), negatable=True),
                                         negatable=True),
                                    handler=lambda s, a, n: s.snoop_enable(not n), negatable=True))
            show = next(x for x in t["priv"] if x.word == "show")
            show.children.append(P.kw("port-security", "Show secure port information",
                                      P.kw("interface", "Show secure interface",
                                           P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.show_psec(a[0]))),
                                      P.kw("address", "Show secure address", handler=lambda s, a, n: s.show_psec_addr()),
                                      handler=lambda s, a, n: s.show_psec(None)))
            ipshow = next(x for x in show.children if x.word == "ip")
            snoop = (P.kw("snooping", "DHCP snooping",
                                             P.kw("binding", "DHCP snooping binding",
                                                  handler=lambda s, a, n: s.show_snoop(True)),
                                             handler=lambda s, a, n: s.show_snoop(False)))
            dhs = next((x for x in ipshow.children if x.word == "dhcp"), None)
            if dhs is None:
                ipshow.children.append(P.kw("dhcp", "Show items in the DHCP database", snoop))
            else:
                dhs.children.append(snoop)
            modes = [P.kw(m, h, handler=lambda s, a, n, m=m: s.channel_group(a[0], m, False))
                     for m, h in (("active", "Enable LACP unconditionally"), ("passive", "Enable LACP only if a LACP device is detected"),
                                  ("on", "Enable Etherchannel only"), ("desirable", "Enable PAgP unconditionally"),
                                  ("auto", "Enable PAgP only if a PAgP device is detected"))]
            t["config-if"] = sorted(t["config-if"] + [
                P.kw("channel-group", "Etherchannel/port bundling configuration",
                     P.arg(P.NumArg(1, 48, "<1-48>"), "Channel group number",
                           P.kw("mode", "Etherchannel Mode of the interface", *modes),
                           handler=lambda s, a, n: s.channel_group(None, None, True) if n else s.err_incomplete("", 0),
                           negatable=True),
                     handler=lambda s, a, n: s.channel_group(None, None, True) if n else s.err_incomplete("", 0),
                     negatable=True)], key=lambda x: x.word or "")
            show = next(x for x in t["priv"] if x.word == "show")
            show.children.append(P.kw("etherchannel", "EtherChannel information",
                                      P.kw("summary", "One-line summary per channel-group",
                                           handler=lambda s, a, n: s.show_etherchannel())))
            for mode in ("config", "config-if"):
                iface = next(x for x in t[mode] if x.word == "interface")
                iface.children.append(P.arg(P.VlanIfArg("Port-channel <1-48>", word="port-channel", min_prefix=2),
                                            "Ethernet Channel of interfaces",
                                            handler=lambda s, a, n: s.enter_po(a[0], n), negatable=True))
        if self.is_router:
            self._add_zbf_trees(t)          # after the QoS class-map / policy-map nodes exist
        self._add_aaa_trees(t)

    def _build_trees_base(self) -> None:
        iface = lambda: P.IfaceArg()

        def show_tree(user: bool) -> P.Node:
            ip_children = [P.kw("interface", "IP interface status and configuration",
                                P.kw("brief", "Brief summary of IP status and configuration",
                                     handler=lambda s, a, n: s.show_ip_int_brief()))]
            if True:
                ip_children.insert(0, P.kw("route", "IP routing table",
                                           P.kw("ospf", "Open Shortest Path First (OSPF)",
                                                handler=lambda s, a, n: s.show_ip_route("O")),
                                           P.kw("static", "Static routes",
                                                handler=lambda s, a, n: s.show_ip_route("S")),
                                           P.kw("connected", "Connected networks",
                                                handler=lambda s, a, n: s.show_ip_route("C")),
                                           P.kw("bgp", "Border Gateway Protocol (BGP)",
                                                handler=lambda s, a, n: s.show_ip_route("B")),
                                           P.kw("rip", "Routing Information Protocol (RIP)",
                                                handler=lambda s, a, n: s.show_ip_route("R")),
                                           handler=lambda s, a, n: s.show_ip_route()))
                ip_children.append(P.kw("ospf", "OSPF information",
                                        P.kw("neighbor", "Neighbor list",
                                             P.kw("detail", "detail of all neighbors",
                                                  handler=lambda s, a, n: s.show_ospf_neighbor(True)),
                                             handler=lambda s, a, n: s.show_ospf_neighbor(False)),
                                        P.kw("interface", "Interface information",
                                             P.kw("brief", "Brief summary of OSPF interfaces",
                                                  handler=lambda s, a, n: s.show_ospf_interface(True)),
                                             handler=lambda s, a, n: s.show_ospf_interface(False)),
                                        P.kw("database", "Database summary",
                                             handler=lambda s, a, n: s.show_ospf_database()),
                                        handler=lambda s, a, n: s.show_ospf()))
                ip_children.append(P.kw("protocols", "IP routing protocol process parameters and statistics",
                                        handler=lambda s, a, n: s.show_ip_protocols()))
                ip_children.append(P.kw("rip", "IP RIP show commands",
                                        P.kw("database", "IP RIP database", handler=lambda s, a, n: s.show_rip_db())))
                ip_children.append(P.kw("dhcp", "Show items in the DHCP database",
                                        P.kw("binding", "DHCP address bindings", handler=lambda s, a, n: s.show_dhcp_binding()),
                                        P.kw("pool", "DHCP pools information", handler=lambda s, a, n: s.show_dhcp_pool())))
                ip_children.append(P.kw("bgp", "BGP information",
                                        P.kw("summary", "Summary of BGP neighbor status",
                                             handler=lambda s, a, n: s.show_bgp_summary()),
                                        P.kw("neighbors", "Detailed information on TCP and BGP neighbor connections",
                                             P.arg(P.IpArg(), "Neighbor to display information about",
                                                   handler=lambda s, a, n: s.show_bgp_neighbors(a[0])),
                                             handler=lambda s, a, n: s.show_bgp_neighbors(None)),
                                        handler=lambda s, a, n: s.show_bgp()))
                ip_children.append(P.kw("nat", "IP NAT information",
                                        P.kw("translations", "Translation entries",
                                             P.kw("verbose", "Show extra information",
                                                  handler=lambda s, a, n: s.show_nat_translations()),
                                             handler=lambda s, a, n: s.show_nat_translations()),
                                        P.kw("statistics", "Translation statistics",
                                             handler=lambda s, a, n: s.show_nat_statistics())))
                ip_children.append(P.kw("access-lists", "List IP access lists",
                                        P.arg(P.WordArg("<1-2699>|WORD"), "Access list number or name",
                                              handler=lambda s, a, n: s.show_acls(a[0])),
                                        handler=lambda s, a, n: s.show_acls(None)))
                ip_children.append(P.kw("arp", "IP ARP table",
                                        handler=lambda s, a, n: s.show_arp()))
            items = [
                *([P.kw("access-lists", "List access lists",
                        P.arg(P.WordArg("<1-2699>|WORD"), "Access list number or name",
                              handler=lambda s, a, n: s.show_acls(a[0])),
                        handler=lambda s, a, n: s.show_acls(None))]),
                P.kw("arp", "Arp table", handler=lambda s, a, n: s.show_arp()),
                P.kw("interfaces", "Interface status and configuration",
                     P.arg(iface(), "Interface name", handler=lambda s, a, n: s.show_interfaces(a[0])),
                     *([P.kw("status", "Show interface line status",
                             handler=lambda s, a, n: s.show_int_status()),
                        P.kw("trunk", "Show interface trunk information",
                             handler=lambda s, a, n: s.show_int_trunk())] if not self.is_router else []),
                     handler=lambda s, a, n: s.show_interfaces(None)),
                P.kw("ip", "IP information", *ip_children),
                P.kw("version", "System hardware and software status",
                     handler=lambda s, a, n: s.show_version()),
                P.kw("clock", "Display the system clock", handler=lambda s, a, n: s.show_clock()),
                P.kw("history", "Display the session command history",
                     handler=lambda s, a, n: ""),
            ]
            if True:
                items.append(P.kw("ipv6", "IPv6 information",
                                  P.kw("interface", "IPv6 interface status and configuration",
                                       P.kw("brief", "Brief summary of IPv6 status and configuration",
                                            handler=lambda s, a, n: s.show_ipv6_brief())),
                                  P.kw("route", "Show IPv6 route table entries", handler=lambda s, a, n: s.show_ipv6_route()),
                                  P.kw("neighbors", "Show IPv6 neighbor cache entries",
                                       handler=lambda s, a, n: s.show_ipv6_neighbors())))
            if not self.is_router:
                items.append(P.kw("spanning-tree", "Spanning tree topology",
                                  P.kw("vlan", "VLAN Switch Spanning Trees",
                                       P.arg(P.NumArg(1, 4094, "<1-4094>"), "VLAN",
                                             handler=lambda s, a, n: s.show_stp(a[0]))),
                                  handler=lambda s, a, n: s.show_stp(None)))
                items.append(P.kw("vlan", "VTP VLAN status",
                                  P.kw("brief", "VTP all VLAN status in brief",
                                       handler=lambda s, a, n: s.show_vlan(True)),
                                  handler=lambda s, a, n: s.show_vlan(False)))
                items.insert(1, P.kw("mac", "MAC configuration",
                                     P.kw("address-table", "MAC forwarding table",
                                          handler=lambda s, a, n: s.show_mac_table())))
                items.insert(1, P.kw("mac-address-table", "MAC forwarding table",
                                     handler=lambda s, a, n: s.show_mac_table(), hidden=True))
            if not user:
                items += [
                    P.kw("running-config", "Current operating configuration",
                         handler=lambda s, a, n: s.show_running()),
                    P.kw("startup-config", "Contents of startup configuration",
                         handler=lambda s, a, n: s.show_startup()),
                ]
            items.sort(key=lambda x: x.word)
            return P.kw("show", "Show running system information", *items)

        ping = P.kw("ping", "Send echo messages",
                    P.kw("ipv6", "IPv6 echo", P.arg(P.Ipv6Arg(), "Ping destination address",
                                                    handler=lambda s, a, n: s.ping(a[0]))),
                    P.arg(P.AnyIpArg(), "Ping destination address",
                          P.kw("repeat", "Specify repeat count",
                               P.arg(P.NumArg(1, 2147483647, "<1-2147483647>"), "Repeat count",
                                     handler=lambda s, a, n: s.ping(a[0], a[1]))),
                          handler=lambda s, a, n: s.ping(a[0])))
        trace = P.kw("traceroute", "Trace route to destination",
                     P.kw("ipv6", "IPv6 trace", P.arg(P.Ipv6Arg(), "Trace route to destination address",
                                                      handler=lambda s, a, n: s.traceroute(a[0]))),
                     P.arg(P.AnyIpArg(), "Trace route to destination address",
                           handler=lambda s, a, n: s.traceroute(a[0])))
        exit_ = P.kw("exit", "Exit from the EXEC", handler=lambda s, a, n: s.logout())
        logout = P.kw("logout", "Exit from the EXEC", handler=lambda s, a, n: s.logout())

        user = [
            P.kw("enable", "Turn on privileged commands", handler=lambda s, a, n: s.set_mode("priv")),
            exit_, logout, ping, show_tree(True), trace,
        ]
        clear_items = [P.kw("arp-cache", "Clear the entire ARP cache",
                            handler=lambda s, a, n: s.clear_arp()),
                       P.kw("ip", "IP",
                            P.kw("dhcp", "Delete items from the DHCP database",
                                 P.kw("binding", "DHCP address bindings",
                                      P.kw("*", "Clear all automatic bindings",
                                           handler=lambda s, a, n: s.clear_dhcp()))),
                            P.kw("bgp", "Clear BGP connections",
                                 P.kw("*", "Clear all peers", handler=lambda s, a, n: s.clear_bgp(None)),
                                 P.arg(P.IpArg(), "BGP neighbor address to clear",
                                       handler=lambda s, a, n: s.clear_bgp(a[0]))),
                            P.kw("nat", "Clear NAT",
                                 P.kw("translation", "Clear dynamic translation",
                                      P.kw("*", "Delete all dynamic translations",
                                           handler=lambda s, a, n: s.clear_nat())))),
                       P.kw("access-list", "Clear access list statistical information",
                            P.kw("counters", "Clear access list counters",
                                 handler=lambda s, a, n: s.clear_acl_counters()))] if self.is_router else [
            P.kw("mac-address-table", "MAC forwarding table",
                 P.kw("dynamic", "dynamic entry type", handler=lambda s, a, n: s.clear_mac()),
                 handler=lambda s, a, n: s.clear_mac()),
            P.kw("mac", "MAC configuration",
                 P.kw("address-table", "MAC forwarding table",
                      P.kw("dynamic", "dynamic entry type", handler=lambda s, a, n: s.clear_mac()),
                      handler=lambda s, a, n: s.clear_mac())),
        ]
        priv = [
            P.kw("clear", "Reset functions", *clear_items),
            P.kw("configure", "Enter configuration mode",
                 P.kw("terminal", "Configure from the terminal",
                      handler=lambda s, a, n: s.enter_config()),
                 handler=lambda s, a, n: s.enter_config()),
            P.kw("copy", "Copy from one file to another",
                 P.kw("running-config", "Copy from current system configuration",
                      P.kw("startup-config", "Copy to startup configuration",
                           handler=lambda s, a, n: s.write_mem(True)),
                      P.arg(P.WordArg("tftp: | ftp: | tftp://A.B.C.D/file | ftp://user:pass@A.B.C.D/file"),
                            "Copy to a remote server", handler=lambda s, a, n: s.copy_out("running", a[0]))),
                 P.kw("startup-config", "Copy from startup configuration",
                      P.arg(P.WordArg("tftp: | ftp: | URL"), "Copy to a remote server",
                            handler=lambda s, a, n: s.copy_out("startup", a[0]))),
                 P.arg(P.WordArg("tftp: | ftp: | tftp://A.B.C.D/file"), "Copy from a remote server",
                       P.kw("running-config", "Update (merge with) current system configuration",
                            handler=lambda s, a, n: s.copy_in(a[0], "running")),
                       P.kw("startup-config", "Copy to startup configuration",
                            handler=lambda s, a, n: s.copy_in(a[0], "startup")))),
            P.kw("disable", "Turn off privileged commands",
                 handler=lambda s, a, n: s.set_mode("user")),
            P.kw("enable", "Turn on privileged commands", handler=lambda s, a, n: None),
            P.kw("erase", "Erase a filesystem",
                 P.kw("startup-config", "Erase contents of configuration memory",
                      handler=lambda s, a, n: s.erase_startup())),
            exit_, logout, ping,
            P.kw("reload", "Halt and perform a cold restart", handler=lambda s, a, n: s.reload()),
            show_tree(False), trace,
            P.kw("write", "Write running configuration to memory",
                 P.kw("memory", "Write to NV memory", handler=lambda s, a, n: s.write_mem()),
                 P.kw("erase", "Erase NV memory", handler=lambda s, a, n: s.erase_startup()),
                 handler=lambda s, a, n: s.write_mem()),
        ]
        do = P.kw("do", "To run exec commands in config mode",
                  P.arg(P.LineArg(), "Exec Command", handler=lambda s, a, n: s.do(a[0])))
        end = P.kw("end", "Exit from configure mode", handler=lambda s, a, n: s.ctrl_z().strip("\n") or None)
        interface = P.kw("interface", "Select an interface to configure",
                         P.arg(iface(), "Interface name",
                               handler=lambda s, a, n: s.enter_iface(a[0], n), negatable=True),
                         *([P.arg(P.SubIfaceArg(), "Subinterface number",
                                  handler=lambda s, a, n: s.enter_subif(a[0], n), negatable=True),
                            P.arg(P.LoopbackArg(), "Loopback interface",
                                  handler=lambda s, a, n: s.enter_loopback(a[0], n), negatable=True)]
                           if self.is_router else [
                               P.arg(P.VlanIfArg(), "Vlan interface",
                                     handler=lambda s, a, n: s.enter_svi(a[0], n), negatable=True),
                               P.arg(P.LoopbackArg(), "Loopback interface",
                                     handler=lambda s, a, n: s.enter_loopback(a[0], n), negatable=True)]),
                         negatable=True)
        config = [
            do, end,
            P.kw("exit", "Exit from configure mode", handler=lambda s, a, n: s.set_mode("priv")),
            P.kw("hostname", "Set system's network name",
                 P.arg(P.WordArg(), "This system's network name",
                       handler=lambda s, a, n: s.set_hostname(a[0], n), negatable=True),
                 handler=lambda s, a, n: s.set_hostname(None, n) if n else s.err_incomplete("", 0),
                 negatable=True),
            interface,
        ]
        if not self.is_router:
            config.append(P.kw("spanning-tree", "Spanning Tree Subsystem",
                               P.kw("mode", "Spanning tree operating mode",
                                    P.kw("pvst", "Per-Vlan spanning tree mode", handler=lambda s, a, n: s.stp_mode("pvst")),
                                    P.kw("rapid-pvst", "Per-Vlan rapid spanning tree mode",
                                         handler=lambda s, a, n: s.stp_mode("rapid-pvst"))),
                               P.kw("vlan", "VLAN Switch Spanning Tree",
                                    P.arg(P.VlanListArg(), "Spanning tree VLAN id(s)",
                                          P.kw("priority", "Set the bridge priority for the spanning tree",
                                               P.arg(P.NumArg(0, 61440, "<0-61440>"), "bridge priority in increments of 4096",
                                                     handler=lambda s, a, n: s.stp_priority(a[1], n), negatable=True),
                                               negatable=True),
                                          P.kw("root", "Configure switch as root",
                                               P.kw("primary", "Configure this switch as primary root for this spanning tree",
                                                    handler=lambda s, a, n: s.stp_root(True, n), negatable=True),
                                               P.kw("secondary", "Configure switch as secondary root",
                                                    handler=lambda s, a, n: s.stp_root(False, n), negatable=True),
                                               negatable=True),
                                          handler=lambda s, a, n: s.stp_enable(not n), negatable=True),
                                    negatable=True),
                               negatable=True))
            config.append(P.kw("vlan", "VLAN commands",
                               P.arg(P.VlanListArg("<1-4094>"), "ISL VLAN IDs 1-1005",
                                     handler=lambda s, a, n: s.vlan_cmd(a[0], n), negatable=True),
                               negatable=True))
        if True:
            route_tail = [
                P.arg(P.IpArg(), "Forwarding router's address",
                      P.arg(P.NumArg(1, 255), "Distance metric for this route",
                            handler=lambda s, a, n: s.ip_route(a[0], a[1], a[2], None, a[3], n),
                            negatable=True),
                      handler=lambda s, a, n: s.ip_route(a[0], a[1], a[2], None, 1, n),
                      negatable=True),
                P.arg(iface(), "Exit interface",
                      P.arg(P.IpArg(), "Forwarding router's address",
                            handler=lambda s, a, n: s.ip_route(a[0], a[1], a[3], a[2], 1, n),
                            negatable=True),
                      P.arg(P.NumArg(1, 255), "Distance metric for this route",
                            handler=lambda s, a, n: s.ip_route(a[0], a[1], None, a[2], a[3], n),
                            negatable=True),
                      handler=lambda s, a, n: s.ip_route(a[0], a[1], None, a[2], 1, n),
                      negatable=True),
            ]
            config.append(P.kw("router", "Enable a routing process",
                               P.kw("ospf", "Open Shortest Path First (OSPF)",
                                    P.arg(P.NumArg(1, 65535, "<1-65535>"), "Process ID",
                                          handler=lambda s, a, n: s.router_ospf(a[0], n), negatable=True),
                                    negatable=True),
                               P.kw("rip", "Routing Information Protocol (RIP)",
                                    handler=lambda s, a, n: s.router_rip(n), negatable=True),
                               P.kw("isis", "ISO IS-IS",
                                    P.arg(P.WordArg("WORD"), "ISO routing area tag",
                                          handler=lambda s, a, n: s.router_isis(a[0], n), negatable=True),
                                    handler=lambda s, a, n: s.router_isis("1", n), negatable=True),
                               P.kw("eigrp", "Enhanced Interior Gateway Routing Protocol (EIGRP)",
                                    P.arg(P.NumArg(1, 65535, "<1-65535>"), "Autonomous System",
                                          handler=lambda s, a, n: s.router_eigrp(a[0], n), negatable=True),
                                    negatable=True),
                               P.kw("bgp", "Border Gateway Protocol (BGP)",
                                    P.arg(P.NumArg(1, 4294967295, "<1-4294967295>"), "Autonomous system number",
                                          handler=lambda s, a, n: s.router_bgp(a[0], n), negatable=True),
                                    negatable=True),
                               negatable=True))
            config.append(P.kw("ipv6", "Global IPv6 configuration commands",
                               P.kw("unicast-routing", "Enable unicast routing",
                                    handler=lambda s, a, n: s.ipv6_routing(not n), negatable=True),
                               P.kw("route", "Configure static routes",
                                    P.arg(P.Ipv6PrefixArg(), "IPv6 prefix",
                                          P.arg(P.Ipv6Arg(), "IPv6 address of next-hop",
                                                handler=lambda s, a, n: s.v6_route(a[0], a[1], None, n), negatable=True),
                                          P.arg(P.IfaceArg(), "Outgoing interface",
                                                P.arg(P.Ipv6Arg(), "IPv6 address of next-hop",
                                                      handler=lambda s, a, n: s.v6_route(a[0], a[2], a[1].name, n),
                                                      negatable=True),
                                                handler=lambda s, a, n: s.v6_route(a[0], None, a[1].name, n),
                                                negatable=True),
                                          handler=lambda s, a, n: s.v6_route(a[0], None, None, True) if n else s.err_incomplete("", 0),
                                          negatable=True),
                                    negatable=True),
                               negatable=True))
            config.append(P.kw("access-list", "Add an access list entry",
                               P.arg(P.NumArg(1, 2699, "<1-2699>"), "IP access list number",
                                     P.kw("permit", "Specify packets to forward",
                                          P.arg(P.LineArg(), "Source/protocol specification",
                                                handler=lambda s, a, n: s.acl_numbered(a[0], "permit", a[1]))),
                                     P.kw("deny", "Specify packets to reject",
                                          P.arg(P.LineArg(), "Source/protocol specification",
                                                handler=lambda s, a, n: s.acl_numbered(a[0], "deny", a[1]))),
                                     P.kw("remark", "Access list entry comment",
                                          P.arg(P.LineArg(), "Comment up to 100 characters",
                                                handler=lambda s, a, n: None)),
                                     handler=lambda s, a, n: s.acl_delete(str(a[0])) if n else s.err_incomplete("", 0),
                                     negatable=True),
                               negatable=True))
            nat_static_tail = P.arg(P.IpArg(), "Inside local IP address",
                                    P.arg(P.IpArg(), "Inside global IP address",
                                          handler=lambda s, a, n: s.nat_static(a[0], a[1], None, None, None, n),
                                          negatable=True),
                                    negatable=True)
            nat_static_port = lambda proto: P.kw(proto, "Transmission Control Protocol" if proto == "tcp" else "User Datagram Protocol",
                                                 P.arg(P.IpArg(), "Inside local IP address",
                                                       P.arg(P.NumArg(1, 65535, "<1-65535>"), "Local UDP/TCP port",
                                                             P.arg(P.IpArg(), "Inside global IP address",
                                                                   P.arg(P.NumArg(1, 65535, "<1-65535>"), "Global UDP/TCP port",
                                                                         handler=lambda s, a, n, proto=proto: s.nat_static(a[0], a[2], proto, a[1], a[3], n),
                                                                         negatable=True),
                                                                   negatable=True),
                                                             negatable=True),
                                                       negatable=True),
                                                 negatable=True)
            nat_list = P.kw("list", "Specify access list describing local addresses",
                            P.arg(P.WordArg("<1-2699>|WORD"), "Access list number for local addresses",
                                  P.kw("interface", "Specify interface for global address",
                                       P.arg(iface(), "Interface name",
                                             P.kw("overload", "Overload an address translation",
                                                  handler=lambda s, a, n: s.nat_dynamic(a[0], a[1], None, True, n),
                                                  negatable=True),
                                             handler=lambda s, a, n: s.nat_dynamic(a[0], a[1], None, True, n),
                                             negatable=True),
                                       negatable=True),
                                  P.kw("pool", "Name pool of global addresses",
                                       P.arg(P.WordArg("WORD"), "Pool name for global addresses",
                                             P.kw("overload", "Overload an address translation",
                                                  handler=lambda s, a, n: s.nat_dynamic(a[0], None, a[1], True, n),
                                                  negatable=True),
                                             handler=lambda s, a, n: s.nat_dynamic(a[0], None, a[1], False, n),
                                             negatable=True),
                                       negatable=True),
                                  negatable=True),
                            negatable=True)
            config.append(P.kw("ip", "Global IP configuration subcommands",
                               P.kw("dhcp", "Configure DHCP server and relay parameters",
                                    P.kw("pool", "Configure DHCP address pools",
                                         P.arg(P.WordArg("WORD"), "Pool name",
                                               handler=lambda s, a, n: s.dhcp_pool(a[0], n), negatable=True),
                                         negatable=True),
                                    P.kw("excluded-address", "Prevent DHCP from assigning certain addresses",
                                         P.arg(P.IpArg(), "Low IP address",
                                               P.arg(P.IpArg(), "High IP address",
                                                     handler=lambda s, a, n: s.dhcp_excluded(a[0], a[1], n),
                                                     negatable=True),
                                               handler=lambda s, a, n: s.dhcp_excluded(a[0], a[0], n),
                                               negatable=True),
                                         negatable=True),
                                    negatable=True),
                               P.kw("access-list", "Named access-list",
                                    P.kw("standard", "Standard Access List",
                                         P.arg(P.WordArg("<1-99>|WORD"), "Access-list name",
                                               handler=lambda s, a, n: s.acl_named(a[0], "standard", n),
                                               negatable=True),
                                         negatable=True),
                                    P.kw("extended", "Extended Access List",
                                         P.arg(P.WordArg("<100-199>|WORD"), "Access-list name",
                                               handler=lambda s, a, n: s.acl_named(a[0], "extended", n),
                                               negatable=True),
                                         negatable=True),
                                    negatable=True),
                               P.kw("nat", "NAT configuration commands",
                                    P.kw("inside", "Inside address translation",
                                         P.kw("source", "Source address translation",
                                              nat_list,
                                              P.kw("static", "Specify static local->global mapping",
                                                   nat_static_tail, nat_static_port("tcp"), nat_static_port("udp"),
                                                   negatable=True),
                                              negatable=True),
                                         negatable=True),
                                    P.kw("pool", "Define pool of addresses",
                                         P.arg(P.WordArg("WORD"), "Pool name",
                                               P.arg(P.IpArg(), "Start IP address",
                                                     P.arg(P.IpArg(), "End IP address",
                                                           P.kw("netmask", "Specify the network mask",
                                                                P.arg(P.MaskArg(), "Network mask",
                                                                      handler=lambda s, a, n: s.nat_pool(a[0], a[1], a[2], a[3]))),
                                                           P.kw("prefix-length", "Specify the prefix length",
                                                                P.arg(P.NumArg(1, 32, "<1-32>"), "Prefix length",
                                                                      handler=lambda s, a, n: s.nat_pool(a[0], a[1], a[2], a[3])))),
                                                     ),
                                               handler=lambda s, a, n: s.nat_pool_delete(a[0]) if n else s.err_incomplete("", 0),
                                               negatable=True),
                                         negatable=True),
                                    negatable=True),
                               P.kw("route", "Establish static routes",
                                    P.arg(P.IpArg(), "Destination prefix",
                                          P.arg(P.MaskArg(), "Destination prefix mask",
                                                *route_tail,
                                                handler=lambda s, a, n: s.ip_route(a[0], a[1], None, None, 1, n) if n else s.err_incomplete("", 0),
                                                negatable=True))),
                               P.kw("routing", "Enable IP routing", handler=lambda s, a, n: s.ip_routing_cmd(not n),
                                    negatable=True),
                               *([] if self.is_router else [
                                   P.kw("default-gateway", "Specify default gateway (if not routing IP)",
                                        P.arg(P.IpArg(), "IP address of default gateway",
                                              handler=lambda s, a, n: s.ip_default_gw(a[0], n), negatable=True),
                                        handler=lambda s, a, n: s.ip_default_gw(None, n) if n else s.err_incomplete("", 0),
                                        negatable=True)]),
                               P.kw("classless", "Follow classless routing forwarding rules",
                                    handler=lambda s, a, n: None, negatable=True)))
        config.sort(key=lambda x: x.word)

        cif = [
            P.kw("description", "Interface specific description",
                 P.arg(P.LineArg(), "Up to 240 characters describing this interface",
                       handler=lambda s, a, n: s.if_description(a[0], n), negatable=True),
                 handler=lambda s, a, n: s.if_description("", n) if n else s.err_incomplete("", 0),
                 negatable=True),
            do, end,
            P.kw("exit", "Exit from interface configuration mode",
                 handler=lambda s, a, n: s.set_mode("config")),
            interface,
            P.kw("shutdown", "Shutdown the selected interface",
                 handler=lambda s, a, n: s.if_shutdown(not n), negatable=True),
            P.kw("duplex", "Configure duplex operation.",
                 P.arg(P.WordArg("auto|full|half"), "", handler=lambda s, a, n: None, negatable=True)),
            P.kw("speed", "Configure speed operation.",
                 P.arg(P.WordArg("10|100|1000|auto"), "", handler=lambda s, a, n: None, negatable=True)),
        ]
        if True:
            ospf_num = lambda lo, hi, h, attr: P.kw(
                attr[0], h, P.arg(P.NumArg(lo, hi, f"<{lo}-{hi}>"), h,
                                  handler=lambda s, a, n: s.if_ospf(attr[1], a[0], n), negatable=True),
                handler=lambda s, a, n: s.if_ospf(attr[1], None, n) if n else s.err_incomplete("", 0),
                negatable=True)
            cif.append(P.kw("ipv6", "IPv6 interface subcommands",
                            P.kw("address", "Configure IPv6 address on interface",
                                 P.arg(P.Ipv6PrefixArg(), "IPv6 prefix",
                                       handler=lambda s, a, n: v6_addr_cmd(s.iface, a[0], n) or s._v6_changed(), negatable=True),
                                 negatable=True),
                            P.kw("enable", "Enable IPv6 on interface",
                                 handler=lambda s, a, n: setattr(s.iface, "ipv6_enabled", not n), negatable=True),
                            P.kw("nd", "IPv6 interface Neighbor Discovery subcommands",
                                 P.kw("ra", "Router Advertisement control",
                                      P.kw("suppress", "Suppress IPv6 Router Advertisements",
                                           handler=lambda s, a, n: setattr(s.iface, "ra", None if n else False),
                                           negatable=True), negatable=True), negatable=True),
                            negatable=True))
            cif.append(P.kw("ip", "Interface Internet Protocol config commands",
                            P.kw("helper-address", "Specify a destination address for UDP broadcasts",
                                 P.arg(P.IpArg(), "IP destination address",
                                       handler=lambda s, a, n: s.if_helper(a[0], n), negatable=True),
                                 handler=lambda s, a, n: s.if_helper(None, n) if n else s.err_incomplete("", 0),
                                 negatable=True),
                            P.kw("access-group", "Specify access control for packets",
                                 P.arg(P.WordArg("<1-2699>|WORD"), "IP access list (standard or extended)",
                                       P.kw("in", "inbound packets",
                                            handler=lambda s, a, n: s.if_access_group(a[0], "in", n), negatable=True),
                                       P.kw("out", "outbound packets",
                                            handler=lambda s, a, n: s.if_access_group(a[0], "out", n), negatable=True),
                                       negatable=True),
                                 negatable=True),
                            P.kw("nat", "NAT interface commands",
                                 P.kw("inside", "Inside interface for address translation",
                                      handler=lambda s, a, n: s.if_nat("inside", n), negatable=True),
                                 P.kw("outside", "Outside interface for address translation",
                                      handler=lambda s, a, n: s.if_nat("outside", n), negatable=True),
                                 negatable=True),
                            P.kw("ospf", "OSPF interface commands",
                                 ospf_num(1, 65535, "Interface cost", ("cost", "costs")),
                                 ospf_num(1, 65535, "Time between HELLO packets", ("hello-interval", "hello")),
                                 ospf_num(1, 65535, "Interval after which a neighbor is declared dead",
                                          ("dead-interval", "dead")),
                                 ospf_num(0, 255, "Router priority", ("priority", "priority")),
                                 negatable=True),
                            P.kw("address", "Set the IP address of an interface",
                                 P.arg(P.IpArg(), "IP address",
                                       P.arg(P.MaskArg(), "IP subnet mask",
                                             handler=lambda s, a, n: s.if_ip(a[0], a[1], n),
                                             negatable=True)),
                                 handler=lambda s, a, n: s.if_ip(None, None, n) if n else s.err_incomplete("", 0),
                                 negatable=True)))
        if not self.is_router:
            vlan_id = lambda: P.NumArg(1, 4094, "<1-4094>")
            allowed_list = P.arg(P.VlanListArg(), "VLAN IDs of the allowed VLANs when this port is in trunking mode",
                                 handler=lambda s, a, n: s.sw_allowed("set", a[0], n), negatable=True)
            cif.append(P.kw("switchport", "Set switching mode characteristics",
                            P.kw("mode", "Set trunking mode of the interface",
                                 P.kw("access", "Set trunking mode to ACCESS unconditionally",
                                      handler=lambda s, a, n: s.sw_mode("access", n), negatable=True),
                                 P.kw("trunk", "Set trunking mode to TRUNK unconditionally",
                                      handler=lambda s, a, n: s.sw_mode("trunk", n), negatable=True),
                                 handler=lambda s, a, n: s.sw_mode("access", n) if n else s.err_incomplete("", 0),
                                 negatable=True),
                            P.kw("access", "Set access mode characteristics of the interface",
                                 P.kw("vlan", "Set VLAN when interface is in access mode",
                                      P.arg(vlan_id(), "VLAN ID of the VLAN when this port is in access mode",
                                            handler=lambda s, a, n: s.sw_access_vlan(a[0], n), negatable=True),
                                      handler=lambda s, a, n: s.sw_access_vlan(1, n) if n else s.err_incomplete("", 0),
                                      negatable=True),
                                 negatable=True),
                            P.kw("trunk", "Set trunking characteristics of the interface",
                                 P.kw("allowed", "Set allowed VLAN characteristics when interface is in trunking mode",
                                      P.kw("vlan", "Set allowed VLANs when interface is in trunking mode",
                                           allowed_list,
                                           P.kw("add", "add VLANs to the current list",
                                                P.arg(P.VlanListArg(), "VLAN IDs of the allowed VLANs",
                                                      handler=lambda s, a, n: s.sw_allowed("add", a[0], n))),
                                           P.kw("remove", "remove VLANs from the current list",
                                                P.arg(P.VlanListArg(), "VLAN IDs of disallowed VLANS",
                                                      handler=lambda s, a, n: s.sw_allowed("remove", a[0], n))),
                                           P.kw("all", "all VLANs", handler=lambda s, a, n: s.sw_allowed("all", None, n)),
                                           P.kw("none", "no VLANs", handler=lambda s, a, n: s.sw_allowed("set", set(), n)),
                                           P.kw("except", "all VLANs except the following",
                                                P.arg(P.VlanListArg(), "VLAN IDs of disallowed VLANS",
                                                      handler=lambda s, a, n: s.sw_allowed("except", a[0], n))),
                                           handler=lambda s, a, n: s.sw_allowed("all", None, n) if n else s.err_incomplete("", 0),
                                           negatable=True),
                                      negatable=True),
                                 P.kw("native", "Set trunking native characteristics when interface is in trunking mode",
                                      P.kw("vlan", "Set native VLAN when interface is in trunking mode",
                                           P.arg(vlan_id(), "VLAN ID of the native VLAN when this port is in trunking mode",
                                                 handler=lambda s, a, n: s.sw_native(a[0], n), negatable=True),
                                           handler=lambda s, a, n: s.sw_native(1, n) if n else s.err_incomplete("", 0),
                                           negatable=True),
                                      negatable=True),
                                 P.kw("encapsulation", "Set trunking encapsulation when interface is in trunking mode",
                                      P.kw("dot1q", "Interface uses only 802.1q trunking encapsulation when trunking",
                                           handler=lambda s, a, n: None, negatable=True), negatable=True),
                                 negatable=True),
                            P.kw("nonegotiate", "Device will not engage in negotiation protocol on this interface",
                                 handler=lambda s, a, n: None, negatable=True),
                            negatable=True))
        if not self.is_router:
            cif.append(P.kw("spanning-tree", "Spanning Tree Subsystem",
                            P.kw("portfast", "Portfast mode", handler=lambda s, a, n: s.stp_portfast(n), negatable=True),
                            P.kw("cost", "Change an interface's spanning tree port path cost",
                                 P.arg(P.NumArg(1, 200000000, "<1-200000000>"), "port path cost",
                                       handler=lambda s, a, n: s.stp_cost(a[0], n), negatable=True),
                                 negatable=True),
                            negatable=True))
        cif.sort(key=lambda x: x.word)
        subif = [n for n in cif if n.word in ("description", "do", "end", "exit", "interface", "ip", "shutdown")]
        subif.append(P.kw("encapsulation", "Set encapsulation type for an interface",
                          P.kw("dot1Q", "IEEE 802.1Q Virtual LAN",
                               P.arg(P.NumArg(1, 4094, "<1-4094>"), "IEEE 802.1Q VLAN ID",
                                     P.kw("native", "Make this as native vlan",
                                          handler=lambda s, a, n: s.subif_encap(a[0], True, n), negatable=True),
                                     handler=lambda s, a, n: s.subif_encap(a[0], False, n), negatable=True),
                               negatable=True),
                          negatable=True))
        subif.sort(key=lambda x: x.word)
        vlan_mode = [do, end,
                     P.kw("exit", "Apply changes, bump revision number, and exit mode",
                          handler=lambda s, a, n: s.set_mode("config")),
                     P.kw("name", "Ascii name of the VLAN",
                          P.arg(P.WordArg(), "The ascii name for the VLAN",
                                handler=lambda s, a, n: s.vlan_name(a[0], n), negatable=True),
                          handler=lambda s, a, n: s.vlan_name(None, n) if n else s.err_incomplete("", 0),
                          negatable=True),
                     interface, P.kw("vlan", "VLAN commands",
                                     P.arg(P.VlanListArg("<1-4094>"), "ISL VLAN IDs 1-1005",
                                           handler=lambda s, a, n: s.vlan_cmd(a[0], n), negatable=True),
                                     negatable=True)]
        rtr = [do, end, P.kw("exit", "Exit from routing protocol configuration mode",
                             handler=lambda s, a, n: s.set_mode("config"))]
        if True:
            rtr += [
                P.kw("router-id", "router-id for this OSPF process",
                     P.arg(P.IpArg(), "OSPF router-id in IP address format",
                           handler=lambda s, a, n: s.ospf_rid(a[0], n), negatable=True),
                     handler=lambda s, a, n: s.ospf_rid(None, n) if n else s.err_incomplete("", 0),
                     negatable=True),
                P.kw("network", "Enable routing on an IP network",
                     P.arg(P.IpArg(), "Network number",
                           P.arg(P.WildcardArg(), "OSPF wild card bits",
                                 P.kw("area", "Set the OSPF area ID",
                                      P.arg(P.WordArg("<0-4294967295>"), "OSPF area ID as a decimal value or in IP address format",
                                            handler=lambda s, a, n: s.ospf_network(a[0], a[1], a[2], n),
                                            negatable=True),
                                      negatable=True),
                                 negatable=True),
                           negatable=True),
                     negatable=True),
                P.kw("passive-interface", "Suppress routing updates on an interface",
                     P.kw("default", "Suppress routing updates on all interfaces",
                          handler=lambda s, a, n: s.ospf_passive(None, n), negatable=True),
                     P.arg(P.IfaceArg(), "Interface name",
                           handler=lambda s, a, n: s.ospf_passive(a[0], n), negatable=True),
                     negatable=True),
                P.kw("default-information", "Control distribution of default information",
                     P.kw("originate", "Distribute a default route",
                          P.kw("always", "Always advertise default route",
                               handler=lambda s, a, n: s.ospf_default("always", n), negatable=True),
                          handler=lambda s, a, n: s.ospf_default("yes", n), negatable=True),
                     negatable=True),
                P.kw("redistribute", "Redistribute information from another routing protocol",
                     *[P.kw(proto, f"{proto.capitalize()} routes",
                            P.kw("subnets", "Consider subnets for redistribution into OSPF",
                                 handler=lambda s, a, n, proto=proto: s.ospf_redistribute(proto, n),
                                 negatable=True),
                            handler=lambda s, a, n, proto=proto: s.ospf_redistribute(proto, n),
                            negatable=True) for proto in ("connected", "static")],
                     negatable=True),
                P.kw("auto-cost", "Calculate OSPF interface cost according to bandwidth",
                     P.kw("reference-bandwidth", "Use reference bandwidth method to assign OSPF cost",
                          P.arg(P.NumArg(1, 4294967, "<1-4294967>"), "The reference bandwidth in terms of Mbits per second",
                                handler=lambda s, a, n: s.ospf_refbw(a[0], n), negatable=True),
                          negatable=True),
                     negatable=True),
            ]
        rtr.sort(key=lambda x: x.word)
        bgp_rtr = [do, end, P.kw("exit", "Exit from routing protocol configuration mode",
                                 handler=lambda s, a, n: s.set_mode("config"))]
        if True:
            nb_opt = lambda word, h, *kids, attr=None, value=True, **kw: P.kw(
                word, h, *kids, handler=(lambda s, a, n: s.bgp_nb(a[0], attr, None if n else value)) if attr else None,
                negatable=True)
            nb = P.arg(P.IpArg(), "Neighbor address",
                       P.kw("remote-as", "Specify a BGP neighbor",
                            P.arg(P.NumArg(1, 4294967295, "<1-4294967295>"), "AS of remote neighbor",
                                  handler=lambda s, a, n: s.bgp_remote_as(a[0], a[1], n), negatable=True),
                            negatable=True),
                       P.kw("update-source", "Source of routing updates",
                            P.arg(P.IfaceArg(), "Interface",
                                  handler=lambda s, a, n: s.bgp_nb(a[0], "update_source", None if n else a[1].name),
                                  negatable=True),
                            handler=lambda s, a, n: s.bgp_nb(a[0], "update_source", None) if n else s.err_incomplete("", 0),
                            negatable=True),
                       P.kw("ebgp-multihop", "Allow EBGP neighbors not on directly connected networks",
                            P.arg(P.NumArg(1, 255, "<1-255>"), "maximum hop count",
                                  handler=lambda s, a, n: s.bgp_nb(a[0], "multihop", None if n else a[1]),
                                  negatable=True),
                            handler=lambda s, a, n: s.bgp_nb(a[0], "multihop", None if n else 255),
                            negatable=True),
                       P.kw("weight", "Set default weight for routes from this neighbor",
                            P.arg(P.NumArg(0, 65535, "<0-65535>"), "default weight",
                                  handler=lambda s, a, n: s.bgp_nb(a[0], "weight", None if n else a[1]),
                                  negatable=True),
                            negatable=True),
                       P.kw("description", "Neighbor specific description",
                            P.arg(P.LineArg(), "Up to 80 characters describing this neighbor",
                                  handler=lambda s, a, n: s.bgp_nb(a[0], "description", None if n else a[1]),
                                  negatable=True),
                            handler=lambda s, a, n: s.bgp_nb(a[0], "description", None) if n else s.err_incomplete("", 0),
                            negatable=True),
                       nb_opt("next-hop-self", "Disable the next hop calculation for this neighbor", attr="next_hop_self"),
                       nb_opt("shutdown", "Administratively shut down this neighbor", attr="shutdown"),
                       nb_opt("default-originate", "Originate default route to this neighbor", attr="default_originate"),
                       nb_opt("route-reflector-client", "Configure a neighbor as Route Reflector client", attr="rr_client"),
                       nb_opt("disable-connected-check", "one-hop away EBGP peer using loopback address",
                              attr="disable_connected_check"),
                       P.kw("activate", "Enable the Address Family for this Neighbor",
                            handler=lambda s, a, n: None, negatable=True),
                       handler=lambda s, a, n: s.bgp_remove_nb(a[0]) if n else s.err_incomplete("", 0),
                       negatable=True)
            bgp_rtr += [
                P.kw("bgp", "BGP specific commands",
                     P.kw("router-id", "Override configured router identifier (peers will reset)",
                          P.arg(P.IpArg(), "Manually configured router identifier",
                                handler=lambda s, a, n: s.bgp_rid(None if n else a[0]), negatable=True),
                          negatable=True),
                     P.kw("log-neighbor-changes", "Log neighbor up/down and reset reason",
                          handler=lambda s, a, n: None, negatable=True),
                     negatable=True),
                P.kw("neighbor", "Specify a neighbor router", nb, negatable=True),
                P.kw("network", "Specify a network to announce via BGP",
                     P.arg(P.IpArg(), "Network number",
                           P.kw("mask", "Network mask",
                                P.arg(P.MaskArg(), "Network mask",
                                      handler=lambda s, a, n: s.bgp_network(a[0], a[1], n), negatable=True),
                                negatable=True),
                           handler=lambda s, a, n: s.bgp_network(a[0], None, n), negatable=True),
                     negatable=True),
                P.kw("redistribute", "Redistribute information from another routing protocol",
                     P.kw("connected", "Connected", handler=lambda s, a, n: s.bgp_redist("connected", n), negatable=True),
                     P.kw("static", "Static routes", handler=lambda s, a, n: s.bgp_redist("static", n), negatable=True),
                     P.kw("ospf", "Open Shortest Path First (OSPF)",
                          P.arg(P.NumArg(1, 65535, "<1-65535>"), "Process ID",
                                handler=lambda s, a, n: s.bgp_redist("ospf", n), negatable=True),
                          negatable=True),
                     negatable=True),
                P.kw("address-family", "Enter Address Family command mode",
                     P.kw("ipv4", "Address family",
                          P.kw("unicast", "Address Family modifier", handler=lambda s, a, n: s.set_mode("config-router-af")),
                          handler=lambda s, a, n: s.set_mode("config-router-af"))),
            ]
        bgp_rtr.sort(key=lambda x: x.word)
        rip_rtr = sorted([
            do, end, P.kw("exit", "Exit from routing protocol configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("version", "Set routing protocol version",
                 P.arg(P.NumArg(1, 2, "<1-2>"), "version", handler=lambda s, a, n: s.rip_set("version", a[0], n),
                       negatable=True), negatable=True),
            P.kw("network", "Enable routing on an IP network",
                 P.arg(P.IpArg(), "Network number", handler=lambda s, a, n: s.rip_network(a[0], n), negatable=True),
                 negatable=True),
            P.kw("auto-summary", "Enable automatic network number summarization",
                 handler=lambda s, a, n: None, negatable=True),
            P.kw("passive-interface", "Suppress routing updates on an interface",
                 P.kw("default", "Suppress routing updates on all interfaces",
                      handler=lambda s, a, n: s.rip_set("passive_default", not n, False), negatable=True),
                 P.arg(P.IfaceArg(), "Interface name", handler=lambda s, a, n: s.rip_passive(a[0], n), negatable=True),
                 negatable=True),
            P.kw("default-information", "Control distribution of default information",
                 P.kw("originate", "Distribute a default route",
                      handler=lambda s, a, n: s.rip_set("default_originate", not n, False), negatable=True),
                 negatable=True),
            P.kw("redistribute", "Redistribute information from another routing protocol",
                 P.kw("connected", "Connected", handler=lambda s, a, n: s.rip_redist("connected", n), negatable=True),
                 P.kw("static", "Static routes", handler=lambda s, a, n: s.rip_redist("static", n), negatable=True),
                 P.kw("ospf", "Open Shortest Path First (OSPF)",
                      P.arg(P.NumArg(1, 65535, "<1-65535>"), "Process ID",
                            P.kw("metric", "Metric for redistributed routes",
                                 P.arg(P.NumArg(0, 16, "<0-16>"), "Default metric",
                                       handler=lambda s, a, n: s.rip_redist("ospf", n))),
                            handler=lambda s, a, n: s.rip_redist("ospf", n), negatable=True),
                      negatable=True),
                 negatable=True),
        ], key=lambda x: x.word)
        bgp_af = [n for n in bgp_rtr if n.word in ("neighbor", "network", "redistribute", "do", "end")]
        bgp_af.append(P.kw("exit-address-family", "Exit from Address Family configuration mode",
                           handler=lambda s, a, n: s.set_mode("config-router-bgp")))
        bgp_af.sort(key=lambda x: x.word)
        def nacl_tree():
            line = lambda action: P.kw(action, "Specify packets to " + ("forward" if action == "permit" else "reject"),
                                       P.arg(P.LineArg(), "Source/protocol specification",
                                             handler=lambda s, a, n: s.nacl_entry(None, action, a[0])))
            seq = P.arg(P.NumArg(1, 2147483647, "<1-2147483647>"), "Sequence Number",
                        *[P.kw(act, "", P.arg(P.LineArg(), "", handler=lambda s, a, n, act=act: s.nacl_entry(a[0], act, a[1])))
                          for act in ("permit", "deny")],
                        handler=lambda s, a, n: s.nacl_delete(a[0]) if n else s.err_incomplete("", 0),
                        negatable=True)
            return sorted([line("permit"), line("deny"), seq, do, end,
                           P.kw("exit", "Exit from access-list configuration mode",
                                handler=lambda s, a, n: s.set_mode("config")),
                           P.kw("remark", "Access list entry comment",
                                P.arg(P.LineArg(), "Comment up to 100 characters", handler=lambda s, a, n: None))],
                          key=lambda x: x.word or "0")
        self._trees = {"user": user, "priv": priv, "config": config, "config-if": cif,
                       "config-subif": subif, "config-vlan": vlan_mode, "config-router": rtr,
                       "config-std-nacl": nacl_tree(), "config-ext-nacl": nacl_tree(),
                       "config-router-bgp": bgp_rtr, "config-router-af": bgp_af, "config-router-rip": rip_rtr,
                       "dhcp-config": sorted([
                           do, end, P.kw("exit", "Exit from DHCP pool configuration mode",
                                         handler=lambda s, a, n: s.set_mode("config")),
                           P.kw("network", "Network number and mask",
                                P.arg(P.IpArg(), "Network number in dotted-decimal notation",
                                      P.arg(P.MaskOrLenArg(), "Network mask or prefix length",
                                            handler=lambda s, a, n: s.dhcp_attr("network", (a[0], a[1]), n),
                                            negatable=True),
                                      negatable=True),
                                handler=lambda s, a, n: s.dhcp_attr("network", None, n) if n else s.err_incomplete("", 0),
                                negatable=True),
                           P.kw("default-router", "Default routers",
                                P.arg(P.IpArg(), "Router's IP address",
                                      handler=lambda s, a, n: s.dhcp_attr("gateway", a[0], n), negatable=True),
                                handler=lambda s, a, n: s.dhcp_attr("gateway", None, n) if n else s.err_incomplete("", 0),
                                negatable=True),
                           P.kw("dns-server", "DNS servers",
                                P.arg(P.IpArg(), "Server's IP address",
                                      handler=lambda s, a, n: s.dhcp_attr("dns", a[0], n), negatable=True),
                                handler=lambda s, a, n: s.dhcp_attr("dns", None, n) if n else s.err_incomplete("", 0),
                                negatable=True),
                           P.kw("domain-name", "Domain name",
                                P.arg(P.WordArg(), "Domain name",
                                      handler=lambda s, a, n: s.dhcp_attr("domain", a[0], n), negatable=True),
                                negatable=True),
                           P.kw("lease", "Address lease time",
                                P.arg(P.NumArg(0, 365, "<0-365>"), "Days",
                                      handler=lambda s, a, n: s.dhcp_attr("lease", a[0] * 86400, n), negatable=True),
                                P.kw("infinite", "Infinite lease",
                                     handler=lambda s, a, n: s.dhcp_attr("lease", 0xFFFFFFFF, n), negatable=True),
                                negatable=True),
                       ], key=lambda x: x.word)}

    # -- mode handling ---------------------------------------------------------
    def set_mode(self, mode: str):
        self.mode = mode
        if mode != "config-if":
            self.iface = None
        return None

    def logout(self):
        self.mode = "user"
        self.iface = None
        return f"\n{self.device.hostname} con0 is now available\n\nPress RETURN to get started.\n"

    def enter_config(self):
        self.mode = "config"
        return "Enter configuration commands, one per line.  End with CNTL/Z."

    def enter_iface(self, port, negate=False):
        if negate:
            if port.is_sub:
                self.device.remove_subinterface(port)
                return None
            if port.is_loopback:
                self.device.remove_loopback(port)
                return None
            return self.err_invalid(self.prompt, 0)
        self.iface = port
        self.mode = "config-subif" if port.is_sub else "config-if"
        return None

    # -- remote access (telnet / ssh) --------------------------------------------------------
    def _acc(self):
        from ..services.remote import access
        return access(self.device)

    def remote(self, proto, ip, user, port=None):
        from ..services.remote import connect
        if proto == "telnet":
            self.write(f"Trying {ip} {'' if port is None else str(port) + ' '}... ")
        return connect(self, ip, proto, user, "cisco", port)

    def enter_console(self):
        self.mode = "config-line"
        self._console = True
        return None

    def enter_vty(self):
        self.mode = "config-line"
        self._console = False
        return None

    def line_password(self, pw, negate):
        if getattr(self, "_console", False):
            return None
        self._acc()["vty_password"] = None if negate else pw
        return None

    def line_login(self, kind, negate):
        if getattr(self, "_console", False):
            return None
        self._acc()["login"] = "none" if negate else kind
        return None

    def line_transport(self, what):
        if getattr(self, "_console", False):
            return None
        a = self._acc()
        a["telnet"] = what in ("all", "telnet")
        a["ssh"] = what in ("all", "ssh")
        return None

    def username(self, name, pw, kind, priv=None):
        users = self._acc().setdefault("users", {})
        if pw is None:
            users.pop(name, None)
        else:
            users[name] = pw
        return None

    def enable_secret(self, pw, negate):
        self._acc()["enable_secret"] = None if negate else pw
        return None

    def domain_name(self, name, negate):
        self._acc()["domain"] = None if negate else name
        return None

    def crypto_rsa(self, modulus):
        a = self._acc()
        d = self.device
        if d.hostname == d.default_hostname:
            return f"% Please define a hostname other than {d.default_hostname}."
        if not a.get("domain"):
            return "% Please define a domain-name first."
        if modulus is None:
            return self.ask("How many bits in the modulus [512]: ", lambda line: self.crypto_rsa(int(line or 512))
                            if (line or "512").isdigit() else "% Invalid input", secret=False) or \
                (f"The name for the keys will be: {d.hostname}.{a['domain']}\n"
                 "Choose the size of the key modulus in the range of 360 to 4096 for your\n"
                 "  General Purpose Keys. Choosing a key modulus greater than 512 may take\n"
                 "  a few minutes.\n")
        a["rsa"] = True
        a["modulus"] = modulus
        return (f"% Generating {modulus} bit RSA keys, keys will be non-exportable...[OK]\n"
                "*Mar 1 0:0:1.1: %SSH-5-ENABLED: SSH 1.99 has been enabled")

    def crypto_zeroize(self):
        self._acc()["rsa"] = False
        return "% All keys will be removed.\n% All router certs issued using these keys will also be removed."

    def enable_cmd(self):
        if not self.vty:
            self.mode = "priv"
            return None
        secret = self._acc().get("enable_secret")
        if not secret:
            return "% No password set"
        tries = [0]

        def check(line):
            if line == secret:
                self.mode = "priv"
                return None
            tries[0] += 1
            if tries[0] >= 3:
                return "% Bad secrets\n"
            self.ask("Password: ", check)
            return None
        self.ask("Password: ", check)
        return None

    def show_ip_ssh(self):
        a = self._acc()
        if not a.get("rsa"):
            return "SSH Disabled - version 1.99\n%Please create RSA keys to enable SSH (and of atleast 768 bits for SSH v2)."
        return ("SSH Enabled - version 2.0\nAuthentication timeout: 120 secs; Authentication retries: 3\n"
                f"Minimum expected Diffie Hellman key size : 1024 bits\nIOS Keys in SECSH format(ssh-rsa, base64 encoded): {self.device.hostname}")

    def show_users(self):
        lines = ["    Line       User       Host(s)              Idle       Location",
                 "*  0 con 0                idle                 00:00:00"]
        srv = getattr(self.device, "remote_server", None)
        for i, c in enumerate(srv.conns.values() if srv else []):
            lines.append(f"   {i + 2} vty {i}     {c.user or '':<11}idle                 00:00:00 {c.client_ip}")
        lines += ["", "  Interface    User               Mode         Idle     Peer Address"]
        return "\n".join(lines)

    # -- interface range -----------------------------------------------------------------------
    def enter_range(self, text):
        import re
        ports = []
        for part in text.split(","):
            m = re.match(r"^\s*([A-Za-z\-]+\s*(?:\d+/)*)(\d+)\s*(?:-\s*(\d+))?\s*$", part)
            if not m:
                return self.err_invalid(self.prompt, 0)
            base, lo = m.group(1).replace(" ", ""), int(m.group(2))
            hi = int(m.group(3)) if m.group(3) else lo
            for n in range(lo, hi + 1):
                port = self.device.get_port(f"{base}{n}")
                if port is None:
                    return "% Command rejected: range contains an interface that does not exist"
                if port not in ports:
                    ports.append(port)
        if not ports:
            return self.err_invalid(self.prompt, 0)
        self.range_ports, self.iface, self.mode = ports, ports[0], "config-if"
        return None

    def _execute(self, line):
        rng = getattr(self, "range_ports", None)
        if not rng or self.mode != "config-if" or self.iface not in rng:
            self.range_ports = None
            return super()._execute(line)
        outs = []
        for port in rng:
            self.iface = port
            o = super()._execute(line)
            if o and o not in outs:
                outs.append(o)
            if self.mode != "config-if" or self.iface is not port:
                if getattr(self, "range_ports", None) is rng:
                    self.range_ports = None      # left the range (exit, end, interface X)
                return "\n".join(outs) or None
        self.iface = rng[0]
        return "\n".join(outs) or None

    # -- voice VLAN / CME -----------------------------------------------------------------------
    def voice_vlan(self, vlan, negate):
        from .base import set_voice_vlan
        return set_voice_vlan(self.iface, None if negate else vlan)

    def cme_mode(self, negate):
        c = self.device.cme
        if negate:
            c.apply(None)
            return None
        c.enabled = True
        c.ensure()
        self.mode = "config-telephony"
        return None

    def cme_set(self, attr, value):
        setattr(self.device.cme, attr, str(value) if attr == "source" else value)
        return None

    def cme_dn(self, tag, negate):
        c = self.device.cme
        if negate:
            c.dns.pop(tag, None)
            return None
        if tag not in c.dns and c.max_dn and len(c.dns) >= c.max_dn:
            return "% Maximum number of ephone-dn reached (max-dn)"
        c.dns.setdefault(tag, "")
        self._dn, self.mode = tag, "config-ephone-dn"
        return None

    def cme_number(self, number, negate):
        if not negate and not number.isdigit():
            return self.err_invalid(self.prompt, 0)
        self.device.cme.dns[self._dn] = "" if negate else number
        return None

    def cme_ephone(self, tag, negate):
        c = self.device.cme
        if negate:
            c.ephones.pop(tag, None)
            return None
        c.ephones.setdefault(tag, {"mac": None, "button": None})
        self._eph, self.mode = tag, "config-ephone"
        return None

    def cme_mac(self, mac):
        h = mac.replace(".", "").replace(":", "").replace("-", "").upper()
        if len(h) != 12:
            return self.err_invalid(self.prompt, 0)
        self.device.cme.ephones[self._eph]["mac"] = h
        return None

    def cme_button(self, spec):
        try:
            _, dn = spec.split(":")
            self.device.cme.ephones[self._eph]["button"] = int(dn)
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        return None

    def show_ephone(self, registered_only):
        c = self.device.cme
        out = []
        for tag, e in sorted(c.ephones.items()):
            reg = c.registered.get(e.get("mac") or "")
            if registered_only and reg is None:
                continue
            out += [f"ephone-{tag}[{tag - 1}] Mac:{e.get('mac') or ''} TCP socket:[1] activeLine:0 whisperLine:0 "
                    f"{'REGISTERED' if reg else 'UNREGISTERED'} in SCCP ver 12/12 max_streams=3",
                    "mediaActive:0 whisper_mediaActive:0 startMedia:0 offhook:0 ringing:0 reset:0 reset_sent:0 "
                    "paging 0 debug:0 caps:8",
                    f"IP:{reg['ip'] if reg else '0.0.0.0'} * 7960  keepalive 0 max_line 2 available_line 2",
                    f"button 1: dn {e.get('button') or '-'}  number {c.dns.get(e.get('button'), '') or '-'} "
                    f"CH1   IDLE", ""]
        return "\n".join(out)

    def show_ephone_dn(self):
        c = self.device.cme
        lines = ["PORT    CH   ORIG-PORT    TYPE     STATE        CODEC        VAD VTSP STATE",
                 "======= ===  ============ ======== ============ ============ === =============="]
        for tag, num in sorted(c.dns.items()):
            busy = any(x["state"] == "connected" and (c.number_of(x["a"]) == num or c.number_of(x["b"]) == num)
                       for x in c.calls.values())
            lines.append(f"50/0/{tag:<3}1    -            -        {'CONNECTED' if busy else 'IDLE':<13}"
                         f"{'g711ulaw' if busy else '-':<13}-   -   ({num})")
        return "\n".join(lines)

    def show_telephony(self):
        c = self.device.cme
        return (f"CONFIG (Version=12.1)\n=====================\nVersion 12.1\nCisco Unified Communications Manager "
                f"Express\nFor on-line documentation please go to:\n  http://www.cisco.com/go/ccme\n\n"
                f"ip source-address {c.source or '0.0.0.0'} port 2000\nmax-ephones {c.max_ephones}\nmax-dn {c.max_dn}\n"
                f"auto assign {'%d to %d' % c.auto_assign if c.auto_assign else 'off'}\n"
                f"registered ephones: {len(c.registered)}")

    # -- QoS (MQC) -------------------------------------------------------------------------------
    def cmap(self, name, negate):
        if negate:
            self.device.qos_classes.pop(name, None)
            return None
        self.device.qos_classes.setdefault(name, {})
        self._cmap, self.mode = name, "config-cmap"
        return None

    def cmatch(self, what, value, negate):
        from .base import qos_class_match
        try:
            return qos_class_match(self.device, self._cmap, what, value, negate)
        except ValueError:
            return self.err_invalid(self.prompt, 0)

    def pmap(self, name, negate):
        from ..security.qos import QosPolicy
        if negate:
            self.device.qos.pop(name, None)
            return None
        self.device.qos.setdefault(name, QosPolicy(name))
        self._pmap, self.mode = name, "config-pmap"
        return None

    def pclass(self, cname, negate):
        from .base import qos_policy_class
        pol = self.device.qos[self._pmap]
        if negate:
            pol.classes = [c for c in pol.classes if c.name != cname]
            return None
        if cname != "class-default" and cname not in self.device.qos_classes:
            return f"% class-map {cname} not configured"
        qos_policy_class(self.device, self._pmap, cname)
        self._pclass, self.mode = cname, "config-pmap-c"
        return None

    def pset(self, attr, value, negate):
        from .base import qos_policy_class
        try:
            return qos_policy_class(self.device, self._pmap, self._pclass, attr, value, negate)
        except ValueError:
            return self.err_invalid(self.prompt, 0)

    def svc_policy(self, direction, name, negate):
        p = self.iface
        if not negate and name not in self.device.qos:
            return f"policy map {name} not configured"
        setattr(p, "qos_in" if direction == "in" else "qos_out", None if negate else name)
        return None

    def show_pmap_if(self, port):
        from .base import qos_stats
        out = []
        for p in ([port] if port else self.device.ports.values()):
            lines = qos_stats(self.device, p, "in") + qos_stats(self.device, p, "out")
            if lines:
                out += [f" {p.name}", ""] + lines
        return "\n".join(out)

    def show_pmaps(self):
        from ..security.qos import DSCP_NAMES
        out = []
        for pol in self.device.qos.values():
            out.append(f"  Policy Map {pol.name}")
            for c in pol.classes:
                out.append(f"    Class {c.name}")
                if c.set_dscp is not None:
                    out.append(f"      set dscp {DSCP_NAMES.get(c.set_dscp, c.set_dscp)}")
                if c.police_bps:
                    out.append(f"      police cir {c.police_bps} bc 1500 conform-action transmit exceed-action drop")
        return "\n".join(out)

    # -- GRE / IPsec ---------------------------------------------------------------------------
    def enter_tunnel(self, n, negate):
        d = self.device
        name = f"Tunnel{n}"
        if negate:
            if name in d.ports:
                d.remove_tunnel(d.ports[name])
            return None
        new = name not in d.ports
        self.iface = d.add_tunnel(name, f"Tu{n}")
        self.mode = "config-if"
        return f"\n%LINK-5-CHANGED: Interface {name}, changed state to up\n" if new else None

    def tun(self, attr, value):
        from .base import tunnel_set
        if not getattr(self.iface, "is_tunnel", False):
            return self.err_invalid(self.prompt, 0)
        tunnel_set(self.iface, attr, value)
        self._ipsec_refresh()
        return None

    def _ipsec_refresh(self):
        x = self.device.extra
        keys = x.get("isakmp_keys", {})
        for name, prof in x.get("tunnel_protection", {}).items():
            t = self.device.ports.get(name)
            if t is not None:
                t.ipsec_key = keys.get(str(t.destination)) or keys.get("0.0.0.0")

    def isakmp_key(self, addr, key, negate):
        keys = self.device.extra.setdefault("isakmp_keys", {})
        if negate:
            keys.pop(addr, None)
        else:
            keys[addr] = key
        self._ipsec_refresh()
        return None

    def tun_protect(self, profile, negate):
        if not getattr(self.iface, "is_tunnel", False):
            return self.err_invalid(self.prompt, 0)
        prot = self.device.extra.setdefault("tunnel_protection", {})
        if negate:
            prot.pop(self.iface.name, None)
            self.iface.ipsec_key = None
        else:
            prot[self.iface.name] = profile
            self._ipsec_refresh()
            if self.iface.ipsec_key is None:
                return f"% No pre-shared key configured for peer {self.iface.destination}"
        return None

    def show_ipsec_sa(self):
        from .base import tunnel_rows
        out = []
        for t in tunnel_rows(self.device):
            if t.ipsec_key is None:
                continue
            c = t.counters
            out += [f"interface: {t.name}", f"    Crypto map tag: {t.name}-head-0, local addr {t.source_ip()}", "",
                    "   protected vrf: (none)", f"   current_peer {t.destination} port 500",
                    f"     #pkts encaps: {c['encaps']}, #pkts encrypt: {c['encaps']}, #pkts digest: {c['encaps']}",
                    f"     #pkts decaps: {c['decaps']}, #pkts decrypt: {c['decaps']}, #pkts verify: {c['decaps']}",
                    f"     #recv errors {c['drops']}", ""]
        return "\n".join(out)

    def show_isakmp_sa(self):
        from .base import tunnel_rows
        lines = ["IPv4 Crypto ISAKMP SA", "dst             src             state          conn-id status"]
        for t in tunnel_rows(self.device):
            if t.ipsec_key is not None:
                st = "QM_IDLE" if t.counters["decaps"] else "MM_NO_STATE"
                lines.append(f"{str(t.destination):<16}{str(t.source_ip()):<16}{st:<15}{1001:<8}"
                             f"{'ACTIVE' if t.counters['decaps'] else ''}")
        return "\n".join(lines)

    # -- NAT64 ---------------------------------------------------------------------------------
    def _n64(self):
        from ..security.nat64 import Nat64
        if self.device.nat64 is None:
            self.device.nat64 = Nat64()
        return self.device.nat64

    def nat64_prefix(self, prefix, negate):
        from ipaddress import IPv6Network
        from ..security.nat64 import WELL_KNOWN
        n = self._n64()
        net = IPv6Network(prefix, strict=False)
        if net.prefixlen != 96 and not negate:
            return "%NAT64: only /96 stateful prefixes are supported"
        n.prefix = WELL_KNOWN if negate else net
        return None

    def nat64_if(self, negate):
        n = self._n64()
        (n.interfaces.discard if negate else n.interfaces.add)(self.iface.name)
        return None

    def show_nat64(self):
        n = self.device.nat64
        lines = ["Proto  Original IPv4         Translated IPv4", "       Translated IPv6       Original IPv6",
                 "-" * 60]
        for s in (n.sessions if n else []):
            lines += [f"{s.proto:<7}{s.v4_dst}:{s.dport if s.proto != 'icmp' else s.ident4:<14}{s.v4_src}:{s.ident4}",
                      f"       [{s.v6_dst}]:{s.ident6}   [{s.v6_src}]:{s.ident6}", "-" * 60]
        lines.append(f"Total number of translations: {len(n.sessions) if n else 0}")
        return "\n".join(lines)

    # -- DHCPv6 --------------------------------------------------------------------------------
    def dhcp6_pool(self, name, negate):
        if negate:
            self.device.dhcp6.pools.pop(name, None)
            return None
        from .base import dhcp6_pool_cmd
        dhcp6_pool_cmd(self.device, name, None, None)
        self._dhcp6_pool = name
        self.mode = "config-dhcpv6"
        return None

    def dhcp6_attr(self, attr, value):
        from .base import dhcp6_pool_cmd
        return dhcp6_pool_cmd(self.device, self._dhcp6_pool, attr, value)

    def dhcp6_if(self, pool, negate):
        p = self.iface
        if negate:
            self.device.dhcp6.ifaces.pop(p.name, None)
        else:
            self.device.dhcp6.ifaces[p.name] = pool
        return None

    def show_dhcp6(self):
        lines = []
        for pool in self.device.dhcp6.pools.values():
            for duid, addr in pool.bindings.items():
                lines += [f"Client: (DUID {duid})", "  IA NA: IA ID 0x00040001, T1 43200, T2 69120",
                          f"    Address: {addr.upper()}", "            preferred lifetime 86400, valid lifetime 172800",
                          f"  Pool: {pool.name}", ""]
        return "\n".join(lines)

    # -- OSPFv3 / RIPng ------------------------------------------------------------------------
    def v6igp_router(self, kind, tag, negate):
        from ..routing.v6igp import ensure
        d = self.device
        if negate:
            proc = d.v6igp.pop(kind, None)
            if proc is not None:
                proc.stop()
                proc._recompute_domain()
            return None
        if not d.ip6_forwarding:
            return "% IPv6 routing not enabled"
        self._v6proc = ensure(d, kind, str(tag))
        self.mode = "config-rtr6"
        return None

    def v6igp_rid(self, rid):
        proc = getattr(self, "_v6proc", None) or self.device.v6igp.get("ospf6")
        if proc is not None:
            proc.cfg.router_id = rid
            proc.kick()
        return None

    def v6igp_if(self, kind, tag, area, negate):
        from ..routing.v6igp import enable_iface
        from ..routing.ospf import area_id
        p = self.iface
        if not self.is_router and not p.is_svi:
            return "% IP addresses may not be configured on L2 links."
        if not negate and not self.device.ip6_forwarding:
            return "% IPv6 routing not enabled"
        try:
            a = area_id(area) if area is not None else "0.0.0.0"
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        enable_iface(self.device, kind, p, a, str(tag), on=not negate)
        return None

    def v6igp_cost(self, cost, negate):
        proc = self.device.v6igp.get("ospf6")
        if proc is None or self.iface.name not in proc.cfg.interfaces:
            return "% OSPFv3: Interface not enabled for OSPFv3"
        proc.cfg.interfaces[self.iface.name]["cost"] = None if negate else cost
        proc._recompute_domain()
        return None

    def show_v6igp_nb(self, kind):
        from .base import v6igp_neighbors
        proc = self.device.v6igp.get(kind)
        if kind == "eigrp6":
            lines = [f"EIGRP-IPv6 Neighbors for AS({proc.cfg.tag if proc else ''})",
                     "H   Address                 Interface              Hold Uptime   SRTT   RTO  Q  Seq",
                     "                                                   (sec)         (ms)       Cnt Num"]
            for i, (rid, p, ll) in enumerate(v6igp_neighbors(self.device, kind)):
                lines += [f"{i:<4}Link-local address:     {p.name if p else '':<23}12 00:00:30   1    100  0  3",
                          f"    {ll.compressed.upper()}"]
            return "\n".join(lines)
        if kind == "ripng":
            if proc is None:
                return ""
            lines = [f'RIP process "{proc.cfg.tag}", port 521, multicast-group FF02::9, pid 255',
                     "     Administrative distance is 120. Maximum paths is 16",
                     "     Updates every 30 seconds, expire after 180", "     Interfaces:"]
            lines += [f"       {self.device.ports[n].name}" for n in proc.cfg.interfaces if n in self.device.ports]
            lines.append("     Neighbors:")
            lines += [f"       {ll.compressed.upper()}" for _, _, ll in v6igp_neighbors(self.device, kind)]
            return "\n".join(lines)
        lines = [f"OSPFv3 Router with ID ({proc.router_id() if proc else '0.0.0.0'}) (Process ID {proc.cfg.tag if proc else 1})", "",
                 "Neighbor ID     Pri   State           Dead Time   Interface ID    Interface"]
        for rid, p, ll in v6igp_neighbors(self.device, kind):
            lines.append(f"{rid:<16}{1:<6}{'FULL/BDR':<16}{'00:00:35':<12}{3:<16}{p.name if p else ''}")
        return "\n".join(lines)

    # -- port security / DHCP snooping -----------------------------------------------------------
    def psec(self, attr, value, negate):
        from .base import psec_set
        p = self.iface
        if p.mode != "access" and not negate:
            return f"Command rejected: {p.name} is a dynamic port."
        if attr == "mac" and value is not None:
            try:
                from ..addresses import MacAddress
                value = MacAddress.parse(value).cisco()
            except ValueError:
                return self.err_invalid(self.prompt, 0)
        return psec_set(p, attr, value, negate)

    def snoop_enable(self, on):
        self.device.dhcp_snooping["enabled"] = on
        return None

    def snoop_vlan(self, vids, negate):
        cur = set(self.device.dhcp_snooping.get("vlans") or [])
        cur = cur - set(vids) if negate else cur | set(vids)
        self.device.dhcp_snooping["vlans"] = sorted(cur)
        return None

    def snoop_trust(self, negate):
        self.iface.dhcp_trust = not negate
        return None

    def show_psec(self, port):
        d = self.device
        if port is not None:
            ps = port.psec
            if ps is None:
                return "Port Security              : Disabled"
            st = "Secure-shutdown" if port.err_disabled else ("Secure-up" if port.is_up else "Secure-down")
            return "\n".join([
                "Port Security              : Enabled", f"Port Status                : {st}",
                f"Violation Mode             : {ps.get('violation', 'shutdown').capitalize()}",
                "Aging Time                 : 0 mins", "Aging Type                 : Absolute",
                "SecureStatic Address Aging : Disabled", f"Maximum MAC Addresses      : {ps.get('max', 1)}",
                f"Total MAC Addresses        : {len(ps.get('macs', []))}",
                f"Configured MAC Addresses   : {0 if ps.get('sticky') else len([m for m in ps.get('macs', []) if m not in ps.get('dynamic', [])])}",
                f"Sticky MAC Addresses       : {len(ps.get('macs', [])) if ps.get('sticky') else 0}",
                f"Last Source Address:Vlan   : {ps.get('last', '0000.0000.0000')}:{port.pvid}",
                f"Security Violation Count   : {ps.get('violations', 0)}"])
        lines = ["Secure Port  MaxSecureAddr  CurrentAddr  SecurityViolation  Security Action",
                 "                (Count)       (Count)          (Count)",
                 "---------------------------------------------------------------------------"]
        for p in d.physical_ports():
            if p.psec is not None:
                ps = p.psec
                lines.append(f"{p.short:>11}{ps.get('max', 1):>15}{len(ps.get('macs', [])):>13}"
                             f"{ps.get('violations', 0):>19}{ps.get('violation', 'shutdown').capitalize():>17}")
        lines.append("---------------------------------------------------------------------------")
        return "\n".join(lines)

    def show_psec_addr(self):
        lines = ["          Secure Mac Address Table", "-" * 63,
                 "Vlan    Mac Address       Type                     Ports   Remaining Age", "-" * 63]
        for p in self.device.physical_ports():
            if p.psec is not None:
                for m in p.psec.get("macs", []):
                    typ = "SecureSticky" if p.psec.get("sticky") else (
                        "SecureDynamic" if m in p.psec.get("dynamic", []) else "SecureConfigured")
                    lines.append(f"{p.pvid:>4}    {m:<18}{typ:<25}{p.short:<8}-")
        return "\n".join(lines)

    def show_snoop(self, binding):
        d = self.device
        if binding:
            lines = ["MacAddress          IpAddress        Lease(sec)  Type           VLAN  Interface",
                     "------------------  ---------------  ----------  -------------  ----  --------------------"]
            for mac, b in d.snoop_bindings.items():
                lines.append(f"{mac:<20}{b['ip']:<17}{b['lease']:<12}{'dhcp-snooping':<15}{b['vlan']:<6}{b['port']}")
            lines.append(f"Total number of bindings: {len(d.snoop_bindings)}")
            return "\n".join(lines)
        ds = d.dhcp_snooping
        lines = [f"Switch DHCP snooping is {'enabled' if ds['enabled'] else 'disabled'}",
                 "DHCP snooping is configured on following VLANs:", vlan_ranges(ds.get("vlans") or []) or "none",
                 "Insertion of option 82 is enabled", "Interface                  Trusted    Allow option    Rate limit (pps)",
                 "-----------------------    -------    ------------    ----------------"]
        for p in d.physical_ports():
            if p.dhcp_trust:
                lines.append(f"{p.name:<27}{'yes':<11}{'yes':<16}unlimited")
        return "\n".join(lines)

    # -- HSRP / VRRP ---------------------------------------------------------------------------
    def fhrp(self, proto, num, attr, value, negate):
        from .base import fhrp_set
        p = self.iface
        if p is None or (not self.is_router and not p.is_svi):
            return "% IP addresses may not be configured on L2 links."
        if attr == "vip" and not negate and p.ip is None:
            return f"% Address {value} in group {num} does not match interface subnet"
        return fhrp_set(self.device, p, proto, num, attr, value, negate)

    def show_fhrp(self, proto, brief):
        from .base import fhrp_rows
        rows = [r for r in fhrp_rows(self.device) if r[0].proto == proto]
        if proto == "hsrp" and brief:
            lines = ["                     P indicates configured to preempt.", "                     |",
                     "Interface   Grp  Pri P State   Active          Standby         Virtual IP"]
            for g, p, act, stb in rows:
                me = "local"
                active = me if g.state == "active" else (act or "unknown")
                standby = me if g.state == "standby" else (stb or "unknown")
                lines.append(f"{p.short:<12}{g.group:<5}{self.device.fhrp.eff_priority(g):<4}{'P' if g.preempting else ' '} "
                             f"{g.state.capitalize():<8}{active:<16}{standby:<16}{g.vip or 'unknown'}")
            return "\n".join(lines)
        if proto == "vrrp" and brief:
            lines = ["Interface          Grp Pri Time  Own Pre State   Master addr     Group addr"]
            for g, p, act, stb in rows:
                master = str(p.ip.ip) if g.state == "master" and p.ip else (act or "unknown")
                own = "Y" if self.device.fhrp.eff_priority(g) == 255 else " "
                lines.append(f"{p.short:<19}{g.group:<4}{self.device.fhrp.eff_priority(g):<4}{3609:<6}{own:<4}"
                             f"{'Y' if g.preempting else ' ':<4}{g.state.capitalize():<8}{master:<16}{g.vip or ''}")
            return "\n".join(lines)
        out = []
        for g, p, act, stb in rows:
            if proto == "hsrp":
                vmac = "0000.0c07.ac" + f"{g.group:02x}"
                out += [f"{p.name} - Group {g.group}", f"  State is {g.state.capitalize()}",
                        f"    {g.changes} state change(s), last state change 00:00:10",
                        f"  Virtual IP address is {g.vip}", f"  Active virtual MAC address is {vmac}",
                        f"    Local virtual MAC address is {vmac} (v1 default)",
                        "  Hello time 3 sec, hold time 10 sec",
                        f"  Preemption {'enabled' if g.preempting else 'disabled'}",
                        f"  Active router is {'local' if g.state == 'active' else (act or 'unknown')}",
                        f"  Standby router is {'local' if g.state == 'standby' else (stb or 'unknown')}",
                        f"  Priority {self.device.fhrp.eff_priority(g)} (default 100)",
                        f"  Group name is \"hsrp-{p.short}-{g.group}\" (default)", ""]
            else:
                out += [f"{p.name} - Group {g.group}", f"  State is {g.state.capitalize()}",
                        f"  Virtual IP address is {g.vip}", f"  Virtual MAC address is 0000.5e00.01{g.group:02x}",
                        "  Advertisement interval is 1.000 sec",
                        f"  Preemption {'enabled' if g.preempting else 'disabled'}",
                        f"  Priority is {self.device.fhrp.eff_priority(g)}",
                        f"  Master Router is {(str(p.ip.ip) + ' (local)') if g.state == 'master' else (act or 'unknown')}",
                        "  Master Down interval is 3.609 sec", ""]
        return "\n".join(out)

    # -- EtherChannel ------------------------------------------------------------------------
    def channel_group(self, group, mode, negate):
        d, p = self.device, self.iface
        if p is None or p.is_channel or p.is_svi:
            return self.err_invalid(self.prompt, 0)
        if negate:
            d.set_channel_member(p, None)
            return None
        po = d.channel_for_group(group)
        msg = None
        if po is None:
            po = d.add_channel(group, f"Port-channel{group}", f"Po{group}")
            po.mode, po.pvid, po.access_vlan, po.native_vlan, po.tagged = p.mode, p.pvid, p.access_vlan, p.native_vlan, p.tagged
            msg = f"Creating a port-channel interface Port-channel {group}"
        others = [m.channel_mode for m in po.members if m is not p]
        from ..port import channel_protocol
        if others and channel_protocol(others[0]) != channel_protocol(mode):
            return "Command rejected (Channel protocol mismatch for interface " + p.short + " in group " + str(group) + ")"
        d.set_channel_member(p, group, mode)
        return msg

    def enter_po(self, group, negate):
        d = self.device
        po = d.channel_for_group(group)
        if negate:
            if po is not None:
                d.remove_channel(po)
            return None
        if po is None:
            po = d.add_channel(group, f"Port-channel{group}", f"Po{group}")
        self.iface, self.mode = po, "config-if"
        return None

    def show_etherchannel(self):
        from .base import lag_summary_rows
        rows = lag_summary_rows(self.device)
        lines = ["Flags:  D - down        P - bundled in port-channel",
                 "        I - stand-alone s - suspended",
                 "        H - Hot-standby (LACP only)",
                 "        R - Layer3      S - Layer2",
                 "        U - in use      f - failed to allocate aggregator", "",
                 "        M - not in use, minimum links not met", "        u - unsuitable for bundling",
                 "        w - waiting to be aggregated", "        d - default port", "",
                 f"Number of channel-groups in use: {len(rows)}", f"Number of aggregators:           {len(rows)}", "",
                 "Group  Port-channel  Protocol    Ports",
                 "------+-------------+-----------+-----------------------------------------------"]
        for po, proto, mem in rows:
            st = "SU" if po.is_up else "SD"
            lines.append(f"{po.group:<7}{po.short + '(' + st + ')':<14}{proto:>6}      "
                         + "  ".join(f"{m.short}({f})" for m, f in mem))
        return "\n".join(lines)

    def enter_svi(self, vid, negate=False):
        d = self.device
        name = f"Vlan{vid}"
        if negate:
            p = d.ports.get(name)
            if p is not None:
                d.remove_svi(p)
            return None
        new = name not in d.ports
        p = d.add_svi(vid, name, f"Vl{vid}")
        self.iface = p
        self.mode = "config-if"
        if new:
            return (f"\n%LINK-5-CHANGED: Interface {name}, changed state to up\n\n"
                    f"%LINEPROTO-5-UPDOWN: Line protocol on Interface {name}, changed state to "
                    f"{'up' if p.is_up else 'down'}\n") if p.is_up else None
        return None

    def ip_routing_cmd(self, on):
        if self.is_router:
            return None
        self.device.ip_routing = on
        self.device.routes_changed()
        return None

    def ip_default_gw(self, ip, negate):
        from ..addresses import IPv4Address as _A
        self.device.default_gateway = None if negate else _A(ip)
        return None

    def _l2_only(self):
        """IOS refuses L3 settings on switchports (only VLAN interfaces route)."""
        if not self.is_router and self.iface is not None and not (self.iface.is_svi or self.iface.is_loopback):
            return "% IP addresses may not be configured on L2 links."
        return None

    def enter_loopback(self, num, negate=False):
        if negate:
            return None
        lo = self.device.add_loopback(f"Loopback{num}", f"Lo{num}")
        self.iface = lo
        self.mode = "config-if"
        return (f"\n%LINK-5-CHANGED: Interface Loopback{num}, changed state to up\n\n"
                f"%LINEPROTO-5-UPDOWN: Line protocol on Interface Loopback{num}, changed state to up\n")

    # -- ACLs ------------------------------------------------------------------------------
    def _acl_err(self, line_offset, e):
        return self.err_invalid(self.prompt, line_offset)

    def acl_numbered(self, num, action, rest):
        kind = ACLX.numbered_kind(num)
        if kind is None:
            return self.err_invalid(self.prompt, 0)
        try:
            rule = ACLX.parse_entry(action, rest, kind == "extended")
        except ACLX.AclSyntaxError:
            return self.err_invalid(self.prompt, len(f"access-list {num} {action} "))
        acl = self.device.acls.setdefault(str(num), Acl(str(num), kind))
        acl.add(rule)
        return None

    def acl_delete(self, name):
        self.device.acls.pop(name, None)
        return None

    def acl_named(self, name, kind, negate):
        acls = self.device.acls
        if negate:
            acls.pop(name, None)
            return None
        if name in acls and acls[name].kind != kind:
            return f"% A named {acls[name].kind} IP access list with this name already exists"
        acls.setdefault(name, Acl(name, kind))
        self.acl_ctx = name
        self.mode = "config-std-nacl" if kind == "standard" else "config-ext-nacl"
        return None

    def nacl_entry(self, seq, action, rest):
        acl = self.device.acls[self.acl_ctx]
        try:
            rule = ACLX.parse_entry(action, rest, acl.kind == "extended")
        except ACLX.AclSyntaxError:
            return self.err_invalid(self.prompt, len(f"{action} "))
        if seq is not None:
            rule.seq = seq
        acl.add(rule)
        return None

    def nacl_delete(self, seq):
        acl = self.device.acls[self.acl_ctx]
        acl.rules = [r for r in acl.rules if r.seq != seq]
        return None

    def if_access_group(self, name, direction, negate):
        if self._l2_only():
            return self._l2_only()
        attr = "acl_in" if direction == "in" else "acl_out"
        setattr(self.iface, attr, None if negate else name)
        return None

    def clear_acl_counters(self):
        for a in self.device.acls.values():
            for r in a.rules:
                r.hits = 0
        return None

    def show_acls(self, name):
        out = []
        for key, acl in self.device.acls.items():
            if name is not None and key != name:
                continue
            ext = acl.kind == "extended"
            head = f"{'Extended' if ext else 'Standard'} IP access list {key}"
            out.append(head)
            for r in acl.rules:
                hits = f" ({r.hits} match{'es' if r.hits != 1 else ''})" if r.hits else ""
                out.append(f"    {r.seq} {ACLX.entry_text(r, ext, show=True)}{hits}")
        return "\n".join(out)

    # -- NAT -------------------------------------------------------------------------------
    def if_nat(self, role, negate):
        if not self.is_router:
            return self.err_invalid(self.prompt, 0)
        cfg = self.device.nat_engine().cfg
        lst = getattr(cfg, role)
        other = cfg.outside if role == "inside" else cfg.inside
        name = self.iface.name
        if negate:
            if name in lst:
                lst.remove(name)
            return None
        if name in other:
            other.remove(name)
        if name not in lst:
            lst.append(name)
        return None

    def nat_static(self, local, glob, proto, lport, gport, negate):
        cfg = self.device.nat_engine().cfg
        rule = NatRule("static", local=local, glob=glob, proto=proto, local_port=lport, global_port=gport)
        same = [r for r in cfg.rules if r.kind == "static" and r.local == local and r.glob == glob
                and r.local_port == lport and r.global_port == gport]
        if negate:
            for r in same:
                cfg.rules.remove(r)
            self.device.nat.table = [t for t in self.device.nat.table if str(t.in_local[0]) != local]
            return None
        if any(r.kind == "static" and r.local == local and r.local_port == lport and r.glob != glob
               for r in cfg.rules):
            return f"% {local} already mapped ({next(r.glob for r in cfg.rules if r.kind == 'static' and r.local == local)} -> {local})"
        if not same:
            cfg.rules.append(rule)
        return None

    def nat_dynamic(self, acl, port, pool, overload, negate):
        cfg = self.device.nat_engine().cfg
        if negate:
            cfg.rules = [r for r in cfg.rules if not (r.kind == "dynamic" and r.acl == acl)]
            return None
        if pool is not None and pool not in cfg.pools:
            return f"%Pool {pool} not found"
        rule = NatRule("dynamic", acl=acl, interface=port.name if port else None,
                       pool=tuple(cfg.pools[pool][:2]) if pool else None, overload=overload,
                       text=f"pool {pool}" if pool else "")
        cfg.rules = [r for r in cfg.rules if not (r.kind == "dynamic" and r.acl == acl)] + [rule]
        return None

    def nat_pool(self, name, start, end, mask_or_len):
        from ..addresses import mask_to_prefix
        plen = mask_or_len if isinstance(mask_or_len, int) else mask_to_prefix(mask_or_len)
        if int(IPv4Address(end)) < int(IPv4Address(start)):
            return "%End address less than start address"
        if IPv4Network(f"{start}/{plen}", strict=False) != IPv4Network(f"{end}/{plen}", strict=False):
            return "%Start and end addresses on different subnets"
        cfg = self.device.nat_engine().cfg
        cfg.pools[name] = (start, end, plen)
        for r in cfg.rules:
            if r.kind == "dynamic" and r.text == f"pool {name}":
                r.pool = (start, end)
        return None

    def nat_pool_delete(self, name):
        cfg = self.device.nat_engine().cfg
        if any(r.text == f"pool {name}" for r in cfg.rules):
            return f"%Pool {name} in use, cannot destroy"
        cfg.pools.pop(name, None)
        return None

    def clear_nat(self):
        if self.device.nat is not None:
            self.device.nat.clear()
        return None

    def show_nat_translations(self):
        nat = self.device.nat
        if nat is None:
            return ""
        nat._expire()
        names = {1: "icmp", 6: "tcp", 17: "udp"}
        fmt = lambda ip, pid: f"{ip}:{pid}" if pid is not None else str(ip)
        lines = [f"{'Pro':<5}{'Inside global':<22}{'Inside local':<22}{'Outside local':<22}Outside global"]
        for t in nat.table:
            out = fmt(t.outside[0], t.outside[1] if t.in_global[1] is None else t.in_global[1])
            lines.append(f"{names.get(t.proto, '---'):<5}{fmt(*t.in_global):<22}{fmt(*t.in_local):<22}{out:<22}{out}")
        for local, glob, proto, lp, gp in nat.static_entries():
            g = f"{glob}:{gp}" if gp else glob
            l_ = f"{local}:{lp}" if lp else local
            lines.append(f"{(proto or '---'):<5}{g:<22}{l_:<22}{'---':<22}---")
        return "\n".join(lines)

    def show_nat_statistics(self):
        nat = self.device.nat
        if nat is None:
            return "Total active translations: 0 (0 static, 0 dynamic; 0 extended)"
        nat._expire()
        static = len(nat.static_entries())
        dyn = len(nat.table)
        ext = sum(1 for t in nat.table if t.in_global[1] is not None)
        lines = [f"Total active translations: {static + dyn} ({static} static, {dyn} dynamic; {ext} extended)",
                 "Outside interfaces:", "  " + ", ".join(nat.cfg.outside),
                 "Inside interfaces:", "  " + ", ".join(nat.cfg.inside),
                 f"Hits: {nat.hits}  Misses: {nat.misses}", "Dynamic mappings:", "-- Inside Source"]
        for r in nat.cfg.rules:
            if r.kind == "dynamic":
                target = f"interface {r.interface}" if r.interface else r.text
                lines.append(f"[Id: 1] access-list {r.acl} {target}{' overload' if r.overload else ''} refcount {dyn}")
        for name, (start, end, plen) in nat.cfg.pools.items():
            lines.append(f" pool {name}: netmask {IPv4Network(f'0.0.0.0/{plen}').netmask}")
            lines.append(f"\tstart {start} end {end}")
        return "\n".join(lines)

    # -- IPv6 ------------------------------------------------------------------------------
    def _v6_changed(self):
        self.device.ipv6_changed()
        return None

    def ipv6_routing(self, on):
        self.device.ipv6_routing = on
        return self._v6_changed()

    def v6_route(self, prefix, nh, iface, negate):
        return v6_route_cmd(self.device, prefix, nh, iface, negate)

    def show_ipv6_brief(self):
        lines = []
        for p in self.device.ports.values():
            st = "[up/up]" if p.is_up else ("[administratively down/down]" if not p.admin_up else "[down/down]")
            lines.append(f"{p.name:<26}{st}")
            addrs = self.device.addrs6(p)
            if not addrs:
                lines.append("    unassigned")
            for a in sorted(addrs, key=lambda x: not x.ip.is_link_local):
                lines.append(f"    {str(a.ip).upper()}")
        return "\n".join(lines)

    def show_ipv6_route(self, only: str | None = None):
        d = self.device
        tbl = d.routing_table6()
        if only:
            tbl = [r for r in tbl if r.code.startswith(only)]
        lines = [f"IPv6 Routing Table - default - {len(tbl)} entries",
                 "Codes: C - Connected, L - Local, S - Static, U - Per-user Static route",
                 "       B - BGP, R - RIP, I1 - ISIS L1, I2 - ISIS L2, D - EIGRP, EX - EIGRP external",
                 "       ND - ND Default, NDp - ND Prefix, DCE - Destination",
                 "       O - OSPF Intra, OI - OSPF Inter, OE1 - OSPF ext 1, OE2 - OSPF ext 2", ""]
        for r in tbl:
            if r.code in ("C", "L"):
                lines += [f"{r.code:<3}{str(r.network).upper()} [0/0]", f"     via {r.port.name}, directly connected"
                          if r.code == "C" else f"     via {r.port.name}, receive"]
            else:
                via = str(r.next_hop).upper() if r.next_hop else r.port.name
                lines += [f"{r.code:<3}{str(r.network).upper()} [{r.distance}/{r.metric}]", f"     via {via}"
                          + (f", {r.port.name}" if r.next_hop else "")]
        return "\n".join(lines)

    def show_ipv6_neighbors(self):
        d = self.device
        lines = [f"{'IPv6 Address':<41}{'Age':<4}{'Link-layer Addr':<16}{'State':<6}Interface"]
        for n in d.nd_cache.values():
            age = int((d.engine.now - n.learned) / 60000)
            lines.append(f"{str(n.ip).upper():<41}{age:<4}{n.mac.cisco():<16}{'REACH':<6}{n.port.name}")
        return "\n".join(lines)

    # -- RIP -------------------------------------------------------------------------------
    def snoop_opt82(self, on):
        self.device.dhcp_snooping["option82"] = on
        return None

    def relay_trust_all(self, negate):
        self.device.extra["dhcp_trust_all"] = not negate
        return None

    # -- copy to / from TFTP and FTP servers --------------------------------------------------------
    @staticmethod
    def _parse_url(url):
        """'tftp://10.0.0.1/R1-confg' -> ('TFTP', ip, file, user, password); 'tftp:' -> ('TFTP', None, ...)."""
        import re
        m = re.match(r"^(tftp|ftp):(?://(?:([^:@/]+):([^@/]*)@)?([0-9.]+)(?:/(.*))?)?$", url, re.I)
        if not m:
            return None
        return m.group(1).upper(), m.group(4), m.group(5), m.group(2) or "", m.group(3) or ""

    def _ask_chain(self, questions, finish):
        """Ask the IOS-style questions one after another; finish(answers) runs the transfer."""
        answers = []

        def step(i):
            if i >= len(questions):
                return finish(answers)
            prompt, default = questions[i]

            def got(line, i=i, default=default):
                answers.append(line.strip() or default)
                return step(i + 1)
            self.ask(f"{prompt} [{default}]? ", got, secret=False)
            return None
        return step(0)

    def copy_out(self, which, url):
        from ..services.files import Transfer
        p = self._parse_url(url)
        if p is None:
            return self.err_invalid(self.prompt, 0)
        proto, ip, name, user, pw = p
        d = self.device
        if which == "running":
            text = render_running(d, d.to_config())
        else:
            if d.startup_config is None:
                return "%Error opening nvram:/startup-config (No such file or directory)"
            text = render_running(d, d.startup_config)
        default_name = f"{d.hostname.lower()}-confg"

        def run(ans):
            host = ans[0]
            fname = ans[1] if len(ans) > 1 else default_name
            if not host or not __import__("netemu.core.addresses", fromlist=["x"]).is_valid_ip(host):
                return f"%Error parsing filename (Bad host address {host})"

            def done(ok, data):
                if ok:
                    self.write(f"!!\n{len(text)} bytes copied in 0.084 secs ({len(text) * 12} bytes/sec)\n")
                else:
                    self.write(f"%Error opening {proto.lower()}://{host}/{fname} ({data})\n")
            return Transfer(d, proto, host, "put", fname, done, text, user or "cisco", pw or "cisco").start()
        if ip and name:
            return run([ip, name])
        qs = [("Address or name of remote host", ip or "")] + ([("Destination filename", default_name)] if not name
                                                               else [])
        if name:
            return self._ask_chain(qs[:1], lambda a: run([a[0], name]))
        return self._ask_chain(qs, run)

    def copy_in(self, url, target):
        from ..services.files import Transfer
        p = self._parse_url(url)
        if p is None:
            return self.err_invalid(self.prompt, 0)
        proto, ip, name, user, pw = p
        d = self.device

        def run(ans):
            host, fname = ans[0], ans[1]

            def done(ok, data):
                if not ok:
                    self.write(f"%Error opening {proto.lower()}://{host}/{fname} ({data})\n")
                    return
                text = str(data)
                self.write(f"Accessing {proto.lower()}://{host}/{fname}...\nLoading {fname} from {host}: !\n"
                           f"[OK - {len(text)} bytes]\n")
                if target == "startup":
                    s2 = CiscoSession(d)
                    s2.mode = "config"
                    self._apply_text(s2, text)
                    d.save_startup()
                    self.write(f"{len(text)} bytes copied\n")
                    return
                s2 = CiscoSession(d)
                s2.mode = "config"
                self._apply_text(s2, text)
                self.write(f"{len(text)} bytes copied in 0.112 secs\n")
            return Transfer(d, proto, host, "get", fname, done, None, user or "cisco", pw or "cisco").start()
        if ip and name:
            return run([ip, name])
        return self._ask_chain([("Address or name of remote host", ip or ""), ("Source filename", name or ""),
                                ("Destination filename", "running-config" if target == "running" else "startup-config")],
                               run)

    @staticmethod
    def _apply_text(session, text):
        for line in text.splitlines():
            st = line.rstrip()
            if not st or st.startswith("!") or st.split()[0] in ("version", "end", "Building", "Current"):
                if st.startswith("!") and session.mode not in ("config",):
                    session.mode = "config"
                    session.iface = None
                continue
            session.execute(st.strip(), lambda x: None)

    # -- AAA: RADIUS / TACACS+ servers, login authentication, 802.1X ----------------------------
    def _add_aaa_trees(self, t):
        de = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
        methods = P.arg(P.LineArg("group tacacs+ | group radius | local ..."), "Method list",
                        handler=lambda s, a, n: s.aaa_login(a[0], n), negatable=True)
        t["config"] += [
            P.kw("aaa", "Authentication, Authorization and Accounting",
                 P.kw("new-model", "Enable NEW access control commands and functions",
                      handler=lambda s, a, n: s.aaa_new_model(n), negatable=True),
                 P.kw("authentication", "Authentication configurations parameters",
                      P.kw("login", "Set authentication lists for logins",
                           P.kw("default", "The default authentication list", methods), negatable=True),
                      P.kw("dot1x", "Set authentication lists for IEEE 802.1x",
                           P.kw("default", "The default authentication list",
                                P.arg(P.LineArg("group radius"), "Method list", handler=lambda s, a, n: None,
                                      negatable=True)), negatable=True), negatable=True),
                 negatable=True),
            P.kw("tacacs", "Modify TACACS query parameters", P.kw("server", "Server configuration",
                 P.arg(P.WordArg("WORD"), "Name for the tacacs server configuration",
                       handler=lambda s, a, n: s.aaa_server("tacacs+", a[0], n), negatable=True), negatable=True),
                 negatable=True),
            P.kw("radius", "Server configuration", P.kw("server", "Server configuration",
                 P.arg(P.WordArg("WORD"), "Name for the radius server configuration",
                       handler=lambda s, a, n: s.aaa_server("radius", a[0], n), negatable=True), negatable=True),
                 negatable=True),
            P.kw("tacacs-server", "Modify TACACS query parameters", P.kw("host", "Specify a TACACS server",
                 P.arg(P.IpArg(), "IP address", P.kw("key", "per-server encryption key",
                       P.arg(P.WordArg("LINE"), "The UNENCRYPTED server key",
                             handler=lambda s, a, n: s.aaa_legacy("tacacs+", a[0], a[1], n))),
                       handler=lambda s, a, n: s.aaa_legacy("tacacs+", a[0], "", n), negatable=True),
                 negatable=True), negatable=True),
            P.kw("radius-server", "Modify RADIUS query parameters", P.kw("host", "Specify a RADIUS server",
                 P.arg(P.IpArg(), "IP address", P.kw("key", "per-server encryption key",
                       P.arg(P.WordArg("LINE"), "The UNENCRYPTED server key",
                             handler=lambda s, a, n: s.aaa_legacy("radius", a[0], a[1], n))),
                       P.arg(P.LineArg("auth-port 1812 acct-port 1813 key K"), "options",
                             handler=lambda s, a, n: s.aaa_legacy("radius", a[0], a[1].split("key")[-1].strip()
                                                                  if "key" in a[1] else "", n)),
                       handler=lambda s, a, n: s.aaa_legacy("radius", a[0], "", n), negatable=True),
                 negatable=True), negatable=True),
            P.kw("dot1x", "IEEE 802.1X Global Configuration Commands",
                 P.kw("system-auth-control", "Enable or Disable SysAuthControl",
                      handler=lambda s, a, n: s.aaa_flag("dot1x", n), negatable=True), negatable=True)]
        srv = lambda mode: sorted(de + [
            P.kw("exit", "Exit from server configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("address", "Server address", P.kw("ipv4", "IPv4 address", P.arg(P.IpArg(), "IP address",
                 P.arg(P.LineArg("auth-port 1812 acct-port 1813"), "ports", handler=lambda s, a, n: s.aaa_attr("ip", a[0])),
                 handler=lambda s, a, n: s.aaa_attr("ip", a[0])))),
            P.kw("key", "Per-server encryption key", P.arg(P.LineArg(), "The UNENCRYPTED server key",
                 handler=lambda s, a, n: s.aaa_attr("key", a[0].split()[-1]), negatable=True), negatable=True)],
            key=lambda x: x.word or "")
        t["config-server-tacacs"] = srv("tacacs")
        t["config-radius-server"] = srv("radius")
        if not self.is_router:
            t["config-if"] += [
                P.kw("authentication", "Auth Manager Interface Configuration Commands",
                     P.kw("port-control", "set the port-control value",
                          P.kw("auto", "PortState will be set to AUTO", handler=lambda s, a, n: s.if_dot1x(n), negatable=True),
                          P.kw("force-authorized", "PortState set to Authorized",
                               handler=lambda s, a, n: s.if_dot1x(True)), negatable=True), negatable=True),
                P.kw("dot1x", "Interface Config Commands for IEEE 802.1X",
                     P.kw("pae", "Set 802.1x interface pae type", P.kw("authenticator", "Set pae type as Authenticator",
                          handler=lambda s, a, n: None, negatable=True)), negatable=True)]
        show = next(x for x in t["priv"] if x.word == "show")
        if not self.is_router:
            show.children += [
                P.kw("dot1x", "Dot1x information", P.kw("all", "Show Dot1x information for all interfaces",
                     handler=lambda s, a, n: s.show_dot1x())),
                P.kw("authentication", "Shows Auth Manager registrations or sessions",
                     P.kw("sessions", "Display sessions", handler=lambda s, a, n: s.show_dot1x(True)))]
        show.children.append(P.kw("aaa", "Show AAA values", P.kw("servers", "Server information",
                                                                handler=lambda s, a, n: s.show_aaa())))

    def _aaa(self):
        return self.device.extra.setdefault("aaa", {"new_model": False, "login": [], "radius": {}, "tacacs+": {}})

    def aaa_new_model(self, negate):
        from ..services.remote import access
        a = self._aaa()
        a["new_model"] = not negate
        acc = access(self.device)
        if not negate and acc.get("login") == "line":
            acc["login"] = "local"                      # vty lines now use the default login list
        return None

    def aaa_login(self, text, negate):
        a = self._aaa()
        if negate:
            a["login"] = []
            return None
        if not a["new_model"]:
            return "% AAA must be enabled first: aaa new-model"
        words = text.split()
        methods, i = [], 0
        while i < len(words):
            if words[i] == "group" and i + 1 < len(words) and words[i + 1] in ("tacacs+", "radius"):
                methods.append(f"group {words[i + 1]}")
                i += 2
            elif words[i] in ("local", "none", "enable"):
                methods.append(words[i])
                i += 1
            else:
                return self.err_invalid(self.prompt, 0)
        a["login"] = methods
        return None

    def aaa_server(self, group, name, negate):
        a = self._aaa()
        if negate:
            a[group].pop(name, None)
            return None
        a[group].setdefault(name, {"ip": None, "key": ""})
        self._aaa_srv = (group, name)
        self.mode = "config-server-tacacs" if group == "tacacs+" else "config-radius-server"
        return None

    def aaa_attr(self, attr, value):
        group, name = self._aaa_srv
        self._aaa()[group][name][attr] = str(value)
        return None

    def aaa_legacy(self, group, ip, key, negate):
        a = self._aaa()
        name = f"host-{ip}"
        if negate:
            a[group].pop(name, None)
        else:
            a[group][name] = {"ip": str(ip), "key": key}
        return None

    def aaa_flag(self, flag, negate):
        self._aaa()[flag] = not negate
        return None

    def if_dot1x(self, negate):
        p = self.iface
        p.dot1x = not negate
        p.dot1x_ok = False
        peer = p.peer
        if p.dot1x and peer is not None and getattr(peer.device, "dot1x", None):
            peer.device.engine.schedule(50.0, peer.device._eapol_start)
        return None

    def show_dot1x(self, sessions=False):
        ports = [p for p in self.device.ports.values() if p.dot1x]
        if sessions:
            lines = ["Interface    MAC Address    Method  Domain  Status  Fg  Session ID"]
            for p in ports:
                mac = p.peer.mac.cisco() if p.peer is not None else "-"
                lines.append(f"{p.short:<13}{mac:<15}dot1x   DATA    {'Auth' if p.dot1x_ok else 'Unauth':<8}    "
                             f"0A00000{len(lines)}00000001")
            return "\n".join(lines)
        out = [f"Sysauthcontrol              {'Enabled' if self._aaa().get('dot1x') else 'Disabled'}",
               "Dot1x Protocol Version            3", ""]
        for p in ports:
            out += [f"Dot1x Info for {p.name}", "-" * 37, "PAE                       = AUTHENTICATOR",
                    "PortControl               = AUTO", f"Status                    = "
                    f"{'AUTHORIZED' if p.dot1x_ok else 'UNAUTHORIZED'}", f"User                      = {p.dot1x_user or '-'}", ""]
        return "\n".join(out)

    def show_aaa(self):
        a = self._aaa()
        out = []
        for group in ("radius", "tacacs+"):
            for name, srv in a[group].items():
                out.append(f"{'RADIUS' if group == 'radius' else 'TACACS+'}: id {len(out) + 1}, priority 1, host {srv['ip']}, "
                           f"auth-port {1812 if group == 'radius' else 49}")
                out.append(f"     State: current UP, duration 0s, previous duration 0s ({name})")
        out.append(f"Login method list: {' '.join(a['login']) or 'not set'}")
        return "\n".join(out)

    # -- Zone-Based Policy Firewall ---------------------------------------------------------------
    def _add_zbf_trees(self, t):
        de = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
        ex = lambda m="config": P.kw("exit", "Exit", handler=lambda s, a, n: s.set_mode(m))
        t["config"].append(P.kw("zone", "FW with zoning", P.kw("security", "Security zone",
            P.arg(P.WordArg("WORD"), "Zone name", handler=lambda s, a, n: s.zbf_zone(a[0], n), negatable=True),
            negatable=True), negatable=True))
        t["config"].append(P.kw("zone-pair", "Zone pair command", P.kw("security", "Zone-pair name",
            P.arg(P.WordArg("WORD"), "Zone-pair name", P.kw("source", "Source zone", P.arg(P.WordArg("WORD"), "Source zone",
                  P.kw("destination", "Destination zone", P.arg(P.WordArg("WORD"), "Destination zone",
                       handler=lambda s, a, n: s.zbf_pair(a[0], a[1], a[2], n), negatable=True)))),
                  handler=lambda s, a, n: s.zbf_pair(a[0], None, None, n) if n else s.err_incomplete("", 0),
                  negatable=True), negatable=True), negatable=True))
        cm = next(x for x in t["config"] if x.word == "class-map")
        pm = next(x for x in t["config"] if x.word == "policy-map")
        cname = lambda: P.arg(P.WordArg("WORD"), "class-map name", handler=lambda s, a, n: s.zbf_cmap(a[-1], n),
                              negatable=True)
        cm.children.append(P.kw("type", "Configure CPL Class Map", P.kw("inspect", "Configure Firewall Class Map",
            P.kw("match-any", "Logical-OR all matching statements", cname()),
            P.kw("match-all", "Logical-AND all matching statements", cname()), cname())))
        pm.children.append(P.kw("type", "Configure CPL Policy Map", P.kw("inspect", "Configure Firewall Policy Map",
            P.arg(P.WordArg("WORD"), "policy-map name", handler=lambda s, a, n: s.zbf_pmap(a[0], n), negatable=True))))
        t["config-sec-zone"] = sorted(de + [ex(), P.kw("description", "Zone description",
                                                     P.arg(P.LineArg(), "Description", handler=lambda s, a, n: None))],
                                      key=lambda x: x.word or "")
        t["config-sec-zone-pair"] = sorted(de + [ex(), P.kw("service-policy", "Configure CPL Service Policy",
            P.kw("type", "Service policy type", P.kw("inspect", "Configure Firewall Policy",
                 P.arg(P.WordArg("WORD"), "policy-map name", handler=lambda s, a, n: s.zbf_service(a[0], n),
                       negatable=True))), negatable=True)], key=lambda x: x.word or "")
        t["config-cmap-insp"] = sorted(de + [ex(), P.kw("match", "Configure classification criteria",
            P.kw("protocol", "Protocol", P.arg(P.WordArg("icmp|tcp|udp|http|https|ssh|dns|telnet"), "Protocol",
                 handler=lambda s, a, n: s.zbf_match("protocol", a[0], n), negatable=True)),
            P.kw("access-group", "Access group", P.kw("name", "Named access list", P.arg(P.WordArg("WORD"), "ACL",
                 handler=lambda s, a, n: s.zbf_match("acl", a[0], n), negatable=True))), negatable=True)],
            key=lambda x: x.word or "")
        t["config-pmap-insp"] = sorted(de + [ex(), P.kw("class", "policy criteria",
            P.kw("type", "class type", P.kw("inspect", "inspect class", P.arg(P.WordArg("WORD"), "class-map name",
                 handler=lambda s, a, n: s.zbf_pclass(a[0], n), negatable=True))),
            P.kw("class-default", "System default class", handler=lambda s, a, n: s.zbf_pclass("class-default", n),
                 negatable=True), negatable=True)], key=lambda x: x.word or "")
        t["config-pmap-c-insp"] = sorted(de + [ex("config-pmap-insp")] + [
            P.kw(w, h, handler=lambda s, a, n, w=w: s.zbf_action(w)) for w, h in
            (("inspect", "Context-based Access Control Engine"), ("pass", "Pass packet"), ("drop", "Drop packet"))],
            key=lambda x: x.word or "")
        t["config-if"].append(P.kw("zone-member", "Apply zone name", P.kw("security", "Security zone",
            P.arg(P.WordArg("WORD"), "Zone name", handler=lambda s, a, n: s.zbf_member(a[0], n), negatable=True),
            handler=lambda s, a, n: s.zbf_member(None, True) if n else s.err_incomplete("", 0), negatable=True),
            negatable=True))
        show = next(x for x in t["priv"] if x.word == "show")
        show.children += [
            P.kw("zone", "Zone information", P.kw("security", "Security zone", handler=lambda s, a, n: s.show_zbf("zones"))),
            P.kw("zone-pair", "Zone pair information", P.kw("security", "Security zone-pair",
                 handler=lambda s, a, n: s.show_zbf("pairs")))]
        pmshow = next(x for x in show.children if x.word == "policy-map")
        pmshow.children.append(P.kw("type", "type", P.kw("inspect", "inspect", P.kw("zone-pair", "zone-pair",
            P.kw("sessions", "Inspect sessions", handler=lambda s, a, n: s.show_zbf("sessions")),
            handler=lambda s, a, n: s.show_zbf("sessions")))))

    def _zbf(self):
        return self.device.extra.setdefault("zbf", {"zones": {}, "members": {}, "classes": {}, "policies": {},
                                                    "pairs": {}})

    def _zbf_sync(self):
        from ..security.stateful import ZoneRule, ZoneFirewall
        z = self._zbf()
        zf = ZoneFirewall()
        for zone in z["zones"]:
            zf.zones[zone] = [i for i, m in z["members"].items() if m == zone]
        for pname, pr in z["pairs"].items():
            pol = z["policies"].get(pr.get("policy") or "", [])
            rules = []
            for cname, action in pol:
                action = {"inspect": "inspect", "pass": "pass"}.get(action, "drop")
                if cname == "class-default":
                    rules.append(ZoneRule(cname, action))
                    continue
                for kind, val in z["classes"].get(cname, []):
                    if kind == "protocol":
                        rules.append(ZoneRule(cname, action, val))
                    else:
                        acl = self.device.acls.get(val)
                        for r in (acl.rules if acl else []):
                            if r.action == "permit":
                                rules.append(ZoneRule(cname, action, r.proto if r.proto != "ip" else "any",
                                                      str(r.src), str(r.dst)))
            if not any(r.name == "class-default" for r in rules):
                rules.append(ZoneRule("class-default", "drop"))
            zf.pairs[f"{pr['src']}>{pr['dst']}"] = rules
        self.device.zfw = zf

    def zbf_zone(self, name, negate):
        z = self._zbf()
        if negate:
            z["zones"].pop(name, None)
            z["members"] = {i: m for i, m in z["members"].items() if m != name}
        else:
            z["zones"].setdefault(name, "")
            self.mode = "config-sec-zone"
        self._zbf_sync()
        return None

    def zbf_pair(self, name, src, dst, negate):
        z = self._zbf()
        if negate:
            z["pairs"].pop(name, None)
        else:
            for zone in (src, dst):
                if zone != "self" and zone not in z["zones"]:
                    return f"% Zone {zone} doesn't exist"
            z["pairs"][name] = {"src": src, "dst": dst, "policy": None}
            self._zpair, self.mode = name, "config-sec-zone-pair"
        self._zbf_sync()
        return None

    def zbf_service(self, policy, negate):
        self._zbf()["pairs"][self._zpair]["policy"] = None if negate else policy
        self._zbf_sync()
        return None

    def zbf_cmap(self, name, negate):
        z = self._zbf()
        if negate:
            z["classes"].pop(name, None)
        else:
            z["classes"].setdefault(name, [])
            self._zcls, self.mode = name, "config-cmap-insp"
        self._zbf_sync()
        return None

    def zbf_match(self, kind, value, negate):
        lst = self._zbf()["classes"][self._zcls]
        item = [kind, value.lower() if kind == "protocol" else value]
        if negate:
            self._zbf()["classes"][self._zcls] = [x for x in lst if x != item]
        elif item not in lst:
            lst.append(item)
        self._zbf_sync()
        return None

    def zbf_pmap(self, name, negate):
        z = self._zbf()
        if negate:
            z["policies"].pop(name, None)
        else:
            z["policies"].setdefault(name, [])
            self._zpol, self.mode = name, "config-pmap-insp"
        self._zbf_sync()
        return None

    def zbf_pclass(self, cname, negate):
        pol = self._zbf()["policies"][self._zpol]
        pol[:] = [x for x in pol if x[0] != cname]
        if not negate:
            pol.append([cname, "drop"])
            self._zpcls, self.mode = cname, "config-pmap-c-insp"
        self._zbf_sync()
        return None

    def zbf_action(self, action):
        for x in self._zbf()["policies"][self._zpol]:
            if x[0] == self._zpcls:
                x[1] = action
        self._zbf_sync()
        return None

    def zbf_member(self, zone, negate):
        z = self._zbf()
        if negate:
            z["members"].pop(self.iface.name, None)
        else:
            if zone not in z["zones"]:
                return f"% Zone {zone} doesn't exist"
            z["members"][self.iface.name] = zone
        self._zbf_sync()
        return None

    def show_zbf(self, what):
        z = self._zbf()
        if what == "zones":
            out = ["zone self", "  Description: System Defined Zone"]
            for zone in z["zones"]:
                out += [f"zone {zone}", "  Member Interfaces:"]
                out += [f"    {i}" for i, m in z["members"].items() if m == zone]
            return "\n".join(out)
        if what == "pairs":
            out = []
            for name, pr in z["pairs"].items():
                out += [f"Zone-pair name {name}", f"    Source-Zone {pr['src']}  Destination-Zone {pr['dst']}",
                        f"    service-policy {pr.get('policy') or 'not configured'}"]
            return "\n".join(out)
        out = []
        for f in self.device.conntrack.active():
            proto, src, dst, sp, dp = f.key
            name = {1: "icmp", 6: "tcp", 17: "udp"}.get(proto, str(proto))
            out.append(f"  Session {abs(hash(f.key)) % 0xFFFFFFFF:08X} ({src}:{sp})=>({dst}:{dp}) {name} SIS_OPEN"
                       f"  [{f.zone_pair}]")
        return "\n".join([" Established Sessions"] + out) if out else " No sessions"

    # -- MPLS / LDP ------------------------------------------------------------------------------
    def _add_mpls_trees(self, t):
        t["config-if"].append(P.kw("mpls", "Configure MPLS interface parameters",
            P.kw("ip", "Configure dynamic MPLS forwarding for IP", handler=lambda s, a, n: s.if_mpls(n), negatable=True),
            P.kw("label", "Label protocol", P.kw("protocol", "Configure label/tag distribution protocol (LDP)",
                 P.kw("ldp", "Use LDP (default)", handler=lambda s, a, n: None, negatable=True))), negatable=True))
        t["config"].append(P.kw("mpls", "Configure MPLS parameters",
            P.kw("label", "Label properties", P.kw("protocol", "Set platform default label distribution protocol",
                 P.kw("ldp", "Use LDP (default)", handler=lambda s, a, n: None, negatable=True), negatable=True),
                 negatable=True),
            P.kw("ldp", "Label Distribution Protocol", P.kw("router-id", "Select interface to be used as LDP router id",
                 P.arg(P.IfaceArg(), "Interface",
                       P.kw("force", "Forcibly change the LDP router id", handler=lambda s, a, n: s.mpls_rid(a[0], n)),
                       handler=lambda s, a, n: s.mpls_rid(a[0], n), negatable=True), negatable=True), negatable=True),
            P.kw("ip", "Dynamic MPLS forwarding for IP", handler=lambda s, a, n: None, negatable=True), negatable=True))
        show = next(x for x in t["priv"] if x.word == "show")
        show.children.append(P.kw("mpls", "MPLS information",
            P.kw("ldp", "Label Distribution Protocol information",
                 P.kw("neighbor", "Display LDP neighbor information", handler=lambda s, a, n: s.show_mpls("neighbor")),
                 P.kw("bindings", "Show the LDP Label Information Base (LIB)",
                      handler=lambda s, a, n: s.show_mpls("bindings"))),
            P.kw("forwarding-table", "Show the Label Forwarding Information Base (LFIB)",
                 handler=lambda s, a, n: s.show_mpls("forwarding")),
            P.kw("interfaces", "Per-interface MPLS forwarding information", handler=lambda s, a, n: s.show_mpls("interfaces"))))

    def if_mpls(self, negate):
        m = self.device.mpls
        name = self.iface.name
        if negate:
            m.interfaces = [x for x in m.interfaces if x != name]
        elif name not in m.interfaces:
            m.interfaces.append(name)
        m.ensure()
        return None

    def mpls_rid(self, port, negate):
        self.device.mpls.router_id_iface = None if negate else port.name
        return None

    def show_mpls(self, what):
        m = self.device.mpls
        if what == "neighbor":
            out = []
            now = self.device.engine.now
            for lid, p in m.peers.items():
                up = int((now - p.up_since) / 1000)
                out += [f"    Peer LDP Ident: {lid}:0; Local LDP Ident {m.lsr_id()}:0",
                        f"        TCP connection: {lid}.646 - {m.lsr_id()}.{11000 + len(out)}",
                        f"        State: Oper; Msgs sent/rcvd: {10 + up // 5}/{10 + up // 5}; Downstream",
                        f"        Up time: {up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}",
                        "        LDP discovery sources:", f"          {p.port.name}, Src IP addr: {p.ip}",
                        "        Addresses bound to peer LDP Ident:",
                        "          " + "  ".join(str(a) for a in sorted(p.addresses))]
            return "\n".join(out)
        if what == "bindings":
            out = []
            for net, lab in sorted(m.local.items(), key=lambda x: (int(x[0].network_address), x[0].prefixlen)):
                out += [f"  lib entry: {net}, rev {lab}",
                        f"        local binding:  label: {'imp-null' if lab == 3 else lab}"]
                for lid, p in m.peers.items():
                    if net in p.bindings:
                        rl = p.bindings[net]
                        out.append(f"        remote binding: lsr: {lid}:0, label: {'imp-null' if rl == 3 else rl}")
            return "\n".join(out)
        if what == "interfaces":
            lines = ["Interface              IP            Tunnel   BGP Static Operational"]
            for name in m.interfaces:
                p = self.device.ports.get(name)
                if p is not None:
                    lines.append(f"{p.name:<23}Yes (ldp)     No       No  No     {'Yes' if p.is_up else 'No'}")
            return "\n".join(lines)
        lines = ["Local      Outgoing   Prefix           Bytes Label   Outgoing   Next Hop",
                 "Label      Label      or Tunnel Id     Switched      interface"]
        for lab, (net, out, port, nh) in sorted(m.lfib.items()):
            o = "Pop Label" if out in (None, 3) else str(out)
            lines.append(f"{lab:<11}{o:<11}{str(net):<17}{0:<14}{port.short:<11}{nh}")
        return "\n".join(lines)

    # -- IS-IS -----------------------------------------------------------------------------------
    def _add_isis_trees(self, t):
        de = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
        noop = lambda w, h: P.kw(w, h, P.arg(P.LineArg(), h, handler=lambda s, a, n: None, negatable=True),
                                 handler=lambda s, a, n: None, negatable=True)
        t["config-router-isis"] = sorted(de + [
            P.kw("exit", "Exit from routing protocol configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("net", "A Network Entity Title for this process (OSI only)",
                 P.arg(P.WordArg("XX.XXXX. ... .XXX.XX"), "Network entity title (NET)",
                       handler=lambda s, a, n: s.isis_net(a[0], n), negatable=True), negatable=True),
            P.kw("passive-interface", "Suppress routing updates on an interface",
                 P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.isis_passive(a[0], n), negatable=True),
                 negatable=True),
            noop("is-type", "IS Level for this routing process"), noop("metric-style", "Use old/new style metrics"),
            noop("log-adjacency-changes", "Log changes in adjacency state")], key=lambda x: x.word or "")
        ifip = next(x for x in t["config-if"] if x.word == "ip")
        ifip.children.append(P.kw("router", "IP router interface commands", P.kw("isis", "IS-IS Routing for IP",
            P.arg(P.WordArg("WORD"), "Routing process tag", handler=lambda s, a, n: s.if_isis(n), negatable=True),
            handler=lambda s, a, n: s.if_isis(n), negatable=True), negatable=True))
        t["config-if"].append(P.kw("isis", "IS-IS commands", P.kw("metric", "Configure the metric for interface",
            P.arg(P.NumArg(1, 16777214, "<1-16777214>"), "Default metric",
                  P.arg(P.LineArg(), "level", handler=lambda s, a, n: s.isis_metric(a[0], n)),
                  handler=lambda s, a, n: s.isis_metric(a[0], n), negatable=True), negatable=True),
            noop("circuit-type", "Configure circuit type for interface"), negatable=True))
        show = next(x for x in t["priv"] if x.word == "show")
        show.children += [
            P.kw("isis", "IS-IS routing information",
                 P.kw("neighbors", "IS-IS neighbors", handler=lambda s, a, n: s.show_isis("neighbors")),
                 P.kw("database", "IS-IS link state database", handler=lambda s, a, n: s.show_isis("database"))),
            P.kw("clns", "CLNS network information", P.kw("neighbors", "CLNS neighbor adjacencies",
                                                          handler=lambda s, a, n: s.show_isis("neighbors")))]
        sip = next(x for x in show.children if x.word == "ip")
        route = next(x for x in sip.children if x.word == "route")
        route.children.append(P.kw("isis", "ISO IS-IS", handler=lambda s, a, n: s.show_ip_route("i")))

    def router_isis(self, tag, negate):
        from ..routing.isis import IsisConfig
        d = self.device
        if negate:
            d.disable_isis()
            return None
        if d.isis is None:
            d.enable_isis(IsisConfig(tag=str(tag)))
        self.mode = "config-router-isis"
        return None

    def isis_net(self, net, negate):
        from ..routing.isis import parse_net
        p = self.device.isis
        if negate:
            p.cfg.net = None
            p.stop()
            return None
        if parse_net(net) is None:
            return "%CLNS: Invalid NET (format: 49.0001.0000.0000.0001.00)"
        p.cfg.net = net
        p.restart()
        return None

    def isis_passive(self, port, negate):
        lst = self.device.isis.cfg.passive
        if negate:
            self.device.isis.cfg.passive = [x for x in lst if x != port.name]
        elif port.name not in lst:
            lst.append(port.name)
        return None

    def if_isis6(self, negate):
        from ..routing.isis import IsisConfig
        d = self.device
        if not negate and not d.ip6_forwarding:
            return "% IPv6 routing not enabled"
        if d.isis is None:
            if negate:
                return None
            d.enable_isis(IsisConfig())
        lst = d.isis.cfg.interfaces6
        if negate:
            d.isis.cfg.interfaces6 = [x for x in lst if x != self.iface.name]
        elif self.iface.name not in lst:
            lst.append(self.iface.name)
        d.isis.link_changed(self.iface)
        return None

    def if_isis(self, negate):
        from ..routing.isis import IsisConfig
        d = self.device
        if d.isis is None:
            if negate:
                return None
            d.enable_isis(IsisConfig())
        lst = d.isis.cfg.interfaces
        if negate:
            d.isis.cfg.interfaces = [x for x in lst if x != self.iface.name]
        elif self.iface.name not in lst:
            lst.append(self.iface.name)
        d.isis.link_changed(self.iface)
        return None

    def isis_metric(self, value, negate):
        d = self.device
        if d.isis is None:
            return None
        if negate:
            d.isis.cfg.metrics.pop(self.iface.name, None)
        else:
            d.isis.cfg.metrics[self.iface.name] = int(value)
        d.isis.link_changed(self.iface)
        return None

    def show_isis(self, what):
        from .base import isis_database_lines, isis_neighbors_rows
        if what == "neighbors":
            lines = ["", f"Tag {self.device.isis.cfg.tag if self.device.isis else ''}:",
                     "System Id           Type Interface     IP Address      State Holdtime Circuit Id"]
            for sid, host, port, ip, state, hold in isis_neighbors_rows(self.device):
                lines.append(f"{host:<20}L2   {port.short:<14}{ip:<16}{state:<6}{hold:<9}{host}.01")
            return "\n".join(lines)
        return "\n".join(["", f"Tag {self.device.isis.cfg.tag if self.device.isis else ''}:",
                          "IS-IS Level-2 Link State Database:",
                          "LSPID                 LSP Seq Num  LSP Checksum  LSP Holdtime/Rcvd      ATT/P/OL"]
                         + isis_database_lines(self.device))

    # -- VRF-lite --------------------------------------------------------------------------------
    def _add_vrf_trees(self, t):
        name = lambda h="VRF name": P.arg(P.WordArg("WORD"), h, handler=lambda s, a, n: s.vrf_define(a[0], n),
                                          negatable=True)
        ipn = next(x for x in t["config"] if x.word == "ip")
        ipn.children.append(P.kw("vrf", "Configure an IP VPN Routing/Forwarding instance", name(), negatable=True))
        t["config"].append(P.kw("vrf", "VRF commands", P.kw("definition", "VRF definition", name(), negatable=True),
                                negatable=True))
        route = next(x for x in ipn.children if x.word == "route")
        via_ip = P.arg(P.IpArg(), "Forwarding router's address",
                       handler=lambda s, a, n: s.ip_route_vrf(a[0], a[1], a[2], a[3], None, n), negatable=True)
        via_if = P.arg(P.IfaceArg(), "Exit interface",
                       handler=lambda s, a, n: s.ip_route_vrf(a[0], a[1], a[2], None, a[3], n), negatable=True)
        mask = P.arg(P.MaskArg(), "Destination prefix mask", via_ip, via_if)
        vrf_name = P.arg(P.WordArg("WORD"), "VPN Routing/Forwarding instance name",
                         P.arg(P.IpArg(), "Destination prefix", mask))
        route.children.append(P.kw("vrf", "Configure static route for a VPN Routing/Forwarding instance", vrf_name,
                                   negatable=True))
        de = [next(x for x in t["config"] if x.word in w) for w in (("do",), ("end",))]
        noop = lambda w, h: P.kw(w, h, P.arg(P.LineArg(), h, handler=lambda s, a, n: None, negatable=True),
                                 handler=lambda s, a, n: None, negatable=True)
        t["config-vrf"] = sorted(de + [
            P.kw("exit", "Exit from VRF configuration mode", handler=lambda s, a, n: s.set_mode("config")),
            P.kw("rd", "Specify Route Distinguisher", P.arg(P.WordArg("ASN:nn or IP-address:nn"), "VPN Route Distinguisher",
                 handler=lambda s, a, n: s.vrf_attr("rd", a[0], n), negatable=True), negatable=True),
            P.kw("description", "VRF specific description", P.arg(P.LineArg(), "Up to 244 characters",
                 handler=lambda s, a, n: s.vrf_attr("description", a[0], n), negatable=True), negatable=True),
            noop("route-target", "Specify Target VPN Extended Communities"),
            noop("address-family", "Enter Address Family command mode"),
            noop("exit-address-family", "Exit from Address Family configuration mode")],
            key=lambda x: x.word or "")
        ifip = next(x for x in t["config-if"] if x.word == "ip")
        fwd = lambda: P.kw("forwarding", "Configure forwarding table",
                           P.arg(P.WordArg("WORD"), "VRF name", handler=lambda s, a, n: s.if_vrf(a[0], n), negatable=True),
                           handler=lambda s, a, n: s.if_vrf(None, True) if n else s.err_incomplete("", 0), negatable=True)
        ifip.children.append(P.kw("vrf", "VPN Routing/Forwarding parameters on the interface", fwd(), negatable=True))
        t["config-if"].append(P.kw("vrf", "VPN Routing/Forwarding parameters on the interface", fwd(), negatable=True))
        show = next(x for x in t["priv"] if x.word == "show")
        sip = next(x for x in show.children if x.word == "ip")
        sip.children.append(P.kw("vrf", "VPN Routing/Forwarding instance information",
                                 P.kw("brief", "Brief VPN Routing/Forwarding instance information",
                                      handler=lambda s, a, n: s.show_vrf()),
                                 handler=lambda s, a, n: s.show_vrf()))
        sroute = next(x for x in sip.children if x.word == "route")
        sroute.children.append(P.kw("vrf", "Display routes from a VPN Routing/Forwarding instance",
                                    P.arg(P.WordArg("WORD"), "VPN Routing/Forwarding instance name",
                                          handler=lambda s, a, n: s.show_route_vrf(a[0]))))
        for word, fn in (("ping", "ping_vrf"), ("traceroute", "trace_vrf")):
            node = next(x for x in t["priv"] if x.word == word)
            node.children.append(P.kw("vrf", "Select VPN Routing/Forwarding instance",
                                      P.arg(P.WordArg("WORD"), "VPN Routing/Forwarding instance name",
                                            P.arg(P.IpArg(), "Destination address",
                                                  handler=lambda s, a, n, fn=fn: getattr(s, fn)(a[0], a[1])))))

    def _vrfs(self):
        return self.device.extra.setdefault("vrfs", {})

    def vrf_define(self, name, negate):
        v = self._vrfs()
        if negate:
            v.pop(name, None)
            for p in self.device.ports.values():
                if p.vrf == name:
                    p.vrf, p.ip = None, None
            self.device.static_routes = [r for r in self.device.static_routes if r.vrf != name]
            self.device.routes_changed()
            return None
        v.setdefault(name, {})
        self._vrf, self.mode = name, "config-vrf"
        return None

    def vrf_attr(self, attr, value, negate):
        d = self._vrfs()[self._vrf]
        if negate:
            d.pop(attr, None)
        else:
            d[attr] = value
        return None

    def if_vrf(self, name, negate):
        p = self.iface
        if negate:
            name = None
        elif name not in self._vrfs():
            return f"% VRF {name} not configured."
        if p.vrf == name:
            return None
        had_ip = p.ip is not None
        p.vrf, p.ip = name, None
        self.device.arp.flush_port(p) if hasattr(self.device.arp, "flush_port") else None
        self.device.routes_changed()
        if had_ip:
            return (f"% Interface {p.name} IPv4 disabled and address(es) removed due to "
                    + (f"enabling VRF {name}" if name else "disabling VRF"))
        return None

    def ip_route_vrf(self, vrf, net, mask, next_hop, port, negate):
        if vrf not in self._vrfs():
            return f"% VRF {vrf} does not exist"
        try:
            network = IPv4Network(f"{net}/{mask}")
        except ValueError:
            return "%Inconsistent address and mask"
        nh = IPv4Address(next_hop) if next_hop else None
        if negate:
            self.device.remove_static_route(network, nh, port.name if port else None, vrf=vrf)
        else:
            self.device.add_static_route(network, nh, port.name if port else None, 1, vrf=vrf)
        self.device.routes_changed()
        return None

    def show_vrf(self):
        lines = ["  Name                             Default RD            Interfaces"]
        for name, d in sorted(self._vrfs().items()):
            ifs = [p.short for p in self.device.ports.values() if p.vrf == name]
            lines.append(f"  {name:<33}{d.get('rd', '<not set>'):<22}{ifs[0] if ifs else ''}")
            lines += [" " * 57 + i for i in ifs[1:]]
        return "\n".join(lines)

    def show_route_vrf(self, vrf):
        if vrf not in self._vrfs():
            return f"% IP routing table {vrf} does not exist"
        with self.device.in_vrf(vrf):
            out = self.show_ip_route()
        return f"\nRouting Table: {vrf}\n" + out

    def ping_vrf(self, vrf, ip):
        if vrf not in self._vrfs():
            return f"% VRF {vrf} does not have a valid routing table"
        dev = self.device
        prev = dev.vrf_ctx
        dev.vrf_ctx = vrf
        job = self.ping(ip)
        if job is None:
            dev.vrf_ctx = prev
            return None
        _orig_done = job.finish

        def finish(*a, **k):
            dev.vrf_ctx = prev
            return _orig_done(*a, **k)
        job.finish = finish
        return job

    def trace_vrf(self, vrf, ip):
        if vrf not in self._vrfs():
            return f"% VRF {vrf} does not have a valid routing table"
        dev = self.device
        prev = dev.vrf_ctx
        dev.vrf_ctx = vrf
        job = self.traceroute(ip)
        _orig_done = job.finish

        def finish(*a, **k):
            dev.vrf_ctx = prev
            return _orig_done(*a, **k)
        job.finish = finish
        return job

    # -- EIGRP ---------------------------------------------------------------------------------
    def router_eigrp(self, asn, negate):
        from ..routing.eigrp import EigrpConfig
        d = self.device
        if negate:
            if d.eigrp is not None and d.eigrp.cfg.asn == asn:
                d.disable_eigrp()
            return None
        if d.eigrp is None:
            d.enable_eigrp(EigrpConfig(asn=asn))
        elif d.eigrp.cfg.asn != asn:
            return "% EIGRP process already running with a different AS number"
        self.mode = "config-router-eigrp"
        return None

    def eigrp_network(self, addr, wildcard, negate):
        from ipaddress import IPv4Address as A
        e = self.device.eigrp
        if wildcard is None:                     # classful: 10.x -> /8, 172.16 -> /16, 192.168.x -> /24
            first = int(str(addr).split(".")[0])
            wildcard = "0.255.255.255" if first < 128 else ("0.0.255.255" if first < 192 else "0.0.0.255")
        base = str(A(int(A(str(addr))) & ~int(A(wildcard)) & 0xFFFFFFFF))
        entry = [base, str(wildcard)]
        if negate:
            e.cfg.networks = [n for n in e.cfg.networks if n != entry]
        elif entry not in e.cfg.networks:
            e.cfg.networks.append(entry)
        e._recompute()
        return None

    def eigrp_set(self, attr, value, negate):
        e = self.device.eigrp
        if attr == "router_id":
            e.cfg.router_id = None if negate else str(value)
        else:
            lst = getattr(e.cfg, attr)
            if negate:
                setattr(e.cfg, attr, [x for x in lst if x != value])
            elif value not in lst:
                lst.append(value)
        e._recompute()
        return None

    def if_metric(self, attr, value, negate):
        setattr(self.iface, attr, None if negate else value)
        if self.device.eigrp is not None:
            self.device.eigrp._recompute()
        return None

    def show_eigrp(self, what):
        from ..routing.eigrp import port_bandwidth, port_delay
        e = self.device.eigrp
        if e is None:
            return ""
        asn = e.cfg.asn
        if what == "neighbors":
            lines = [f"EIGRP-IPv4 Neighbors for AS({asn})",
                     "H   Address                 Interface              Hold Uptime   SRTT   RTO  Q  Seq",
                     "                                                   (sec)         (ms)       Cnt Num"]
            now = self.device.engine.now
            for i, nb in enumerate(e.neighbors.values()):
                up = int((now - nb.up_since) / 1000)
                hold = max(0, 15 - int((now - nb.last_seen) / 1000))
                lines.append(f"{i:<4}{str(nb.ip):<24}{nb.port.short:<23}{hold:<5}"
                             f"{up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d} 1      100  0  {i + 3}")
            return "\n".join(lines)
        if what == "interfaces":
            lines = [f"EIGRP-IPv4 Interfaces for AS({asn})",
                     "                        Xmit Queue   PeerQ        Mean   Pacing Time   Multicast    Pending",
                     "Interface        Peers  Un/Reliable  Un/Reliable  SRTT   Un/Reliable   Flow Timer   Routes"]
            for p in self.device.ports.values():
                if e.enabled_on(p) and not e.is_passive(p):
                    peers = sum(1 for nb in e.neighbors.values() if nb.port is p)
                    lines.append(f"{p.short:<17}{peers:<7}0/0          0/0          1      0/0           50           0")
            return "\n".join(lines)
        lines = [f"EIGRP-IPv4 Topology Table for AS({asn})/ID({e.router_id()})",
                 "Codes: P - Passive, A - Active, U - Update, Q - Query, R - Reply,",
                 "       r - reply Status, s - sia Status", ""]
        for net, fd, vias in e.topology():
            succ = sum(1 for v in vias if v[4])
            lines.append(f"P {net}, {succ} successors, FD is {fd}")
            for ip, port, p_fd, rd, is_succ in vias:
                if ip == "Connected":
                    p = next((x for x in self.device.ports.values() if x.ip is not None and x.ip.network == net), None)
                    lines.append(f"        via Connected, {p.name if p else ''}")
                else:
                    lines.append(f"        via {ip} ({p_fd}/{rd}), {port}")
        _ = (port_bandwidth, port_delay)
        return "\n".join(lines)

    def router_rip(self, negate):
        from ..routing.rip import RipConfig
        if negate:
            self.device.disable_rip()
            return None
        if self.device.rip is None:
            self.device.enable_rip(RipConfig())
        self.mode = "config-router-rip"
        return None

    def rip_set(self, attr, value, negate):
        setattr(self.device.rip.cfg, attr, (2 if attr == "version" else False) if negate else value)
        self.device.rip.trigger()
        return None

    def rip_network(self, ip, negate):
        net = str(classful(IPv4Network(f"{ip}/32")))
        lst = self.device.rip.cfg.networks
        if negate:
            if net in lst:
                lst.remove(net)
        elif net not in lst:
            lst.append(net)
        self.device.rip.trigger()
        return None

    def rip_passive(self, port, negate):
        lst = self.device.rip.cfg.passive
        if negate:
            if port.name in lst:
                lst.remove(port.name)
        elif port.name not in lst:
            lst.append(port.name)
        return None

    def rip_redist(self, proto, negate):
        lst = self.device.rip.cfg.redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        self.device.rip.trigger()
        return None

    def show_rip_db(self):
        rip = self.device.rip
        if rip is None:
            return ""
        lines = []
        for net in rip._local_routes():
            lines += [f"{net}    auto-summary", f"{net}    directly connected"]
        for net, rr in sorted(rip.table.items(), key=lambda x: int(x[0].network_address)):
            if rr.metric < 16:
                lines += [f"{net}    auto-summary",
                          f"{net}", f"    [{rr.metric}] via {rr.next_hop}, {hms(rip.age(rr))}, {rr.port.name}"]
        return "\n".join(lines)

    # -- DHCP ------------------------------------------------------------------------------
    def dhcp_pool(self, name, negate):
        from ..services.dhcp import DhcpPool
        d = self.device.dhcp()
        if negate:
            d.cfg.pools = [p for p in d.cfg.pools if p.name != name]
            return None
        if d.cfg.pool(name) is None:
            d.cfg.pools.append(DhcpPool(name))
        self.dhcp_ctx = name
        self.mode = "dhcp-config"
        return None

    def dhcp_attr(self, attr, value, negate):
        pool = self.device.dhcp().cfg.pool(self.dhcp_ctx)
        if attr == "network" and value is not None and not negate:
            from ..addresses import make_interface
            net = make_interface(value[0], value[1]).network
            if net.network_address != IPv4Address(value[0]):
                return "% Inconsistent address and mask"
            value = str(net)
        default = 86400 if attr == "lease" else ("" if attr == "domain" else None)
        setattr(pool, attr, default if negate else (str(value) if attr in ("gateway", "dns") else value))
        return None

    def dhcp_excluded(self, lo, hi, negate):
        d = self.device.dhcp()
        entry = (lo, hi)
        if negate:
            d.cfg.excluded = [e for e in d.cfg.excluded if tuple(e) != entry]
        elif entry not in [tuple(e) for e in d.cfg.excluded]:
            d.cfg.excluded.append(entry)
        return None

    def if_helper(self, ip, negate):
        if self._l2_only():
            return self._l2_only()
        h = self.iface.helpers
        if negate:
            if ip is None:
                h.clear()
            elif ip in h:
                h.remove(ip)
        elif ip not in h:
            h.append(ip)
        return None

    def clear_dhcp(self):
        if self.device.dhcp_server is not None:
            self.device.dhcp_server.bindings.clear()
        return None

    def show_dhcp_binding(self):
        lines = ["IP address       Client-ID/              Lease expiration        Type",
                 "                 Hardware address"]
        srv = self.device.dhcp_server
        for b in (srv.bindings.values() if srv else []):
            exp = sim_clock(b.expires).strftime("%b %d %Y %I:%M %p")
            lines.append(f"{str(b.ip):<17}{b.mac.cisco().upper():<24}{exp:<24}Automatic")
        return "\n".join(lines)

    def show_dhcp_pool(self):
        srv = self.device.dhcp_server
        out = []
        for p in (srv.cfg.pools if srv else []):
            net = p.net()
            used = sum(1 for b in srv.bindings.values() if b.pool == p.name)
            total = net.num_addresses - 2 if net else 0
            out += ["", f"Pool {p.name} :", " Utilization mark (high/low)    : 100 / 0",
                    " Subnet size (first/next)       : 0 / 0",
                    f" Total addresses                : {total}", f" Leased addresses               : {used}",
                    f" Excluded addresses             : {len(srv.cfg.excluded)}",
                    " Pending event                  : none"]
            if net:
                out += [" 1 subnet is currently in the pool :",
                        " Current index        IP address range                    Leased/Excluded/Total",
                        f" {str(net.network_address + 1):<21}{str(net.network_address + 1):>15} - {str(net.broadcast_address - 1):<20}"
                        f"{used} / {len(srv.cfg.excluded)} / {total}"]
        return "\n".join(out)

    # -- BGP -------------------------------------------------------------------------------
    @property
    def bcfg(self):
        return self.device.bgp.cfg

    def router_bgp(self, asn, negate):
        from ..routing.bgp import BgpConfig
        d = self.device
        if negate:
            if d.bgp is not None and d.bgp.cfg.asn == asn:
                d.disable_bgp()
            return None
        if d.bgp is not None and d.bgp.cfg.asn != asn:
            return f"BGP is already running; AS is {d.bgp.cfg.asn}"
        if d.bgp is None:
            d.enable_bgp(BgpConfig(asn=asn))
        self.mode = "config-router-bgp"
        return None

    def _nb(self, ip):
        return self.bcfg.neighbors.get(str(ip))

    def bgp_remote_as(self, ip, asn, negate):
        nbs = self.bcfg.neighbors
        if negate:
            return self.bgp_remove_nb(ip)
        old = nbs.get(str(ip), {}).get("remote_as")
        if old is not None and old != asn:
            self.device.bgp.reset_peer(ip, "Remote AS changed")
        nbs.setdefault(str(ip), {})["remote_as"] = asn
        return None

    def bgp_remove_nb(self, ip):
        self.device.bgp.reset_peer(ip, "Neighbor deleted")
        self.bcfg.neighbors.pop(str(ip), None)
        return None

    def bgp_nb(self, ip, attr, value):
        nb = self._nb(ip)
        if nb is None:
            return "% Specify remote-as or peer-group commands first"
        if value is None or value is False:
            nb.pop(attr, None)
        else:
            nb[attr] = value
        if attr in ("update_source", "multihop", "shutdown", "disable_connected_check") \
                and self.device.bgp.peers.get(IPv4Address(str(ip))) is not None:
            self.device.bgp.reset_peer(ip, "Config change")
        return None

    def bgp_rid(self, rid):
        self.bcfg.router_id = rid
        for ip in list(self.device.bgp.peers):
            self.device.bgp.reset_peer(ip, "Router ID changed")
        return None

    def bgp_network(self, ip, mask, negate):
        if mask is None:
            net = classful(IPv4Network(f"{ip}/32"))
        else:
            try:
                net = IPv4Network(f"{ip}/{mask}")
            except ValueError:
                return "% Inconsistent address and mask"
        lst = self.bcfg.networks
        if negate:
            if str(net) in lst:
                lst.remove(str(net))
        elif str(net) not in lst:
            lst.append(str(net))
        return None

    def bgp_redist(self, proto, negate):
        lst = self.bcfg.redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        return None

    def clear_bgp(self, ip):
        b = self.device.bgp
        if b is None:
            return None
        for p in ([IPv4Address(ip)] if ip else list(b.peers)):
            b.reset_peer(p, "User reset")
        return None

    def _bgp_on(self):
        b = self.device.bgp
        return b if b is not None and b.running else None

    @staticmethod
    def _bgp_net(net):
        return str(net.network_address) if classful(net) == net else str(net)

    def show_bgp(self):
        b = self._bgp_on()
        if b is None:
            return "% BGP not active"
        lines = [f"BGP table version is {b.table_version}, local router ID is {b.router_id}",
                 "Status codes: s suppressed, d damped, h history, * valid, > best, i - internal,",
                 "              r RIB-failure, S Stale",
                 "Origin codes: i - IGP, e - EGP, ? - incomplete", "",
                 f"   {'Network':<17}{'Next Hop':<20}{'Metric':>6} {'LocPrf':>6} {'Weight':>6} Path"]
        prev = None
        for bp in b.table():
            a = bp.attrs
            status = "*" + (">" if bp.best else " ") + ("i" if bp.peer is not None and not bp.ebgp else " ")
            net = self._bgp_net(bp.prefix) if bp.prefix != prev else ""
            prev = bp.prefix
            nh = str(a.next_hop)
            med = "" if a.med is None else str(a.med)
            lp = "" if a.local_pref is None else str(a.local_pref)
            path = (a.text_path() + " " if a.as_path else "") + a.origin
            lines.append(f"{status}{net:<17}{nh:<20}{med:>6} {lp:>6} {bp.weight:>6} {path}")
        return "\n".join(lines)

    def show_bgp_summary(self):
        b = self._bgp_on()
        if b is None:
            return "% BGP not active"
        n = len(b.paths)
        lines = [f"BGP router identifier {b.router_id}, local AS number {b.cfg.asn}",
                 f"BGP table version is {b.table_version}, main routing table version {b.table_version}",
                 f"{n} network entries using {n * 132} bytes of memory",
                 f"{sum(len(v) for v in b.paths.values())} path entries using {sum(len(v) for v in b.paths.values()) * 64} bytes of memory",
                 "", f"{'Neighbor':<16}{'V':<5}{'AS':>5} {'MsgRcvd':>7} {'MsgSent':>7} {'TblVer':>8} {'InQ':>4} "
                     f"{'OutQ':>4} {'Up/Down':<8} State/PfxRcd"]
        now = self.device.engine.now
        for ip in sorted(b.peers, key=int):
            p = b.peers[ip]
            nc = b.ncfg(ip)
            if p.state == "Established":
                updown, st = hms((now - p.up_since) / 1000), str(b.received_count(p))
            else:
                updown = "never" if not p.flaps else hms((now - p.last_attempt) / 1000)
                st = "Idle (Admin)" if nc.get("shutdown") else ("Active" if p.state in ("Active", "OpenSent")
                                                                else "Idle")
            lines.append(f"{str(ip):<16}{'4':<5}{nc.get('remote_as', 0):>5} {p.msgs_rx:>7} {p.msgs_tx:>7} "
                         f"{b.table_version:>8} {0:>4} {0:>4} {updown:<8} {st}")
        return "\n".join(lines)

    def show_bgp_neighbors(self, ip):
        b = self._bgp_on()
        if b is None:
            return "% BGP not active"
        out = []
        for addr in sorted(b.peers, key=int):
            if ip is not None and str(addr) != ip:
                continue
            p = b.peers[addr]
            nc = b.ncfg(addr)
            kind = "external" if b.is_ebgp(addr) else "internal"
            block = [f"BGP neighbor is {addr},  remote AS {nc.get('remote_as')}, {kind} link"]
            if nc.get("description"):
                block.append(f" Description: {nc['description']}")
            block += [f"  BGP version 4, remote router ID {p.remote_rid or '0.0.0.0'}",
                      f"  BGP state = {p.state.split()[0]}" +
                      (f", up for {hms((self.device.engine.now - p.up_since) / 1000)}" if p.state == "Established" else ""),
                      f"  Last read {hms((self.device.engine.now - p.last_rx) / 1000)}, "
                      f"hold time is 180, keepalive interval is 60 seconds",
                      f"  Message statistics: {p.msgs_rx} received, {p.msgs_tx} sent",
                      f"  Prefixes: {len(p.adj_in)} received, {len(p.adj_out)} advertised"]
            if nc.get("update_source"):
                block.append(f"  Update source is {nc['update_source']}")
            if b.is_ebgp(addr):
                hops = nc.get("multihop")
                block.append(f"  External BGP neighbor may be up to {hops} hops away." if hops
                             else "  External BGP neighbor configured for connected checks (single-hop no-disable-connected-check)")
            if p.last_error:
                block.append(f"  Last reset due to: {p.last_error}")
            if p.local_ip:
                block.append(f"Local host: {p.local_ip}, Local port: 179")
                block.append(f"Foreign host: {addr}, Foreign port: 11000")
            out.append("\n".join(block))
        return "\n\n".join(out)

    # -- OSPF ------------------------------------------------------------------------------
    @property
    def ocfg(self) -> OspfConfig:
        return self.device.ospf.cfg

    def _ospf_changed(self):
        self.device.ospf.reconfigure()
        return None

    def router_ospf(self, pid, negate):
        d = self.device
        running = d.ospf is not None and d.ospf.cfg.enabled
        if negate:
            if running and d.ospf.cfg.process_id == pid:
                old = d.ospf.cfg
                if old.has_interface_settings():     # "ip ospf ..." stays on the interfaces
                    d.enable_ospf(OspfConfig(enabled=False, costs=old.costs, hello=old.hello,
                                             dead=old.dead, priority=old.priority))
                else:
                    d.disable_ospf()
            return None
        if running and d.ospf.cfg.process_id != pid:
            return "% Only one OSPF process is supported in this simulator"
        if not running:
            cfg = d.ospf.cfg if d.ospf is not None else OspfConfig()
            cfg.process_id, cfg.enabled = pid, True
            d.enable_ospf(cfg)
        self.mode = "config-router"
        return None

    def ospf_rid(self, rid, negate):
        self.ocfg.router_id = None if negate else rid
        self.device.ospf.reset_router_id()
        msg = None if negate else "Reload or use \"clear ip ospf process\" command, for this to take effect"
        if not negate and self.device.ospf.neighbors:
            return msg
        self.device.ospf.lsdb.clear()
        return self._ospf_changed()

    def ospf_network(self, ip, wildcard, area, negate):
        try:
            a = area_id(area)
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        prefix = str(wildcard_to_prefix(ip, wildcard))
        entry = {"prefix": prefix, "area": a}
        nets = self.ocfg.networks
        if negate:
            if entry in nets:
                nets.remove(entry)
            return self._ospf_changed()
        if entry not in nets:
            nets.append(entry)
        return self._ospf_changed()

    def ospf_passive(self, port, negate):
        c = self.ocfg
        if port is None:
            c.passive_default = not negate
            c.non_passive = []
            return self._ospf_changed()
        if c.passive_default:
            lst, add = c.non_passive, negate
        else:
            lst, add = c.passive, not negate
        if add and port.name not in lst:
            lst.append(port.name)
        elif not add and port.name in lst:
            lst.remove(port.name)
        return self._ospf_changed()

    def ospf_default(self, mode, negate):
        self.ocfg.default_originate = None if negate else mode
        return self._ospf_changed()

    def ospf_redistribute(self, proto, negate):
        lst = self.ocfg.redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        return self._ospf_changed()

    def ospf_refbw(self, bw, negate):
        self.ocfg.reference_bandwidth = 100 if negate else bw
        msg = None if negate else "% OSPF: Reference bandwidth is changed.\n        Please ensure reference bandwidth is consistent across all routers."
        self._ospf_changed()
        return msg

    def if_ospf(self, attr, value, negate):
        if self._l2_only():
            return self._l2_only()
        d = self.device
        if d.ospf is None:
            d.enable_ospf(OspfConfig(enabled=False))   # kept until "router ospf" is configured
        table = getattr(d.ospf.cfg, attr)
        if negate or value is None:
            table.pop(self.iface.name, None)
        else:
            table[self.iface.name] = value
        if d.ospf.running:
            d.ospf.reconfigure()
        return None

    def enter_subif(self, parent_num, negate=False):
        parent, num = parent_num
        if negate:
            return None  # nothing to delete
        sub = self.device.add_subinterface(parent, num)
        self.iface = sub
        self.mode = "config-subif"
        return None

    def subif_encap(self, vid, native, negate):
        sub = self.iface
        if negate:
            sub.vlan, sub.native = None, False
            sub.ip = None
            return None
        sub.vlan, sub.native = vid, native
        self.device.arp.flush_port(sub)
        return None

    # -- VLANs (switch) -------------------------------------------------------------
    def vlan_cmd(self, vids, negate):
        sw = self.device
        if negate:
            for v in vids:
                if v == 1 or 1002 <= v <= 1005:
                    return f"%Default VLAN {v} may not be deleted."
                sw.remove_vlan(v)
            return None
        for v in vids:
            sw.add_vlan(v)
        self.vlan_ctx = sorted(vids)
        self.mode = "config-vlan"
        return None

    def vlan_name(self, name, negate):
        for v in getattr(self, "vlan_ctx", []):
            self.device.vlans[v] = f"VLAN{v:04d}" if negate else name
        return None

    def _sw_changed(self):
        sw = self.device
        for key in [k for k, e in sw.mac_table.items() if e.port is self.iface]:
            del sw.mac_table[key]
        sw.engine.emit("device_changed", sw)
        return None

    def sw_mode(self, mode, negate):
        p = self.iface
        p.mode = "access" if negate else mode
        p.pvid = p.access_vlan if p.mode == "access" else p.native_vlan
        return self._sw_changed()

    def sw_access_vlan(self, vid, negate):
        p = self.iface
        msg = None
        if negate:
            vid = 1
        elif vid not in self.device.vlans:
            self.device.add_vlan(vid)
            msg = f"% Access VLAN does not exist. Creating vlan {vid}"
        if p.mode == "access":
            p.pvid = vid
        p.access_vlan = vid
        self._sw_changed()
        return msg

    def sw_native(self, vid, negate):
        p = self.iface
        p.native_vlan = 1 if negate else vid
        if p.mode == "trunk":
            p.pvid = p.native_vlan
        return self._sw_changed()

    def sw_allowed(self, op, vids, negate):
        p = self.iface
        cur = set(range(1, 4095)) if p.tagged is None else set(p.tagged)
        if negate or op == "all":
            new = None
        elif op == "set":
            new = set(vids)
        elif op == "add":
            new = cur | vids
        elif op == "remove":
            new = cur - vids
        else:  # except
            new = set(range(1, 4095)) - vids
        p.tagged = None if new is None or len(new) == 4094 else frozenset(new)
        return self._sw_changed()

    def do(self, line: str):
        return self.run_in_mode("priv", line)

    # -- config handlers ----------------------------------------------------------
    def set_hostname(self, name, negate):
        self.device.hostname = self.device.default_hostname if negate else name
        self.device.engine.emit("device_changed", self.device)
        return None

    def if_description(self, text, negate):
        self.iface.description = "" if negate else text
        return None

    def if_shutdown(self, down: bool):
        p = self.iface
        was = p.admin_up
        p.set_admin(not down)
        if was == p.admin_up:
            return None
        if down:
            return (f"\n%LINK-5-CHANGED: Interface {p.name}, changed state to administratively down\n"
                    + (f"\n%LINEPROTO-5-UPDOWN: Line protocol on Interface {p.name}, changed state to down\n"
                       if p.peer else ""))
        text = f"\n%LINK-5-CHANGED: Interface {p.name}, changed state to {'up' if p.is_up else 'down'}\n"
        if p.is_up:
            text += f"\n%LINEPROTO-5-UPDOWN: Line protocol on Interface {p.name}, changed state to up\n"
        return text

    def if_ip(self, ip, mask, negate):
        if self._l2_only():
            return self._l2_only()
        p = self.iface
        if p.is_sub and p.vlan is None and not negate:
            return ("% Configuring IP routing on a LAN subinterface is only allowed if that\n"
                    "subinterface is already configured as part of an IEEE 802.10, IEEE 802.1Q,\n"
                    "or ISL vLAN.")
        if negate:
            p.ip = None
            self.device.arp.flush_port(p)
            return None
        iface = make_interface(ip, mask)
        if iface.ip == iface.network.network_address and iface.network.prefixlen < 31:
            return "Bad mask /%d for address %s" % (iface.network.prefixlen, ip)
        other = overlapping_port(self.device, p, iface)
        if other is not None and getattr(other, "vrf", None) == getattr(p, "vrf", None):   # VRFs may overlap
            return f"% {iface.network.network_address} overlaps with {other.name}"
        p.ip = iface
        self.device.arp.flush_port(p)
        return None

    def ip_route(self, net, mask, next_hop, port, distance, negate):
        try:
            network = IPv4Network(f"{net}/{mask}")
        except ValueError:
            return "%Inconsistent address and mask"
        nh = IPv4Address(next_hop) if next_hop else None
        if negate:
            self.device.remove_static_route(network, nh, port.name if port else None)
            return None
        if nh is None and port is None:
            return self.err_incomplete("", 0)
        if nh is not None and self.device.owns_ip(nh):
            return "%Invalid next hop address (it's this router)"
        self.device.add_static_route(network, nh, port.name if port else None, distance)
        return None

    # -- exec handlers --------------------------------------------------------------
    def ping(self, ip, count=5):
        return make_ping(self.device, ip, CiscoPing(self.write), count=count,
                           size=100).start()

    def traceroute(self, ip):
        return make_trace(self.device, ip, CiscoTrace(self.write)).start()

    def write_mem(self, copy=False):
        self.device.save_startup()
        prefix = "Destination filename [startup-config]? \n" if copy else ""
        return prefix + "Building configuration...\n[OK]"

    def erase_startup(self):
        self.device.startup_config = None
        return ("Erasing the nvram filesystem will remove all configuration files! Continue? [confirm]\n"
                "[OK]\nErase of nvram: complete\n%SYS-7-NV_BLOCK_INIT: Initialized the geometry of nvram")

    def reload(self):
        dev = self.device
        dev.factory_reset()
        if dev.startup_config:
            dev.apply_config(dev.startup_config)
        self.mode = "user"
        self.iface = None
        return "Proceed with reload? [confirm]\n%SYS-5-RELOAD: Reload requested by console.\n\nPress RETURN to get started!"

    def clear_arp(self):
        self.device.arp.clear()
        return None

    def clear_mac(self):
        self.device.clear_mac_table()
        return None

    # -- show -----------------------------------------------------------------------
    def show_running(self):
        text = render_running(self.device, self.device.to_config())
        return f"Building configuration...\n\nCurrent configuration : {len(text)} bytes\n{text}"

    def show_startup(self):
        cfg = self.device.startup_config
        if not cfg:
            return "startup-config is not present"
        text = render_running(self.device, cfg)
        return f"Using {len(text)} bytes\n{text}"

    def show_version(self):
        d = self.device
        if self.is_router:
            return (f"Cisco IOS Software, C2900 Software (C2900-UNIVERSALK9-M), Version 15.1(4)M4, "
                    f"RELEASE SOFTWARE (fc2)\nTechnical Support: http://www.cisco.com/techsupport\n\n"
                    f"{d.hostname} uptime is {uptime_text(d.engine.now)}\n"
                    f"System image file is \"flash0:c2900-universalk9-mz.SPA.151-1.M4.bin\"\n\n"
                    f"Cisco CISCO2911/K9 (revision 1.0) with 491520K/32768K bytes of memory.\n"
                    f"{len(d.ports)} Gigabit Ethernet interfaces\n"
                    f"255K bytes of non-volatile configuration memory.\n\n"
                    f"Configuration register is 0x2102")
        return (f"Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE4, "
                f"RELEASE SOFTWARE (fc1)\n\n{d.hostname} uptime is {uptime_text(d.engine.now)}\n"
                f"System image file is \"flash:c2960-lanbasek9-mz.150-2.SE4.bin\"\n\n"
                f"cisco WS-C2960-24TT-L (PowerPC405) processor with 65536K bytes of memory.\n"
                f"24 FastEthernet interfaces\n2 Gigabit Ethernet interfaces\n\n"
                f"Configuration register is 0xF")

    def show_clock(self):
        from .render import sim_clock
        star = "" if getattr(self.device, "mgmt", None) is not None and self.device.mgmt.synced else "*"
        return star + sim_clock(self.device.engine.now).strftime("%H:%M:%S.000 UTC %a %b %d %Y")

    def show_ip_int_brief(self):
        lines = [f"{'Interface':<23}{'IP-Address':<16}{'OK?':<4}{'Method':<7}{'Status':<22}Protocol"]
        for p in self.device.ports.values():
            ip = str(p.ip.ip) if p.ip else "unassigned"
            method = "manual" if p.ip else "unset"
            proto = "up" if p.is_up else "down"
            lines.append(f"{p.name:<23}{ip:<16}{'YES':<4}{method:<7}{p.status_text():<22}{proto}")
        if not self.is_router and "Vlan1" not in self.device.ports:
            lines.append(f"{'Vlan1':<23}{'unassigned':<16}{'YES':<4}{'unset':<7}"
                         f"{'administratively down':<22}down")
        return "\n".join(lines)

    def show_interfaces(self, port):
        ports = [port] if port else list(self.device.ports.values())
        out = []
        for p in ports:
            proto = "up" if p.is_up else "down"
            conn = " (connected)" if p.is_up else (" (disabled)" if not p.admin_up else " (notconnect)")
            hw = "CN Gigabit Ethernet" if p.name.startswith("Gig") else "Lance"
            mac = p.mac.cisco()
            block = [f"{p.name} is {p.status_text()}, line protocol is {proto}{conn}",
                     f"  Hardware is {hw}, address is {mac} (bia {mac})"]
            if p.description:
                block.append(f"  Description: {p.description}")
            if p.ip:
                block.append(f"  Internet address is {p.ip.with_prefixlen}")
            bw = 1000000 if p.name.startswith("Gig") else 100000
            block += [f"  MTU 1500 bytes, BW {bw} Kbit, DLY 10 usec,",
                      "     reliability 255/255, txload 1/255, rxload 1/255",
                      "  Encapsulation ARPA, loopback not set",
                      "  Full-duplex, Auto-speed" if p.is_up else "  Auto-duplex, Auto-speed",
                      f"     {p.rx_frames} packets input, {p.rx_frames * 64} bytes, 0 no buffer",
                      f"     {p.tx_frames} packets output, {p.tx_frames * 64} bytes, 0 underruns",
                      f"     {p.drops} output errors, 0 collisions, 0 interface resets"]
            out.append("\n".join(block))
        return "\n".join(out)

    def show_int_status(self):
        lines = [f"{'Port':<10}{'Name':<19}{'Status':<13}{'Vlan':<11}{'Duplex':<8}{'Speed':<6}Type"]
        for p in self.device.ports.values():
            st = "disabled" if not p.admin_up else ("connected" if p.is_up else "notconnect")
            typ = "10/100BaseTX" if p.name.startswith("Fast") else "10/100/1000BaseTX"
            dup, spd = ("a-full", "a-100") if p.is_up else ("auto", "auto")
            if p.is_up and p.name.startswith("Gig"):
                spd = "a-1000"
            vl = "trunk" if p.mode == "trunk" else str(p.pvid)
            lines.append(f"{p.short:<10}{p.description[:18]:<19}{st:<13}{vl:<11}{dup:<8}{spd:<6}{typ}")
        return "\n".join(lines)

    # -- spanning tree ---------------------------------------------------------------------
    def _stp_changed(self):
        self.device.stp.schedule()
        return None

    def stp_mode(self, mode):
        self.device.stp.mode = mode
        return self._stp_changed()

    def stp_priority(self, prio, negate):
        if not negate and prio % 4096:
            return ("% Bridge Priority must be in increments of 4096.\n% Allowed values are:\n"
                    "  0     4096  8192  12288 16384 20480 24576 28672\n"
                    "  32768 36864 40960 45056 49152 53248 57344 61440")
        self.device.stp.priority = 32768 if negate else prio
        return self._stp_changed()

    def stp_root(self, primary, negate):
        stp = self.device.stp
        if negate:
            stp.priority = 32768
        elif primary:
            root = stp.root_id[0] if stp.root_id else 32768
            stp.priority = 24576 if root > 24576 or stp.root_id == stp.bridge_id else max(0, root - 4096)
        else:
            stp.priority = 28672
        return self._stp_changed()

    def stp_enable(self, on):
        self.device.stp.enabled = on
        if not on:
            self.device.stp.states.clear()
        return self._stp_changed()

    def stp_portfast(self, negate):
        e = self.device.stp.edge
        (e.discard if negate else e.add)(self.iface.name)
        msg = None if negate else ("%Warning: portfast should only be enabled on ports connected to a single\n"
                                   " host. Connecting hubs, concentrators, switches, bridges, etc... to this\n"
                                   " interface  when portfast is enabled, can cause temporary bridging loops.\n"
                                   " Use with CAUTION")
        self._stp_changed()
        return msg

    def stp_cost(self, cost, negate):
        if negate:
            self.device.stp.costs.pop(self.iface.name, None)
        else:
            self.device.stp.costs[self.iface.name] = cost
        return self._stp_changed()

    def show_stp(self, vlan):
        from ..addresses import MacAddress
        sw = self.device
        stp = sw.stp
        if not stp.enabled:
            return "No spanning tree instance exists."
        out = []
        for vid in sorted(sw.vlans) if vlan is None else [vlan]:
            if vid not in sw.vlans:
                return f"Spanning tree instance(s) for vlan {vid} does not exist."
            ports = [p for p in sw.ports.values() if p.is_up and p.vlan_out(vid)[0]]
            if not ports:
                continue
            root = stp.root_id or stp.bridge_id
            out += [f"VLAN{vid:04d}", f"  Spanning tree enabled protocol {'rstp' if stp.rapid else 'ieee'}",
                    f"  Root ID    Priority    {root[0] + vid}", f"             Address     {MacAddress(root[1]).cisco()}"]
            if root == stp.bridge_id:
                out.append("             This bridge is the root")
            else:
                rp = sw.ports.get(stp.root_port)
                out += [f"             Cost        {stp.root_cost}",
                        f"             Port        {list(sw.ports).index(rp.name) + 1}({rp.name})" if rp else ""]
            out += ["             Hello Time  2 sec  Max Age 20 sec  Forward Delay 15 sec", "",
                    f"  Bridge ID  Priority    {stp.priority + vid}  (priority {stp.priority} sys-id-ext {vid})",
                    f"             Address     {MacAddress(stp.bridge_id[1]).cisco()}",
                    "             Hello Time  2 sec  Max Age 20 sec  Forward Delay 15 sec", "             Aging Time  20", "",
                    "Interface        Role Sts Cost      Prio.Nbr Type",
                    "---------------- ---- --- --------- -------- --------------------------------"]
            for p in ports:
                role = STP_ROLE.get(stp.roles.get(p.name, "designated"), "Desg")
                st = STP_STATE.get(stp.states.get(p.name, "forwarding"), "FWD")
                typ = "P2p Edge" if stp.roles.get(p.name) == "edge" else "P2p"
                out.append(f"{p.short:<17}{role:<5}{st:<4}{stp.port_cost(p):<10}128.{list(sw.ports).index(p.name) + 1:<4}{typ}")
            out.append("")
        return "\n".join(out)

    def show_vlan(self, brief):
        sw = self.device
        lines = [f"{'VLAN':<5}{'Name':<33}{'Status':<10}Ports",
                 f"{'----':<5}{'-' * 32:<33}{'-' * 9:<10}{'-' * 31}"]
        for vid, name in sorted(sw.vlans.items()):
            ports = [p.short for p in sw.ports.values() if p.mode == "access" and p.pvid == vid]
            chunks = [", ".join(ports[i:i + 4]) for i in range(0, len(ports), 4)] or [""]
            lines.append(f"{vid:<5}{name[:32]:<33}{'active':<10}{chunks[0]}")
            for c in chunks[1:]:
                lines.append(" " * 48 + c)
        for vid, name in ((1002, "fddi-default"), (1003, "token-ring-default"),
                          (1004, "fddinet-default"), (1005, "trnet-default")):
            lines.append(f"{vid:<5}{name:<33}{'act/unsup':<10}")
        return "\n".join(lines)

    def show_int_trunk(self):
        trunks = [p for p in self.device.ports.values() if p.mode == "trunk" and p.is_up]
        if not trunks:
            return ""
        out = [f"{'Port':<12}{'Mode':<13}{'Encapsulation':<15}{'Status':<14}Native vlan"]
        out += [f"{p.short:<12}{'on':<13}{'802.1q':<15}{'trunking':<14}{p.pvid}" for p in trunks]
        out += ["", f"{'Port':<12}Vlans allowed on trunk"]
        out += [f"{p.short:<12}{vlan_ranges(p.tagged)}" for p in trunks]
        out += ["", f"{'Port':<12}Vlans allowed and active in management domain"]
        for p in trunks:
            active = {v for v in self.device.vlans if p.vlan_allowed(v)}
            out.append(f"{p.short:<12}{vlan_ranges(active)}")
        return "\n".join(out)

    def show_mac_table(self):
        lines = ["          Mac Address Table", "-------------------------------------------", "",
                 f"{'Vlan':<8}{'Mac Address':<18}{'Type':<12}Ports",
                 f"{'----':<8}{'-----------':<18}{'--------':<12}-----", ""]
        for e in self.device.mac_entries():
            lines.append(f"{e.vlan:>4}    {e.mac.cisco():<18}{'DYNAMIC':<12}{e.port.short}")
        return "\n".join(lines)

    def show_arp(self):
        d = self.device
        rows = []
        for p in d.ip_ports():
            if p.is_up:
                rows.append((int(p.ip.ip), str(p.ip.ip), "-", p.mac.cisco(), p.name))
        for e in d.arp.entries(d.engine.now):
            age = int((d.engine.now - e.learned_at) / 60000)
            rows.append((int(e.ip), str(e.ip), str(age), e.mac.cisco(), e.port.name))
        rows.sort()
        lines = [f"{'Protocol':<10}{'Address':<17}{'Age (min)':<11}{'Hardware Addr':<16}{'Type':<7}Interface"]
        for _, ip, age, mac, name in rows:
            lines.append(f"{'Internet':<10}{ip:<17}{age:>9}  {mac:<16}{'ARPA':<7}{name}")
        return "\n".join(lines)

    def _ospf_on(self):
        o = self.device.ospf
        return o if o is not None and o.running else None

    def show_ospf_neighbor(self, detail):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = ["", f"{'Neighbor ID':<16}{'Pri':<5}{'State':<17}{'Dead Time':<12}{'Address':<16}Interface"]
        for n, state, dead in o.neighbor_rows():
            lines.append(f"{str(n.rid):<16}{n.priority:<5}{state:<17}{hms(dead):<12}{str(n.ip):<16}{n.port.name}")
        return "\n".join(lines)

    def show_ospf_interface(self, brief):
        o = self._ospf_on()
        if o is None:
            return ""
        ports = [p for p in self.device.ports.values() if p.ip and o.iface_area(p) is not None]
        if brief:
            lines = [f"{'Interface':<11}{'PID':<6}{'Area':<16}{'IP Address/Mask':<20}{'Cost':<6}{'State':<6}Nbrs F/C"]
            for p in ports:
                nb = sum(1 for n in o.neighbors.values() if n.port is p)
                full = sum(1 for n in o.neighbors.values() if n.port is p and n.two_way)
                st = "LOOP" if p.is_loopback else ("DOWN" if not p.is_up else self._ospf_if_state(o, p))
                lines.append(f"{p.short:<11}{o.cfg.process_id:<6}{area_dec(o.iface_area(p)):<16}"
                             f"{p.ip.with_prefixlen:<20}{o.cost(p):<6}{st:<6}{full}/{nb}")
            return "\n".join(lines)
        out = []
        for p in ports:
            dr, bdr = o.dr_bdr(p)
            block = [f"{p.name} is {'up' if p.is_up else 'down'}, line protocol is {'up' if p.is_up else 'down'}",
                     f"  Internet Address {p.ip.with_prefixlen}, Area {area_dec(o.iface_area(p))}",
                     f"  Process ID {o.cfg.process_id}, Router ID {o.router_id}, Network Type "
                     f"{'LOOPBACK' if p.is_loopback else 'BROADCAST'}, Cost: {o.cost(p)}"]
            if p.is_loopback:
                block.append("  Loopback interface is treated as a stub Host")
            elif o.is_passive(p):
                block.append("  No Hellos (Passive interface)")
            else:
                block += [f"  Transmit Delay is 1 sec, State {self._ospf_if_state(o, p)}, Priority {o.priority(p)}",
                          f"  Designated Router (ID) {dr or '0.0.0.0'}",
                          f"  Backup Designated Router (ID) {bdr or '0.0.0.0'}",
                          f"  Timer intervals configured, Hello {int(o.hello_interval(p))}, "
                          f"Dead {int(o.dead_interval(p))}, Wait {int(o.dead_interval(p))}, Retransmit 5",
                          f"  Neighbor Count is {sum(1 for n in o.neighbors.values() if n.port is p)}, "
                          f"Adjacent neighbor count is {sum(1 for n in o.neighbors.values() if n.port is p and n.two_way)}"]
            out.append("\n".join(block))
        return "\n".join(out)

    @staticmethod
    def _ospf_if_state(o, p):
        dr, bdr = o.dr_bdr(p)
        if not any(n.port is p and n.two_way for n in o.neighbors.values()):
            return "DR" if dr == p.ip.ip else "WAIT"
        return "DR" if p.ip.ip == dr else "BDR" if p.ip.ip == bdr else "DROTHER"

    def show_ospf_database(self):
        o = self._ospf_on()
        if o is None:
            return ""
        now = int(self.device.engine.now / 1000)
        lines = ["", f"            OSPF Router with ID ({o.router_id}) (Process ID {o.cfg.process_id})"]
        titles = {1: "Router Link States", 3: "Summary Net Link States",
                  4: "Summary ASB Link States", 5: "Type-5 AS External Link States"}
        group = None
        for l in o.database():
            if not l.body:
                continue
            g = (l.area, l.type)
            if g != group:
                group = g
                where = f" (Area {area_dec(l.area)})" if l.area is not None else ""
                lines += ["", f"                {titles[l.type]}{where}", "",
                          f"{'Link ID':<16}{'ADV Router':<16}{'Age':<8}{'Seq#':<12}{'Checksum':<10}"
                          + ("Link count" if l.type == 1 else "Tag" if l.type == 5 else "")]
            extra = str(sum(1 for lk in l.body if lk[0] == "router") + sum(1 for lk in l.body if lk[0] == "stub")) \
                if l.type == 1 else ("0" if l.type == 5 else "")
            lines.append(f"{str(l.ls_id):<16}{str(l.adv):<16}{min(now, 3599):<8}0x{l.seq:08x}  "
                         f"0x{(l.seq * 2654435761 + int(l.ls_id)) & 0xFFFF:04x}    {extra}")
        return "\n".join(lines)

    def show_ospf(self):
        o = self._ospf_on()
        if o is None:
            return "%OSPF: No router process is configured"
        areas = sorted(o.areas())
        return "\n".join([
            f" Routing Process \"ospf {o.cfg.process_id}\" with ID {o.router_id}",
            " Supports only single TOS(TOS0) routes",
            " Supports opaque LSA",
            *([" It is an area border router"] if o.is_abr else []),
            *([" It is an autonomous system boundary router"] if o.is_asbr else []),
            " SPF schedule delay 5 secs, Hold time between two SPFs 10 secs",
            f" Number of areas in this router is {len(areas)}. {len(areas)} normal 0 stub 0 nssa",
            f" Reference bandwidth unit is {o.cfg.reference_bandwidth} mbps",
            *[f"    Area {'BACKBONE(0)' if a == '0.0.0.0' else area_dec(a)}\n"
              f"        Number of interfaces in this area is "
              f"{sum(1 for p in o.active_ports() if o.iface_area(p) == a)}\n"
              f"        SPF algorithm executed {o.spf_runs} times" for a in areas]])

    def show_ip_protocols(self):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = [f'Routing Protocol is "ospf {o.cfg.process_id}"',
                 "  Outgoing update filter list for all interfaces is not set",
                 "  Incoming update filter list for all interfaces is not set",
                 f"  Router ID {o.router_id}"]
        if o.is_asbr:
            lines.append("  It is an autonomous system boundary router")
            if o.cfg.redistribute:
                lines.append("  Redistributing External Routes from,")
                lines += [f"    {r}" for r in o.cfg.redistribute]
        lines += [f"  Number of areas in this router is {len(o.areas())}. {len(o.areas())} normal 0 stub 0 nssa",
                  "  Maximum path: 4", "  Routing for Networks:"]
        for n in o.cfg.networks:
            net = IPv4Network(n["prefix"])
            wc = IPv4Address(int(net.hostmask))
            lines.append(f"    {net.network_address} {wc} area {area_dec(n['area'])}")
        passive = [p.name for p in self.device.ports.values() if o.iface_area(p) and o.is_passive(p)
                   and not p.is_loopback]
        if passive:
            lines.append("  Passive Interface(s):")
            lines += [f"    {x}" for x in passive]
        lines.append("  Routing Information Sources:")
        lines.append(f"    {'Gateway':<16}{'Distance':<14}Last Update")
        for n in o.neighbors.values():
            lines.append(f"    {str(n.rid):<16}{110:<14}{hms((self.device.engine.now - n.full_since) / 1000)}")
        lines.append("  Distance: (default is 110)")
        return "\n".join(lines)

    def show_ip_route(self, only=None):
        d = self.device
        lines = ["Codes: L - local, C - connected, S - static, R - RIP, M - mobile, B - BGP",
                 "       D - EIGRP, EX - EIGRP external, O - OSPF, IA - OSPF inter area",
                 "       N1 - OSPF NSSA external type 1, N2 - OSPF NSSA external type 2",
                 "       E1 - OSPF external type 1, E2 - OSPF external type 2, E - EGP",
                 "       i - IS-IS, L1 - IS-IS level-1, L2 - IS-IS level-2, ia - IS-IS inter area",
                 "       * - candidate default, U - per-user static route, o - ODR",
                 "       P - periodic downloaded static route", ""]
        gw = d.gateway_of_last_resort()
        if gw is None:
            lines.append("Gateway of last resort is not set")
        elif gw.next_hop:
            lines.append(f"Gateway of last resort is {gw.next_hop} to network 0.0.0.0")
        else:
            lines.append("Gateway of last resort is 0.0.0.0 to network 0.0.0.0")
        lines.append("")
        groups: dict[IPv4Network, list] = {}
        table = d.routing_table()
        if only:
            table = [r for r in table if r.code.startswith(only)]
            lines = lines[:7] + lines[8:] if False else lines
        for r in table:
            groups.setdefault(classful(r.network), []).append(r)
        for major in sorted(groups, key=lambda n: int(n.network_address)):
            routes = groups[major]
            nets = {r.network for r in routes}          # ECMP paths are one subnet
            nested = not (len(nets) == 1 and routes[0].network == major)
            if nested and major.prefixlen > 0:
                masks = {n.prefixlen for n in nets}
                if len(masks) == 1:
                    lines.append(f"     {major} is subnetted, {len(nets)} subnets")
                else:
                    lines.append(f"     {major} is variably subnetted, {len(nets)} subnets, "
                                 f"{len(masks)} masks")
            prev = None
            for r in routes:
                code = r.code + ("*" if r.network.prefixlen == 0 else "")
                if code.startswith("O") and len(code) > 1:
                    code = code.replace("O*", "O*").replace("O E2*", "O*E2").replace("O E2", "O E2")
                pad = 8 if nested and major.prefixlen > 0 else 5
                pad = max(pad, len(code) + 2)
                head = f"{code:<{pad}}{r.network}"
                if r.code.startswith(("O", "B", "R", "D", "i")):
                    age = hms(d.engine.now / 1000 % 86400)
                    tail = f"[{r.distance}/{r.metric}] via {r.next_hop}, {age}, {r.port.name}"
                    if prev is not None and prev.network == r.network:
                        lines.append(" " * (len(head) + 1) + tail)
                    else:
                        lines.append(f"{head} {tail}")
                    prev = r
                    continue
                prev = r
                if r.code in ("C", "L"):
                    lines.append(f"{head} is directly connected, {r.port.name}")
                elif r.next_hop:
                    lines.append(f"{head} [{r.distance}/0] via {r.next_hop}")
                else:
                    lines.append(f"{head} is directly connected, {r.port.name}")
        return "\n".join(lines)



STP_ROLE = {"root": "Root", "designated": "Desg", "alternate": "Altn", "edge": "Desg", "disabled": "Disb"}
STP_STATE = {"forwarding": "FWD", "blocking": "BLK", "listening": "LIS", "learning": "LRN", "disabled": "DIS"}


def _hash(text: str) -> str:
    """Stand-in for an MD5 crypt hash: stable and not the clear text."""
    import hashlib
    return hashlib.md5(text.encode()).hexdigest()[:22]


def area_dec(area: str) -> str:
    """Cisco prints area 0.0.0.0 as 0 and 0.0.0.1 as 1."""
    v = int(IPv4Address(area))
    return str(v) if v < 1 << 16 else area


def hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def vlan_ranges(vids) -> str:
    """{1,2,3,10} -> '1-3,10'; None -> '1-4094'."""
    if vids is None:
        return "1-4094"
    vs = sorted(vids)
    out, i = [], 0
    while i < len(vs):
        j = i
        while j + 1 < len(vs) and vs[j + 1] == vs[j] + 1:
            j += 1
        out.append(str(vs[i]) if i == j else f"{vs[i]}-{vs[j]}")
        i = j + 1
    return ",".join(out)


def classful(net: IPv4Network) -> IPv4Network:
    if net.prefixlen == 0:
        return net
    first = int(net.network_address) >> 24
    plen = 8 if first < 128 else 16 if first < 192 else 24
    if net.prefixlen < plen:
        return net
    return IPv4Network(f"{net.network_address}/{plen}", strict=False)


def render_running(device, cfg: dict) -> str:
    """IOS-style running-config text from a vendor-neutral config dict."""
    router = device.category == "router"
    lines = ["!", "version 15.1" if router else "version 15.0",
             "no service timestamps log datetime msec",
             "no service timestamps debug datetime msec",
             "no service password-encryption", "!", f"hostname {cfg.get('hostname')}", "!"]
    if not router:
        stp = cfg.get("stp") or {}
        lines.append(f"spanning-tree mode {stp.get('mode', 'pvst')}")
        if not stp.get("enabled", True):
            lines.append("no spanning-tree vlan 1")
        if stp.get("priority", 32768) != 32768:
            lines.append(f"spanning-tree vlan 1 priority {stp['priority']}")
        lines.append("!")
        for vid, vname in sorted((int(k), v) for k, v in cfg.get("vlans", {}).items()):
            if vid != 1:
                lines += [f"vlan {vid}", f" name {vname}", "!"]
    for vname, vd in sorted((cfg.get("extra") or {}).get("vrfs", {}).items()):
        lines.append(f"ip vrf {vname}")
        if vd.get("description"):
            lines.append(f" description {vd['description']}")
        if vd.get("rd"):
            lines.append(f" rd {vd['rd']}")
        lines.append("!")
    for name, ic in cfg.get("interfaces", {}).items():
        lines.append(f"interface {name}")
        if ic.get("description"):
            lines.append(f" description {ic['description']}")
        if ic.get("vrf"):
            lines.append(f" ip vrf forwarding {ic['vrf']}")
        if ic.get("parent"):
            if ic.get("vlan") is not None:
                lines.append(f" encapsulation dot1Q {ic['vlan']}" + (" native" if ic.get("native") else ""))
            if ic.get("ip"):
                i = IPv4Interface(ic["ip"])
                lines.append(f" ip address {i.ip} {i.netmask}")
            if not ic.get("admin_up", True):
                lines.append(" shutdown")
            lines.append("!")
            continue
        svi = bool(ic.get("svi")) or bool(ic.get("loopback"))
        if ic.get("voice_vlan"):
            lines.append(f" switchport voice vlan {ic['voice_vlan']}")
        if ic.get("dot1x"):
            lines += [" authentication port-control auto", " dot1x pae authenticator"]
        if name in (cfg.get("mpls") or {}).get("interfaces", []):
            lines.append(" mpls ip")
        _isis = cfg.get("isis") or {}
        if name in _isis.get("interfaces", []):
            lines.append(" ip router isis" + ("" if _isis.get("tag", "1") == "1" else " " + _isis["tag"]))
            if name in _isis.get("metrics", {}):
                lines.append(f" isis metric {_isis['metrics'][name]}")
        if ic.get("bandwidth"):
            lines.append(f" bandwidth {ic['bandwidth']}")
        if ic.get("delay"):
            lines.append(f" delay {ic['delay']}")
        if ic.get("qos_in"):
            lines.append(f" service-policy input {ic['qos_in']}")
        if ic.get("qos_out"):
            lines.append(f" service-policy output {ic['qos_out']}")
        if ic.get("tunnel"):
            if ic.get("source"):
                lines.append(f" tunnel source {ic['source']}")
            if ic.get("destination"):
                lines.append(f" tunnel destination {ic['destination']}")
            prof = (cfg.get("extra") or {}).get("tunnel_protection", {}).get(name)
            if prof:
                lines.append(f" tunnel protection ipsec profile {prof}")
        if name in (cfg.get("nat64") or {}).get("interfaces", []):
            lines.append(" nat64 enable")
        for v in cfg.get("v6igp", []):
            ic6 = v.get("interfaces", {}).get(name)
            if ic6 is not None:
                if v["kind"] == "ospf6":
                    lines.append(f" ipv6 ospf {v.get('tag', '1')} area {area_dec(ic6.get('area', '0.0.0.0'))}")
                    if ic6.get("cost"):
                        lines.append(f" ipv6 ospf cost {ic6['cost']}")
                elif v["kind"] == "eigrp6":
                    lines.append(f" ipv6 eigrp {v.get('tag', '1')}")
                else:
                    lines.append(f" ipv6 rip {v.get('tag', '1')} enable")
        if name in (cfg.get("isis") or {}).get("interfaces6", []):
            lines.append(" ipv6 router isis" + ("" if cfg["isis"].get("tag", "1") == "1" else " " + cfg["isis"]["tag"]))
        for g in cfg.get("fhrp", []):
            if g["port"] != name:
                continue
            w = "standby" if g["proto"] == "hsrp" else "vrrp"
            if g.get("vip"):
                lines.append(f" {w} {g['group']} ip {g['vip']}")
            if g.get("priority", 100) != 100:
                lines.append(f" {w} {g['group']} priority {g['priority']}")
            if g.get("preempt"):
                lines.append(f" {w} {g['group']} preempt")
        if ic.get("channel_group") is not None:
            lines.append(f" channel-group {ic['channel_group']} mode {ic.get('channel_mode', 'on')}")
        ps = ic.get("port_security")
        if ps is not None:
            lines.append(" switchport port-security")
            if ps.get("max", 1) != 1:
                lines.append(f" switchport port-security maximum {ps['max']}")
            if ps.get("violation", "shutdown") != "shutdown":
                lines.append(f" switchport port-security violation {ps['violation']}")
            if ps.get("sticky"):
                lines.append(" switchport port-security mac-address sticky")
            lines += [f" switchport port-security mac-address {'sticky ' if ps.get('sticky') else ''}{m}"
                      for m in ps.get("macs", [])]
        if ic.get("dhcp_trust"):
            lines.append(" ip dhcp snooping trust")
        if not router and not svi:
            stp_c = cfg.get("stp") or {}
            if name in stp_c.get("edge", []):
                lines.append(" spanning-tree portfast")
            if name in stp_c.get("costs", {}):
                lines.append(f" spanning-tree cost {stp_c['costs'][name]}")
            mode = ic.get("mode", "access")
            if mode == "trunk":
                if ic.get("native_vlan", ic.get("pvid", 1)) != 1:
                    lines.append(f" switchport trunk native vlan {ic.get('native_vlan', ic.get('pvid'))}")
                if ic.get("tagged") is not None:
                    lines.append(f" switchport trunk allowed vlan {vlan_ranges(ic['tagged']) or 'none'}")
                lines.append(" switchport mode trunk")
            else:
                if ic.get("access_vlan", ic.get("pvid", 1)) != 1:
                    lines.append(f" switchport access vlan {ic.get('access_vlan', ic.get('pvid'))}")
                if "mode" in ic:
                    lines.append(" switchport mode access")
        if router or svi:
            if ic.get("ip"):
                i = IPv4Interface(ic["ip"])
                lines.append(f" ip address {i.ip} {i.netmask}")
            else:
                lines.append(" no ip address")
            for a in ic.get("ipv6", []):
                lines.append(f" ipv6 address {a.upper()}")
            if ic.get("ipv6_enabled") and not ic.get("ipv6"):
                lines.append(" ipv6 enable")
            if not svi:
                lines += [" duplex auto", " speed auto"]
        if not ic.get("admin_up", True):
            lines.append(" shutdown")
        for h in ic.get("helpers", []):
            lines.append(f" ip helper-address {h}")
        if ic.get("acl_in"):
            lines.append(f" ip access-group {ic['acl_in']} in")
        if ic.get("acl_out"):
            lines.append(f" ip access-group {ic['acl_out']} out")
        nat_c = cfg.get("nat") or {}
        if name in nat_c.get("inside", []):
            lines.append(" ip nat inside")
        if name in nat_c.get("outside", []):
            lines.append(" ip nat outside")
        ospf = cfg.get("ospf") or {}
        for key, word in (("costs", "cost"), ("hello", "hello-interval"), ("dead", "dead-interval"),
                          ("priority", "priority")):
            if name in ospf.get(key, {}):
                lines.append(f" ip ospf {word} {int(ospf[key][name])}")
        lines.append("!")
    if not router and "Vlan1" not in cfg.get("interfaces", {}):
        lines += ["interface Vlan1", " no ip address", " shutdown", "!"]
    if not router:
        if cfg.get("ip_routing"):
            lines.append("ip routing")
        ds = cfg.get("dhcp_snooping") or {}
        if ds.get("vlans"):
            lines.append(f"ip dhcp snooping vlan {vlan_ranges(ds['vlans'])}")
        if ds.get("enabled"):
            lines.append("ip dhcp snooping")
        if cfg.get("default_gateway"):
            lines.append(f"ip default-gateway {cfg['default_gateway']}")
        if cfg.get("ip_routing") or cfg.get("default_gateway"):
            lines.append("!")
    if True:
        aaa = (cfg.get("extra") or {}).get("aaa") or {}
        if aaa.get("new_model"):
            lines.append("aaa new-model")
            if aaa.get("login"):
                lines.append("aaa authentication login default " + " ".join(aaa["login"]))
            if aaa.get("dot1x"):
                lines.append("aaa authentication dot1x default group radius")
            lines.append("!")
        for group, word in (("tacacs+", "tacacs"), ("radius", "radius")):
            for name, srv in (aaa.get(group) or {}).items():
                lines += [f"{word} server {name}", f" address ipv4 {srv.get('ip')}"]
                if srv.get("key"):
                    lines.append(f" key {srv['key']}")
                lines.append("!")
        if aaa.get("dot1x"):
            lines += ["dot1x system-auth-control", "!"]
        mp = cfg.get("mpls") or {}
        if mp.get("interfaces"):
            lines.append("mpls label protocol ldp")
            if mp.get("router_id"):
                lines.append(f"mpls ldp router-id {mp['router_id']} force")
            lines.append("!")
        isis = cfg.get("isis")
        if isis and isis.get("net"):
            tag = isis.get("tag", "1")
            lines += [f"router isis{'' if tag == '1' else ' ' + tag}", f" net {isis['net']}", " is-type level-2-only",
                      " metric-style wide"]
            for x in isis.get("passive", []):
                lines.append(f" passive-interface {x}")
            lines.append("!")
        eg = cfg.get("eigrp")
        if eg and eg.get("enabled", True):
            lines.append(f"router eigrp {eg['asn']}")
            if eg.get("router_id"):
                lines.append(f" eigrp router-id {eg['router_id']}")
            for x in eg.get("passive", []):
                lines.append(f" passive-interface {x}")
            for proto in eg.get("redistribute", []):
                lines.append(f" redistribute {proto}")
            for a, w in eg.get("networks", []):
                lines.append(f" network {a} {w}")
            lines += [" no auto-summary", "!"]
        rip = cfg.get("rip")
        if rip and rip.get("enabled", True):
            lines += ["router rip", f" version {rip.get('version', 2)}"]
            for x in rip.get("passive", []):
                lines.append(f" passive-interface {x}")
            if rip.get("passive_default"):
                lines.append(" passive-interface default")
            for proto in rip.get("redistribute", []):
                lines.append(f" redistribute {proto}" + (" 1 metric 1" if proto == "ospf" else ""))
            for n in rip.get("networks", []):
                lines.append(f" network {IPv4Network(n).network_address}")
            if rip.get("default_originate"):
                lines.append(" default-information originate")
            lines += [" no auto-summary", "!"]
        bgp = cfg.get("bgp")
        if bgp and bgp.get("asn"):
            lines.append(f"router bgp {bgp['asn']}")
            if bgp.get("router_id"):
                lines.append(f" bgp router-id {bgp['router_id']}")
            lines.append(" bgp log-neighbor-changes")
            for net in bgp.get("networks", []):
                n = IPv4Network(net)
                lines.append(f" network {n.network_address}" + ("" if classful(n) == n else f" mask {n.netmask}"))
            for ip, nc in bgp.get("neighbors", {}).items():
                lines.append(f" neighbor {ip} remote-as {nc.get('remote_as')}")
                if nc.get("description"):
                    lines.append(f" neighbor {ip} description {nc['description']}")
                if nc.get("update_source"):
                    lines.append(f" neighbor {ip} update-source {nc['update_source']}")
                if nc.get("multihop"):
                    lines.append(f" neighbor {ip} ebgp-multihop {nc['multihop']}")
                for key, word in (("next_hop_self", "next-hop-self"), ("rr_client", "route-reflector-client"),
                                  ("default_originate", "default-originate"), ("shutdown", "shutdown"),
                                  ("disable_connected_check", "disable-connected-check")):
                    if nc.get(key):
                        lines.append(f" neighbor {ip} {word}")
                if nc.get("weight"):
                    lines.append(f" neighbor {ip} weight {nc['weight']}")
            for proto in bgp.get("redistribute", []):
                lines.append(f" redistribute {proto}" + (" 1" if proto == "ospf" else ""))
            lines.append("!")
        ospf = cfg.get("ospf")
        if ospf and ospf.get("enabled", True):
            lines.append(f"router ospf {ospf.get('process_id', 1)}")
            if ospf.get("router_id"):
                lines.append(f" router-id {ospf['router_id']}")
            if ospf.get("reference_bandwidth", 100) != 100:
                lines.append(f" auto-cost reference-bandwidth {ospf['reference_bandwidth']}")
            lines.append(" log-adjacency-changes")
            for proto in ospf.get("redistribute", []):
                lines.append(f" redistribute {proto} subnets")
            if ospf.get("passive_default"):
                lines.append(" passive-interface default")
                lines += [f" no passive-interface {x}" for x in ospf.get("non_passive", [])]
            lines += [f" passive-interface {x}" for x in ospf.get("passive", [])]
            for n in ospf.get("networks", []):
                net = IPv4Network(n["prefix"])
                lines.append(f" network {net.network_address} {IPv4Address(int(net.hostmask))} area {area_dec(n['area'])}")
            if ospf.get("default_originate"):
                lines.append(" default-information originate" +
                             (" always" if ospf["default_originate"] == "always" else ""))
            lines.append("!")
        dh = cfg.get("dhcp_server") or {}
        for lo, hi in dh.get("excluded", []):
            lines.append(f"ip dhcp excluded-address {lo}" + (f" {hi}" if hi and hi != lo else ""))
        for pool in dh.get("pools", []):
            lines.append(f"ip dhcp pool {pool['name']}")
            if pool.get("network"):
                n = IPv4Network(pool["network"])
                lines.append(f" network {n.network_address} {n.netmask}")
            if pool.get("gateway"):
                lines.append(f" default-router {pool['gateway']}")
            if pool.get("dns"):
                lines.append(f" dns-server {pool['dns']}")
            if pool.get("domain"):
                lines.append(f" domain-name {pool['domain']}")
            if pool.get("tftp"):
                lines.append(f" option 150 ip {pool['tftp']}")
        if dh.get("pools") or dh.get("excluded"):
            lines.append("!")
        nat_c = cfg.get("nat") or {}
        for pname, (start, end, plen) in nat_c.get("pools", {}).items():
            lines.append(f"ip nat pool {pname} {start} {end} netmask {IPv4Network(f'0.0.0.0/{plen}').netmask}")
        for r in nat_c.get("rules", []):
            if r["kind"] == "dynamic":
                if r.get("interface"):
                    lines.append(f"ip nat inside source list {r['acl']} interface {r['interface']} overload")
                else:
                    lines.append(f"ip nat inside source list {r['acl']} {r.get('text', '')}"
                                 + (" overload" if r.get("overload") else ""))
            elif r["kind"] == "static":
                if r.get("local_port"):
                    lines.append(f"ip nat inside source static {r['proto']} {r['local']} {r['local_port']} "
                                 f"{r['glob']} {r['global_port']}")
                else:
                    lines.append(f"ip nat inside source static {r['local']} {r['glob']}")
        if cfg.get("ipv6_routing"):
            lines.append("ipv6 unicast-routing")
        cme = cfg.get("cme") or {}
        if cme.get("enabled"):
            lines += ["telephony-service", f" max-ephones {cme.get('max_ephones', 0)}", f" max-dn {cme.get('max_dn', 0)}"]
            if cme.get("source"):
                lines.append(f" ip source-address {cme['source']} port 2000")
            if cme.get("auto_assign"):
                lines.append(" auto assign %d to %d" % tuple(cme["auto_assign"]))
            lines.append("!")
        for tag, num in sorted(((int(k), v) for k, v in cme.get("dns", {}).items())):
            lines += [f"ephone-dn  {tag}", f" number {num}", "!"]
        for tag, e in sorted(((int(k), v) for k, v in cme.get("ephones", {}).items())):
            lines.append(f"ephone  {tag}")
            if e.get("mac"):
                m = e["mac"].lower()
                lines.append(f" mac-address {m[:4]}.{m[4:8]}.{m[8:]}".upper().replace("MAC-ADDRESS", "mac-address"))
            if e.get("button"):
                lines.append(f" button 1:{e['button']}")
            lines.append("!")
        mgc = cfg.get("mgmt") or {}
        from ..services.mgmt import SEVERITY
        for h in mgc.get("syslog", []):
            lines.append(f"logging host {h}")
        if mgc.get("trap_level") is not None:
            lines.append(f"logging trap {SEVERITY[mgc['trap_level']]}")
        for c, v in mgc.get("snmp", {}).items():
            lines.append(f"snmp-server community {c} {v.upper()}")
        if mgc.get("location"):
            lines.append(f"snmp-server location {mgc['location']}")
        if mgc.get("contact"):
            lines.append(f"snmp-server contact {mgc['contact']}")
        if mgc.get("ntp_master") is not None:
            lines.append(f"ntp master {mgc['ntp_master']}")
        for h in mgc.get("ntp", []):
            lines.append(f"ntp server {h}")
        if mgc:
            lines.append("!")
        if cfg.get("nat64"):
            lines += [f"nat64 prefix stateful {cfg['nat64']['prefix'].upper()}", "!"]
        from ..security.qos import DSCP_NAMES
        q = cfg.get("qos") or {}
        for cname, m in q.get("classes", {}).items():
            lines.append(f"class-map match-any {cname}")
            if m.get("acl"):
                lines.append(f" match access-group name {m['acl']}")
            if m.get("protocol"):
                lines.append(f" match protocol {m['protocol']}")
            if m.get("dscp") is not None:
                lines.append(f" match dscp {DSCP_NAMES.get(m['dscp'], m['dscp'])}")
            if m.get("any"):
                lines.append(" match any")
            lines.append("!")
        for pol in q.get("policies", []):
            lines.append(f"policy-map {pol['name']}")
            for c in pol["classes"]:
                lines.append(f" class {c['name']}")
                if c.get("set_dscp") is not None:
                    lines.append(f"  set dscp {DSCP_NAMES.get(c['set_dscp'], c['set_dscp'])}")
                if c.get("police_bps"):
                    lines.append(f"  police cir {c['police_bps']}")
            lines.append("!")
        for addr, key in (cfg.get("extra") or {}).get("isakmp_keys", {}).items():
            lines += ["crypto isakmp policy 10", " encr aes 256", " authentication pre-share", " group 14", "!",
                      f"crypto isakmp key {key} address {addr}", "!"]
        for prof in sorted(set((cfg.get("extra") or {}).get("tunnel_protection", {}).values())):
            lines += ["crypto ipsec transform-set TS esp-aes 256 esp-sha-hmac", " mode tunnel", "!",
                      f"crypto ipsec profile {prof}", " set transform-set TS", "!"]
        for v in cfg.get("v6igp", []):
            if v["kind"] == "ospf6":
                lines.append(f"ipv6 router ospf {v.get('tag', '1')}")
                if v.get("router_id"):
                    lines.append(f" router-id {v['router_id']}")
            elif v["kind"] == "eigrp6":
                lines.append(f"ipv6 router eigrp {v.get('tag', '1')}")
                if v.get("router_id"):
                    lines.append(f" eigrp router-id {v['router_id']}")
            else:
                lines.append(f"ipv6 router rip {v.get('tag', '1')}")
            lines.append("!")
        for s6 in cfg.get("static_routes6", []):
            parts = [f"ipv6 route {s6['network'].upper()}"]
            if s6.get("interface"):
                parts.append(s6["interface"])
            if s6.get("next_hop"):
                parts.append(s6["next_hop"].upper())
            lines.append(" ".join(parts))
        if router:
            lines.append("ip classless")
        for s in cfg.get("static_routes", []):
            n = IPv4Network(s["network"])
            parts = [f"ip route{' vrf ' + s['vrf'] if s.get('vrf') else ''} {n.network_address} {n.netmask}"]
            if s.get("interface"):
                parts.append(s["interface"])
            if s.get("next_hop"):
                parts.append(s["next_hop"])
            if int(s.get("distance", 1)) != 1:
                parts.append(str(s["distance"]))
            lines.append(" ".join(parts))
        if router:
            lines += ["!", "ip flow-export version 9", "!"]
        elif cfg.get("static_routes"):
            lines.append("!")
        for name, a in cfg.get("acls", {}).items():
            acl = Acl.from_dict(a)
            ext = acl.kind == "extended"
            if name.isdigit():
                lines += [f"access-list {name} {ACLX.entry_text(r, ext)}" for r in acl.rules]
            else:
                lines.append(f"ip access-list {acl.kind} {name}")
                lines += [f" {ACLX.entry_text(r, ext)}" for r in acl.rules]
            lines.append("!")
    from ..services.remote import default_access
    acc = dict(default_access(device))
    acc.update(cfg.get("access") or {})
    head = []
    if acc.get("enable_secret"):
        head.append(f"enable secret 5 $1$mERr${_hash(acc['enable_secret'])}")
    for u, pw in acc.get("users", {}).items():
        head.append(f"username {u} secret 5 $1$mERr${_hash(pw)}")
    if acc.get("domain"):
        head.append(f"ip domain-name {acc['domain']}")
    if acc.get("rsa"):
        head.append("ip ssh version 2")
    if head:
        i = lines.index(f"hostname {cfg.get('hostname')}") + 2
        lines[i:i] = head + ["!"]
    vty = ["line vty 0 4"]
    if acc.get("vty_password"):
        vty.append(f" password {acc['vty_password']}")
    vty.append({"line": " login", "local": " login local", "none": " no login"}[acc.get("login", "line")])
    tr = "all" if acc.get("telnet") and acc.get("ssh") else ("ssh" if acc.get("ssh") else
                                                             ("telnet" if acc.get("telnet") else "none"))
    if tr != "all":
        vty.append(f" transport input {tr}")
    lines += ["!", "line con 0", "!"] + vty + ["!", "!", "end"]
    return "\n".join(lines)
