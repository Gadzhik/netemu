"""Huawei VRP dialect (AR routers, S-series switches)."""
from __future__ import annotations

from ..addresses import IPv4Address, IPv4Interface, IPv4Network, make_interface
from ..stack.icmp import PingListener, TraceListener
from . import parser as P
from .base import TreeSession, make_ping, overlapping_port, v6_addr_cmd, v6_route_cmd, make_trace
from .render import uptime_text

HUAWEI_STATIC_PREF = 60


class HuaweiPing(PingListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f"  PING {s.dst}: {s.size}  data bytes, press CTRL_C to break\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w(f"    Reply from {src}: bytes={size} Sequence={seq + 1} ttl={ttl} time={round(rtt)} ms\n")

    def on_timeout(self, seq):
        self.w("    Request time out\n")

    on_send_fail = on_timeout

    def on_error(self, seq, kind, src):
        text = "Destination unreachable" if kind == "unreachable" else "TTL exceeded"
        self.w(f"    Reply from {src}: {text}\n")

    def on_finish(self, st):
        self.w(f"\n  --- {self.dst} ping statistics ---\n"
               f"    {st.sent} packet(s) transmitted\n"
               f"    {st.received} packet(s) received\n"
               f"    {st.loss_percent:.2f}% packet loss\n")
        if st.received:
            self.w(f"    round-trip min/avg/max = {st.rtt('min')}/{st.rtt('avg')}/{st.rtt('max')} ms\n")


class HuaweiTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f" traceroute to  {s.dst}({s.dst}), max hops: {s.max_hops} ,packet length: 40,"
               f"press CTRL_C to break \n")

    def on_hop(self, hop):
        parts = ["*" if r is None else f"{round(r)} ms" for r in hop.probes]
        addr = f"{hop.address} " if hop.address else ""
        self.w(f" {hop.ttl} {addr}" + "  ".join(parts) + "\n")


class HuaweiSession(TreeSession):
    negate_word = "undo"
    negate_modes = ("system", "interface", "vlan", "ospf", "ospf-area", "acl", "bgp", "bgp-af", "ip-pool", "rip",
                    "ui-vty", "aaa", "ospfv3", "ripng", "dhcpv6-pool", "classifier", "behavior", "trafficpolicy", "isis", "ipsec-proposal", "ike-peer", "ipsec-profile")

    @property
    def is_router(self) -> bool:
        return self.device.category == "router"

    @property
    def prompt(self) -> str:
        h = self.device.hostname
        if self.mode == "user":
            return f"<{h}>"
        if self.mode == "system":
            return f"[{h}]"
        if self.mode == "vlan":
            return f"[{h}-vlan{self.vlan_ctx}]"
        if self.mode == "rip":
            return f"[{h}-rip-1]"
        if self.mode == "ip-pool":
            return f"[{h}-ip-pool-{self.pool_ctx}]"
        if self.mode == "bgp":
            return f"[{h}-bgp]"
        if self.mode == "bgp-af":
            return f"[{h}-bgp-af-ipv4]"
        if self.mode == "acl":
            kind = "basic" if self.device.acls[self.acl_ctx].kind == "basic" else "adv"
            return f"[{h}-acl-{kind}-{self.acl_ctx}]"
        if self.mode == "ospf":
            return f"[{h}-ospf-{self.device.ospf.cfg.process_id}]"
        if self.mode == "ospf-area":
            return f"[{h}-ospf-{self.device.ospf.cfg.process_id}-area-{self.area_ctx}]"
        if self.mode == "isis":
            return f"[{h}-isis-{self.device.isis.cfg.tag if self.device.isis else 1}]"
        if self.mode in ("ipsec-proposal", "ike-peer", "ipsec-profile"):
            return f"[{h}-{self.mode}-{self._sec_name}]"
        if self.mode == "classifier":
            return f"[{h}-classifier-{self._qc}]"
        if self.mode == "behavior":
            return f"[{h}-behavior-{self._qb}]"
        if self.mode == "trafficpolicy":
            return f"[{h}-trafficpolicy-{self._qp}]"
        if self.mode == "dhcpv6-pool":
            return f"[{h}-dhcpv6-pool-{self._dhcp6_pool}]"
        if self.mode == "ospfv3":
            return f"[{h}-ospfv3-1]"
        if self.mode == "ripng":
            return f"[{h}-ripng-1]"
        if self.mode == "ui-vty":
            return f"[{h}-ui-vty0-4]"
        if self.mode == "aaa":
            return f"[{h}-aaa]"
        return f"[{h}-{self.iface.name}]"

    def banner(self) -> str:
        return ("\nInfo: The max number of VTY users is 5, and the number\n"
                "      of current VTY users on line is 0.\n")

    def err_invalid(self, prompt, pos):
        return " " * (len(prompt) + pos) + "^\nError: Unrecognized command found at '^' position.\n"

    def err_ambiguous(self, prompt, pos, token):
        return " " * (len(prompt) + pos) + "^\nError: Ambiguous command found at '^' position.\n"

    def err_incomplete(self, prompt, pos):
        return " " * (len(prompt) + pos) + "^\nError:Incomplete command found at '^' position.\n"

    def ctrl_z(self) -> str:
        self.mode = "user"
        self.iface = None
        return ""

    # -- trees -------------------------------------------------------------------
    def build_trees(self) -> None:
        self._build_trees_base()
        t = self._trees
        word = lambda h: P.WordArg(h)
        ip = lambda: P.IpArg()
        clients = [
            P.kw("telnet", "Open a telnet connection",
                 P.arg(ip(), "IP address of the remote system", handler=lambda s, a, n: s.remote_telnet(a[0]))),
            P.kw("stelnet", "Open an STelnet (SSH) connection",
                 P.arg(ip(), "IP address of the remote system", handler=lambda s, a, n: s.remote_stelnet(a[0]))),
        ]
        for mode in ("user", "system"):
            t[mode] = t[mode] + clients
        common = [x for x in t["system"] if x.word in ("display", "quit", "return")]
        t["system"] = t["system"] + [
            P.kw("telnet", "Telnet server", P.kw("server", "Server",
                 P.kw("enable", "Enable the telnet server", handler=lambda s, a, n: s.srv_enable("telnet", not n),
                      negatable=True), negatable=True), negatable=True),
            P.kw("stelnet", "STelnet (SSH) server", P.kw("server", "Server",
                 P.kw("enable", "Enable the STelnet server", handler=lambda s, a, n: s.srv_enable("ssh", not n),
                      negatable=True), negatable=True), negatable=True),
            P.kw("user-interface", "Configure the user interface",
                 P.kw("vty", "Virtual terminal",
                      P.arg(P.NumArg(0, 4, "INTEGER<0-4>"), "First number",
                            P.arg(P.NumArg(0, 4, "INTEGER<0-4>"), "Last number", handler=lambda s, a, n: s.set_mode_h("ui-vty")),
                            handler=lambda s, a, n: s.set_mode_h("ui-vty")))),
            P.kw("aaa", "AAA view", handler=lambda s, a, n: s.set_mode_h("aaa")),
            P.kw("ssh", "SSH", P.kw("user", "SSH user",
                 P.arg(word("STRING"), "User name",
                       P.kw("authentication-type", "Authentication type",
                            P.kw("password", "Password", handler=lambda s, a, n: None)),
                       P.kw("service-type", "Service type",
                            P.kw("stelnet", "STelnet", handler=lambda s, a, n: None))))),
        ]
        # merge the two "telnet" / "stelnet" words of the system view into one node each
        merged = {}
        for node in t["system"]:
            if node.word in ("telnet", "stelnet") and node.word in merged:
                merged[node.word].children += node.children
                merged[node.word].handler = merged[node.word].handler or node.handler
                merged[node.word].negatable = True
                continue
            merged.setdefault(node.word, node) if node.word in ("telnet", "stelnet") else None
        t["system"] = [n for n in t["system"] if n.word not in ("telnet", "stelnet") or merged.get(n.word) is n]
        if self.is_router:
            for iface_root in (x for m in ("system", "interface") for x in t[m] if x.word == "interface"):
                iface_root.children.append(P.arg(P.TunnelNameArg(), "Tunnel interface",
                                                 handler=lambda s, a, n: s.enter_tun(a[0], n), negatable=True))
            c6 = [x for x in t["system"] if x.word in ("display", "quit", "return")]
            nop = lambda w, h: P.kw(w, h, P.arg(P.LineArg(), h, handler=lambda s, a, n: None))
            t["system"].append(P.kw("isis", "IS-IS", P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                    handler=lambda s, a, n: s.isis_view(a[0], n), negatable=True),
                                    handler=lambda s, a, n: s.isis_view(1, n), negatable=True))
            t["isis"] = c6 + [
                P.kw("network-entity", "Network entity title", P.arg(P.WordArg("XX.XXXX....XXXX.00"), "NET",
                     handler=lambda s, a, n: s.isis_net(a[0], n), negatable=True), negatable=True),
                nop("is-level", "Level"), nop("cost-style", "Cost style"), nop("is-name", "Host name"),
                P.kw("ipv6", "IPv6", P.kw("enable", "Enable IPv6 for the IS-IS process",
                                          handler=lambda s, a, n: None, negatable=True), negatable=True)]
            t["interface"].append(P.kw("isis", "IS-IS", P.kw("enable", "Enable IS-IS on the interface",
                P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID", handler=lambda s, a, n: s.if_isis(n),
                      negatable=True), handler=lambda s, a, n: s.if_isis(n), negatable=True),
                P.kw("cost", "Interface cost", P.arg(P.NumArg(1, 16777214, "INTEGER<1-16777214>"), "Cost",
                     handler=lambda s, a, n: s.isis_cost(a[0], n), negatable=True), negatable=True),
                P.kw("ipv6", "IPv6", P.kw("enable", "Enable IS-IS for IPv6 on the interface",
                    P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID", handler=lambda s, a, n: s.if_isis6(n),
                          negatable=True), handler=lambda s, a, n: s.if_isis6(n), negatable=True), negatable=True),
                nop("circuit-type", "Circuit type"), negatable=True))
            next(x for x in t["user"] if x.word == "display").children.append(P.kw("isis", "IS-IS",
                P.kw("peer", "Neighbours", handler=lambda s, a, n: s.display_isis("peer")),
                P.kw("lsdb", "Link state database", handler=lambda s, a, n: s.display_isis("lsdb"))))
            sec = lambda mode, h: P.kw(mode, h, P.arg(P.WordArg("STRING<1-15>"), "Name",
                                                      handler=lambda s, a, n, m=mode: s.sec_view(m, a[0], n), negatable=True),
                                       negatable=True)
            t["system"] += [P.kw("ipsec", "IPsec", P.kw("proposal", "IPsec proposal",
                                 P.arg(P.WordArg("STRING<1-15>"), "Name",
                                       handler=lambda s, a, n: s.sec_view("ipsec-proposal", a[0], n), negatable=True)),
                                 P.kw("profile", "IPsec profile", P.arg(P.WordArg("STRING<1-15>"), "Name",
                                       handler=lambda s, a, n: s.sec_view("ipsec-profile", a[0], n), negatable=True)),
                                 negatable=True),
                            P.kw("ike", "IKE", P.kw("peer", "IKE peer", P.arg(P.WordArg("STRING<1-15>"), "Name",
                                 handler=lambda s, a, n: s.sec_view("ike-peer", a[0], n), negatable=True)), negatable=True)]
            _ = sec
            noop = lambda w, h: P.kw(w, h, P.arg(P.LineArg(), h, handler=lambda s, a, n: None))
            common6 = [x for x in t["system"] if x.word in ("display", "quit", "return")]
            t["ipsec-proposal"] = common6 + [
                P.kw("esp", "ESP", noop("authentication-algorithm", "Authentication"),
                     noop("encryption-algorithm", "Encryption")),
                noop("transform", "Transform"), noop("encapsulation-mode", "Mode")]
            t["ike-peer"] = common6 + [
                P.kw("pre-shared-key", "Pre-shared key",
                     P.kw("cipher", "Cipher text", P.arg(P.WordArg("STRING"), "Key", handler=lambda s, a, n: s.sec_set("key", a[0]))),
                     P.kw("simple", "Plain text", P.arg(P.WordArg("STRING"), "Key", handler=lambda s, a, n: s.sec_set("key", a[0])))),
                P.kw("remote-address", "Remote address", P.arg(P.IpArg(), "IP address",
                                                               handler=lambda s, a, n: s.sec_set("remote", a[0]))),
                noop("ike-proposal", "IKE proposal"), noop("version", "IKE version")]
            t["ipsec-profile"] = common6 + [
                P.kw("ike-peer", "IKE peer", P.arg(P.WordArg("STRING"), "Peer", handler=lambda s, a, n: s.sec_set("peer", a[0]))),
                P.kw("proposal", "Proposal", P.arg(P.WordArg("STRING"), "Proposal", handler=lambda s, a, n: None))]
            t["interface"] = t["interface"] + [
                P.kw("ipsec", "IPsec", P.kw("profile", "Apply an IPsec profile", P.arg(P.WordArg("STRING"), "Profile",
                     handler=lambda s, a, n: s.tun_ipsec(a[0], n), negatable=True), negatable=True), negatable=True),
                P.kw("tunnel-protocol", "Tunnel protocol", P.kw("gre", "GRE", handler=lambda s, a, n: None),
                     P.kw("ipsec", "IPsec", handler=lambda s, a, n: None),
                     P.kw("none", "None", handler=lambda s, a, n: None)),
                P.kw("source", "Tunnel source", P.arg(P.IpArg(), "IP address", handler=lambda s, a, n: s.tun("source", a[0])),
                     P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.tun("source", a[0].name))),
                P.kw("destination", "Tunnel destination",
                     P.arg(P.IpArg(), "IP address", handler=lambda s, a, n: s.tun("destination", a[0])))]
        if not self.is_router:
            for iface_root in (x for m in ("system", "interface") for x in t[m] if x.word == "interface"):
                iface_root.children.append(P.arg(P.VlanIfArg("Eth-Trunk <0-63>", word="eth-trunk", min_prefix=3, lo=0, hi=63),
                                                 "Ethernet trunk interface",
                                                 handler=lambda s, a, n: s.enter_trunk(a[0], n), negatable=True))
            t["interface"] = t["interface"] + [
                P.kw("eth-trunk", "Add the interface to an Eth-Trunk",
                     P.arg(P.NumArg(0, 63, "INTEGER<0-63>"), "Eth-Trunk ID",
                           handler=lambda s, a, n: s.trunk_member(a[0], n), negatable=True),
                     handler=lambda s, a, n: s.trunk_member(None, True) if n else s.err_incomplete(s.prompt, 0),
                     negatable=True),
                P.kw("mode", "Eth-Trunk working mode",
                     P.kw("lacp", "LACP mode", handler=lambda s, a, n: s.trunk_mode("active")),
                     P.kw("lacp-static", "Static LACP mode", handler=lambda s, a, n: s.trunk_mode("active")),
                     P.kw("manual", "Manual load balance mode",
                          P.kw("load-balance", "Load balance", handler=lambda s, a, n: s.trunk_mode("on")),
                          handler=lambda s, a, n: s.trunk_mode("on")))]
            disp = next(x for x in t["user"] if x.word == "display")
            disp.children.append(P.kw("eth-trunk", "Eth-Trunk information",
                                      P.arg(P.NumArg(0, 63, "INTEGER<0-63>"), "Eth-Trunk ID",
                                            handler=lambda s, a, n: s.display_trunk(a[0])),
                                      handler=lambda s, a, n: s.display_trunk(None)))
        vr = P.kw("vrrp", "VRRP",
                  P.kw("vrid", "Virtual router ID",
                       P.arg(P.NumArg(1, 255, "INTEGER<1-255>"), "VRID",
                             P.kw("virtual-ip", "Virtual IP address",
                                  P.arg(P.IpArg(), "IP address",
                                        handler=lambda s, a, n: s.vrrp_cmd(a[0], "vip", a[1], n), negatable=True),
                                  negatable=True),
                             P.kw("priority", "Priority", P.arg(P.NumArg(1, 254, "INTEGER<1-254>"), "Priority",
                                                              handler=lambda s, a, n: s.vrrp_cmd(a[0], "priority", a[1], n),
                                                              negatable=True), negatable=True),
                             P.kw("preempt-mode", "Preemption", P.kw("disable", "Disable preemption",
                                                                     handler=lambda s, a, n: s.vrrp_cmd(a[0], "preempt", n, False))),
                             handler=lambda s, a, n: s.vrrp_cmd(a[0], None, None, True) if n else s.err_incomplete(s.prompt, 0),
                             negatable=True),
                       negatable=True),
                  negatable=True)
        if not self.is_router:
            t["interface"] = t["interface"] + [
                P.kw("port-security", "Port security",
                     P.kw("enable", "Enable port security", handler=lambda s, a, n: s.psec("enable", None, n), negatable=True),
                     P.kw("max-mac-num", "Maximum secure MAC addresses",
                          P.arg(P.NumArg(1, 4096, "INTEGER<1-4096>"), "Number",
                                handler=lambda s, a, n: s.psec("max", a[0], n), negatable=True), negatable=True),
                     P.kw("protect-action", "Action on violation",
                          *[P.kw(m, m, handler=lambda s, a, n, m=m: s.psec("violation", {"error-down": "shutdown"}.get(m, m), n))
                            for m in ("protect", "restrict", "error-down")]),
                     P.kw("mac-address", "Secure MAC address", P.kw("sticky", "Sticky MAC",
                          handler=lambda s, a, n: s.psec("sticky", None, n), negatable=True), negatable=True),
                     negatable=True),
]
            snoop_if = P.kw("snooping", "DHCP snooping",
                            P.kw("trusted", "Trusted interface", handler=lambda s, a, n: s.snoop_trust(n), negatable=True),
                            negatable=True)
            dif = next((x for x in t["interface"] if x.word == "dhcp"), None)
            if dif is None:
                t["interface"].append(P.kw("dhcp", "DHCP", snoop_if, negatable=True))
            else:
                dif.children.append(snoop_if)
            dsys = next((x for x in t["system"] if x.word == "dhcp"), None)
            node = P.kw("snooping", "DHCP snooping",
                        P.kw("enable", "Enable DHCP snooping",
                             P.kw("vlan", "VLAN", P.arg(P.VlanListArg(), "VLAN list",
                                                        handler=lambda s, a, n: s.snoop_vlan(a[0], n), negatable=True)),
                             handler=lambda s, a, n: s.snoop_enable(not n), negatable=True),
                        negatable=True)
            if dsys is None:
                t["system"].append(P.kw("dhcp", "DHCP", node, negatable=True))
            else:
                dsys.children.append(node)
            disp0 = next(x for x in t["user"] if x.word == "display")
            disp0.children += [
                P.kw("port-security", "Port security", handler=lambda s, a, n: s.display_psec()),
                P.kw("dhcp", "DHCP", P.kw("snooping", "DHCP snooping",
                     P.kw("user-bind", "Binding table", P.kw("all", "All", handler=lambda s, a, n: s.display_snoop(True))),
                     handler=lambda s, a, n: s.display_snoop(False)))]
        t["interface"] = t["interface"] + [vr,
            P.kw("ospfv3", "OSPFv3 interface configuration",
                 P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                       P.kw("area", "Area", P.arg(P.WordArg("IPADDR<X.X.X.X>|INTEGER"), "Area ID",
                                                  handler=lambda s, a, n: s.v6igp_if("ospf6", a[0], a[1], n),
                                                  negatable=True), negatable=True),
                       negatable=True),
                 P.kw("cost", "Cost", P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Cost",
                                            handler=lambda s, a, n: s.v6igp_cost(a[0], n))),
                 negatable=True),
            P.kw("ripng", "RIPng interface configuration",
                 P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                       P.kw("enable", "Enable RIPng on the interface",
                            handler=lambda s, a, n: s.v6igp_if("ripng", a[0], None, n), negatable=True),
                       negatable=True),
                 negatable=True)]
        t["system"] = t["system"] + [
            P.kw("ospfv3", "OSPFv3", P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                           handler=lambda s, a, n: s.v6igp_view("ospf6", a[0], n), negatable=True),
                 negatable=True),
            P.kw("ripng", "RIPng", P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                         handler=lambda s, a, n: s.v6igp_view("ripng", a[0], n), negatable=True),
                 negatable=True)]
        common6 = [x for x in t["system"] if x.word in ("display", "quit", "return")]
        t["ospfv3"] = common6 + [
            P.kw("router-id", "Router ID", P.arg(P.IpArg(), "Router ID", handler=lambda s, a, n: s.v6igp_rid(a[0]))),
            P.kw("area", "Area", P.arg(P.WordArg("IPADDR"), "Area ID", handler=lambda s, a, n: None))]
        t["ripng"] = list(common6)
        t["system"].append(P.kw("dhcpv6", "DHCPv6", P.kw("pool", "DHCPv6 address pool",
                                P.arg(P.WordArg("STRING<1-31>"), "Pool name",
                                      handler=lambda s, a, n: s.dhcp6_pool(a[0], n), negatable=True), negatable=True),
                                negatable=True))
        if self.is_router:
            t["system"].append(P.kw("nat64", "NAT64",
                P.kw("enable", "Enable NAT64", handler=lambda s, a, n: s.nat64_cmd("enable", None, n), negatable=True),
                P.kw("prefix", "NAT64 prefix", P.arg(P.Ipv6Arg(), "Prefix",
                     P.kw("prefix-length", "Prefix length", P.arg(P.NumArg(32, 96, "INTEGER<32-96>"), "Length",
                          handler=lambda s, a, n: s.nat64_cmd("prefix", f"{a[0]}/{a[1]}", n), negatable=True)))),
                negatable=True))
            t["interface"].append(P.kw("nat64", "NAT64",
                P.kw("enable", "Enable NAT64 on the interface", handler=lambda s, a, n: s.nat64_cmd("if", None, n),
                     negatable=True), negatable=True))
            next(x for x in t["user"] if x.word == "display").children.append(
                P.kw("nat64", "NAT64", P.kw("session", "Sessions", P.kw("all", "All",
                     handler=lambda s, a, n: s.display_nat64()))))
        if not self.is_router:
            from .base import set_voice_vlan
            t["interface"].append(P.kw("voice-vlan", "Voice VLAN", P.arg(P.NumArg(1, 4094, "INTEGER<1-4094>"), "VLAN ID",
                P.kw("enable", "Enable voice VLAN on the port",
                     handler=lambda s, a, n: set_voice_vlan(s.iface, None if n else a[0]), negatable=True),
                negatable=True), negatable=True))
        from .base import mgmt_add, mgmt_logging_text, mgmt_ntp_assoc, mgmt_ntp_status
        hmg = lambda what: (lambda s, a, n: mgmt_add(s.device, what, a[-1] if a else None, n))
        t["system"] += [
            P.kw("info-center", "Information center",
                 P.kw("loghost", "Log host", P.arg(P.IpArg(), "IP address", handler=hmg("syslog"), negatable=True),
                      negatable=True),
                 P.kw("enable", "Enable the information center", handler=lambda s, a, n: None, negatable=True),
                 negatable=True),
            P.kw("snmp-agent", "SNMP agent",
                 P.kw("community", "Community",
                      P.kw("read", "Read-only", P.arg(P.WordArg("STRING<1-32>"), "Community name", handler=hmg("ro"),
                                                     negatable=True), negatable=True),
                      P.kw("write", "Read-write", P.arg(P.WordArg("STRING<1-32>"), "Community name", handler=hmg("rw"),
                                                       negatable=True), negatable=True), negatable=True),
                 P.kw("sys-info", "System information",
                      P.kw("location", "Location", P.arg(P.LineArg(), "Location", handler=hmg("location"), negatable=True),
                           handler=lambda s, a, n: mgmt_add(s.device, "location", "", True), negatable=True),
                      P.kw("contact", "Contact", P.arg(P.LineArg(), "Contact", handler=hmg("contact"), negatable=True),
                           handler=lambda s, a, n: mgmt_add(s.device, "contact", "", True), negatable=True),
                      P.kw("version", "Version", P.arg(P.LineArg(), "v1|v2c|v3|all", handler=lambda s, a, n: None)),
                      negatable=True),
                 handler=lambda s, a, n: None, negatable=True),
            P.kw("ntp-service", "NTP service",
                 P.kw("unicast-server", "Unicast server", P.arg(P.IpArg(), "IP address", handler=hmg("ntp"), negatable=True),
                      negatable=True),
                 P.kw("refclock-master", "Local reference clock",
                      P.arg(P.NumArg(1, 15, "INTEGER<1-15>"), "Stratum", handler=hmg("ntp_master"), negatable=True),
                      handler=lambda s, a, n: mgmt_add(s.device, "ntp_master", None, n), negatable=True),
                 P.kw("enable", "Enable NTP", handler=lambda s, a, n: None, negatable=True),
                 negatable=True)]
        next(x for x in t["user"] if x.word == "display").children += [
            P.kw("logbuffer", "Log buffer", handler=lambda s, a, n: "\n".join(s.device.mgmt.buffer) or "Info: The log buffer is empty."),
            P.kw("info-center", "Information center", handler=lambda s, a, n: mgmt_logging_text(s.device)),
            P.kw("ntp-service", "NTP service",
                 P.kw("status", "Status", handler=lambda s, a, n: mgmt_ntp_status(s.device).replace(
                     "Clock is synchronized", "clock status: synchronized\nClock is synchronized").replace(
                     "Clock is unsynchronized", "clock status: unsynchronized\nClock is unsynchronized")),
                 P.kw("sessions", "Sessions", handler=lambda s, a, n: mgmt_ntp_assoc(s.device))),
            P.kw("snmp-agent", "SNMP agent",
                 P.kw("community", "Community", handler=lambda s, a, n: "\n".join(
                     f"   Community name: {c}\n       Group name: {c}\n       Storage-type: nonVolatile"
                     for c in s.device.mgmt.communities)),
                 P.kw("sys-info", "System information", handler=lambda s, a, n:
                      f"   The contact person for this managed node:\n           {s.device.mgmt.contact}\n\n"
                      f"   The physical location of this node:\n           {s.device.mgmt.location}\n\n"
                      "   SNMP version running in the system:\n           SNMPv2c"))]
        if self.is_router:
            word = lambda h: P.WordArg(h)
            t["system"].append(P.kw("traffic", "Traffic (QoS)",
                P.kw("classifier", "Traffic classifier", P.arg(word("STRING<1-31>"), "Classifier name",
                     P.kw("operator", "Operator", P.kw("or", "OR", handler=lambda s, a, n: s.q_view("classifier", a[0])),
                          P.kw("and", "AND", handler=lambda s, a, n: s.q_view("classifier", a[0]))),
                     handler=lambda s, a, n: s.q_view("classifier", a[0]))),
                P.kw("behavior", "Traffic behavior", P.arg(word("STRING<1-31>"), "Behavior name",
                     handler=lambda s, a, n: s.q_view("behavior", a[0]))),
                P.kw("policy", "Traffic policy", P.arg(word("STRING<1-31>"), "Policy name",
                     handler=lambda s, a, n: s.q_view("trafficpolicy", a[0])))))
            t["classifier"] = common6 + [P.kw("if-match", "Match rule",
                P.kw("acl", "ACL", P.arg(word("INTEGER<2000-3999>|STRING"), "ACL", handler=lambda s, a, n: s.q_match("acl", a[0]))),
                P.kw("dscp", "DSCP", P.arg(word("INTEGER<0-63>|ef|af41"), "DSCP", handler=lambda s, a, n: s.q_match("dscp", a[0]))),
                P.kw("protocol", "Protocol", P.arg(word("icmp|tcp|udp"), "Protocol", handler=lambda s, a, n: s.q_match("protocol", a[0]))),
                P.kw("any", "Any packet", handler=lambda s, a, n: s.q_match("any", True)))]
            t["behavior"] = common6 + [
                P.kw("remark", "Remark", P.kw("dscp", "DSCP", P.arg(word("INTEGER<0-63>|ef|af41"), "DSCP",
                                                                  handler=lambda s, a, n: s.q_behave("dscp", a[0])))),
                P.kw("car", "Committed access rate", P.kw("cir", "CIR", P.arg(P.NumArg(8, 10000000, "INTEGER<8-10000000>"),
                     "kbit/s", handler=lambda s, a, n: s.q_behave("police", a[0] * 1000))))]
            t["trafficpolicy"] = common6 + [P.kw("classifier", "Classifier", P.arg(word("STRING"), "Classifier",
                P.kw("behavior", "Behavior", P.arg(word("STRING"), "Behavior", handler=lambda s, a, n: s.q_bind(a[0], a[1])))))]
            t["interface"].append(P.kw("traffic-policy", "Apply a traffic policy", P.arg(word("STRING"), "Policy name",
                P.kw("inbound", "Inbound", handler=lambda s, a, n: s.q_apply(a[0], "in", n), negatable=True),
                P.kw("outbound", "Outbound", handler=lambda s, a, n: s.q_apply(a[0], "out", n), negatable=True),
                negatable=True), negatable=True))
            next(x for x in t["user"] if x.word == "display").children.append(P.kw("traffic", "Traffic",
                P.kw("policy", "Policy", P.kw("statistics", "Statistics", handler=lambda s, a, n: s.q_stats()))))
        t["dhcpv6-pool"] = common6 + [
            P.kw("address", "Address", P.kw("prefix", "Prefix",
                 P.arg(P.Ipv6PrefixArg(), "Prefix", handler=lambda s, a, n: s.dhcp6_attr("prefix", a[0])))),
            P.kw("dns-server", "DNS server", P.arg(P.Ipv6Arg(), "Address", handler=lambda s, a, n: s.dhcp6_attr("dns", a[0])))]
        t["interface"].append(P.kw("dhcpv6", "DHCPv6", P.kw("server", "DHCPv6 server",
                                   P.arg(P.WordArg("STRING<1-31>"), "Pool name",
                                         handler=lambda s, a, n: s.dhcp6_if(a[0], n), negatable=True), negatable=True),
                                   negatable=True))
        next(x for x in t["user"] if x.word == "display").children.append(
            P.kw("dhcpv6", "DHCPv6", P.kw("pool", "Pools", handler=lambda s, a, n: s.display_dhcp6())))
        next(x for x in t["user"] if x.word == "display").children.append(P.kw("ospfv3", "OSPFv3",
            P.kw("lsdb", "Link state database", handler=lambda s, a, n: __import__(
                "netemu.core.cli.base", fromlist=["x"]).ospf6_database_text(s.device)), P.kw("peer", "Neighbours",
                                                          handler=lambda s, a, n: s.display_v6igp("ospf6"))))
        next(x for x in t["user"] if x.word == "display").children.append(P.kw("ripng", "RIPng", handler=lambda s, a, n: s.display_v6igp("ripng")))
        disp = next(x for x in t["user"] if x.word == "display")
        disp.children.append(P.kw("vrrp", "VRRP information",
                                  P.kw("brief", "Brief", handler=lambda s, a, n: s.display_vrrp(True)),
                                  handler=lambda s, a, n: s.display_vrrp(False)))
        t["ui-vty"] = common + [
            P.kw("authentication-mode", "Login authentication mode",
                 *[P.kw(m, m, handler=lambda s, a, n, m=m: s.vty_auth(m)) for m in ("aaa", "password", "none")]),
            P.kw("set", "Set", P.kw("authentication", "Authentication", P.kw("password", "Password",
                 P.kw("cipher", "Cipher text", P.arg(word("STRING"), "Password",
                                                     handler=lambda s, a, n: s.vty_password(a[0])))))),
            P.kw("protocol", "Protocol", P.kw("inbound", "Inbound",
                 *[P.kw(m, m, handler=lambda s, a, n, m=m: s.vty_proto(m)) for m in ("all", "ssh", "telnet")])),
            P.kw("user", "User", P.kw("privilege", "Privilege", P.kw("level", "Level",
                 P.arg(P.NumArg(0, 15, "INTEGER<0-15>"), "Level", handler=lambda s, a, n: None)))),
            P.kw("idle-timeout", "Idle timeout", P.arg(P.NumArg(0, 35791, "INTEGER<0-35791>"), "Minutes",
                                                       handler=lambda s, a, n: None)),
        ]
        t["aaa"] = common + [
            P.kw("local-user", "Local user",
                 P.arg(word("STRING<1-64>"), "User name",
                       P.kw("password", "Password",
                            *[P.kw(k, k, P.arg(word("STRING"), "Password",
                                               handler=lambda s, a, n: s.local_user(a[0], password=a[1])))
                              for k in ("cipher", "irreversible-cipher")]),
                       P.kw("service-type", "Service type",
                            P.arg(P.LineArg("telnet ssh terminal ..."), "Services",
                                  handler=lambda s, a, n: s.local_user(a[0], services=a[1]))),
                       P.kw("privilege", "Privilege", P.kw("level", "Level",
                            P.arg(P.NumArg(0, 15, "INTEGER<0-15>"), "Level", handler=lambda s, a, n: s.local_user(a[0])))),
                       handler=lambda s, a, n: s.local_user(a[0], delete=True) if n else s.err_incomplete(s.prompt, 0),
                       negatable=True),
                 negatable=True),
        ]

    def _build_trees_base(self) -> None:
        display_items = [
            P.kw("arp", "Display ARP entries", handler=lambda s, a, n: s.display_arp()),
            P.kw("current-configuration", "Current configuration",
                 handler=lambda s, a, n: s.display_cfg(s.device.to_config())),
            P.kw("saved-configuration", "Saved configuration",
                 handler=lambda s, a, n: s.display_saved()),
            P.kw("interface", "Interface status and configuration information",
                 P.kw("brief", "Brief information of status and configuration",
                      handler=lambda s, a, n: s.display_int_brief()),
                 P.arg(P.IfaceArg(), "Interface name",
                       handler=lambda s, a, n: s.display_interface(a[0])),
                 handler=lambda s, a, n: s.display_interface(None)),
            P.kw("ip", "Display IP information",
                 P.kw("interface", "Interface information",
                      P.kw("brief", "Brief information", handler=lambda s, a, n: s.display_ip_int_brief())),
                 *([P.kw("routing-table", "Information about routing table",
                         handler=lambda s, a, n: s.display_routing_table())])),
            P.kw("version", "Display version information", handler=lambda s, a, n: s.display_version()),
            *([P.kw("ipv6", "IPv6 information",
                    P.kw("interface", "Interface", P.kw("brief", "Brief", handler=lambda s, a, n: s.display_v6_brief())),
                    P.kw("routing-table", "IPv6 routing table", handler=lambda s, a, n: s.display_v6_routes()),
                    P.kw("neighbors", "IPv6 neighbors", handler=lambda s, a, n: s.display_v6_neighbors())),
               P.kw("acl", "ACL information",
                    P.kw("all", "All ACLs", handler=lambda s, a, n: s.display_acl(None)),
                    P.arg(P.NumArg(2000, 3999, "INTEGER<2000-3999>"), "ACL number",
                          handler=lambda s, a, n: s.display_acl(str(a[0])))),
               P.kw("rip", "RIP information",
                    P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                          P.kw("route", "Routing table", handler=lambda s, a, n: s.display_rip_route()),
                          handler=lambda s, a, n: s.display_rip_route())),
               P.kw("bgp", "BGP information",
                    P.kw("peer", "Peers", handler=lambda s, a, n: s.display_bgp_peer()),
                    P.kw("routing-table", "BGP routing table", handler=lambda s, a, n: s.display_bgp_table())),
               P.kw("nat", "NAT information",
                    P.kw("outbound", "Outbound NAT", handler=lambda s, a, n: s.display_nat("outbound")),
                    P.kw("static", "Static NAT", handler=lambda s, a, n: s.display_nat("static")),
                    P.kw("server", "NAT server", handler=lambda s, a, n: s.display_nat("server")),
                    P.kw("address-group", "Address group", handler=lambda s, a, n: s.display_nat("group")),
                    P.kw("session", "NAT sessions",
                         P.kw("all", "All sessions", handler=lambda s, a, n: s.display_nat("session")),
                         handler=lambda s, a, n: s.display_nat("session")))] if self.is_router else []),
            *([P.kw("ospf", "OSPF information",
                    P.kw("peer", "Specify a neighbor router",
                         P.kw("brief", "Brief information of neighbor",
                              handler=lambda s, a, n: s.display_ospf_peer(True)),
                         handler=lambda s, a, n: s.display_ospf_peer(False)),
                    P.kw("interface", "Interface information",
                         handler=lambda s, a, n: s.display_ospf_interface()),
                    P.kw("lsdb", "Link state database",
                         handler=lambda s, a, n: s.display_ospf_lsdb()),
                    P.kw("routing", "OSPF routing table",
                         handler=lambda s, a, n: s.display_ospf_routing()),
                    handler=lambda s, a, n: s.display_ospf_brief())]),
            P.kw("clock", "Clock status and configuration information",
                 handler=lambda s, a, n: s.display_clock()),
        ]
        if not self.is_router:
            display_items.append(P.kw("mac-address", "MAC address table",
                                      handler=lambda s, a, n: s.display_mac()))
            display_items.append(P.kw("vlan", "VLAN information",
                                      P.arg(P.NumArg(1, 4094, "INTEGER<1-4094>"), "VLAN ID",
                                            handler=lambda s, a, n: s.display_vlan(a[0])),
                                      handler=lambda s, a, n: s.display_vlan(None)))
            display_items.append(P.kw("stp", "Spanning tree",
                                      P.kw("brief", "Brief information", handler=lambda s, a, n: s.display_stp(True)),
                                      handler=lambda s, a, n: s.display_stp(False)))
            display_items.append(P.kw("port", "Port", P.kw("vlan", "VLAN information of port",
                                                          handler=lambda s, a, n: s.display_port_vlan())))
        if True:
            next(n for n in display_items if n.word == "ip").children.append(
                P.kw("pool", "IP address pool",
                     P.kw("name", "Pool name",
                          P.arg(P.WordArg("STRING<1-64>"), "Pool name",
                                P.kw("used", "Used addresses", handler=lambda s, a, n: s.display_pool(a[0], True)),
                                handler=lambda s, a, n: s.display_pool(a[0], False))),
                     handler=lambda s, a, n: s.display_pool(None, False)))
        display_items.sort(key=lambda x: x.word)
        display = P.kw("display", "Display information", *display_items)
        ping = P.kw("ping", "Ping function",
                    P.kw("ipv6", "IPv6 ping", P.arg(P.Ipv6Arg(), "IPv6 address", handler=lambda s, a, n: s.ping(a[0]))),
                    P.kw("-c", "Specify the number of echo requests",
                         P.arg(P.NumArg(1, 4294967295, "INTEGER<1-4294967295>"), "",
                               P.arg(P.IpArg(), "IP address of remote system",
                                     handler=lambda s, a, n: s.ping(a[1], a[0])))),
                    P.arg(P.IpArg(), "IP address of remote system",
                          handler=lambda s, a, n: s.ping(a[0])))
        tracert = P.kw("tracert", "Tracert function",
                       P.kw("ipv6", "IPv6 tracert", P.arg(P.Ipv6Arg(), "IPv6 address of the remote system",
                                                          handler=lambda s, a, n: s.tracert(a[0]))),
                       P.arg(P.IpArg(), "IP address of the remote system",
                             handler=lambda s, a, n: s.tracert(a[0])))
        quit_ = P.kw("quit", "Exit from current mode and enter prior mode",
                     handler=lambda s, a, n: s.quit())
        return_ = P.kw("return", "Enter the privileged mode", handler=lambda s, a, n: s.ctrl_z() or None)
        save = P.kw("save", "Save current configuration", handler=lambda s, a, n: s.save())
        reset = P.kw("reset", "Reset operation",
                     *([P.kw("arp", "Reset ARP", P.kw("all", "All ARP entries",
                                                         handler=lambda s, a, n: s.reset_arp()),
                             handler=lambda s, a, n: s.reset_arp())] + ([] if self.is_router else [
                         P.kw("mac-address", "MAC address table",
                              handler=lambda s, a, n: s.device.clear_mac_table())])))
        user = [display, ping, quit_, reset, return_, save,
                P.kw("system-view", "Enter the system view", handler=lambda s, a, n: s.system_view()),
                tracert]
        interface = P.kw("interface", "Specify the interface configuration view",
                         P.arg(P.IfaceArg(), "Interface name",
                               handler=lambda s, a, n: s.enter_iface(a[0], n), negatable=True),
                         *([P.arg(P.SubIfaceArg(), "Sub-interface number",
                                  handler=lambda s, a, n: s.enter_subif(a[0], n), negatable=True),
                            P.arg(P.LoopbackArg(), "LoopBack interface",
                                  handler=lambda s, a, n: s.enter_loopback(a[0], n), negatable=True)]
                           if self.is_router else [
                               P.arg(P.VlanIfArg("Vlanif <1-4094>", word="vlanif", min_prefix=5), "VLAN interface",
                                     handler=lambda s, a, n: s.enter_vlanif(a[0], n), negatable=True),
                               P.arg(P.LoopbackArg(), "LoopBack interface",
                                     handler=lambda s, a, n: s.enter_loopback(a[0], n), negatable=True)]),
                         negatable=True)
        system = [display, interface, ping, quit_, return_,
                  P.kw("sysname", "Set the host name",
                       P.arg(P.WordArg("TEXT"), "Host name (1 to 246 characters)",
                             handler=lambda s, a, n: s.sysname(a[0], n)),
                       handler=lambda s, a, n: s.sysname(None, n) if n else s.err_incomplete(s.prompt, 0),
                       negatable=True),
                  tracert]
        if not self.is_router:
            system.append(P.kw("stp", "Spanning tree protocol",
                               P.kw("enable", "Enable STP", handler=lambda s, a, n: s.stp_set("enabled", not n), negatable=True),
                               P.kw("disable", "Disable STP", handler=lambda s, a, n: s.stp_set("enabled", False)),
                               P.kw("mode", "Spanning tree mode",
                                    *[P.kw(m, f"{m.upper()} mode", handler=lambda s, a, n, m=m: s.stp_set("mode", m))
                                      for m in ("stp", "rstp", "mstp")]),
                               P.kw("priority", "Bridge priority",
                                    P.arg(P.NumArg(0, 61440, "INTEGER<0-61440>"), "Priority in steps of 4096",
                                          handler=lambda s, a, n: s.stp_set("priority", 32768 if n else a[0]), negatable=True),
                                    negatable=True),
                               P.kw("root", "Root bridge",
                                    P.kw("primary", "Primary root", handler=lambda s, a, n: s.stp_set("priority", 32768 if n else 0),
                                         negatable=True),
                                    P.kw("secondary", "Secondary root",
                                         handler=lambda s, a, n: s.stp_set("priority", 32768 if n else 4096), negatable=True),
                                    negatable=True),
                               negatable=True))
            system.append(P.kw("vlan", "Virtual LAN",
                               P.kw("batch", "Batch process",
                                    P.arg(P.LineArg("INTEGER<1-4094>"), "VLAN IDs, e.g. 10 20 30 to 40",
                                          handler=lambda s, a, n: s.vlan_batch(a[0], n), negatable=True),
                                    negatable=True),
                               P.arg(P.NumArg(1, 4094, "INTEGER<1-4094>"), "VLAN ID",
                                     handler=lambda s, a, n: s.vlan_view(a[0], n), negatable=True),
                               negatable=True))
        if True:
            ospf_rid = P.kw("router-id", "Router identifier",
                            P.arg(P.IpArg(), "Router identifier, in IP address format",
                                  handler=lambda s, a, n: s.ospf_enter(a[0], a[1], n)))
            system.append(P.kw("acl", "Specify ACL configuration information",
                               P.kw("number", "Specify a numbered ACL",
                                    P.arg(P.NumArg(2000, 3999, "INTEGER<2000-3999>"), "ACL number",
                                          handler=lambda s, a, n: s.acl_view(a[0], n), negatable=True),
                                    negatable=True),
                               P.arg(P.NumArg(2000, 3999, "INTEGER<2000-3999>"), "ACL number",
                                     handler=lambda s, a, n: s.acl_view(a[0], n), negatable=True),
                               negatable=True))
            system.append(P.kw("nat", "NAT",
                               P.kw("address-group", "IP address-group of NAT",
                                    P.arg(P.NumArg(0, 4095, "INTEGER<0-4095>"), "Index of address-group",
                                          P.arg(P.IpArg(), "Start address",
                                                P.arg(P.IpArg(), "End address",
                                                      handler=lambda s, a, n: s.nat_group(a[0], a[1], a[2], n),
                                                      negatable=True),
                                                negatable=True),
                                          handler=lambda s, a, n: s.nat_group(a[0], None, None, n) if n else s.err_incomplete(s.prompt, 0),
                                          negatable=True),
                                    negatable=True),
                               negatable=True))
            ip_rs6 = P.kw("ipv6", "IPv6",
                          P.kw("route-static", "Static route",
                               P.arg(P.Ipv6Arg(), "Destination",
                                     P.arg(P.NumArg(0, 128, "INTEGER<0-128>"), "Prefix length",
                                           P.arg(P.Ipv6Arg(), "Next hop",
                                                 handler=lambda s, a, n: v6_route_cmd(s.device, f"{a[0]}/{a[1]}", a[2], None, n),
                                                 negatable=True),
                                           negatable=True),
                                     negatable=True),
                               negatable=True),
                          handler=lambda s, a, n: s.v6_global(not n), negatable=True)
            system.append(ip_rs6)
            system.append(P.kw("dhcp", "DHCP",
                               P.kw("enable", "Enable DHCP", handler=lambda s, a, n: s.dhcp_enable(not n),
                                    negatable=True),
                               negatable=True))
            system.append(P.kw("rip", "RIP",
                               P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                     handler=lambda s, a, n: s.rip_enter(n), negatable=True),
                               handler=lambda s, a, n: s.rip_enter(n), negatable=True))
            system.append(P.kw("bgp", "Border Gateway Protocol (BGP)",
                               P.arg(P.NumArg(1, 4294967295, "INTEGER<1-4294967295>"), "AS number",
                                     handler=lambda s, a, n: s.bgp_enter(a[0], n), negatable=True),
                               negatable=True))
            system.append(P.kw("ospf", "OSPF protocol",
                               P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                     ospf_rid,
                                     handler=lambda s, a, n: s.ospf_enter(a[0], None, n), negatable=True),
                               P.kw("router-id", "Router identifier",
                                    P.arg(P.IpArg(), "Router identifier, in IP address format",
                                          handler=lambda s, a, n: s.ospf_enter(1, a[0], n))),
                               handler=lambda s, a, n: s.ospf_enter(1, None, n), negatable=True))
        if True:
            def route_handler(with_iface, with_nh, with_pref):
                def fn(s, a, n):
                    i = 2
                    port = a[i] if with_iface else None
                    i += 1 if with_iface else 0
                    nh = a[i] if with_nh else None
                    i += 1 if with_nh else 0
                    pref = a[i] if with_pref else HUAWEI_STATIC_PREF
                    return s.route_static(a[0], a[1], nh, port, pref, n)
                return fn

            pref = lambda wi, wn: P.kw("preference", "Preference value",
                                       P.arg(P.NumArg(1, 255), "", handler=route_handler(wi, wn, True),
                                             negatable=True))
            nh_node = lambda wi: P.arg(P.IpArg(), "Next hop IP address", pref(wi, True),
                                       handler=route_handler(wi, True, False), negatable=True)
            system.append(P.kw("ip", "Specify IP configurations for the system",
                               P.kw("route-static", "Establish a static route",
                                    P.arg(P.IpArg(), "Destination IP address",
                                          P.arg(P.MaskOrLenArg(), "IP address mask or mask length",
                                                nh_node(False),
                                                P.kw("NULL0", "NULL interface (discard)",
                                                     handler=lambda s, a, n: s.route_null(a[0], a[1], n), negatable=True),
                                                P.arg(P.IfaceArg(), "Outbound interface", nh_node(True),
                                                      pref(True, False),
                                                      handler=route_handler(True, False, False),
                                                      negatable=True),
                                                handler=lambda s, a, n: s.route_static(a[0], a[1], None, None, 0, n) if n else s.err_incomplete(s.prompt, 0),
                                                negatable=True)))))
        if True:
            pool_kw = P.kw("pool", "IP address pool",
                           P.arg(P.WordArg("STRING<1-64>"), "Pool name",
                                 handler=lambda s, a, n: s.ip_pool(a[0], n), negatable=True),
                           negatable=True)
            ip_kw = next(n for n in system if n.word == "ip")
            ip_kw.children.append(pool_kw)
            ip_kw.negatable = True
        system.sort(key=lambda x: x.word)
        iface_view = [display, interface, ping, quit_, return_,
                      P.kw("description", "Describe the interface",
                           P.arg(P.LineArg(), "Up to 242 characters",
                                 handler=lambda s, a, n: s.if_description(a[0], n)),
                           handler=lambda s, a, n: s.if_description("", n) if n else s.err_incomplete(s.prompt, 0),
                           negatable=True),
                      P.kw("urpf", "Unicast reverse path forwarding (anti-spoofing)",
                           P.kw("strict", "Strict check: the source must be reachable via this interface",
                                P.arg(P.LineArg(), "allow-default-route", handler=lambda s, a, n: s.if_urpf("strict", n)),
                                handler=lambda s, a, n: s.if_urpf("strict", n), negatable=True),
                           P.kw("loose", "Loose check: the source must be in the routing table",
                                handler=lambda s, a, n: s.if_urpf("loose", n), negatable=True),
                           handler=lambda s, a, n: s.if_urpf(None, True) if n else s.err_incomplete(s.prompt, 0),
                           negatable=True),
                      P.kw("shutdown", "Shutdown the interface",
                           handler=lambda s, a, n: s.if_shutdown(not n), negatable=True)]
        if True:
            iface_view.append(P.kw("ip", "Specify IP configurations",
                                   P.kw("address", "Configure the IP address of an interface",
                                        P.arg(P.IpArg(), "IP address",
                                              P.arg(P.MaskOrLenArg(), "IP mask or mask length",
                                                    P.kw("sub", "Indicate a subordinate address",
                                                         handler=lambda s, a, n: s.if_ip2(a[0], a[1], n),
                                                         negatable=True),
                                                    handler=lambda s, a, n: s.if_ip(a[0], a[1], n),
                                                    negatable=True)),
                                        handler=lambda s, a, n: s.if_ip(None, None, n) if n else s.err_incomplete(s.prompt, 0),
                                        negatable=True)))
        if not self.is_router:
            iface_view.append(P.kw("stp", "Spanning tree protocol",
                                   P.kw("edged-port", "Edge port",
                                        P.kw("enable", "Enable", handler=lambda s, a, n: s.stp_edge(n), negatable=True),
                                        negatable=True),
                                   P.kw("cost", "Port path cost",
                                        P.arg(P.NumArg(1, 200000000, "INTEGER<1-200000000>"), "Cost",
                                              handler=lambda s, a, n: s.stp_cost(a[0], n), negatable=True),
                                        negatable=True),
                                   negatable=True))
            vid = lambda: P.NumArg(1, 4094, "INTEGER<1-4094>")
            iface_view.append(P.kw(
                "port", "Port",
                P.kw("link-type", "Link type",
                     P.kw("access", "Access port", handler=lambda s, a, n: s.link_type("access", n), negatable=True),
                     P.kw("trunk", "Trunk port", handler=lambda s, a, n: s.link_type("trunk", n), negatable=True),
                     handler=lambda s, a, n: s.link_type("access", n) if n else s.err_incomplete(s.prompt, 0),
                     negatable=True),
                P.kw("default", "Set the default VLAN of the port",
                     P.kw("vlan", "Virtual LAN",
                          P.arg(vid(), "VLAN ID", handler=lambda s, a, n: s.default_vlan(a[0], n), negatable=True),
                          handler=lambda s, a, n: s.default_vlan(1, n) if n else s.err_incomplete(s.prompt, 0),
                          negatable=True),
                     negatable=True),
                P.kw("trunk", "Trunk port",
                     P.kw("allow-pass", "Allowed VLANs",
                          P.kw("vlan", "Virtual LAN",
                               P.kw("all", "All", handler=lambda s, a, n: s.allow_pass(None, n), negatable=True),
                               P.arg(P.LineArg("INTEGER<1-4094>"), "VLAN IDs, e.g. 10 20 30 to 40",
                                     handler=lambda s, a, n: s.allow_pass(a[0], n), negatable=True),
                               negatable=True),
                          negatable=True),
                     P.kw("pvid", "Specify the PVID of the trunk port",
                          P.kw("vlan", "Virtual LAN",
                               P.arg(vid(), "VLAN ID", handler=lambda s, a, n: s.trunk_pvid(a[0], n), negatable=True),
                               handler=lambda s, a, n: s.trunk_pvid(1, n) if n else s.err_incomplete(s.prompt, 0),
                               negatable=True),
                          negatable=True),
                     negatable=True),
                negatable=True))
        if True:
            num = lambda lo, hi: P.NumArg(lo, hi, f"INTEGER<{lo}-{hi}>")
            iface_view.append(P.kw(
                "ipv6", "IPv6",
                P.kw("enable", "Enable IPv6 on the interface",
                     handler=lambda s, a, n: setattr(s.iface, "ipv6_enabled", not n), negatable=True),
                P.kw("address", "Configure an IPv6 address",
                     P.arg(P.Ipv6PrefixArg(), "IPv6 address/prefix length",
                           handler=lambda s, a, n: s.v6_addr(a[0], n), negatable=True),
                     P.arg(P.Ipv6Arg(), "IPv6 address",
                           P.arg(P.NumArg(1, 128, "INTEGER<1-128>"), "Prefix length",
                                 handler=lambda s, a, n: s.v6_addr(f"{a[0]}/{a[1]}", n), negatable=True),
                           negatable=True),
                     P.kw("auto", "Automatic", P.kw("link-local", "Link-local",
                                                    handler=lambda s, a, n: setattr(s.iface, "ipv6_enabled", True))),
                     negatable=True),
                P.kw("nd", "Neighbor discovery",
                     P.kw("ra", "Router advertisement",
                          P.kw("halt", "Stop sending RAs", handler=lambda s, a, n: s.v6_ra(not n), negatable=True),
                          negatable=True), negatable=True),
                negatable=True))
            iface_view.append(P.kw(
                "dhcp", "DHCP",
                P.kw("select", "Select an address allocation mode",
                     P.kw("global", "Allocate addresses from a global pool",
                          handler=lambda s, a, n: s.dhcp_select("global", n), negatable=True),
                     P.kw("relay", "Act as a DHCP relay", handler=lambda s, a, n: s.dhcp_select("relay", n),
                          negatable=True),
                     negatable=True),
                P.kw("relay", "DHCP relay",
                     P.kw("server-ip", "DHCP server address",
                          P.arg(P.IpArg(), "Server IP address",
                                handler=lambda s, a, n: s.dhcp_relay_server(a[0], n), negatable=True),
                          negatable=True),
                     negatable=True),
                negatable=True))
            iface_view.append(P.kw(
                "traffic-filter", "Specify traffic filtering",
                *[P.kw(d, f"Filter {d} packets",
                       P.kw("acl", "Specify ACL",
                            P.arg(P.NumArg(2000, 3999, "INTEGER<2000-3999>"), "ACL number",
                                  handler=lambda s, a, n, d=d: s.traffic_filter(d, a[0], n), negatable=True),
                            negatable=True),
                       negatable=True) for d in ("inbound", "outbound")],
                negatable=True))
            iface_view.append(P.kw(
                "nat", "NAT",
                P.kw("outbound", "Outbound NAT",
                     P.arg(P.NumArg(2000, 3999, "INTEGER<2000-3999>"), "ACL number",
                           P.kw("address-group", "IP address-group of NAT",
                                P.arg(P.NumArg(0, 4095, "INTEGER<0-4095>"), "Index of address-group",
                                      P.kw("no-pat", "Not use PAT",
                                           handler=lambda s, a, n: s.nat_outbound(a[0], a[1], False, n), negatable=True),
                                      handler=lambda s, a, n: s.nat_outbound(a[0], a[1], True, n), negatable=True),
                                negatable=True),
                           handler=lambda s, a, n: s.nat_outbound(a[0], None, True, n), negatable=True),
                     negatable=True),
                P.kw("static", "Static NAT",
                     P.kw("global", "Global address",
                          P.arg(P.IpArg(), "Global IP address",
                                P.kw("inside", "Inside address",
                                     P.arg(P.IpArg(), "Inside IP address",
                                           handler=lambda s, a, n: s.nat_static(a[1], a[0], n), negatable=True),
                                     negatable=True),
                                negatable=True),
                          negatable=True),
                     negatable=True),
                P.kw("server", "NAT server (port forwarding)",
                     P.kw("protocol", "Protocol",
                          *[P.kw(proto, proto.upper(),
                                 P.kw("global", "Global address",
                                      P.arg(P.IpArg(), "Global IP address",
                                            P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Global port",
                                                  P.kw("inside", "Inside address",
                                                       P.arg(P.IpArg(), "Inside IP address",
                                                             P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Inside port",
                                                                   handler=lambda s, a, n, proto=proto: s.nat_server(proto, a[0], a[1], a[2], a[3], n),
                                                                   negatable=True),
                                                             negatable=True),
                                                       negatable=True),
                                                  negatable=True),
                                            negatable=True),
                                      negatable=True),
                                 negatable=True) for proto in ("tcp", "udp")],
                          negatable=True),
                     negatable=True),
                negatable=True))
            iface_view.append(P.kw(
                "ospf", "OSPF interface commands",
                P.kw("cost", "Interface cost",
                     P.arg(num(1, 65535), "Cost value", handler=lambda s, a, n: s.if_ospf("costs", a[0], n),
                           negatable=True),
                     handler=lambda s, a, n: s.if_ospf("costs", None, n) if n else s.err_incomplete(s.prompt, 0),
                     negatable=True),
                P.kw("timer", "Specify timer interval",
                     P.kw("hello", "Specify interval at which hello packet sends",
                          P.arg(num(1, 65535), "Second(s)", handler=lambda s, a, n: s.if_ospf("hello", a[0], n),
                                negatable=True),
                          handler=lambda s, a, n: s.if_ospf("hello", None, n) if n else s.err_incomplete(s.prompt, 0),
                          negatable=True),
                     P.kw("dead", "Specify interval after which a neighbor is declared dead",
                          P.arg(num(1, 235926000), "Second(s)", handler=lambda s, a, n: s.if_ospf("dead", a[0], n),
                                negatable=True),
                          handler=lambda s, a, n: s.if_ospf("dead", None, n) if n else s.err_incomplete(s.prompt, 0),
                          negatable=True),
                     negatable=True),
                P.kw("dr-priority", "Router priority",
                     P.arg(num(0, 255), "Router priority value", handler=lambda s, a, n: s.if_ospf("priority", a[0], n),
                           negatable=True),
                     handler=lambda s, a, n: s.if_ospf("priority", None, n) if n else s.err_incomplete(s.prompt, 0),
                     negatable=True),
                negatable=True))
            iface_view += [
                P.kw("dot1q", "802.1Q",
                     P.kw("termination", "Termination",
                          P.kw("vid", "VLAN ID",
                               P.arg(P.NumArg(1, 4094, "INTEGER<1-4094>"), "VLAN ID",
                                     handler=lambda s, a, n: s.dot1q_vid(a[0], n), negatable=True),
                               handler=lambda s, a, n: s.dot1q_vid(None, n) if n else s.err_incomplete(s.prompt, 0),
                               negatable=True),
                          negatable=True),
                     negatable=True),
                P.kw("arp", "ARP",
                     P.kw("broadcast", "Broadcast",
                          P.kw("enable", "Enable ARP broadcast on the termination sub-interface",
                               handler=lambda s, a, n: s.arp_broadcast(not n), negatable=True),
                          negatable=True),
                     negatable=True)]
        iface_view.sort(key=lambda x: x.word)
        vlan_view = [display, interface, quit_, return_,
                     P.kw("name", "Set the VLAN name",
                          P.arg(P.WordArg("TEXT"), "VLAN name", handler=lambda s, a, n: s.vlan_name(a[0], n)),
                          handler=lambda s, a, n: s.vlan_name(None, n) if n else s.err_incomplete(s.prompt, 0),
                          negatable=True),
                     P.kw("description", "Set the VLAN description",
                          P.arg(P.LineArg(), "VLAN description", handler=lambda s, a, n: s.vlan_name(a[0], n)),
                          negatable=True)]
        vlan_view.sort(key=lambda x: x.word)
        ospf_view = [display, interface, quit_, return_]
        area_view = [display, interface, quit_, return_]
        if True:
            ospf_view += [
                P.kw("area", "Specify the OSPF area",
                     P.arg(P.WordArg("IPADDR<X.X.X.X>|INTEGER<0-4294967295>"), "Area ID",
                           handler=lambda s, a, n: s.ospf_area(a[0], n), negatable=True),
                     negatable=True),
                P.kw("silent-interface", "Suppress sending and receiving OSPF packets on an interface",
                     P.kw("all", "All interfaces", handler=lambda s, a, n: s.ospf_silent(None, n), negatable=True),
                     P.arg(P.IfaceArg(), "Interface name", handler=lambda s, a, n: s.ospf_silent(a[0], n),
                           negatable=True),
                     negatable=True),
                P.kw("default-route-advertise", "Advertise default route",
                     P.kw("always", "Always advertise default route",
                          handler=lambda s, a, n: s.ospf_default("always", n), negatable=True),
                     handler=lambda s, a, n: s.ospf_default("yes", n), negatable=True),
                P.kw("import-route", "Import routes from other protocols",
                     P.kw("static", "Static routes", handler=lambda s, a, n: s.ospf_import("static", n),
                          negatable=True),
                     P.kw("direct", "Direct routes", handler=lambda s, a, n: s.ospf_import("connected", n),
                          negatable=True),
                     negatable=True),
                P.kw("bandwidth-reference", "Specify reference bandwidth",
                     P.arg(P.NumArg(1, 2147483648, "INTEGER<1-2147483648>"), "Reference bandwidth (Mbit/s)",
                           handler=lambda s, a, n: s.ospf_refbw(a[0], n), negatable=True),
                     negatable=True),
            ]
            area_view.append(P.kw("network", "Enable OSPF on an interface",
                                  P.arg(P.IpArg(), "IP address",
                                        P.arg(P.WildcardArg(), "Wildcard mask",
                                              handler=lambda s, a, n: s.ospf_network(a[0], a[1], n),
                                              negatable=True),
                                        negatable=True),
                                  negatable=True))
        acl_view = [display, interface, quit_, return_,
                    P.kw("rule", "Specify an ACL rule",
                         P.arg(P.NumArg(0, 4294967294, "INTEGER<0-4294967294>"), "ID of ACL rule",
                               *[P.kw(act, f"Specify matched packets to {act}",
                                      P.arg(P.LineArg(), "Rule conditions",
                                            handler=lambda s, a, n, act=act: s.acl_rule(a[0], act, a[1])),
                                      handler=lambda s, a, n, act=act: s.acl_rule(a[0], act, ""))
                                 for act in ("permit", "deny")],
                               handler=lambda s, a, n: s.acl_undo_rule(a[0]) if n else s.err_incomplete(s.prompt, 0),
                               negatable=True),
                         *[P.kw(act, f"Specify matched packets to {act}",
                                P.arg(P.LineArg(), "Rule conditions",
                                      handler=lambda s, a, n, act=act: s.acl_rule(None, act, a[0])),
                                handler=lambda s, a, n, act=act: s.acl_rule(None, act, ""))
                           for act in ("permit", "deny")],
                         negatable=True)]
        acl_view.sort(key=lambda x: x.word)
        bgp_view = [display, interface, quit_, return_]
        if True:
            flag = lambda word, h, attr: P.kw(word, h, handler=lambda s, a, n: s.bgp_peer(a[0], attr, not n),
                                              negatable=True)
            peer = P.arg(P.IpArg(), "Peer IP address",
                         P.kw("as-number", "Specify the AS number of the peer",
                              P.arg(P.NumArg(1, 4294967295, "INTEGER<1-4294967295>"), "AS number",
                                    handler=lambda s, a, n: s.bgp_peer_as(a[0], a[1], n), negatable=True),
                              negatable=True),
                         P.kw("connect-interface", "Set interface name to be used as session's output interface",
                              P.arg(P.IfaceArg(), "Interface",
                                    handler=lambda s, a, n: s.bgp_peer(a[0], "update_source", None if n else a[1].name),
                                    negatable=True),
                              negatable=True),
                         P.kw("ebgp-max-hop", "Allow EBGP neighbors not on directly connected networks",
                              P.arg(P.NumArg(1, 255, "INTEGER<1-255>"), "Maximum hop count",
                                    handler=lambda s, a, n: s.bgp_peer(a[0], "multihop", None if n else a[1]),
                                    negatable=True),
                              handler=lambda s, a, n: s.bgp_peer(a[0], "multihop", None if n else 255),
                              negatable=True),
                         P.kw("description", "Configure the description",
                              P.arg(P.LineArg(), "Description",
                                    handler=lambda s, a, n: s.bgp_peer(a[0], "description", None if n else a[1]),
                                    negatable=True),
                              negatable=True),
                         P.kw("preferred-value", "Specify the preferred value",
                              P.arg(P.NumArg(0, 65535, "INTEGER<0-65535>"), "Preferred value",
                                    handler=lambda s, a, n: s.bgp_peer(a[0], "weight", None if n else a[1]),
                                    negatable=True),
                              negatable=True),
                         flag("next-hop-local", "Specify local address as the next hop of routes", "next_hop_self"),
                         flag("ignore", "Disable session with the peer", "shutdown"),
                         flag("reflect-client", "Configure the peer as a route reflector client", "rr_client"),
                         flag("default-route-advertise", "Advertise the default route", "default_originate"),
                         flag("enable", "Enable the peer in the address family", "_enable"),
                         handler=lambda s, a, n: s.bgp_peer_remove(a[0]) if n else s.err_incomplete(s.prompt, 0),
                         negatable=True)
            bgp_view += [
                P.kw("router-id", "Configure a router ID",
                     P.arg(P.IpArg(), "Router ID in IP address format",
                           handler=lambda s, a, n: s.bgp_rid(None if n else a[0]), negatable=True),
                     negatable=True),
                P.kw("peer", "Specify a peer", peer, negatable=True),
                P.kw("network", "Specify the network to advertise",
                     P.arg(P.IpArg(), "IP address",
                           P.arg(P.MaskOrLenArg(), "Mask or mask length",
                                 handler=lambda s, a, n: s.bgp_network(a[0], a[1], n), negatable=True),
                           handler=lambda s, a, n: s.bgp_network(a[0], None, n), negatable=True),
                     negatable=True),
                P.kw("import-route", "Import routes from other protocols",
                     P.kw("direct", "Direct routes", handler=lambda s, a, n: s.bgp_import("connected", n), negatable=True),
                     P.kw("static", "Static routes", handler=lambda s, a, n: s.bgp_import("static", n), negatable=True),
                     P.kw("ospf", "OSPF routes",
                          P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                handler=lambda s, a, n: s.bgp_import("ospf", n), negatable=True),
                          negatable=True),
                     negatable=True),
                P.kw("ipv4-family", "Specify IPv4 address family",
                     P.kw("unicast", "Unicast address family", handler=lambda s, a, n: s._set("bgp-af")),
                     handler=lambda s, a, n: s._set("bgp-af")),
            ]
        bgp_view.sort(key=lambda x: x.word)
        ospf_view.sort(key=lambda x: x.word)
        area_view.sort(key=lambda x: x.word)
        self._trees = {"user": user, "system": system, "interface": iface_view, "vlan": vlan_view,
                       "ospf": ospf_view, "ospf-area": area_view, "acl": acl_view,
                       "bgp": bgp_view, "bgp-af": bgp_view,
                       "rip": sorted([
                           display, interface, quit_, return_,
                           P.kw("version", "Specify the RIP version",
                                P.arg(P.NumArg(1, 2, "INTEGER<1-2>"), "Version",
                                      handler=lambda s, a, n: s.rip_attr("version", a[0], n), negatable=True),
                                negatable=True),
                           P.kw("network", "Enable RIP on the network",
                                P.arg(P.IpArg(), "Network address", handler=lambda s, a, n: s.rip_network(a[0], n),
                                      negatable=True),
                                negatable=True),
                           P.kw("summary", "Automatic summarization", handler=lambda s, a, n: None, negatable=True),
                           P.kw("silent-interface", "Suppress sending RIP packets on an interface",
                                P.kw("all", "All interfaces", handler=lambda s, a, n: s.rip_attr("passive_default", True, n),
                                     negatable=True),
                                P.arg(P.IfaceArg(), "Interface", handler=lambda s, a, n: s.rip_silent(a[0], n),
                                      negatable=True),
                                negatable=True),
                           P.kw("default-route", "Default route",
                                P.kw("originate", "Originate a default route",
                                     handler=lambda s, a, n: s.rip_attr("default_originate", True, n), negatable=True),
                                negatable=True),
                           P.kw("import-route", "Import routes from other protocols",
                                P.kw("direct", "Direct routes", handler=lambda s, a, n: s.rip_import("connected", n), negatable=True),
                                P.kw("static", "Static routes", handler=lambda s, a, n: s.rip_import("static", n), negatable=True),
                                P.kw("ospf", "OSPF routes",
                                     P.arg(P.NumArg(1, 65535, "INTEGER<1-65535>"), "Process ID",
                                           handler=lambda s, a, n: s.rip_import("ospf", n), negatable=True),
                                     negatable=True),
                                negatable=True),
                       ], key=lambda x: x.word),
                       "ip-pool": sorted([
                           display, interface, quit_, return_,
                           P.kw("gateway-list", "Configure the gateway",
                                P.arg(P.IpArg(), "Gateway address",
                                      handler=lambda s, a, n: s.pool_attr("gateway", a[0], n), negatable=True),
                                negatable=True),
                           P.kw("network", "Configure the network",
                                P.arg(P.IpArg(), "Network address",
                                      P.kw("mask", "Network mask",
                                           P.arg(P.MaskOrLenArg(), "Mask or mask length",
                                                 handler=lambda s, a, n: s.pool_attr("network", (a[0], a[1]), n),
                                                 negatable=True)),
                                      negatable=True),
                                negatable=True),
                           P.kw("dns-list", "Configure DNS servers",
                                P.arg(P.IpArg(), "DNS server address",
                                      handler=lambda s, a, n: s.pool_attr("dns", a[0], n), negatable=True),
                                negatable=True),
                           P.kw("domain-name", "Configure the domain name",
                                P.arg(P.WordArg(), "Domain name",
                                      handler=lambda s, a, n: s.pool_attr("domain", a[0], n), negatable=True),
                                negatable=True),
                           P.kw("excluded-ip-address", "Exclude IP addresses",
                                P.arg(P.IpArg(), "Start address",
                                      P.arg(P.IpArg(), "End address",
                                            handler=lambda s, a, n: s.pool_excluded(a[0], a[1], n), negatable=True),
                                      handler=lambda s, a, n: s.pool_excluded(a[0], a[0], n), negatable=True),
                                negatable=True),
                           P.kw("lease", "Configure the lease",
                                P.kw("day", "Days",
                                     P.arg(P.NumArg(0, 999, "INTEGER<0-999>"), "Days",
                                           handler=lambda s, a, n: s.pool_attr("lease", a[0] * 86400, n),
                                           negatable=True))),
                       ], key=lambda x: x.word)}

    # -- modes ----------------------------------------------------------------------
    def system_view(self):
        self.mode = "system"
        return "Enter system view, return user view with Ctrl+Z."

    def quit(self):
        if self.mode == "bgp-af":
            self.mode = "bgp"
            return None
        if self.mode in ("vlan", "ospf", "acl", "bgp", "ip-pool", "rip", "ui-vty", "aaa", "ospfv3", "ripng",
                         "dhcpv6-pool", "classifier", "behavior", "trafficpolicy", "isis", "ipsec-proposal", "ike-peer", "ipsec-profile"):
            self.mode = "system"
            return None
        if self.mode == "ospf-area":
            self.mode = "ospf"
            return None
        if self.mode == "interface":
            self.mode, self.iface = "system", None
        elif self.mode == "system":
            self.mode = "user"
        else:
            return "  Configuration console exit, please press any key to log on"
        return None

    def enter_iface(self, port, negate=False):
        if negate:
            if port.is_sub:
                self.device.remove_subinterface(port)
                return None
            if port.is_loopback:
                self.device.remove_loopback(port)
                return None
            return "Error: The interface cannot be deleted."
        self.mode, self.iface = "interface", port
        return None

    # -- IPv6 ------------------------------------------------------------------------------
    def v6_global(self, on):
        self.device.ipv6_routing = on
        self.device.ipv6_changed()
        return None

    def v6_addr(self, text, negate):
        if not negate and not self.iface.ipv6_enabled:
            return "Error: Please enable IPv6 on the interface first."
        v6_addr_cmd(self.iface, text, negate)
        self.device.ipv6_changed()
        return None

    def v6_ra(self, halt):
        self.iface.ra = False if halt else True
        self.device.ipv6_changed()
        return None

    def display_v6_brief(self):
        lines = ["*down: administratively down", "(s): spoofing", f"{'Interface':<34}{'Physical':<10}Protocol"]
        for p in self.device.ports.values():
            addrs = self.device.addrs6(p)
            if not addrs:
                continue
            lines.append(f"{p.name:<34}{self._phy(p):<10}{'up' if p.is_up else 'down'}")
            lines.append(f"[IPv6 Address] {next((str(a.ip) for a in p.ipv6), str(addrs[0].ip)).upper()}")
        return "\n".join(lines)

    def display_v6_routes(self):
        tbl = self.device.routing_table6()
        lines = ["Routing Table : Public", f"         Destinations : {len(tbl)}        Routes : {len(tbl)}", ""]
        for r in tbl:
            proto = {"C": "Direct", "L": "Direct", "S": "Static", "O": "OSPFv3", "OI": "OSPFv3", "R": "RIPng"}.get(r.code, r.code)
            lines += [f" Destination  : {r.network.network_address}{'':<20}PrefixLength : {r.network.prefixlen}",
                      f" NextHop      : {r.next_hop or (r.port.ipv6[0].ip if r.code == 'C' and r.port.ipv6 else '::1')}"
                      f"{'':<10}Preference   : {0 if r.code in ('C', 'L') else 60}",
                      f" Interface    : {r.port.name}{'':<10}Protocol     : {proto}", ""]
        return "\n".join(lines)

    def display_v6_neighbors(self):
        lines = []
        for n in self.device.nd_cache.values():
            lines += [f"IPv6 Address : {n.ip}", f"Link-layer   : {n.mac.cisco().replace('.', '-')}     State : REACH",
                      f"Interface    : {n.port.name}", ""]
        return "\n".join(lines) or "Total: 0"

    # -- RIP -------------------------------------------------------------------------------
    def rip_enter(self, negate):
        from ..routing.rip import RipConfig
        if negate:
            self.device.disable_rip()
            return None
        if self.device.rip is None:
            self.device.enable_rip(RipConfig())
        self.mode = "rip"
        return None

    def rip_attr(self, attr, value, negate):
        setattr(self.device.rip.cfg, attr, (2 if attr == "version" else False) if negate else value)
        self.device.rip.trigger()
        return None

    def rip_network(self, ip, negate):
        from .cisco import classful
        net = classful(IPv4Network(f"{ip}/32"))
        if net.network_address != IPv4Address(ip):
            return "Error: The network address is invalid, and the specified address must be major-net address without any subnets."
        lst = self.device.rip.cfg.networks
        if negate:
            if str(net) in lst:
                lst.remove(str(net))
        elif str(net) not in lst:
            lst.append(str(net))
        self.device.rip.trigger()
        return None

    def rip_silent(self, port, negate):
        lst = self.device.rip.cfg.passive
        if negate:
            if port.name in lst:
                lst.remove(port.name)
        elif port.name not in lst:
            lst.append(port.name)
        return None

    def rip_import(self, proto, negate):
        lst = self.device.rip.cfg.redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        self.device.rip.trigger()
        return None

    def display_rip_route(self):
        rip = self.device.rip
        if rip is None:
            return "Info: RIP is not enabled."
        lines = [" Route Flags : R - RIP", "               A - Aging, G - Garbage-collect", " " + "-" * 70]
        by_port: dict = {}
        for rr in rip.table.values():
            by_port.setdefault((rr.next_hop, rr.port.name), []).append(rr)
        for (nh, pname), rrs in by_port.items():
            lines += [f"  Peer {nh}  on {pname}", f"      {'Destination/Mask':<20}{'Nexthop':<16}{'Cost':<6}{'Tag':<6}{'Flags':<7}Sec"]
            for rr in rrs:
                flag = "RA" if rr.metric < 16 else "RG"
                lines.append(f"      {str(rr.network):<20}{str(nh):<16}{rr.metric:<6}{0:<6}{flag:<7}{rip.age(rr)}")
        lines.append(" " + "-" * 70)
        return "\n".join(lines)

    # -- DHCP ------------------------------------------------------------------------------
    def _dhcp(self):
        d = self.device.dhcp()
        if d.cfg.interfaces is None:
            d.cfg.interfaces = []                 # VRP serves only on "dhcp select global" interfaces
            d.cfg.enabled = bool(self.device.extra.get("dhcp_enable"))
        return d

    def dhcp_enable(self, on):
        self.device.extra["dhcp_enable"] = on
        if not on:
            self.device.extra.pop("dhcp_enable")
        self._dhcp().cfg.enabled = on
        return "Info: The operation may take a few seconds. Please wait for a moment.done." if on else None

    def ip_pool(self, name, negate):
        from ..services.dhcp import DhcpPool
        d = self._dhcp()
        if negate:
            d.cfg.pools = [p for p in d.cfg.pools if p.name != name]
            return None
        if d.cfg.pool(name) is None:
            d.cfg.pools.append(DhcpPool(name))
            msg = "Info: It's successful to create an IP address pool."
        else:
            msg = None
        self.pool_ctx = name
        self.mode = "ip-pool"
        return msg

    def pool_attr(self, attr, value, negate):
        pool = self._dhcp().cfg.pool(self.pool_ctx)
        if attr == "network" and value is not None and not negate:
            from ..addresses import make_interface
            net = make_interface(value[0], value[1]).network
            if net.network_address != IPv4Address(value[0]):
                return "Error: The network address and mask do not match."
            value = str(net)
        default = 86400 if attr == "lease" else ("" if attr == "domain" else None)
        setattr(pool, attr, default if negate else (str(value) if attr in ("gateway", "dns") else value))
        return None

    def pool_excluded(self, lo, hi, negate):
        d = self._dhcp()
        entry = (lo, hi)
        if negate:
            d.cfg.excluded = [e for e in d.cfg.excluded if tuple(e) != entry]
        elif entry not in [tuple(e) for e in d.cfg.excluded]:
            d.cfg.excluded.append(entry)
        return None

    def dhcp_select(self, mode, negate):
        if not self.device.extra.get("dhcp_enable"):
            return "Error: Please enable DHCP in the system view first."
        d = self._dhcp()
        name = self.iface.name
        if name in d.cfg.interfaces:
            d.cfg.interfaces.remove(name)
        self.device.extra.setdefault("dhcp_relay_ifs", [])
        relays = self.device.extra["dhcp_relay_ifs"]
        if name in relays:
            relays.remove(name)
        if negate:
            self.iface.helpers.clear()
            return None
        if mode == "global":
            d.cfg.interfaces.append(name)
        else:
            relays.append(name)
        return None

    def dhcp_relay_server(self, ip, negate):
        if self.iface.name not in self.device.extra.get("dhcp_relay_ifs", []):
            return "Error: Please configure dhcp select relay first."
        h = self.iface.helpers
        if negate:
            if ip in h:
                h.remove(ip)
        elif ip not in h:
            h.append(ip)
        return None

    def display_pool(self, name, used):
        srv = self.device.dhcp_server
        pools = srv.cfg.pools if srv else []
        if name is not None:
            pools = [p for p in pools if p.name == name]
            if not pools:
                return "Error: The IP pool does not exist."
        out = []
        for p in pools:
            net = p.net()
            total = net.num_addresses - 2 if net else 0
            n_used = sum(1 for b in srv.bindings.values() if b.pool == p.name)
            out += [f"  Pool-name      : {p.name}", "  Pool-No        : 0", f"  Lease          : {p.lease // 86400} Days 0 Hours 0 Minutes",
                    f"  Domain-name    : {p.domain or '-'}", f"  DNS-server0    : {p.dns or '-'}",
                    f"  Gateway-0      : {p.gateway or '-'}", f"  Network        : {net.network_address if net else '-'}",
                    f"  Mask           : {net.netmask if net else '-'}",
                    "  -----------------------------------------------------------------------------",
                    "         Start           End     Total  Used  Idle(Expired)  Conflict  Disabled",
                    "  -----------------------------------------------------------------------------"]
            if net:
                out.append(f"  {str(net.network_address + 1):>15} {str(net.broadcast_address - 1):>15} {total:>7} {n_used:>5}"
                           f" {total - n_used:>8}(0)         0        {len(srv.cfg.excluded)}")
            if used:
                out += ["", "  Network section :", "  -----------------------------------------------------------------",
                        "  Index              IP               MAC      Lease   Status  "]
                for i, b in enumerate(x for x in srv.bindings.values() if x.pool == p.name):
                    out.append(f"  {i:>5}  {str(b.ip):>15}  {b.mac.cisco().replace('.', '-'):>15}  {int(p.lease):>6}   Used")
            out.append("")
        return "\n".join(out)

    # -- BGP -------------------------------------------------------------------------------
    def _set(self, mode):
        self.mode = mode
        return None

    def bgp_enter(self, asn, negate):
        from ..routing.bgp import BgpConfig
        d = self.device
        if negate:
            if d.bgp is None or d.bgp.cfg.asn != asn:
                return "Error: The specified AS number does not exist."
            d.disable_bgp()
            return None
        if d.bgp is not None and d.bgp.cfg.asn != asn:
            return f"Error: The BGP process has been configured with AS {d.bgp.cfg.asn}."
        if d.bgp is None:
            d.enable_bgp(BgpConfig(asn=asn))
        self.mode = "bgp"
        return None

    def bgp_rid(self, rid):
        self.device.bgp.cfg.router_id = rid
        for ip in list(self.device.bgp.peers):
            self.device.bgp.reset_peer(ip, "Router ID changed")
        return None

    def bgp_peer_as(self, ip, asn, negate):
        nbs = self.device.bgp.cfg.neighbors
        if negate:
            return self.bgp_peer_remove(ip)
        old = nbs.get(str(ip), {}).get("remote_as")
        if old is not None and old != asn:
            return "Error: The peer has been configured with another AS number."
        nbs.setdefault(str(ip), {})["remote_as"] = asn
        return None

    def bgp_peer_remove(self, ip):
        if str(ip) not in self.device.bgp.cfg.neighbors:
            return "Error: The peer does not exist."
        self.device.bgp.reset_peer(ip, "Peer deleted")
        del self.device.bgp.cfg.neighbors[str(ip)]
        return None

    def bgp_peer(self, ip, attr, value):
        nb = self.device.bgp.cfg.neighbors.get(str(ip))
        if nb is None:
            return "Error: The peer does not exist."
        if attr == "_enable":
            return None
        if value is None or value is False:
            nb.pop(attr, None)
        else:
            nb[attr] = value
        if attr in ("update_source", "multihop", "shutdown"):
            self.device.bgp.reset_peer(ip, "Config change")
        return None

    def bgp_network(self, ip, mask, negate):
        from ..addresses import make_interface
        from .cisco import classful
        if mask is None:
            net = classful(IPv4Network(f"{ip}/32"))
        else:
            net = make_interface(ip, mask).network
            if net.network_address != IPv4Address(ip):
                return "Error: The network address and mask do not match."
        lst = self.device.bgp.cfg.networks
        if negate:
            if str(net) not in lst:
                return "Error: The network does not exist."
            lst.remove(str(net))
        elif str(net) not in lst:
            lst.append(str(net))
        return None

    def bgp_import(self, proto, negate):
        lst = self.device.bgp.cfg.redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        return None

    def _bgp_on(self):
        b = self.device.bgp
        return b if b is not None and b.running else None

    def display_bgp_peer(self):
        b = self._bgp_on()
        if b is None:
            return "Info: BGP is not enabled."
        est = sum(1 for p in b.peers.values() if p.state == "Established")
        lines = ["", f" BGP local router ID : {b.router_id}", f" Local AS number : {b.cfg.asn}",
                 f" Total number of peers : {len(b.peers)}\t\t  Peers in established state : {est}", "",
                 f"  {'Peer':<16}{'V':<5}{'AS':>7}  {'MsgRcvd':>7}  {'MsgSent':>7}  {'OutQ':>4}  {'Up/Down':<12}"
                 f"{'State':<12}PrefRcv", ""]
        now = self.device.engine.now
        for ip in sorted(b.peers, key=int):
            p = b.peers[ip]
            nc = b.ncfg(ip)
            if p.state == "Established":
                ud = hms_vrp((now - p.up_since) / 1000)
            else:
                ud = "00:00:00" if not p.flaps else hms_vrp((now - p.last_attempt) / 1000)
            st = {"Idle (Admin)": "Idle", "OpenSent": "OpenSent", "OpenConfirm": "OpenConfirm"}.get(p.state, p.state)
            if st == "Active" or (st == "Idle" and not nc.get("shutdown")):
                st = "Connect" if st == "Active" else "Idle"
            lines.append(f"  {str(ip):<16}{'4':<5}{nc.get('remote_as', 0):>7}  {p.msgs_rx:>7}  {p.msgs_tx:>7}  "
                         f"{0:>4}  {ud:<12}{st:<12}{b.received_count(p)}")
        return "\n".join(lines)

    def display_bgp_table(self):
        b = self._bgp_on()
        if b is None:
            return "Info: BGP is not enabled."
        rows = b.table()
        lines = ["", f" BGP Local router ID is {b.router_id}",
                 " Status codes: * - valid, > - best, d - damped, x - best external, a - add path,",
                 "               h - history,  i - internal, s - suppressed, S - Stale",
                 "               Origin : i - IGP, e - EGP, ? - incomplete", "", "",
                 f" Total Number of Routes: {len(rows)}",
                 f"      {'Network':<19}{'NextHop':<16}{'MED':<11}{'LocPrf':<10}{'PrefVal':<8}Path/Ogn", ""]
        for bp in rows:
            a = bp.attrs
            st = "*" + (">" if bp.best else " ") + ("i" if bp.peer is not None and not bp.ebgp else " ")
            pv = bp.weight if bp.peer is not None else 0
            med = "" if a.med is None else str(a.med)
            lp = "" if a.local_pref is None else str(a.local_pref)
            path = (a.text_path() if a.as_path else "") + a.origin
            lines.append(f" {st}  {str(bp.prefix.network_address):<19}{str(a.next_hop):<16}{med:<11}{lp:<10}"
                         f"{pv:<8}{path}")
        return "\n".join(lines)

    # -- ACL -------------------------------------------------------------------------------
    def acl_view(self, num, negate):
        from ..security.acl import Acl
        name = str(num)
        if negate:
            self.device.acls.pop(name, None)
            return None
        kind = "basic" if num < 3000 else "advanced"
        self.device.acls.setdefault(name, Acl(name, kind, default="permit"))
        self.acl_ctx = name
        self.mode = "acl"
        return None

    def acl_rule(self, rid, action, text):
        acl = self.device.acls[self.acl_ctx]
        try:
            rule = parse_vrp_rule(action, text, acl.kind == "advanced")
        except ValueError:
            return "Error: Wrong parameter found at '^' position."
        if rid is not None:
            rule.seq = rid
        acl.add(rule, step=5)
        return None

    def acl_undo_rule(self, rid):
        acl = self.device.acls[self.acl_ctx]
        if not any(r.seq == rid for r in acl.rules):
            return "Error: The ACL rule does not exist."
        acl.rules = [r for r in acl.rules if r.seq != rid]
        return None

    def traffic_filter(self, direction, num, negate):
        attr = "acl_in" if direction == "inbound" else "acl_out"
        if not negate and str(num) not in self.device.acls:
            from ..security.acl import Acl
            self.device.acls[str(num)] = Acl(str(num), "basic" if num < 3000 else "advanced", default="permit")
        setattr(self.iface, attr, None if negate else str(num))
        return None

    def display_acl(self, name):
        out = []
        acls = self.device.acls
        if name is None:
            out.append(f"Total quantity of nonempty ACL number is {sum(1 for a in acls.values() if a.rules)}")
        for key, acl in acls.items():
            if name is not None and key != name:
                continue
            kind = "Basic" if acl.kind == "basic" else "Advanced"
            out += ["", f"{kind} ACL {key}, {len(acl.rules)} rule" + ("s" if len(acl.rules) != 1 else ""),
                    "Acl's step is 5"]
            for r in acl.rules:
                hits = f" ({r.hits} matches)" if r.hits else ""
                out.append(f" rule {r.seq} {vrp_rule_text(r, acl.kind == 'advanced')}{hits}")
        return "\n".join(out)

    # -- NAT -------------------------------------------------------------------------------
    def nat_group(self, idx, start, end, negate):
        cfg = self.device.nat_engine().cfg
        key = str(idx)
        if negate:
            if any(r.text == f"address-group {idx}" for r in cfg.rules):
                return "Error: The address group is in use."
            cfg.pools.pop(key, None)
            return None
        if int(IPv4Address(end)) < int(IPv4Address(start)):
            return "Error: The end address must be greater than the start address."
        cfg.pools[key] = (start, end, 32)
        return None

    def nat_outbound(self, acl, group, pat, negate):
        from ..security.nat import NatRule
        cfg = self.device.nat_engine().cfg
        port = self.iface.name
        cfg.rules = [r for r in cfg.rules if not (r.kind == "dynamic" and r.out_iface == port and r.acl == str(acl))]
        if negate:
            return None
        if group is not None and str(group) not in cfg.pools:
            return "Error: The address group does not exist."
        if str(acl) not in self.device.acls:
            from ..security.acl import Acl
            self.device.acls[str(acl)] = Acl(str(acl), "basic" if acl < 3000 else "advanced", default="permit")
        rule = NatRule("dynamic", acl=str(acl), out_iface=port,
                       interface=port if group is None else None,
                       pool=tuple(cfg.pools[str(group)][:2]) if group is not None else None,
                       overload=pat, text=f"address-group {group}" if group is not None else "")
        cfg.rules.append(rule)
        return None

    def nat_static(self, inside, glob, negate):
        from ..security.nat import NatRule
        cfg = self.device.nat_engine().cfg
        same = [r for r in cfg.rules if r.kind == "static" and r.local == inside and r.glob == glob]
        if negate:
            for r in same:
                cfg.rules.remove(r)
            return None
        if not same:
            cfg.rules.append(NatRule("static", local=inside, glob=glob, out_iface=self.iface.name))
        return None

    def nat_server(self, proto, glob, gport, inside, lport, negate):
        from ..security.nat import NatRule
        cfg = self.device.nat_engine().cfg
        same = [r for r in cfg.rules if r.kind == "dnat" and r.glob == glob and r.global_port == gport
                and r.proto == proto]
        for r in same:
            cfg.rules.remove(r)
        if not negate:
            cfg.rules.append(NatRule("dnat", local=inside, glob=glob, proto=proto, local_port=lport,
                                     global_port=gport, in_iface=self.iface.name))
        return None

    def display_nat(self, what):
        nat = self.device.nat
        if nat is None:
            return ""
        cfg = nat.cfg
        if what == "outbound":
            lines = [" NAT Outbound Information:", " " + "-" * 70,
                     f" {'Interface':<26}{'Acl':<8}{'Address-group/IP/Interface':<29}Type", " " + "-" * 70]
            for r in cfg.rules:
                if r.kind == "dynamic":
                    target = r.interface or r.text.split()[-1]
                    typ = "easyip" if r.interface else ("pat" if r.overload else "no-pat")
                    lines.append(f" {r.out_iface:<26}{r.acl:<8}{target:<29}{typ}")
            lines += [" " + "-" * 70, f"  Total : {sum(1 for r in cfg.rules if r.kind == 'dynamic')}"]
            return "\n".join(lines)
        if what == "static":
            lines = ["  Static Nat Information:"]
            for r in cfg.rules:
                if r.kind == "static":
                    lines += [f"  Interface  : {r.out_iface}", f"    Global IP/Port     : {r.glob}/----",
                              f"    Inside IP/Port     : {r.local}/----", "    Protocol : ----", "    VPN instance-name  : ----",
                              "    Acl number         : ----", "    Netmask  : 255.255.255.255", "    Description : ----", ""]
            return "\n".join(lines)
        if what == "server":
            lines = ["  Nat Server Information:"]
            for r in cfg.rules:
                if r.kind == "dnat":
                    lines += [f"  Interface  : {r.in_iface}", f"    Global IP/Port     : {r.glob}/{r.global_port}",
                              f"    Inside IP/Port     : {r.local}/{r.local_port}",
                              f"    Protocol : {6 if r.proto == 'tcp' else 17}({r.proto})", ""]
            return "\n".join(lines)
        if what == "group":
            lines = [" NAT Address-Group Information:", " " + "-" * 50,
                     f" {'Index':<9}{'Start-address':<17}End-address", " " + "-" * 50]
            for k, (a, b, _) in cfg.pools.items():
                lines.append(f" {k:<9}{a:<17}{b}")
            return "\n".join(lines)
        nat._expire()
        lines = ["  NAT Session Table Information:", ""]
        names = {1: "ICMP", 6: "TCP", 17: "UDP"}
        for t in nat.table:
            lines += [f"     Protocol          : {names.get(t.proto, t.proto)}({t.proto})",
                      f"     SrcAddr   Vpn     : {t.in_local[0]}",
                      f"     DestAddr  Vpn     : {t.outside[0]}",
                      "     NAT-Info", f"       New SrcAddr     : {t.in_global[0]}",
                      f"       New SrcPort     : {t.in_global[1] if t.in_global[1] is not None else '----'}",
                      ""]
        lines.append(f"  Total : {len(nat.table)}")
        return "\n".join(lines)

    # -- remote access ------------------------------------------------------------------------
    def _acc(self):
        from ..services.remote import access
        return access(self.device)

    def set_mode_h(self, mode):
        self.mode = mode
        return None

    def srv_enable(self, proto, on):
        self._acc()[proto] = on
        name = "Telnet" if proto == "telnet" else "STelnet"
        return f"Info: Succeeded in {'starting' if on else 'stopping'} the {name} server."

    def vty_auth(self, mode):
        self._acc()["login"] = mode
        return None

    def vty_password(self, pw):
        self._acc()["vty_password"] = pw
        return None

    def vty_proto(self, p):
        self._acc()["protocols"] = p
        return None

    def local_user(self, name, password=None, services=None, delete=False):
        a = self._acc()
        users, svc = a.setdefault("users", {}), a.setdefault("services", {})
        if delete:
            users.pop(name, None)
            svc.pop(name, None)
            return None
        if password is not None:
            users[name] = password
            return "Info: Add a new user." if name not in svc else None
        if services is not None:
            if name not in users:
                return "Error: The user does not exist."
            svc[name] = ["ssh" if w in ("ssh", "stelnet") else w for w in services.split()]
        return None

    def remote_telnet(self, ip):
        from ..services.remote import connect
        self.write(f"  Trying {ip} ...\n  Press CTRL+K to abort\n")
        return connect(self, ip, "telnet", "", "huawei")

    def remote_stelnet(self, ip):
        from ..services.remote import connect

        def got(user):
            return connect(self, ip, "ssh", user.strip(), "huawei")
        self.ask("Please input the username:", got, secret=False)
        return None

    # -- port security / DHCP snooping -----------------------------------------------------------
    def psec(self, attr, value, negate):
        from .base import psec_set
        return psec_set(self.iface, attr, value, negate)

    def snoop_enable(self, on):
        self.device.dhcp_snooping["enabled"] = on
        return None

    def snoop_vlan(self, vids, negate):
        cur = set(self.device.dhcp_snooping.get("vlans") or [])
        self.device.dhcp_snooping["vlans"] = sorted(cur - set(vids) if negate else cur | set(vids))
        self.device.dhcp_snooping["enabled"] = True
        return None

    def snoop_trust(self, negate):
        self.iface.dhcp_trust = not negate
        return None

    def display_psec(self):
        lines = [f"{'Interface':<26}{'Max':<6}{'Current':<9}{'Action':<12}Violations"]
        for p in self.device.physical_ports():
            if p.psec is not None:
                act = {"shutdown": "error-down"}.get(p.psec.get("violation", "shutdown"), p.psec.get("violation"))
                lines.append(f"{p.name:<26}{p.psec.get('max', 1):<6}{len(p.psec.get('macs', [])):<9}{act:<12}"
                             f"{p.psec.get('violations', 0)}")
        return "\n".join(lines)

    def display_snoop(self, binding):
        d = self.device
        if binding:
            lines = ["BindTblShowmode: All", "Binding Table:", "IP Address       MAC Address     VSI/VLAN(O/I/P) Interface      Lease",
                     "-" * 78]
            for mac, b in d.snoop_bindings.items():
                lines.append(f"{b['ip']:<17}{mac.replace('.', '-'):<16}{str(b['vlan']) + ' /-- /--':<16}{b['port']:<15}{b['lease']}")
            lines += ["-" * 78, f"Print count:        {len(d.snoop_bindings)}          Total count:        {len(d.snoop_bindings)}"]
            return "\n".join(lines)
        ds = d.dhcp_snooping
        return (f"DHCP snooping global running information :\n DHCP snooping                          : "
                f"{'Enable' if ds['enabled'] else 'Disable'}\n DHCP snooping VLAN                     : "
                f"{' '.join(map(str, ds.get('vlans') or [])) or 'all'}")

    # -- QoS (traffic classifier / behavior / policy) --------------------------------------------
    def q_view(self, kind, name):
        from ..security.qos import QosPolicy
        if kind == "classifier":
            self.device.qos_classes.setdefault(name, {})
            self._qc = name
        elif kind == "behavior":
            self.device.extra.setdefault("qos_behaviors", {}).setdefault(name, {})
            self._qb = name
        else:
            self.device.qos.setdefault(name, QosPolicy(name))
            self._qp = name
        self.mode = kind
        return None

    def q_match(self, what, value):
        from .base import qos_class_match
        try:
            return qos_class_match(self.device, self._qc, what, value)
        except ValueError:
            return self.err_invalid(self.prompt, 0)

    def q_behave(self, attr, value):
        from ..security.qos import parse_dscp
        b = self.device.extra.setdefault("qos_behaviors", {}).setdefault(self._qb, {})
        try:
            b[attr] = parse_dscp(value) if attr == "dscp" else int(value)
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        for pol in self.device.qos.values():                       # keep bound policies in step
            for c in pol.classes:
                if c.__dict__.get("behavior") == self._qb:
                    c.set_dscp, c.police_bps = b.get("dscp"), b.get("police")
        return None

    def q_bind(self, classifier, behavior):
        from .base import qos_policy_class
        if classifier not in self.device.qos_classes:
            return f"Error: The classifier {classifier} does not exist."
        b = self.device.extra.get("qos_behaviors", {}).get(behavior)
        if b is None:
            return f"Error: The behavior {behavior} does not exist."
        qos_policy_class(self.device, self._qp, classifier)
        c = next(x for x in self.device.qos[self._qp].classes if x.name == classifier)
        c.set_dscp, c.police_bps = b.get("dscp"), b.get("police")
        c.behavior = behavior
        return None

    def q_apply(self, name, direction, negate):
        if not negate and name not in self.device.qos:
            return f"Error: The traffic policy {name} does not exist."
        setattr(self.iface, "qos_in" if direction == "in" else "qos_out", None if negate else name)
        return None

    def q_stats(self):
        from .base import qos_stats
        out = []
        for p in self.device.ports.values():
            for direction in ("in", "out"):
                lines = qos_stats(self.device, p, direction)
                if lines:
                    out += [f" Interface: {p.name}"] + lines
        return "\n".join(out) or "Info: No traffic policy is applied."

    # -- NAT64 ---------------------------------------------------------------------------------
    def nat64_cmd(self, what, value, negate):
        from ipaddress import IPv6Network
        from ..security.nat64 import WELL_KNOWN, Nat64
        d = self.device
        if d.nat64 is None:
            if negate:
                return None
            d.nat64 = Nat64()
        if what == "enable" and negate:
            d.nat64 = None
        elif what == "prefix":
            d.nat64.prefix = WELL_KNOWN if negate else IPv6Network(value, strict=False)
        elif what == "if":
            (d.nat64.interfaces.discard if negate else d.nat64.interfaces.add)(self.iface.name)
        return None

    def display_nat64(self):
        n = self.device.nat64
        out = []
        for s in (n.sessions if n else []):
            out += [f"  Protocol          : {s.proto.upper()}", f"  IPv6 SrcAddr/ID   : {s.v6_src}/{s.ident6}",
                    f"  IPv6 DestAddr     : {s.v6_dst}", f"  IPv4 SrcAddr/ID   : {s.v4_src}/{s.ident4}",
                    f"  IPv4 DestAddr     : {s.v4_dst}", ""]
        return "\n".join(out) + f"  Total : {len(n.sessions) if n else 0}"

    # -- DHCPv6 --------------------------------------------------------------------------------
    def dhcp6_pool(self, name, negate):
        if negate:
            self.device.dhcp6.pools.pop(name, None)
            return None
        from .base import dhcp6_pool_cmd
        dhcp6_pool_cmd(self.device, name, None, None)
        self._dhcp6_pool = name
        self.mode = "dhcpv6-pool"
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

    def display_dhcp6(self):
        out = []
        for pool in self.device.dhcp6.pools.values():
            out += [f"Pool name : {pool.name}", f"  Prefix : {pool.prefix}", f"  DNS server : {pool.dns or '-'}",
                    f"  Used address : {len(pool.bindings)}", ""]
        return "\n".join(out) or "Info: No DHCPv6 pool exists."

    # -- OSPFv3 / RIPng ------------------------------------------------------------------------
    def v6igp_view(self, kind, tag, negate):
        from ..routing.v6igp import ensure
        d = self.device
        if negate:
            proc = d.v6igp.pop(kind, None)
            if proc is not None:
                proc.stop()
                proc._recompute_domain()
            return None
        self._v6proc = ensure(d, kind, str(tag))
        self.mode = "ospfv3" if kind == "ospf6" else "ripng"
        return None

    def v6igp_rid(self, rid):
        proc = self.device.v6igp.get("ospf6")
        if proc is not None:
            proc.cfg.router_id = rid
            proc.kick()
        return None

    def v6igp_if(self, kind, tag, area, negate):
        from ..routing.v6igp import enable_iface
        from ..routing.ospf import area_id
        p = self.iface
        if not self.is_router and not p.is_svi:
            return "Error: This command is not supported on a Layer 2 interface."
        if not negate and not (p.ipv6_enabled or p.ipv6):
            return "Error: Please enable IPv6 on the interface first."
        if not negate and kind not in self.device.v6igp:
            return f"Error: The {'OSPFv3' if kind == 'ospf6' else 'RIPng'} process does not exist."
        try:
            a = area_id(area) if area is not None else "0.0.0.0"
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        enable_iface(self.device, kind, p, a, str(tag), on=not negate)
        return None

    def v6igp_cost(self, cost, negate):
        proc = self.device.v6igp.get("ospf6")
        if proc is not None and self.iface.name in proc.cfg.interfaces:
            proc.cfg.interfaces[self.iface.name]["cost"] = None if negate else cost
            proc._recompute_domain()
        return None

    def display_v6igp(self, kind):
        from .base import v6igp_neighbors
        if kind == "ripng":
            proc = self.device.v6igp.get("ripng")
            return ("Public VPN-Instance name : Public\nRIPng process : 1\n      Preference : 100\n"
                    f"      Number of routes in database : {len(proc.routes) if proc else 0}")
        lines = ["OSPFv3 Process (1)", f"{'Neighbor ID':<16}{'Pri':<5}{'State':<16}{'Dead Time':<11}{'Interface':<26}Instance ID"]
        for rid, p, ll in v6igp_neighbors(self.device, kind):
            lines.append(f"{rid:<16}{1:<5}{'Full/ - ':<16}{'00:00:35':<11}{(p.name if p else ''):<26}0")
        return "\n".join(lines)

    # -- VRRP --------------------------------------------------------------------------------
    def vrrp_cmd(self, vrid, attr, value, negate):
        from .base import fhrp_set
        p = self.iface
        if not self.is_router and not p.is_svi:
            return "Error: VRRP can be configured only on a Layer 3 interface (Vlanif)."
        if attr == "vip" and not negate and (p.ip is None or __import__("ipaddress").IPv4Address(value) not in p.ip.network):
            return "Error: The virtual IP address and the interface IP address are not in the same network segment."
        return fhrp_set(self.device, p, "vrrp", vrid, attr, value, negate)

    def display_vrrp(self, brief):
        from .base import fhrp_rows
        rows = [r for r in fhrp_rows(self.device) if r[0].proto == "vrrp"]
        if brief:
            lines = [f"{'VRID':<6}{'State':<13}{'Interface':<25}{'Type':<9}Virtual IP", "-" * 64]
            for g, p, act, stb in rows:
                lines.append(f"{g.group:<6}{g.state.capitalize():<13}{p.short:<25}{'Normal':<9}{g.vip}")
            m = sum(1 for g, *_ in rows if g.state == "master")
            lines += ["-" * 64, f"Total:{len(rows)}     Master:{m}     Backup:{len(rows) - m}     Non-active:0"]
            return "\n".join(lines)
        out = []
        for g, p, act, stb in rows:
            out += [f"  {p.name} | Virtual Router {g.group}", f"    State           : {g.state.capitalize()}",
                    f"    Virtual IP      : {g.vip}", f"    Master IP       : {p.ip.ip if g.state == 'master' else (act or '0.0.0.0')}",
                    f"    PriorityRun     : {self.device.fhrp.eff_priority(g)}", f"    PriorityConfig  : {g.priority}",
                    f"    Preempt         : {'YES' if g.preempting else 'NO'}   Delay Time : 0 s",
                    "    TimerRun        : 1 s", f"    Virtual MAC     : 0000-5e00-01{g.group:02x}", ""]
        return "\n".join(out)

    # -- GRE tunnel --------------------------------------------------------------------------
    def enter_tun(self, num, negate):
        d = self.device
        name = f"Tunnel{num}"
        if negate:
            if name in d.ports:
                d.remove_tunnel(d.ports[name])
            return None
        self.mode, self.iface = "interface", d.add_tunnel(name, f"Tun{num}")
        return None

    def tun(self, attr, value):
        from .base import tunnel_set
        if not getattr(self.iface, "is_tunnel", False):
            return self.err_invalid(self.prompt, 0)
        return tunnel_set(self.iface, attr, value)

    # -- IS-IS -----------------------------------------------------------------------------------
    def isis_view(self, pid, negate):
        from ..routing.isis import IsisConfig
        d = self.device
        if negate:
            d.disable_isis()
            return None
        if d.isis is None:
            d.enable_isis(IsisConfig(tag=str(pid)))
        self.mode = "isis"
        return None

    def isis_net(self, net, negate):
        from ..routing.isis import parse_net
        p = self.device.isis
        if negate:
            p.cfg.net = None
            p.stop()
            return None
        if parse_net(net) is None:
            return "Error: The network entity title is invalid."
        p.cfg.net = net
        p.restart()
        return None

    def if_isis6(self, negate):
        from ..routing.isis import IsisConfig
        d = self.device
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

    def isis_cost(self, value, negate):
        d = self.device
        if d.isis is not None:
            if negate:
                d.isis.cfg.metrics.pop(self.iface.name, None)
            else:
                d.isis.cfg.metrics[self.iface.name] = int(value)
            d.isis.link_changed(self.iface)
        return None

    def display_isis(self, what):
        from .base import isis_database_lines, isis_neighbors_rows
        tag = self.device.isis.cfg.tag if self.device.isis else 1
        if what == "peer":
            lines = ["", f"                          Peer information for ISIS({tag})", "",
                     "  System Id     Interface          Circuit Id        State HoldTime Type     PRI",
                     "-" * 80]
            for sid, host, port, ip, state, hold in isis_neighbors_rows(self.device):
                lines.append(f"  {sid:<14}{port.name:<19}{sid}.01  {state:<6}{hold:<9}L2       64")
            lines.append(f"\nTotal Peer(s): {len(isis_neighbors_rows(self.device))}")
            return "\n".join(lines)
        return "\n".join(["", f"                        Database information for ISIS({tag})",
                          "                        -------------------------------------", "",
                          "                          Level-2 Link State Database", "",
                          "LSPID                 Seq Num      Checksum      Holdtime      Length  ATT/P/OL"]
                         + isis_database_lines(self.device))

    # -- IPsec (ike peer / ipsec profile on a tunnel interface) ---------------------------------
    _SEC_KEYS = {"ipsec-proposal": "hw_proposals", "ike-peer": "hw_ike_peers", "ipsec-profile": "hw_ipsec_profiles"}

    def sec_view(self, mode, name, negate):
        store = self.device.extra.setdefault(self._SEC_KEYS[mode], {})
        if negate:
            store.pop(name, None)
            self._ipsec_apply()
            return None
        store.setdefault(name, {})
        self.mode, self._sec_name = mode, name
        return None

    def sec_set(self, attr, value):
        self.device.extra[self._SEC_KEYS[self.mode]][self._sec_name][attr] = str(value)
        self._ipsec_apply()
        return None

    def tun_ipsec(self, profile, negate):
        if not getattr(self.iface, "is_tunnel", False):
            return self.err_invalid(self.prompt, 0)
        prot = self.device.extra.setdefault("tunnel_protection", {})
        if negate:
            prot.pop(self.iface.name, None)
        else:
            if profile not in self.device.extra.get("hw_ipsec_profiles", {}):
                return f"Error: The IPsec profile {profile} does not exist."
            prot[self.iface.name] = profile
        self._ipsec_apply()
        return None

    def _ipsec_apply(self):
        """Tunnels with an IPsec profile get the pre-shared key of the profile's IKE peer."""
        x = self.device.extra
        peers, profs = x.get("hw_ike_peers", {}), x.get("hw_ipsec_profiles", {})
        for tname, prof in x.get("tunnel_protection", {}).items():
            t = self.device.ports.get(tname)
            if t is not None:
                peer = peers.get(profs.get(prof, {}).get("peer", ""), {})
                t.ipsec_key = peer.get("key")
        for t in self.device.ports.values():
            if getattr(t, "is_tunnel", False) and t.name not in x.get("tunnel_protection", {}):
                t.ipsec_key = None

    # -- Eth-Trunk ---------------------------------------------------------------------------
    def enter_trunk(self, n, negate):
        d = self.device
        po = d.channel_for_group(n)
        if negate:
            if po is None:
                return "Error: The interface does not exist."
            d.remove_channel(po)
            return None
        if po is None:
            po = d.add_channel(n, f"Eth-Trunk{n}", f"Eth-Trunk{n}")
            po.hw_mode = "on"
        self.mode, self.iface = "interface", po
        return None

    def trunk_mode(self, mode):
        po = self.iface
        if not getattr(po, "is_channel", False):
            return self.err_invalid(self.prompt, 0)
        po.hw_mode = mode
        for m in po.members:
            self.device.set_channel_member(m, po.group, mode)
        return None

    def trunk_member(self, n, negate):
        d, p = self.device, self.iface
        if p.is_channel or p.is_svi:
            return self.err_invalid(self.prompt, 0)
        if negate:
            d.set_channel_member(p, None)
            return None
        po = d.channel_for_group(n)
        if po is None:
            return f"Error: The Eth-Trunk{n} does not exist."
        d.set_channel_member(p, n, getattr(po, "hw_mode", "on"))
        return None

    def display_trunk(self, n):
        from .base import lag_summary_rows
        out = []
        for po, proto, mem in lag_summary_rows(self.device):
            if n is not None and po.group != n:
                continue
            mode = "LACP" if proto == "LACP" else "NORMAL"
            out += [f"{po.name}'s state information is:",
                    f"WorkingMode: {mode}       Hash arithmetic: According to SIP-XOR-DIP",
                    "Least Active-linknumber: 1  Max Bandwidth-affected-linknumber: 8",
                    f"Operate status: {'up' if po.is_up else 'down'}        Number Of Up Port In Trunk: {len(po.active_members)}",
                    "-" * 72, "PortName                      Status      Weight"]
            out += [f"{m.name:<30}{'Up' if f == 'P' else 'Down':<12}1" for m, f in mem]
            out.append("")
        return "\n".join(out) or "Info: No Eth-Trunk exists."

    def enter_vlanif(self, vid, negate=False):
        d = self.device
        name = f"Vlanif{vid}"
        if negate:
            p = d.ports.get(name)
            if p is None:
                return "Error: The interface does not exist."
            d.remove_svi(p)
            return None
        if vid not in d.vlans:
            d.add_vlan(vid)
        self.mode, self.iface = "interface", d.add_svi(vid, name, name)
        return None

    def enter_loopback(self, num, negate=False):
        if negate:
            return "Error: The interface does not exist."
        lo = self.device.add_loopback(f"LoopBack{num}", f"Loop{num}")
        self.mode, self.iface = "interface", lo
        return None

    # -- OSPF ------------------------------------------------------------------------------
    def _ocfg(self):
        return self.device.ospf.cfg

    def _ospf_changed(self):
        self.device.ospf.reconfigure()
        return None

    def ospf_enter(self, pid, rid, negate):
        from ..routing.ospf import OspfConfig
        d = self.device
        running = d.ospf is not None and d.ospf.cfg.enabled
        if negate:
            if running and d.ospf.cfg.process_id == pid:
                old = d.ospf.cfg
                if old.has_interface_settings():
                    d.enable_ospf(OspfConfig(enabled=False, costs=old.costs, hello=old.hello,
                                             dead=old.dead, priority=old.priority))
                else:
                    d.disable_ospf()
                return None
            return "Error: The OSPF process does not exist."
        if running and d.ospf.cfg.process_id != pid:
            return "Error: Only one OSPF process is supported in this simulator."
        if not running:
            cfg = d.ospf.cfg if d.ospf is not None else OspfConfig()
            cfg.process_id, cfg.enabled, cfg.router_id = pid, True, rid
            d.enable_ospf(cfg)
        elif rid is not None and rid != d.ospf.cfg.router_id:
            d.ospf.cfg.router_id = rid
            self.mode = "ospf"
            return "Info: The configuration succeeded. You need to restart OSPF process to validate the new router ID."
        self.mode = "ospf"
        return None

    def ospf_area(self, text, negate):
        from ..routing.ospf import area_id
        try:
            a = area_id(text)
        except ValueError:
            return self.err_invalid(self.prompt, 0)
        if negate:
            self._ocfg().networks = [x for x in self._ocfg().networks if x["area"] != a]
            return self._ospf_changed()
        self.area_ctx = a
        self.mode = "ospf-area"
        return None

    def ospf_network(self, ip, wildcard, negate):
        from ..routing.ospf import wildcard_to_prefix
        entry = {"prefix": str(wildcard_to_prefix(ip, wildcard)), "area": self.area_ctx}
        nets = self._ocfg().networks
        if negate:
            if entry not in nets:
                return "Error: The network does not exist."
            nets.remove(entry)
        elif entry not in nets:
            nets.append(entry)
        return self._ospf_changed()

    def ospf_silent(self, port, negate):
        c = self._ocfg()
        if port is None:
            c.passive_default, c.non_passive = not negate, []
        elif negate:
            if port.name in c.passive:
                c.passive.remove(port.name)
        elif port.name not in c.passive:
            c.passive.append(port.name)
        return self._ospf_changed()

    def ospf_default(self, mode, negate):
        self._ocfg().default_originate = None if negate else mode
        return self._ospf_changed()

    def ospf_import(self, proto, negate):
        lst = self._ocfg().redistribute
        if negate and proto in lst:
            lst.remove(proto)
        elif not negate and proto not in lst:
            lst.append(proto)
        return self._ospf_changed()

    def ospf_refbw(self, bw, negate):
        self._ocfg().reference_bandwidth = 100 if negate else bw
        return self._ospf_changed()

    def if_ospf(self, attr, value, negate):
        from ..routing.ospf import OspfConfig
        d = self.device
        if d.ospf is None:
            d.enable_ospf(OspfConfig(enabled=False))
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
            return "Error: The interface does not exist."
        sub = self.device.add_subinterface(parent, num)
        if sub.vlan is None:
            sub.arp_broadcast = False   # VRP: ARP broadcast is off on new termination sub-interfaces
        self.mode, self.iface = "interface", sub
        return None

    def dot1q_vid(self, vid, negate):
        if not self.iface.is_sub:
            return self.err_invalid(self.prompt, 0)
        self.iface.vlan = None if negate else vid
        self.device.arp.flush_port(self.iface)
        return None

    def arp_broadcast(self, on):
        if not self.iface.is_sub:
            return self.err_invalid(self.prompt, 0)
        self.iface.arp_broadcast = on
        return None

    # -- STP (switch) ------------------------------------------------------------------
    def stp_set(self, attr, value):
        stp = self.device.stp
        if attr == "priority" and value % 4096:
            return "Error: The priority value must be a multiple of 4096."
        setattr(stp, attr, value)
        if attr == "enabled" and not value:
            stp.states.clear()
        stp.schedule()
        return None

    def stp_edge(self, negate):
        e = self.device.stp.edge
        (e.discard if negate else e.add)(self.iface.name)
        self.device.stp.schedule()
        return None

    def stp_cost(self, cost, negate):
        if negate:
            self.device.stp.costs.pop(self.iface.name, None)
        else:
            self.device.stp.costs[self.iface.name] = cost
        self.device.stp.schedule()
        return None

    def display_stp(self, brief):
        from ..addresses import MacAddress
        sw = self.device
        stp = sw.stp
        if not stp.enabled:
            return "Protocol Status   :Disabled"
        roles = {"root": "ROOT", "designated": "DESI", "alternate": "ALTE", "edge": "DESI", "disabled": "DISA"}
        states = {"forwarding": "FORWARDING", "blocking": "DISCARDING", "listening": "DISCARDING",
                  "learning": "LEARNING", "disabled": "DISCARDING"}
        if brief:
            lines = [f" {'MSTID':<7}{'Port':<28}{'Role':<6}{'STP State':<14}Protection"]
            for p in sw.ports.values():
                if p.is_up:
                    lines.append(f" {0:>5}  {p.name:<28}{roles.get(stp.roles.get(p.name), 'DESI'):<6}"
                                 f"{states.get(stp.states.get(p.name, 'forwarding')):<14}NONE")
            return "\n".join(lines)
        root = stp.root_id or stp.bridge_id
        fmt = lambda b: f"{b[0]}.{MacAddress(b[1]).cisco().replace('.', '-')}"
        return "\n".join([
            "-------[CIST Global Info][Mode " + stp.mode.upper() + "]-------",
            f"CIST Bridge         :{fmt(stp.bridge_id)}",
            "Config Times        :Hello 2s MaxAge 20s FwDly 15s MaxHop 20",
            f"CIST Root/ERPC      :{fmt(root)} / {stp.root_cost}",
            f"CIST RegRoot/IRPC   :{fmt(stp.bridge_id)} / 0",
            f"CIST RootPortId     :{('128.' + str(list(sw.ports).index(stp.root_port) + 1)) if stp.root_port else '0.0'}",
            f"TC or TCN received  :{stp.topology_changes}"])

    # -- VLANs (switch) --------------------------------------------------------------
    @staticmethod
    def _vlan_ids(text):
        words = text.replace(",", " ").split()
        out, i = set(), 0
        while i < len(words):
            if not words[i].isdigit():
                return None
            lo = int(words[i])
            if i + 2 < len(words) and words[i + 1] == "to" and words[i + 2].isdigit():
                hi = int(words[i + 2])
                i += 3
            else:
                hi = lo
                i += 1
            if not 1 <= lo <= hi <= 4094:
                return None
            out.update(range(lo, hi + 1))
        return out or None

    def vlan_batch(self, text, negate):
        vids = self._vlan_ids(text)
        if vids is None:
            return "Error: Wrong parameter found at '^' position."
        for v in sorted(vids):
            self.device.remove_vlan(v) if negate else self.device.add_vlan(v)
        if not negate:
            return "Info: This operation may take a few seconds. Please wait for a moment...done."
        return None

    def vlan_view(self, vid, negate):
        if negate:
            if vid == 1:
                return "Error: The default VLAN cannot be deleted."
            self.device.remove_vlan(vid)
            return None
        self.device.add_vlan(vid)
        self.vlan_ctx = vid
        self.mode = "vlan"
        return None

    def vlan_name(self, name, negate):
        v = self.vlan_ctx
        self.device.vlans[v] = f"VLAN{v:04d}" if negate or not name else name
        return None

    def _sw_changed(self):
        sw = self.device
        for key in [k for k, e in sw.mac_table.items() if e.port is self.iface]:
            del sw.mac_table[key]
        sw.engine.emit("device_changed", sw)
        return None

    def link_type(self, mode, negate):
        p = self.iface
        mode = "access" if negate else mode
        if mode == p.mode:
            return None
        p.mode = mode
        if mode == "trunk":
            p.tagged = frozenset({1})      # VRP trunks pass only VLAN 1 by default
            p.pvid = p.native_vlan
        else:
            p.tagged = None
            p.pvid = p.access_vlan
        return self._sw_changed()

    def default_vlan(self, vid, negate):
        p = self.iface
        if p.mode != "access":
            return "Error: Please renew the default configurations."
        vid = 1 if negate else vid
        if vid not in self.device.vlans:
            return "Error: The VLAN does not exist."
        p.access_vlan = p.pvid = vid
        return self._sw_changed()

    def allow_pass(self, text, negate):
        p = self.iface
        if p.mode != "trunk":
            return "Error: Please change the link type of the port to trunk or hybrid first."
        if text is None:
            p.tagged = frozenset() if negate else None
            return self._sw_changed()
        vids = self._vlan_ids(text)
        if vids is None:
            return "Error: Wrong parameter found at '^' position."
        cur = set(range(1, 4095)) if p.tagged is None else set(p.tagged)
        new = cur - vids if negate else cur | vids
        p.tagged = None if len(new) == 4094 else frozenset(new)
        return self._sw_changed()

    def trunk_pvid(self, vid, negate):
        p = self.iface
        if p.mode != "trunk":
            return "Error: Please change the link type of the port to trunk or hybrid first."
        p.native_vlan = p.pvid = 1 if negate else vid
        return self._sw_changed()

    # -- config --------------------------------------------------------------------
    def sysname(self, name, negate):
        self.device.hostname = self.device.default_hostname if negate else name
        self.device.engine.emit("device_changed", self.device)
        return None

    def if_description(self, text, negate):
        self.iface.description = "" if negate else text
        return None

    def if_shutdown(self, down):
        p = self.iface
        was = p.admin_up
        p.set_admin(not down)
        if was == p.admin_up:
            return None
        state = "UP" if p.is_up else "DOWN"
        return (f"Info: Interface {p.name} is {'administratively DOWN' if down else 'UP'}.\n"
                if down else
                f"Info: The line protocol IP on the interface {p.name} has entered the {state} state.")

    def if_ip(self, ip, mask, negate):
        p = self.iface
        if not self.is_router and not (p.is_svi or p.is_loopback):
            return "Error: This command is not supported on a Layer 2 interface. Use interface Vlanif."
        if negate:
            p.ip = None
            self.device.arp.flush_port(p)
            return None
        iface = make_interface(ip, mask)
        if iface.ip in (iface.network.network_address, iface.network.broadcast_address) \
                and iface.network.prefixlen < 31:
            return "Error: The specified IP address is invalid."
        other = overlapping_port(self.device, p, iface)
        if other is not None:
            return "Error: The specified IP address conflicts with another address."
        p.ip = iface
        self.device.arp.flush_port(p)
        return None

    def if_ip2(self, ip, mask, negate):
        from .base import secondary_cmd
        return secondary_cmd(self.device, self.iface, make_interface(ip, mask), negate,
                             "Error: The specified IP address conflicts with another address.")

    def route_null(self, net, mask, negate):
        try:
            network = IPv4Network(f"{net}/{mask}")
        except ValueError:
            return "Error: The destination address and mask do not match."
        if negate:
            self.device.remove_static_route(network, None, "Null0")
        else:
            self.device.add_static_route(network, None, "Null0", 60)
        self.device.routes_changed()
        return None

    def if_urpf(self, mode, negate):
        self.iface.urpf = None if negate else mode
        return None

    def route_static(self, net, mask, nh, port, pref, negate):
        try:
            network = IPv4Network(f"{net}/{mask}")
        except ValueError:
            return "Error: The destination address and mask do not match."
        nh_ip = IPv4Address(nh) if nh else None
        if negate:
            if not self.device.remove_static_route(network, nh_ip, port.name if port else None):
                return "Error: The static route does not exist."
            return None
        if nh_ip is not None and self.device.owns_ip(nh_ip):
            return "Error: The next-hop address is invalid."
        self.device.add_static_route(network, nh_ip, port.name if port else None, pref)
        return None

    # -- user commands ------------------------------------------------------------
    def ping(self, ip, count=5):
        lis = HuaweiPing(self.write)
        lis.dst = ip
        return make_ping(self.device, ip, lis, count=count, size=56).start()

    def tracert(self, ip):
        return make_trace(self.device, ip, HuaweiTrace(self.write)).start()

    def save(self):
        self.device.save_startup()
        return ("The current configuration will be written to the device.\n"
                "Are you sure to continue? (y/n)[n]:y\n"
                "It will take several minutes to save configuration file, please wait.......\n"
                "Configuration file had been saved successfully\n"
                "Note: The configuration file will take effect after being activated")

    def reset_arp(self):
        self.device.arp.clear()
        return None

    # -- display ---------------------------------------------------------------------
    def display_cfg(self, cfg):
        return render_current(self.device, cfg)

    def display_saved(self):
        if not self.device.startup_config:
            return "Info: The system is now comparing the configuration, please wait.\n" \
                   "Warning: The saved configuration file does not exist."
        return render_current(self.device, self.device.startup_config)

    def display_version(self):
        d = self.device
        return ("Huawei Versatile Routing Platform Software\n"
                "VRP (R) software, Version 5.170 (" + ("AR2200 V200R009C00SPC500" if self.is_router
                                                       else "S5720 V200R011C10SPC500") + ")\n"
                "Copyright (C) 2011-2018 HUAWEI TECH CO., LTD\n"
                f"Huawei {d.model.replace('Huawei ', '')} uptime is {uptime_text(d.engine.now)}")

    def display_clock(self):
        from .render import sim_clock
        return sim_clock(self.device.engine.now).strftime("%Y-%m-%d %H:%M:%S\n%A\nTime Zone(UTC) : UTC")

    def _phy(self, p):
        if not p.admin_up:
            return "*down"
        return "up" if p.is_up else "down"

    def display_ip_int_brief(self):
        lines = ["*down: administratively down", "^down: standby", "(l): loopback", "(s): spoofing",
                 "(E): E-Trunk down",
                 f"The number of interface that is UP in Physical is "
                 f"{sum(p.is_up for p in self.device.ports.values())}",
                 f"The number of interface that is DOWN in Physical is "
                 f"{sum(not p.is_up for p in self.device.ports.values())}", "",
                 f"{'Interface':<34}{'IP Address/Mask':<20}{'Physical':<10}Protocol"]
        for p in self.device.ports.values():
            ip = p.ip.with_prefixlen if p.ip else "unassigned"
            proto = "up" if (p.is_up and p.ip) else "down"
            lines.append(f"{p.name:<34}{ip:<20}{self._phy(p):<10}{proto}")
        return "\n".join(lines)

    def display_int_brief(self):
        lines = ["PHY: Physical", "*down: administratively down", "(l): loopback", "(s): spoofing",
                 "(b): BFD down", "(e): ETHOAM down", "(d): Dampening Suppressed",
                 "InUti/OutUti: input utility/output utility",
                 f"{'Interface':<29}{'PHY':<8}{'Protocol':<9}{'InUti':<6}{'OutUti':<7}{'inErrors':<9}outErrors"]
        for p in self.device.ports.values():
            proto = "up" if p.is_up else "down"
            lines.append(f"{p.name:<29}{self._phy(p):<8}{proto:<9}{'0%':<6}{'0%':<7}{'0':<9}0")
        return "\n".join(lines)

    def display_interface(self, port):
        ports = [port] if port else list(self.device.ports.values())
        out = []
        for p in ports:
            state = "Administratively DOWN" if not p.admin_up else ("UP" if p.is_up else "DOWN")
            block = [f"{p.name} current state : {state}",
                     f"Line protocol current state : {'UP' if p.is_up else 'DOWN'}",
                     f"Description:{p.description or 'HUAWEI, ' + p.name + ' Interface'}",
                     "Route Port,The Maximum Transmit Unit is 1500" if self.is_router else
                     "Switch Port, PVID :    1, TPID : 8100(Hex), The Maximum Frame Length is 9216"]
            if p.ip:
                block.append(f"Internet Address is {p.ip.with_prefixlen}")
            m = p.mac.cisco().replace(".", "-")
            block += [f"IP Sending Frames' Format is PKTFMT_ETHNT_2, Hardware address is {m}",
                      f"    Input:  {p.rx_frames} packets", f"    Output: {p.tx_frames} packets"]
            out.append("\n".join(block))
        return "\n\n".join(out)

    def display_arp(self):
        d = self.device
        lines = ["IP ADDRESS      MAC ADDRESS     EXPIRE(M) TYPE        INTERFACE      VPN-INSTANCE",
                 "                                          VLAN/CEVLAN PVC",
                 "-" * 78]
        count = 0
        for p in d.ip_ports():
            if p.is_up:
                lines.append(f"{str(p.ip.ip):<16}{p.mac.cisco().replace('.', '-'):<16}"
                             f"{'':<10}{'I -':<12}{p.short}")
                count += 1
        for e in d.arp.entries(d.engine.now):
            exp = max(0, int((d.arp.ttl - (d.engine.now - e.learned_at)) / 60000))
            exp = min(exp, 20)
            lines.append(f"{str(e.ip):<16}{e.mac.cisco().replace('.', '-'):<16}{exp:<10}{'D-0':<12}{e.port.short}")
            count += 1
        lines += ["-" * 78, f"Total:{count}         Dynamic:{len(d.arp)}         Static:0     Interface:{count - len(d.arp)}"]
        return "\n".join(lines)

    def display_mac(self):
        d = self.device
        entries = d.mac_entries()
        lines = ["-" * 79, "MAC Address    VLAN/       PEVLAN CEVLAN Port            Type      LSP/LSR-ID",
                 "               VSI/SI                                              MAC-Tunnel",
                 "-" * 79]
        for e in entries:
            lines.append(f"{e.mac.cisco().replace('.', '-'):<15}{e.vlan:<12}{'-':<7}{'-':<7}"
                         f"{e.port.short:<16}{'dynamic':<10}0/-")
        lines += ["-" * 79, f"Total matching items on slot 0 displayed = {len(entries)}"]
        return "\n".join(lines)

    def display_vlan(self, vid):
        sw = self.device
        if vid is not None and vid not in sw.vlans:
            return "Error: The VLAN does not exist."
        vids = [vid] if vid is not None else sorted(sw.vlans)
        lines = ["The total number of vlans is : %d" % len(sw.vlans),
                 "-" * 80,
                 "U: Up;         D: Down;         TG: Tagged;         UT: Untagged;",
                 "MP: Vlan-mapping;               ST: Vlan-stacking;",
                 "#: ProtocolTransparent-vlan;    *: Management-vlan;",
                 "-" * 80, "", "VID  Type    Ports", "-" * 80]
        for v in vids:
            members = []
            for p in sw.ports.values():
                member, tag = p.vlan_out(v)
                if member:
                    st = "U" if p.is_up else "D"
                    members.append(("UT" if tag is None else "TG", f"{p.short}({st})"))
            ut = [m for k, m in members if k == "UT"]
            tg = [m for k, m in members if k == "TG"]
            first = True
            for label, ms in (("UT", ut), ("TG", tg)):
                for i in range(0, len(ms), 4):
                    head = f"{v:<5}{'common':<8}" if first else " " * 13
                    lines.append(f"{head}{label + ':' if i == 0 else '   '}{'   '.join(ms[i:i + 4])}")
                    first = False
            if first:
                lines.append(f"{v:<5}{'common':<8}")
        lines += ["", "VID  Status  Property      MAC-LRN Statistics Description", "-" * 80]
        for v in vids:
            lines.append(f"{v:<5}{'enable':<8}{'default':<14}{'enable':<8}{'disable':<11}{sw.vlans[v]}")
        return "\n".join(lines)

    def display_port_vlan(self):
        lines = [f"{'Port':<29}{'Link Type':<12}{'PVID':<6}Trunk VLAN List", "-" * 79]
        for p in self.device.ports.values():
            if p.mode == "trunk":
                tl = "1-4094" if p.tagged is None else " ".join(map(str, sorted(p.tagged))) or "-"
            else:
                tl = "-"
            lines.append(f"{p.name:<29}{p.mode:<12}{p.pvid:<6}{tl}")
        return "\n".join(lines)

    def _ospf_on(self):
        o = self.device.ospf
        return o if o is not None and o.running else None

    def _ospf_head(self, o):
        return [f"\t OSPF Process {o.cfg.process_id} with Router ID {o.router_id}"]

    def display_ospf_peer(self, brief):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = self._ospf_head(o)
        if brief:
            lines += ["\t\t  Peer Statistic Information", " " + "-" * 76,
                      f" {'Area Id':<17}{'Interface':<33}{'Neighbor id':<17}State"]
            for n, state, _ in o.neighbor_rows():
                st = "Full" if n.two_way and state.startswith("FULL") else ("2-Way" if n.two_way else "Init")
                lines.append(f" {o.iface_area(n.port):<17}{n.port.name:<33}{str(n.rid):<17}{st}")
            lines.append(" " + "-" * 76)
            return "\n".join(lines)
        lines.append("\t\t Neighbors")
        for n, state, dead in o.neighbor_rows():
            dr, bdr = o.dr_bdr(n.port)
            st = "Full" if n.two_way and state.startswith("FULL") else ("2-Way" if n.two_way else "Init")
            lines += ["", f" Area {o.iface_area(n.port)} interface {n.port.ip.ip}({n.port.name})'s neighbors",
                      f" Router ID: {str(n.rid):<17}Address: {n.ip}",
                      f"   State: {st}  Mode:Nbr is  {'Master' if n.rid > o.router_id else 'Slave'}  "
                      f"Priority: {n.priority}",
                      f"   DR: {dr or 'None'}  BDR: {bdr or 'None'}  MTU: 0",
                      f"   Dead timer due in {int(dead)}  sec"]
        return "\n".join(lines)

    def display_ospf_interface(self):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = self._ospf_head(o) + ["\t\t Interfaces", ""]
        by_area: dict[str, list] = {}
        for p in self.device.ports.values():
            if p.ip and o.iface_area(p) is not None:
                by_area.setdefault(o.iface_area(p), []).append(p)
        for area, ports in sorted(by_area.items()):
            lines += [f" Area: {area}          (MPLS TE not enabled)",
                      f" {'IP Address':<16}{'Type':<11}{'State':<9}{'Cost':<6}{'Pri':<5}{'DR':<16}BDR"]
            for p in ports:
                dr, bdr = o.dr_bdr(p)
                typ = "P2P" if p.is_loopback else "Broadcast"
                if p.is_loopback:
                    st = "P-2-P"
                elif not p.is_up:
                    st = "Down"
                else:
                    st = "DR" if p.ip.ip == dr else "BDR" if p.ip.ip == bdr else "DROther"
                lines.append(f" {str(p.ip.ip):<16}{typ:<11}{st:<9}{o.cost(p):<6}{o.priority(p):<5}"
                             f"{str(dr or '0.0.0.0'):<16}{bdr or '0.0.0.0'}")
            lines.append("")
        return "\n".join(lines)

    def display_ospf_lsdb(self):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = self._ospf_head(o) + ["\t\t Link State Database", ""]
        names = {1: "Router", 3: "Sum-Net", 4: "Sum-Asbr", 5: "External"}
        area = object()
        now = int(self.device.engine.now / 1000)
        for l in o.database():
            if not l.body:
                continue
            if l.area != area:
                area = l.area
                lines += ["", f"\t\t         Area: {area}" if area else "\t\t AS External Database",
                          f" {'Type':<11}{'LinkState ID':<16}{'AdvRouter':<17}{'Age':<6}{'Len':<5}{'Sequence':<10}Metric"]
            metric = l.body[1] if l.type in (3, 4, 5) else min((lk[2] for lk in l.body if lk[0] == "stub"), default=0)
            lines.append(f" {names[l.type]:<11}{str(l.ls_id):<16}{str(l.adv):<17}{min(now, 3600):<6}"
                         f"{36 + 12 * len(l.body) if l.type == 1 else 28:<5}{l.seq:08x}  {metric}")
        return "\n".join(lines)

    def display_ospf_routing(self):
        o = self._ospf_on()
        if o is None:
            return ""
        lines = self._ospf_head(o) + ["\t\t Routing Tables", "", " Routing for Network",
                                      f" {'Destination':<20}{'Cost':<6}{'Type':<12}{'NextHop':<16}{'AdvRouter':<16}Area"]
        for r in o.routes:
            typ = {"O": "Transit", "O IA": "Inter-area", "O E2": "Type2"}.get(r.code, r.code)
            lines.append(f" {str(r.network):<20}{r.metric:<6}{typ:<12}{str(r.next_hop):<16}{'-':<16}"
                         f"{o.iface_area(r.port) or '-'}")
        return "\n".join(lines)

    def display_ospf_brief(self):
        o = self._ospf_on()
        if o is None:
            return "Info: OSPF is not enabled."
        return "\n".join(self._ospf_head(o) + [
            f" RouterID: {o.router_id}        Border Router: {' AREA' if o.is_abr else ''}"
            f"{' AS' if o.is_asbr else ''}",
            " Route Tag: 0", " Multi-VPN-Instance is not enabled",
            f" Area Count: {len(o.areas())}   Nssa Area Count: 0",
            " ExChange/Loading Neighbors: 0", f" Process total up interface count: {len(o.active_ports())}"])

    def display_routing_table(self):
        d = self.device
        table = d.routing_table()
        lines = ["Route Flags: R - relay, D - download to fib", "-" * 78,
                 "Routing Tables: Public",
                 f"         Destinations : {len(table)}        Routes : {len(table)}", "",
                 "Destination/Mask    Proto   Pre  Cost      Flags NextHop         Interface", ""]
        for r in table:
            if r.code == "C":
                proto, flags, nh = "Direct", "D", str(r.port.ip.ip)
            elif r.code == "L":
                proto, flags, nh = "Direct", "D", "127.0.0.1"
            elif r.code.startswith("O"):
                proto, flags, nh = ("O_ASE" if r.code.startswith("O E") else "OSPF"), "D", str(r.next_hop)
            elif r.code == "R":
                proto, flags, nh = "RIP", "D", str(r.next_hop)
            elif r.code.startswith("i"):
                proto, flags, nh = "ISIS-L2", "D", str(r.next_hop)
            elif r.code == "B":
                lst = d.bgp.paths.get(r.network, []) if d.bgp else []
                ebgp = next((bp.ebgp for bp in lst if bp.best), True)
                proto, flags, nh = ("EBGP" if ebgp else "IBGP"), "RD", str(r.next_hop)
            else:
                proto, flags = "Static", "RD"
                if r.next_hop:
                    nh = str(r.next_hop)
                else:
                    nh = str(r.port.ip.ip) if r.port.ip else "0.0.0.0"
            pre = 0 if r.code in ("C", "L") else r.distance
            lines.append(f"{str(r.network):>19} {proto:<8}{pre:<5}{r.metric:<10}{flags:>5}   {nh:<16}{r.port.name}")
        return "\n".join(lines)


def render_current(device, cfg: dict) -> str:
    router = device.category == "router"
    lines = ["#", f"sysname {cfg.get('hostname')}", "#"]
    acc = cfg.get("access") or {}
    if acc.get("telnet"):
        lines += ["telnet server enable", "#"]
    if acc.get("ssh"):
        lines += ["stelnet server enable", "#"]
    if acc.get("users"):
        import hashlib
        lines.append("aaa")
        for u, pw in acc["users"].items():
            lines.append(f" local-user {u} password irreversible-cipher $1a${hashlib.sha256(pw.encode()).hexdigest()[:24]}$")
            svc = acc.get("services", {}).get(u)
            if svc:
                lines.append(f" local-user {u} service-type " + " ".join(svc))
        lines.append("#")
    if cfg.get("ipv6_routing"):
        lines += ["ipv6", "#"]
    for name, a in cfg.get("acls", {}).items():
        from ..security.acl import Acl
        acl = Acl.from_dict(a)
        lines.append(f"acl number {name}")
        lines += [f" rule {r.seq} {vrp_rule_text(r, acl.kind == 'advanced')}" for r in acl.rules]
        lines.append("#")
    for k, (a, b, _) in (cfg.get("nat") or {}).get("pools", {}).items():
        lines += [f"nat address-group {k} {a} {b}", "#"]
    if cfg.get("extra", {}).get("dhcp_enable"):
        lines += ["dhcp enable", "#"]
    for pool in (cfg.get("dhcp_server") or {}).get("pools", []):
        lines.append(f"ip pool {pool['name']}")
        if pool.get("gateway"):
            lines.append(f" gateway-list {pool['gateway']}")
        if pool.get("network"):
            n = IPv4Network(pool["network"])
            lines.append(f" network {n.network_address} mask {n.netmask}")
        for lo, hi in (cfg.get("dhcp_server") or {}).get("excluded", []):
            if pool.get("network") and IPv4Address(lo) in IPv4Network(pool["network"]):
                lines.append(f" excluded-ip-address {lo}" + (f" {hi}" if hi and hi != lo else ""))
        if pool.get("dns"):
            lines.append(f" dns-list {pool['dns']}")
        lines.append("#")
    stp = cfg.get("stp")
    if device.category == "switch" and stp:
        if stp.get("mode", "mstp") != "mstp":
            lines.append(f"stp mode {stp['mode']}")
        if stp.get("priority", 32768) != 32768:
            lines.append(f"stp priority {stp['priority']}")
        if not stp.get("enabled", True):
            lines.append("stp disable")
        lines.append("#")
    vlans = sorted(int(k) for k in cfg.get("vlans", {}) if int(k) != 1)
    if vlans:
        lines += [f"vlan batch {' '.join(map(str, vlans))}", "#"]
    for name, ic in cfg.get("interfaces", {}).items():
        lines.append(f"interface {name}")
        if ic.get("description"):
            lines.append(f" description {ic['description']}")
        if not ic.get("admin_up", True):
            lines.append(" shutdown")
        if ic.get("parent") and ic.get("vlan") is not None:
            lines.append(f" dot1q termination vid {ic['vlan']}")
        if ic.get("tunnel"):
            lines.append(" tunnel-protocol gre")
            if ic.get("source"):
                lines.append(f" source {ic['source']}")
            if ic.get("destination"):
                lines.append(f" destination {ic['destination']}")
        if ic.get("channel") and device.channel_for_group(ic.get("group")) is not None:
            hm = getattr(device.channel_for_group(ic["group"]), "hw_mode", "on")
            if hm == "active":
                lines.append(" mode lacp")
        if ic.get("channel_group") is not None:
            lines.append(f" eth-trunk {ic['channel_group']}")
        for g in cfg.get("fhrp", []):
            if g["port"] == name and g["proto"] == "vrrp":
                if g.get("vip"):
                    lines.append(f" vrrp vrid {g['group']} virtual-ip {g['vip']}")
                if g.get("priority", 100) != 100:
                    lines.append(f" vrrp vrid {g['group']} priority {g['priority']}")
                if g.get("preempt") is False:
                    lines.append(f" vrrp vrid {g['group']} preempt-mode disable")
        l3 = router or ic.get("svi") or ic.get("loopback")
        if l3 and ic.get("ip"):
            i = IPv4Interface(ic["ip"])
            lines.append(f" ip address {i.ip} {i.netmask}")
            for a2 in ic.get("ip2", []):
                i2 = IPv4Interface(a2)
                lines.append(f" ip address {i2.ip} {i2.netmask} sub")
        if l3 and (ic.get("ipv6") or ic.get("ipv6_enabled")):
            lines.append(" ipv6 enable")
            lines += [f" ipv6 address {a.upper()}" for a in ic.get("ipv6", [])]
            if ic.get("ra") is True:
                lines.append(" undo ipv6 nd ra halt")
        if ic.get("parent") and ic.get("arp_broadcast", True):
            lines.append(" arp broadcast enable")
        dh = cfg.get("dhcp_server") or {}
        if name in (dh.get("interfaces") or []):
            lines.append(" dhcp select global")
        if name in cfg.get("extra", {}).get("dhcp_relay_ifs", []):
            lines.append(" dhcp select relay")
            lines += [f" dhcp relay server-ip {h}" for h in ic.get("helpers", [])]
        for r in (cfg.get("nat") or {}).get("rules", []):
            if r["kind"] == "dynamic" and r.get("out_iface") == name:
                tail = f" {r['text']}" if r.get("text") else ""
                lines.append(f" nat outbound {r['acl']}{tail}" + ("" if r.get("overload") or not tail else " no-pat"))
            elif r["kind"] == "static" and r.get("out_iface") == name:
                lines.append(f" nat static global {r['glob']} inside {r['local']}")
            elif r["kind"] == "dnat" and r.get("in_iface") == name:
                lines.append(f" nat server protocol {r['proto']} global {r['glob']} {r['global_port']} "
                             f"inside {r['local']} {r['local_port']}")
        if ic.get("urpf"):
            lines.append(f" urpf {ic['urpf']}")
        if ic.get("acl_in"):
            lines.append(f" traffic-filter inbound acl {ic['acl_in']}")
        if ic.get("acl_out"):
            lines.append(f" traffic-filter outbound acl {ic['acl_out']}")
        isis_c = cfg.get("isis") or {}
        if name in isis_c.get("interfaces", []):
            lines.append(f" isis enable {isis_c.get('tag', '1')}")
        if name in isis_c.get("interfaces6", []):
            lines.append(f" isis ipv6 enable {isis_c.get('tag', '1')}")
        if name in isis_c.get("metrics", {}):
            lines.append(f" isis cost {isis_c['metrics'][name]}")
        ospf_c = cfg.get("ospf") or {}
        if name in ospf_c.get("costs", {}):
            lines.append(f" ospf cost {int(ospf_c['costs'][name])}")
        if name in ospf_c.get("hello", {}):
            lines.append(f" ospf timer hello {int(ospf_c['hello'][name])}")
        if name in ospf_c.get("dead", {}):
            lines.append(f" ospf timer dead {int(ospf_c['dead'][name])}")
        if name in ospf_c.get("priority", {}):
            lines.append(f" ospf dr-priority {int(ospf_c['priority'][name])}")
        if not router and "mode" in ic:
            if ic["mode"] == "trunk":
                lines.append(" port link-type trunk")
                if ic.get("native_vlan", 1) != 1:
                    lines.append(f" port trunk pvid vlan {ic['native_vlan']}")
                tagged = ic.get("tagged")
                if tagged is None:
                    lines.append(" port trunk allow-pass vlan all")
                else:
                    extra = [v for v in tagged if v != 1]
                    if extra:
                        lines.append(f" port trunk allow-pass vlan {' '.join(map(str, extra))}")
                    if 1 not in tagged:
                        lines.append(" undo port trunk allow-pass vlan 1")
            else:
                lines.append(" port link-type access")
                if ic.get("access_vlan", 1) != 1:
                    lines.append(f" port default vlan {ic['access_vlan']}")
        lines.append("#")
    for s in cfg.get("static_routes", []):
        n = IPv4Network(s["network"])
        parts = [f"ip route-static {n.network_address} {n.netmask}"]
        if s.get("interface"):
            parts.append("NULL0" if s["interface"] == "Null0" else s["interface"])
        if s.get("next_hop"):
            parts.append(s["next_hop"])
        if int(s.get("distance", HUAWEI_STATIC_PREF)) != HUAWEI_STATIC_PREF:
            parts.append(f"preference {s['distance']}")
        lines.append(" ".join(parts))
    for s in cfg.get("static_routes6", []):
        n6 = s["network"].split("/")
        lines.append(f"ipv6 route-static {n6[0].upper()} {n6[1]} {(s.get('next_hop') or '').upper()}".rstrip())
    rip = cfg.get("rip")
    if rip and rip.get("enabled", True):
        lines += ["rip 1", f" version {rip.get('version', 2)}", " undo summary"]
        if rip.get("default_originate"):
            lines.append(" default-route originate")
        for x in rip.get("passive", []):
            lines.append(f" silent-interface {x}")
        for proto in rip.get("redistribute", []):
            lines.append(f" import-route {'direct' if proto == 'connected' else proto}" + (" 1" if proto == "ospf" else ""))
        for n in rip.get("networks", []):
            lines.append(f" network {IPv4Network(n).network_address}")
        lines.append("#")
    bgp = cfg.get("bgp")
    if bgp and bgp.get("asn"):
        lines.append(f"bgp {bgp['asn']}")
        if bgp.get("router_id"):
            lines.append(f" router-id {bgp['router_id']}")
        for ip, nc in bgp.get("neighbors", {}).items():
            lines.append(f" peer {ip} as-number {nc.get('remote_as')}")
            if nc.get("description"):
                lines.append(f" peer {ip} description {nc['description']}")
            if nc.get("update_source"):
                lines.append(f" peer {ip} connect-interface {nc['update_source']}")
            if nc.get("multihop"):
                lines.append(f" peer {ip} ebgp-max-hop {nc['multihop']}")
            if nc.get("shutdown"):
                lines.append(f" peer {ip} ignore")
        lines += [" #", " ipv4-family unicast", "  undo synchronization"]
        for net in bgp.get("networks", []):
            n = IPv4Network(net)
            lines.append(f"  network {n.network_address} {n.netmask}")
        for proto in bgp.get("redistribute", []):
            lines.append(f"  import-route {'direct' if proto == 'connected' else proto}" + (" 1" if proto == "ospf" else ""))
        for ip, nc in bgp.get("neighbors", {}).items():
            lines.append(f"  peer {ip} enable")
            for key, word in (("next_hop_self", "next-hop-local"), ("rr_client", "reflect-client"),
                              ("default_originate", "default-route-advertise")):
                if nc.get(key):
                    lines.append(f"  peer {ip} {word}")
            if nc.get("weight"):
                lines.append(f"  peer {ip} preferred-value {nc['weight']}")
        lines.append("#")
    isis_c = cfg.get("isis")
    if isis_c:
        lines += [f"isis {isis_c.get('tag', '1')}", " is-level level-2", " cost-style wide"]
        if isis_c.get("net"):
            lines.append(f" network-entity {isis_c['net']}")
        if isis_c.get("interfaces6"):
            lines.append(" ipv6 enable")
        lines.append("#")
    ospf = cfg.get("ospf")
    if ospf and ospf.get("enabled", True):
        head = f"ospf {ospf.get('process_id', 1)}"
        if ospf.get("router_id"):
            head += f" router-id {ospf['router_id']}"
        lines.append(head)
        if ospf.get("default_originate"):
            lines.append(" default-route-advertise" + (" always" if ospf["default_originate"] == "always" else ""))
        for proto in ospf.get("redistribute", []):
            lines.append(f" import-route {'direct' if proto == 'connected' else proto}")
        if ospf.get("passive_default"):
            lines.append(" silent-interface all")
        lines += [f" silent-interface {x}" for x in ospf.get("passive", [])]
        if ospf.get("reference_bandwidth", 100) != 100:
            lines.append(f" bandwidth-reference {ospf['reference_bandwidth']}")
        areas: dict[str, list] = {}
        for n in ospf.get("networks", []):
            areas.setdefault(n["area"], []).append(IPv4Network(n["prefix"]))
        for area, nets in sorted(areas.items()):
            lines.append(f" area {area}")
            lines += [f"  network {net.network_address} {IPv4Address(int(net.hostmask))}" for net in nets]
        lines.append("#")
    if cfg.get("static_routes"):
        lines.append("#")
    if acc:
        ui = ["user-interface vty 0 4", f" authentication-mode {acc.get('login', 'aaa')}"]
        if acc.get("vty_password"):
            ui.append(" set authentication password cipher %^%#netemu%^%#")
        if acc.get("protocols", "all") != "all":
            ui.append(f" protocol inbound {acc['protocols']}")
        lines += ui + ["#"]
    lines += ["user-interface con 0", " authentication-mode password", "#", "return"]
    return "\n".join(lines)


def _vrp_addr(words, i):
    from ..security.acl import wildcard_net
    if i < len(words) and words[i] == "any":
        return IPv4Network("0.0.0.0/0"), i + 1
    if i + 1 < len(words):
        wc = words[i + 1]
        if wc == "0":
            wc = "0.0.0.0"
        return wildcard_net(words[i], wc), i + 2
    raise ValueError(i)


def parse_vrp_rule(action: str, text: str, advanced: bool):
    """VRP ACL rule conditions: 'source 192.168.1.0 0.0.0.255', 'icmp destination 8.8.8.8 0 icmp-type echo'."""
    from ..security.acl import ICMP_TYPES, AclRule
    words = text.split()
    rule = AclRule(action)
    i = 0
    if advanced:
        if not words or words[0] not in ("ip", "icmp", "tcp", "udp", "ospf"):
            raise ValueError(0)
        rule.proto = words[0]
        i = 1
    while i < len(words):
        w = words[i]
        if w == "source":
            rule.src, i = _vrp_addr(words, i + 1)
        elif w == "destination" and advanced:
            rule.dst, i = _vrp_addr(words, i + 1)
        elif w in ("source-port", "destination-port") and advanced:
            if i + 2 >= len(words):
                raise ValueError(i)
            op, val = words[i + 1], words[i + 2]
            if op not in ("eq", "gt", "lt") or not val.isdigit():
                raise ValueError(i)
            setattr(rule, "sport" if w == "source-port" else "dport", (op, int(val)))
            i += 3
        elif w == "icmp-type" and advanced and i + 1 < len(words) and words[i + 1] in ICMP_TYPES:
            rule.icmp_type = ICMP_TYPES[words[i + 1]]
            i += 2
        else:
            raise ValueError(i)
    return rule


def vrp_rule_text(r, advanced: bool) -> str:
    def addr(net):
        if net.prefixlen == 32:
            return f"{net.network_address} 0"
        return f"{net.network_address} {IPv4Address(int(net.hostmask))}"
    parts = [r.action]
    if advanced:
        parts.append(r.proto)
    if r.src.prefixlen:
        parts.append(f"source {addr(r.src)}")
    if advanced and r.dst.prefixlen:
        parts.append(f"destination {addr(r.dst)}")
    if r.sport:
        parts.append(f"source-port {r.sport[0]} {r.sport[1]}")
    if r.dport:
        parts.append(f"destination-port {r.dport[0]} {r.dport[1]}")
    if r.icmp_type is not None:
        parts.append("icmp-type " + {8: "echo", 0: "echo-reply", 3: "unreachable", 11: "time-exceeded"}
                     .get(r.icmp_type, str(r.icmp_type)))
    return " ".join(parts)


def hms_vrp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}h{s // 60 % 60:02d}m{s % 60:02d}s"
