"""ExtremeXOS (EXOS) dialect for Extreme Networks switches (X440-G2 style).

EXOS has a flat command set (no configuration modes): every change is made with
``create`` / ``configure`` / ``enable`` / ``disable`` / ``unconfigure`` / ``delete``
and stays in the running configuration until ``save``.  VLANs are referenced by
name, ports by number (``1``, ``1-4,7``), routing interfaces are the VLANs' IP
addresses (``configure vlan v10 ipaddress ...``) and routing between them needs
``enable ipforwarding``."""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from ..addresses import is_valid_ip, is_valid_mask
from ..stack.icmp import PingListener, TraceListener
from . import parser as P
from .base import TreeSession, make_ping, overlapping_port, make_trace


def _mac(m) -> str:
    h = f"{m.value:012x}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _dur(ms: float) -> str:
    s = int(ms / 1000)
    return f"{s // 86400}d:{s // 3600 % 24}h:{s // 60 % 60}m:{s % 60}s"


class ExosPing(PingListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.dst = s.dst
        self.w(f"Ping(ICMP) {s.dst}: {s.count} packets, {s.size} data bytes, interval 1 second(s).\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w(f"{size + 8} bytes from {src}: icmp_seq={seq} ttl={ttl} time={max(rtt, 0.05):.3f} ms\n")

    def on_error(self, seq, kind, src):
        self.w(f"From {src}: icmp_seq={seq} {'Destination Host Unreachable' if kind == 'unreachable' else 'Time to live exceeded'}\n")

    def on_send_fail(self, seq):
        self.w("ping: sendto: Network is unreachable\n")

    def on_finish(self, st):
        loss = st.loss_percent
        self.w(f"\n--- ping statistics ---\n{st.sent} packets transmitted, {st.received} packets received, {loss}% loss\n")
        if st.rtts:
            self.w(f"round-trip min/avg/max = {min(st.rtts):.3f}/{sum(st.rtts) / len(st.rtts):.3f}/{max(st.rtts):.3f} ms\n")


class ExosTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f"traceroute to {s.dst}, {s.max_hops} hops max\n")

    def on_hop(self, hop):
        if hop.address is None:
            self.w(f"{hop.ttl:<3}*  *  *\n")
            return
        times = "  ".join("*" if r is None else f"{max(r, 0.05):.3f} ms" for r in hop.probes)
        self.w(f"{hop.ttl:<3}{str(hop.address):<16}{times}{' !H' if hop.unreachable else ''}\n")


def parse_ports(text: str, device) -> list | None:
    """'1-4,7' / 'all' -> list of Port objects (None when a port does not exist).

    A load-sharing master port number stands for its whole LAG."""
    phys = device.l2_ports()
    if text.lower() == "all":
        return phys
    out = []
    for part in text.split(","):
        if "-" in part:
            a, _, b = part.partition("-")
            if not (a.isdigit() and b.isdigit()) or int(a) > int(b):
                return None
            nums = range(int(a), int(b) + 1)
        elif part.isdigit():
            nums = [int(part)]
        else:
            return None
        for n in nums:
            lag = device.channel_for_group(n)
            p = lag if lag is not None else device.ports.get(str(n))
            if p is None or p.is_svi:
                return None
            if p.channel_group is not None and device.channel_for_group(p.channel_group) is not None:
                continue                                   # member of a LAG: configured through the master
            if p not in out:
                out.append(p)
    return out


def port_ranges(ports) -> str:
    nums = sorted(p.group if p.is_channel else int(p.name) for p in ports)
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


class ExosSession(TreeSession):
    def __init__(self, device):
        self.counter = 1
        self.dirty = False
        super().__init__(device)
        self.mode = "user"

    @property
    def prompt(self) -> str:
        return f"{'* ' if self.dirty else ''}{self.device.hostname}.{self.counter} # "

    def banner(self) -> str:
        return ("\nExtreme Networks, Inc.\nExtremeXOS (X440G2-24t-10G4) version 31.7.1.4 31.7.1.4-patch1-4\n"
                "Copyright (C) 1996-2024 Extreme Networks. All rights reserved.\n")

    def err_invalid(self, prompt, pos):
        return " " * (len(prompt) + pos) + "^\n%% Invalid input detected at '^' marker.\n"

    def err_ambiguous(self, prompt, pos, token):
        return " " * (len(prompt) + pos) + f"^\n%% Ambiguous command: \"{token}\"\n"

    def err_incomplete(self, prompt, pos):
        return " " * (len(prompt) + pos) + "^\n%% Incomplete command\n"

    CHANGES = ("cre", "con", "del", "ena", "dis", "unc")

    def _execute(self, line: str):
        out = super()._execute(line)
        if line.strip():
            self.counter += 1
            word = line.strip().split()[0].lower()
            if word.startswith(self.CHANGES) and not (isinstance(out, str) and "%%" in out):
                self.dirty = True
        return out

    # -- trees ---------------------------------------------------------------------------------
    def build_trees(self) -> None:
        name = lambda h="VLAN name": P.WordArg("<vlan_name>")
        plist = lambda: P.WordArg("<port_list>")
        vlan_cfg = P.arg(name(), "VLAN name",
                         P.kw("tag", "802.1Q tag", P.arg(P.NumArg(1, 4094, "<tag>"), "VLAN tag",
                                                          handler=lambda s, a, n: s.vlan_tag(a[0], a[1]))),
                         P.kw("add", "Add ports",
                              P.kw("ports", "Ports",
                                   P.arg(plist(), "Port list",
                                         P.kw("tagged", "Tagged", handler=lambda s, a, n: s.vlan_ports(a[0], a[1], "tagged")),
                                         P.kw("untagged", "Untagged",
                                              handler=lambda s, a, n: s.vlan_ports(a[0], a[1], "untagged")),
                                         handler=lambda s, a, n: s.vlan_ports(a[0], a[1], "untagged")))),
                         P.kw("delete", "Delete ports",
                              P.kw("ports", "Ports",
                                   P.arg(plist(), "Port list", handler=lambda s, a, n: s.vlan_ports(a[0], a[1], "delete")))),
                         P.kw("ipaddress", "IP address of the VLAN router interface",
                              P.arg(P.IpArg(), "IP address",
                                    P.arg(P.MaskArg(), "Subnet mask", handler=lambda s, a, n: s.vlan_ip(a[0], a[1], a[2]))),
                              P.arg(P.PrefixArg("<ipaddress>/<mask_length>"), "IP address/prefix",
                                    handler=lambda s, a, n: s.vlan_ip(a[0], a[1], None)),
                              P.arg(P.Ipv6PrefixArg(), "IPv6 address/prefix",
                                    handler=lambda s, a, n: s.vlan_ip6(a[0], a[1]))),
                         P.kw("name", "Rename the VLAN",
                              P.arg(P.WordArg("<new_name>"), "New name", handler=lambda s, a, n: s.vlan_rename(a[0], a[1]))))
        gw = lambda: P.arg(P.IpArg(), "Gateway", handler=None)
        iproute = P.kw("iproute", "IP routes",
                       *[P.kw(verb, f"{verb.capitalize()} a static route",
                              P.kw("default", "Default route",
                                   P.arg(P.IpArg(), "Gateway", handler=lambda s, a, n, v=verb: s.iproute(v, "0.0.0.0/0", a[0]))),
                              P.arg(P.PrefixArg("<ipNetmask>"), "Destination prefix",
                                    P.arg(P.IpArg(), "Gateway", handler=lambda s, a, n, v=verb: s.iproute(v, a[0], a[1]))),
                              P.arg(P.IpArg(), "Destination",
                                    P.arg(P.MaskArg(), "Mask",
                                          P.arg(P.IpArg(), "Gateway",
                                                handler=lambda s, a, n, v=verb: s.iproute(v, f"{a[0]}/{a[1]}", a[2])))))
                         for verb in ("add", "delete")])
        _ = gw
        stpd = P.kw("stpd", "Spanning tree domain",
                    P.arg(P.WordArg("s0"), "STP domain name",
                          P.kw("mode", "Protocol mode",
                               *[P.kw(m, h, handler=lambda s, a, n, m=m: s.stp_mode(m))
                                 for m, h in (("dot1d", "802.1D STP"), ("dot1w", "802.1w RSTP"), ("mstp", "802.1s MSTP"))]),
                          P.kw("priority", "Bridge priority",
                               P.arg(P.NumArg(0, 61440, "<priority>"), "Priority (multiple of 4096)",
                                     handler=lambda s, a, n: s.stp_priority(a[1]))),
                          P.kw("ports", "Port settings",
                               P.kw("link-type", "Link type",
                                    P.kw("edge", "Edge port", P.arg(plist(), "Port list",
                                                                     handler=lambda s, a, n: s.stp_edge(a[1], True))),
                                    P.kw("point-to-point", "Point-to-point link",
                                         P.arg(plist(), "Port list", handler=lambda s, a, n: s.stp_edge(a[1], False)))),
                               P.kw("cost", "Path cost",
                                    P.arg(P.NumArg(1, 200000000, "<cost>"), "Cost",
                                          P.arg(plist(), "Port list", handler=lambda s, a, n: s.stp_cost(a[1], a[2])))))))
        ospf_cfg = P.kw("ospf", "OSPF",
                        P.kw("routerid", "Router ID",
                             P.arg(P.IpArg(), "Router ID", handler=lambda s, a, n: s.ospf_rid(a[0]))),
                        P.kw("add", "Add an interface",
                             P.kw("vlan", "VLAN",
                                  P.arg(name(), "VLAN name",
                                        P.kw("area", "Area",
                                             P.arg(P.WordArg("<area_identifier>"), "Area ID",
                                                   P.kw("passive", "Passive interface",
                                                        handler=lambda s, a, n: s.ospf_vlan(a[0], a[1], True)),
                                                   handler=lambda s, a, n: s.ospf_vlan(a[0], a[1], False)))))),
                        P.kw("delete", "Delete an interface",
                             P.kw("vlan", "VLAN", P.arg(name(), "VLAN name",
                                                        handler=lambda s, a, n: s.ospf_vlan(a[0], None, False)))))
        configure = P.kw("configure", "Configure a feature",
                         P.kw("vlan", "VLAN", vlan_cfg),
                         iproute, stpd, ospf_cfg,
                         P.kw("ports", "Ports",
                              P.arg(plist(), "Port list",
                                    P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                         P.kw("limit-learning", "Limit MAC learning",
                                              P.arg(P.NumArg(1, 500000, "<number>"), "Number of MACs",
                                                    P.arg(P.LineArg("action blackhole|stop-learning"), "",
                                                          handler=lambda s, a, n: s.limit_learning(a[0], a[2])),
                                                    handler=lambda s, a, n: s.limit_learning(a[0], a[2]))))),
                                    P.kw("display-string", "Port description",
                                         P.arg(P.LineArg("<string>"), "Description",
                                               handler=lambda s, a, n: s.port_desc(a[0], a[1]))))),
                         P.kw("account", "User account",
                              P.arg(P.WordArg("<name>"), "Account name",
                                    P.kw("password", "Change the password",
                                         handler=lambda s, a, n: s.account_password(a[0])))),
                         P.kw("ospfv3", "OSPFv3",
                              P.kw("routerid", "Router ID", P.arg(P.IpArg(), "Router ID",
                                                                  handler=lambda s, a, n: s.v6igp_rid(a[0]))),
                              P.kw("add", "Add", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                   P.kw("area", "Area", P.arg(P.WordArg("<area>"), "Area ID",
                                                              handler=lambda s, a, n: s.v6igp_vlan("ospf6", a[0], a[1], True)))))),
                              P.kw("delete", "Delete", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                   handler=lambda s, a, n: s.v6igp_vlan("ospf6", a[0], None, False))))),
                         P.kw("ripng", "RIPng",
                              P.kw("add", "Add", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                   handler=lambda s, a, n: s.v6igp_vlan("ripng", a[0], None, True)))),
                              P.kw("delete", "Delete", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                                   handler=lambda s, a, n: s.v6igp_vlan("ripng", a[0], None, False))))),
                         P.kw("trusted-ports", "DHCP snooping trusted ports",
                              P.arg(P.WordArg("<port_list>"), "Port list",
                                    P.kw("trust-for", "Trust for", P.kw("dhcp-server", "DHCP server",
                                         handler=lambda s, a, n: s.snoop_trust(a[0], True))))),
                         P.kw("vrrp", "VRRP", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                              P.kw("vrid", "VRID", P.arg(P.NumArg(1, 255, "<vrid>"), "VRID",
                                   P.kw("priority", "Priority", P.arg(P.NumArg(1, 254, "<priority>"), "Priority",
                                        handler=lambda s, a, n: s.vrrp_cfg(a[0], a[1], "priority", a[2]))),
                                   P.kw("add", "Add a virtual IP", P.arg(P.IpArg(), "Virtual IP",
                                        handler=lambda s, a, n: s.vrrp_cfg(a[0], a[1], "vip", a[2]))),
                                   P.kw("preempt", "Preempt", handler=lambda s, a, n: s.vrrp_cfg(a[0], a[1], "preempt", True)),
                                   P.kw("dont-preempt", "Do not preempt",
                                        handler=lambda s, a, n: s.vrrp_cfg(a[0], a[1], "preempt", False))))))),
                         P.kw("snmp", "SNMP",
                              P.kw("sysName", "System name",
                                   P.arg(P.WordArg("<name>"), "System name", handler=lambda s, a, n: s.sysname(a[0])))))
        fwd = P.kw("ipforwarding", "IP forwarding (routing between VLANs)",
                   P.kw("vlan", "VLAN", P.arg(name(), "VLAN name", handler=lambda s, a, n: None)),
                   handler=lambda s, a, n: None)

        def onoff(on):
            return [
                P.kw("ipforwarding", "IP forwarding (routing between VLANs)",
                     P.kw("vlan", "VLAN", P.arg(name(), "VLAN name", handler=lambda s, a, n: s.ipfwd(on, a[0]))),
                     P.kw("ipv6", "IPv6 forwarding", handler=lambda s, a, n: s.ipfwd6(on)),
                     handler=lambda s, a, n: s.ipfwd(on, None)),
                P.kw("ospfv3", "OSPFv3", handler=lambda s, a, n: s.v6igp_onoff("ospf6", on)),
                P.kw("ripng", "RIPng", handler=lambda s, a, n: s.v6igp_onoff("ripng", on)),
                P.kw("ip-security", "IP security",
                     P.kw("dhcp-snooping", "DHCP snooping",
                          P.kw("vlan", "VLAN", P.arg(name(), "VLAN name",
                               P.arg(P.LineArg("ports all violation-action drop-packet"), "Options",
                                     handler=lambda s, a, n: s.snoop_vlan(a[0], on)),
                               handler=lambda s, a, n: s.snoop_vlan(a[0], on))))),
                P.kw("ports", "Ports", P.arg(plist(), "Port list", handler=lambda s, a, n: s.ports_admin(a[0], on))),
                P.kw("ospf", "OSPF", handler=lambda s, a, n: s.ospf_onoff(on)),
                P.kw("stpd", "Spanning tree", P.arg(P.WordArg("s0"), "STP domain", handler=lambda s, a, n: s.stp_onoff(on)),
                     handler=lambda s, a, n: s.stp_onoff(on)),
                P.kw("ssh2", "SSH server", handler=lambda s, a, n: s.service("ssh", on)),
                P.kw("vrrp", "VRRP",
                     P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                          P.kw("vrid", "VRID", P.arg(P.NumArg(1, 255, "<vrid>"), "VRID",
                                                     handler=lambda s, a, n: s.vrrp_onoff(on, a[0], a[1]))))),
                     handler=lambda s, a, n: s.vrrp_onoff(on, None, None)),
                P.kw("sharing", "Link aggregation (load sharing)",
                     P.arg(P.NumArg(1, 64, "<master_port>"), "Master port",
                           *([P.kw("grouping", "Member ports",
                                   P.arg(P.WordArg("<port_list>"), "Port list",
                                         P.arg(P.LineArg("algorithm address-based L2 lacp"), "Options",
                                               handler=lambda s, a, n: s.sharing(a[0], a[1], a[2])),
                                         handler=lambda s, a, n: s.sharing(a[0], a[1], "")))] if on else []),
                           handler=(lambda s, a, n: s.sharing_off(a[0])) if not on else None)),
                P.kw("telnet", "Telnet server", handler=lambda s, a, n: s.service("telnet", on)),
            ]
        _ = fwd
        show = P.kw("show", "Show information",
                    P.kw("vlan", "VLAN information", P.arg(name(), "VLAN name", handler=lambda s, a, n: s.show_vlan(a[0])),
                         handler=lambda s, a, n: s.show_vlan(None)),
                    P.kw("ports", "Port information",
                         P.kw("no-refresh", "Static output", handler=lambda s, a, n: s.show_ports()),
                         P.kw("information", "Port configuration", handler=lambda s, a, n: s.show_ports()),
                         handler=lambda s, a, n: s.show_ports()),

                    P.kw("iproute", "IP routing table", handler=lambda s, a, n: s.show_iproute()),
                    P.kw("iparp", "ARP table", handler=lambda s, a, n: s.show_iparp()),
                    P.kw("ipconfig", "IP configuration", handler=lambda s, a, n: s.show_ipconfig()),
                    P.kw("configuration", "Current configuration", handler=lambda s, a, n: s.show_config()),
                    P.kw("switch", "Switch information", handler=lambda s, a, n: s.show_switch()),
                    P.kw("version", "Software version", handler=lambda s, a, n: s.show_version()),
                    P.kw("sharing", "Load sharing groups", handler=lambda s, a, n: s.show_sharing()),
                    P.kw("vrrp", "VRRP status", handler=lambda s, a, n: s.show_vrrp()),
                    P.kw("ospfv3", "OSPFv3", P.kw("neighbor", "Neighbors", handler=lambda s, a, n: s.show_v6nb("ospf6"))),
                    P.kw("ripng", "RIPng", handler=lambda s, a, n: s.show_v6nb("ripng")),
                    P.kw("ip-security", "IP security", P.kw("dhcp-snooping", "DHCP snooping",
                         P.kw("entries", "Binding entries", handler=lambda s, a, n: s.show_snoop()))),
                    P.kw("fdb", "Forwarding database (MAC table)",
                         P.kw("limit-learning", "Learning limits", handler=lambda s, a, n: s.show_limits()),
                         handler=lambda s, a, n: s.show_fdb()),
                    P.kw("ospf", "OSPF information",
                         P.kw("neighbor", "OSPF neighbors", handler=lambda s, a, n: s.show_ospf_neighbor()),
                         handler=lambda s, a, n: s.show_ospf()),
                    P.kw("stpd", "Spanning tree",
                         P.arg(P.WordArg("s0"), "STP domain",
                               P.kw("ports", "Port states", handler=lambda s, a, n: s.show_stpd_ports()),
                               handler=lambda s, a, n: s.show_stpd()),
                         handler=lambda s, a, n: s.show_stpd()))
        root = [
            P.kw("create", "Create a feature",
                 P.kw("vrrp", "VRRP", P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "VLAN name",
                      P.kw("vrid", "Virtual router ID", P.arg(P.NumArg(1, 255, "<vrid>"), "VRID",
                                                               handler=lambda s, a, n: s.vrrp_create(a[0], a[1])))))),
                 P.kw("account", "User account",
                      P.kw("admin", "Administrator account",
                           P.arg(P.WordArg("<name>"), "Account name",
                                 P.arg(P.WordArg("<password>"), "Password", handler=lambda s, a, n: s.account(a[0], a[1])),
                                 handler=lambda s, a, n: s.account(a[0], "")))),
                 P.kw("vlan", "VLAN",
                      P.arg(name(), "VLAN name",
                            P.kw("tag", "802.1Q tag", P.arg(P.NumArg(1, 4094, "<tag>"), "VLAN tag",
                                                             handler=lambda s, a, n: s.create_vlan(a[0], a[1]))),
                            handler=lambda s, a, n: s.create_vlan(a[0], None)))),
            P.kw("delete", "Delete a feature",
                 P.kw("account", "User account", P.arg(P.WordArg("<name>"), "Account name",
                                                        handler=lambda s, a, n: s.account(a[0], None))),
                 P.kw("vlan", "VLAN", P.arg(name(), "VLAN name", handler=lambda s, a, n: s.delete_vlan(a[0])))),
            configure,
            P.kw("unconfigure", "Remove configuration",
                 P.kw("vlan", "VLAN",
                      P.arg(name(), "VLAN name",
                            P.kw("ipaddress", "Remove the IP address", handler=lambda s, a, n: s.vlan_ip(a[0], None, None)))),
                 P.kw("switch", "Reset the switch",
                      P.kw("all", "All configuration", handler=lambda s, a, n: s.unconfigure_all()))),
            P.kw("enable", "Enable a feature", *onoff(True)),
            P.kw("disable", "Disable a feature", *onoff(False)),
            show,
            P.kw("save", "Save the configuration",
                 P.kw("configuration", "Configuration",
                      P.kw("primary", "Primary configuration", handler=lambda s, a, n: s.save()),
                      handler=lambda s, a, n: s.save()),
                 handler=lambda s, a, n: s.save()),
            P.kw("ping", "Send ICMP echo",
                 P.kw("count", "Number of packets",
                      P.arg(P.NumArg(1, 10000, "<count>"), "Count",
                            P.arg(P.AnyIpArg("<host>"), "Destination", handler=lambda s, a, n: s.ping(a[1], a[0])))),
                 P.arg(P.AnyIpArg("<host>"), "Destination", handler=lambda s, a, n: s.ping(a[0], 4))),
            P.kw("traceroute", "Trace the route", P.arg(P.AnyIpArg("<host>"), "Destination",
                                                       handler=lambda s, a, n: s.traceroute(a[0]))),
            P.kw("clear", "Clear tables",
                 P.kw("fdb", "MAC table", handler=lambda s, a, n: s.device.clear_mac_table()),
                 P.kw("iparp", "ARP table", handler=lambda s, a, n: s.device.arp.clear())),
            P.kw("ssh2", "SSH client",
                 P.arg(P.WordArg("<user>@<host>"), "user@host", handler=lambda s, a, n: s.remote("ssh", a[0]))),
            P.kw("telnet", "Telnet client",
                 P.arg(P.IpArg("<host>"), "Destination", handler=lambda s, a, n: s.remote("telnet", a[0]))),
            P.kw("exit", "Log out", handler=lambda s, a, n: None),
            P.kw("logout", "Log out", handler=lambda s, a, n: None),
            P.kw("quit", "Log out", handler=lambda s, a, n: None),
        ]
        self._add_mgmt(root)
        root.sort(key=lambda x: x.word)
        self._trees = {"user": root}

    def _add_mgmt(self, root):
        from .base import mgmt_add, mgmt_ntp_assoc, mgmt_ntp_status
        conf = next(x for x in root if x.word == "configure")
        snmp = next(x for x in conf.children if x.word == "snmp")
        e = lambda what, neg=False: (lambda s, a, n: mgmt_add(s.device, what, a[-1], neg))
        snmp.children += [
            P.kw("add", "Add", P.kw("community", "Community",
                 P.kw("readonly", "Read-only", P.arg(P.WordArg("<community>"), "", handler=e("ro"))),
                 P.kw("readwrite", "Read-write", P.arg(P.WordArg("<community>"), "", handler=e("rw"))))),
            P.kw("delete", "Delete", P.kw("community", "Community",
                 P.kw("readonly", "Read-only", P.arg(P.WordArg("<community>"), "", handler=e("ro", True))),
                 P.kw("readwrite", "Read-write", P.arg(P.WordArg("<community>"), "", handler=e("rw", True))))),
            P.kw("sysContact", "System contact", P.arg(P.LineArg("<contact>"), "", handler=e("contact"))),
            P.kw("sysLocation", "System location", P.arg(P.LineArg("<location>"), "", handler=e("location")))]
        vlan_n = P.kw("vlan", "VLAN", P.arg(P.WordArg("<vlan_name>"), "Voice VLAN",
                                            P.kw("dscp", "DSCP", P.arg(P.NumArg(0, 63, "<dscp>"), "DSCP",
                                                                       handler=lambda s, a, n: s.lldp_voice(a[0], a[1]))),
                                            handler=lambda s, a, n: s.lldp_voice(a[0], a[1])))

        def med(voice):
            return P.kw("vendor-specific", "Vendor-specific TLVs", P.kw("med", "LLDP-MED",
                        P.kw("policy", "Network policy", P.kw("application", "Application", voice))))
        adv = P.kw("advertise", "Advertise", med(P.kw("voice", "Voice", vlan_n)))
        noadv = P.kw("no-advertise", "Stop advertising",
                     med(P.kw("voice", "Voice", handler=lambda s, a, n: s.lldp_voice(a[0], None))))
        conf.children.append(P.kw("lldp", "LLDP", P.kw("ports", "Ports",
                                                         P.arg(P.WordArg("<port_list>"), "Port list", adv, noadv))))
        ports_node = next(x for x in conf.children if x.word == "ports")
        ports_node.children[0].children.append(P.kw("rate-limit", "Rate limiting",
            P.kw("egress", "Egress rate limit",
                 P.kw("no-limit", "Remove the limit", handler=lambda s, a, n: s.rate_limit(a[0], None)),
                 P.arg(P.NumArg(64, 100000000, "<rate>"), "Rate",
                       P.kw("Kbps", "Kilobits per second", handler=lambda s, a, n: s.rate_limit(a[0], a[1] * 1000)),
                       P.kw("Mbps", "Megabits per second", handler=lambda s, a, n: s.rate_limit(a[0], a[1] * 1000000)),
                       P.kw("Gbps", "Gigabits per second",
                            handler=lambda s, a, n: s.rate_limit(a[0], a[1] * 1000000000))))))
        conf.children += [
            P.kw("syslog", "Syslog",
                 P.kw("add", "Add a syslog target", P.arg(P.IpArg("<ip>"), "Syslog host",
                      P.arg(P.WordArg("local0..local7"), "Facility", handler=lambda s, a, n: mgmt_add(s.device, "syslog", a[0])),
                      handler=e("syslog"))),
                 P.kw("delete", "Delete a syslog target", P.arg(P.IpArg("<ip>"), "Syslog host",
                      P.arg(P.WordArg("local0..local7"), "Facility",
                            handler=lambda s, a, n: mgmt_add(s.device, "syslog", a[0], True)),
                      handler=e("syslog", True)))),
            P.kw("ntp", "NTP", P.kw("server", "NTP server",
                 P.kw("add", "Add", P.arg(P.IpArg("<ip>"), "Server", handler=e("ntp"))),
                 P.kw("delete", "Delete", P.arg(P.IpArg("<ip>"), "Server", handler=e("ntp", True)))))]
        for verb in ("enable", "disable"):
            node = next(x for x in root if x.word == verb)
            node.children += [P.kw("syslog", "Remote syslog", handler=lambda s, a, n: None),
                              P.kw("ntp", "NTP client", handler=lambda s, a, n: None)]
            snmp_n = next((x for x in node.children if x.word == "snmp"), None)
            if snmp_n is None:
                node.children.append(P.kw("snmp", "SNMP", P.kw("access", "Access", handler=lambda s, a, n: None)))
        show = next(x for x in root if x.word == "show")
        show.children += [
            P.kw("ntp", "NTP", P.kw("association", "Associations", handler=lambda s, a, n: mgmt_ntp_assoc(s.device)),
                 handler=lambda s, a, n: mgmt_ntp_status(s.device)),
            P.kw("log", "Log", handler=lambda s, a, n: "\n".join(x.lstrip("*") for x in reversed(s.device.mgmt.buffer)))]

    # -- VLAN helpers --------------------------------------------------------------------------
    def _vid(self, name):
        for vid, vname in self.device.vlans.items():
            if vname.lower() == name.lower():
                return vid
        return None

    def _need_vlan(self, name):
        vid = self._vid(name)
        if vid is None:
            return None, f"Error: VLAN \"{name}\" does not exist."
        return vid, None

    def _untagged(self, p):
        vl = self.device.vlans
        if p.mode == "access":
            return p.pvid if p.pvid in vl else None
        return p.pvid if p.pvid in vl and (p.tagged is None or p.pvid in p.tagged) else None

    def _tagged(self, p):
        if p.mode != "trunk":
            return set()
        allv = set(self.device.vlans) if p.tagged is None else set(p.tagged)
        return {v for v in allv if v in self.device.vlans} - {self._untagged(p)}

    def _set_membership(self, p, untagged, tagged):
        if tagged:
            p.mode = "trunk"
            p.pvid = untagged or 0
            p.native_vlan = untagged or 0
            p.tagged = frozenset(tagged | ({untagged} if untagged else set()))
        else:
            p.mode = "access"
            p.pvid = p.access_vlan = untagged or 0
            p.tagged = None
        self.device.clear_mac_table()
        for svi in self.device.svis():
            self.device.link_state_changed(svi)
        self.device.stp.schedule()

    def _free_tag(self):
        used = set(self.device.vlans)
        return next(t for t in range(4094, 1, -1) if t not in used)

    # -- VLAN commands -------------------------------------------------------------------------
    def create_vlan(self, name, tag):
        d = self.device
        if not name[0].isalpha():
            return "Error: VLAN name must start with an alphabetic character."
        if self._vid(name) is not None:
            return f"Error: VLAN \"{name}\" already exists."
        tag = tag or self._free_tag()
        if tag in d.vlans:
            return f"Error: 802.1Q tag {tag} is assigned to VLAN {d.vlans[tag]}."
        d.vlans[tag] = name
        return None

    def delete_vlan(self, name):
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        if vid == 1:
            return "Error: Cannot delete default VLAN"
        for p in d.physical_ports():
            u, t = self._untagged(p), self._tagged(p)
            if u == vid or vid in t:
                self._set_membership(p, None if u == vid else u, t - {vid})
        svi = d.svi_for_vlan(vid)
        if svi is not None:
            d.remove_svi(svi)
        d.remove_vlan(vid)
        return None

    def vlan_tag(self, name, tag):
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        if tag == vid:
            return None
        if tag in d.vlans:
            return f"Error: 802.1Q tag {tag} is assigned to VLAN {d.vlans[tag]}."
        vname = d.vlans.pop(vid)
        d.vlans[tag] = vname
        for p in d.physical_ports():
            u, t = self._untagged(p), self._tagged(p)
            if u == vid or vid in t:
                self._set_membership(p, tag if u == vid else u, (t - {vid}) | ({tag} if vid in t else set()))
        svi = d.svi_for_vlan(vid)
        if svi is not None:
            svi.vlan = tag
        return None

    def vlan_rename(self, name, new):
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        d.vlans[vid] = new
        svi = d.svi_for_vlan(vid)
        if svi is not None:
            items = [(new if k == svi.name else k, v) for k, v in d.ports.items()]
            svi.name = svi.short = new
            d.ports = dict(items)
        return None

    def vlan_ports(self, name, plist, how):
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        ports = parse_ports(plist, d)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            u, t = self._untagged(p), self._tagged(p)
            if how == "delete":
                self._set_membership(p, None if u == vid else u, t - {vid})
            elif how == "untagged":
                if u is not None and u != vid:
                    return (f"Error: Protocol conflict when adding untagged port {p.name}.  Either remove {p.name} "
                            f"from vlan {d.vlans[u]} or change {p.name} to tagged.")
                self._set_membership(p, vid, t - {vid})
            else:
                self._set_membership(p, None if u == vid else u, t | {vid})
        return None

    def vlan_ip(self, name, ip, mask):
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        svi = d.svi_for_vlan(vid)
        if ip is None:
            if svi is None or svi.ip is None:
                return f"Error: VLAN {name} has no IP address configured."
            svi.ip = None
            d.arp.flush_port(svi)
            return None
        try:
            iface = IPv4Interface(f"{ip}/{mask}" if mask else ip)
        except ValueError:
            return "Error: Invalid IP address or mask."
        if svi is None:
            svi = d.add_svi(vid, d.vlans[vid], d.vlans[vid])
        other = overlapping_port(d, svi, iface)
        if other is not None:
            return f"Error: Subnet {iface.network} overlaps with VLAN {other.name}."
        svi.ip = iface
        d.arp.flush_port(svi)
        return None

    def port_desc(self, plist, text):
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            p.description = text.strip('"')
        return None

    def ports_admin(self, plist, on):
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            p.set_admin(on)
        return None

    def lldp_voice(self, plist, vlan_name):
        from .base import set_voice_vlan
        d = self.device
        ports = parse_ports(plist, d)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        vid = None
        if vlan_name is not None:
            vid, err = self._need_vlan(vlan_name)
            if err:
                return err
        for p in ports:
            set_voice_vlan(p, vid)
        return None

    def rate_limit(self, plist, bps):
        from ..security.qos import QosClass, QosPolicy
        d = self.device
        ports = parse_ports(plist, d)
        if not ports or any(p is None for p in ports):
            return f"Error: Invalid port list {plist}"
        for p in ports:
            name = f"rl-{p.name}"
            if bps is None:
                d.qos.pop(name, None)
                p.qos_out = None
            else:
                d.qos[name] = QosPolicy(name, [QosClass("class-default", {"any": True}, police_bps=bps)])
                p.qos_out = name
        return None

    def sysname(self, name):
        self.device.hostname = name.strip('"')
        self.device.engine.emit("device_changed", self.device)
        return None

    # -- routing -------------------------------------------------------------------------------
    def ipfwd(self, on, vlan):
        d = self.device
        if vlan is not None:
            vid, err = self._need_vlan(vlan)
            if err:
                return err
            svi = d.svi_for_vlan(vid)
            if svi is None or svi.ip is None:
                return f"Error: VLAN {vlan} has no IP address configured."
            fwd = d.extra.setdefault("exos_fwd", [])
            if on and svi.name not in fwd:
                fwd.append(svi.name)
            elif not on and svi.name in fwd:
                fwd.remove(svi.name)
            d.ip_routing = bool(fwd)
        else:
            d.extra["exos_fwd"] = [s.name for s in d.svis() if s.ip is not None] if on else []
            d.ip_routing = on and bool(d.extra["exos_fwd"])
        d.routes_changed()
        return None

    def iproute(self, verb, prefix, gw):
        d = self.device
        try:
            net = IPv4Network(prefix, strict=False)
        except ValueError:
            return "Error: Invalid IP address or mask."
        nh = IPv4Address(gw)
        if verb == "add":
            d.add_static_route(net, nh)
        elif not d.remove_static_route(net, nh):
            return "Error: Route not found."
        return None

    def ospf_rid(self, rid):
        from ..routing.ospf import OspfConfig
        d = self.device
        if d.ospf is None:
            d.enable_ospf(OspfConfig(enabled=False))
        d.ospf.cfg.router_id = rid
        d.ospf.reset_router_id()
        return None

    def ospf_vlan(self, name, area, passive):
        from ..routing.ospf import OspfConfig, area_id
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        svi = d.svi_for_vlan(vid)
        if d.ospf is None:
            d.enable_ospf(OspfConfig(enabled=False))
        ifs = d.ospf.cfg.interfaces
        if area is None:
            if svi is None or ifs.pop(svi.name, None) is None:
                return f"Error: VLAN {name} is not configured for OSPF."
        else:
            if svi is None or svi.ip is None:
                return f"Error: VLAN {name} has no IP address configured."
            try:
                a = area_id(area)
            except ValueError:
                return "Error: Invalid area identifier."
            ifs[svi.name] = {"area": a, "passive": passive}
            if passive and svi.name not in d.ospf.cfg.passive:
                d.ospf.cfg.passive.append(svi.name)
        if d.ospf.running:
            d.ospf.reconfigure()
        return None

    def ospf_onoff(self, on):
        from ..routing.ospf import OspfConfig
        d = self.device
        if d.ospf is None:
            if not on:
                return None
            d.enable_ospf(OspfConfig(enabled=False))
        cfg = d.ospf.cfg
        cfg.enabled = on
        d.enable_ospf(cfg)
        return None

    # -- STP -----------------------------------------------------------------------------------
    def stp_mode(self, m):
        self.device.stp.mode = {"dot1d": "stp", "dot1w": "rstp", "mstp": "mstp"}[m]
        self.device.stp.schedule()
        return None

    def stp_priority(self, prio):
        if prio % 4096:
            return "Error: Priority must be a multiple of 4096."
        self.device.stp.priority = prio
        self.device.stp.schedule()
        return None

    def stp_edge(self, plist, edge):
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            (self.device.stp.edge.add if edge else self.device.stp.edge.discard)(p.name)
        self.device.stp.schedule()
        return None

    def stp_cost(self, cost, plist):
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            self.device.stp.costs[p.name] = cost
        self.device.stp.schedule()
        return None

    def stp_onoff(self, on):
        stp = self.device.stp
        stp.enabled = on
        if not on:
            stp.states.clear()
        stp.schedule()
        return None

    # -- IPv6, OSPFv3, RIPng ---------------------------------------------------------------------
    def vlan_ip6(self, name, prefix):
        from ipaddress import IPv6Interface
        d = self.device
        vid, err = self._need_vlan(name)
        if err:
            return err
        svi = d.svi_for_vlan(vid) or d.add_svi(vid, d.vlans[vid], d.vlans[vid])
        a = IPv6Interface(prefix)
        if a not in svi.ipv6:
            svi.ipv6.append(a)
        svi.ipv6_enabled = True
        d.ipv6_changed()
        return None

    def ipfwd6(self, on):
        self.device.ipv6_routing = on
        self.device.ipv6_changed()
        return None

    def _v6proc(self, kind):
        from ..routing.v6igp import ensure
        new = kind not in self.device.v6igp
        proc = ensure(self.device, kind)
        if new:
            proc.cfg.enabled = False                 # EXOS: "enable ospfv3" / "enable ripng" starts it
        return proc

    def v6igp_rid(self, rid):
        proc = self._v6proc("ospf6")
        proc.cfg.router_id = rid
        return None

    def v6igp_vlan(self, kind, vlan, area, add):
        from ..routing.ospf import area_id
        vid, err = self._need_vlan(vlan)
        if err:
            return err
        svi = self.device.svi_for_vlan(vid)
        if svi is None or not svi.ipv6:
            return f"Error: VLAN {vlan} has no IPv6 address configured."
        proc = self._v6proc(kind)
        if add:
            try:
                proc.cfg.interfaces[svi.name] = {"area": area_id(area) if area else "0.0.0.0"}
            except ValueError:
                return "Error: Invalid area identifier."
        else:
            proc.cfg.interfaces.pop(svi.name, None)
        proc.kick()
        return None

    def v6igp_onoff(self, kind, on):
        proc = self._v6proc(kind)
        proc.cfg.enabled = on
        if on:
            proc.kick()
        else:
            proc.neighbors.clear()
            proc._recompute_domain()
        return None

    def show_v6nb(self, kind):
        from .base import v6igp_neighbors
        lines = ["Neighbor ID     Pri State          Interface       Address"]
        for rid, p, ll in v6igp_neighbors(self.device, kind):
            lines.append(f"{rid:<16}{1:<4}{'FULL':<15}{(p.name if p else ''):<16}{ll}")
        return "\n".join(lines)

    # -- port security / DHCP snooping -----------------------------------------------------------
    def limit_learning(self, plist, n):
        from .base import psec_set
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            psec_set(p, "enable")
            psec_set(p, "max", n)
            psec_set(p, "violation", "protect")      # EXOS: frames from extra MACs are blackholed
        return None

    def snoop_vlan(self, vlan, on):
        vid, err = self._need_vlan(vlan)
        if err:
            return err
        ds = self.device.dhcp_snooping
        vl = set(ds.get("vlans") or [])
        vl = vl | {vid} if on else vl - {vid}
        ds["vlans"] = sorted(vl)
        ds["enabled"] = bool(vl)
        return None

    def snoop_trust(self, plist, on):
        ports = parse_ports(plist, self.device)
        if ports is None:
            return f"Error: Port {plist} does not exist."
        for p in ports:
            p.dhcp_trust = on
        return None

    def show_snoop(self):
        lines = ["Port    IP Addr          MAC Addr           VLAN"]
        for mac, b in self.device.snoop_bindings.items():
            lines.append(f"{b['port']:<8}{b['ip']:<17}{mac:<19}{b['vlan']}")
        return "\n".join(lines)

    def show_limits(self):
        lines = ["Port   Limit  Learned  Action"]
        for p in self.device.physical_ports():
            if p.psec is not None:
                lines.append(f"{p.name:<7}{p.psec.get('max', 1):<7}{len(p.psec.get('macs', [])):<9}blackhole")
        return "\n".join(lines)

    # -- VRRP ----------------------------------------------------------------------------------
    def _vrrp_port(self, vlan):
        vid, err = self._need_vlan(vlan)
        if err:
            return None, err
        svi = self.device.svi_for_vlan(vid)
        if svi is None or svi.ip is None:
            return None, f"Error: VLAN {vlan} has no IP address configured."
        return svi, None

    def vrrp_create(self, vlan, vrid):
        svi, err = self._vrrp_port(vlan)
        if err:
            return err
        g = self.device.fhrp.group(svi.name, "vrrp", vrid)
        g.disabled = True
        return None

    def vrrp_cfg(self, vlan, vrid, attr, value):
        from .base import fhrp_set
        svi, err = self._vrrp_port(vlan)
        if err:
            return err
        if self.device.fhrp.group(svi.name, "vrrp", vrid, create=False) is None:
            return f"Error: VRRP VR {vlan}/{vrid} does not exist."
        return fhrp_set(self.device, svi, "vrrp", vrid, attr, value)

    def vrrp_onoff(self, on, vlan, vrid):
        f = self.device.fhrp
        port_name = None
        if vlan is not None:
            svi, err = self._vrrp_port(vlan)
            if err:
                return err
            port_name = svi.name
        for g in f.groups.values():
            if (port_name is None or g.port == port_name) and (vrid is None or g.group == vrid):
                g.disabled = not on
                g.since = self.device.engine.now
        f._arm()
        f.tick(send=True)
        return None

    def show_vrrp(self):
        lines = ["VLAN Name        VRID Pri Virtual IP Addr  State  Master Mac Address  TP/TR/TV/P/T"]
        for g in self.device.fhrp.groups.values():
            st = {"master": "MSTR", "backup": "BKUP"}.get(g.state, "INIT")
            en = "Ds" if g.disabled else "En"
            lines.append(f"{(g.port + '(' + en + ')'):<17}{g.group:<5}{self.device.fhrp.eff_priority(g):<4}"
                         f"{str(g.vip or '0.0.0.0'):<17}{st:<7}{'00:00:5e:00:01:' + format(g.group, '02x'):<20}"
                         f"0  0  0  {'Y' if g.preempting else 'N'}  1")
        return "\n".join(lines)

    # -- load sharing (LAG) --------------------------------------------------------------------
    def sharing(self, master, plist, opts):
        d = self.device
        members = []
        for part in plist.split(","):
            a, _, b = part.partition("-")
            if not a.isdigit() or (b and not b.isdigit()):
                return f"Error: Port {plist} does not exist."
            for n in range(int(a), int(b or a) + 1):
                p = d.ports.get(str(n))
                if p is None:
                    return f"Error: Port {n} does not exist."
                members.append(p)
        mp = d.ports.get(str(master))
        if mp not in members:
            return "Error: The master port must be a member of the group."
        if d.channel_for_group(master) is not None:
            return f"Error: Port {master} is already a load sharing master."
        po = d.add_channel(master, f"lag{master}", f"lag{master}")
        po.mode, po.pvid, po.access_vlan, po.native_vlan, po.tagged = mp.mode, mp.pvid, mp.access_vlan, mp.native_vlan, mp.tagged
        mode = "active" if "lacp" in opts.lower() else "on"
        for p in members:
            d.set_channel_member(p, master, mode)
        return None

    def sharing_off(self, master):
        d = self.device
        po = d.channel_for_group(master)
        if po is None:
            return f"Error: Port {master} is not a load sharing master."
        mp = d.ports.get(str(master))
        mp.mode, mp.pvid, mp.access_vlan, mp.native_vlan, mp.tagged = po.mode, po.pvid, po.access_vlan, po.native_vlan, po.tagged
        d.remove_channel(po)
        return None

    def show_sharing(self):
        from .base import lag_summary_rows
        lines = ["Load Sharing Monitor", "Config    Current Agg     Min    Ld Share  Dist  Ld Share  Agg Link  Link Up",
                 "Master    Master  Control Active Algorithm Flags Group     Mbr State Transitns", "=" * 82]
        for po, proto, mem in lag_summary_rows(d := self.device):
            ctl = "LACP" if proto == "LACP" else "Static"
            for m, f in mem:
                lines.append(f"{po.group:<10}{po.group if po.is_up else '':<8}{ctl:<8}{1:<7}{'L2':<10}{'A':<6}{m.name:<10}"
                             f"{'Y' if f == 'P' else '-':<4}{'A' if m.is_up else 'R':<6}0")
        _ = d
        lines.append("=" * 82)
        return "\n".join(lines)

    # -- remote access -------------------------------------------------------------------------
    def service(self, proto, on):
        from ..services.remote import access
        access(self.device)[proto] = on
        return None

    def account(self, name, pw):
        from ..services.remote import access
        users = access(self.device).setdefault("users", {})
        if pw is None:
            if users.pop(name, None) is None:
                return f"Error: Account {name} does not exist."
            return None
        users[name] = pw
        return None

    def account_password(self, name):
        from ..services.remote import access
        users = access(self.device).setdefault("users", {})
        if name not in users:
            return f"Error: Account {name} does not exist."
        first = {}

        def second(pw):
            if pw != first["pw"]:
                return "Error: Passwords do not match."
            users[name] = pw
            return None

        def one(pw):
            first["pw"] = pw
            self.ask("Reenter password: ", second)
            return None
        self.ask("New password: ", one)
        return None

    def remote(self, proto, target):
        from ..services.remote import connect
        user = ""
        if "@" in target:
            user, target = target.split("@", 1)
        if not is_valid_ip(target):
            return f"Error: Invalid host {target}."
        return connect(self, target, proto, user or "admin", "extreme")

    # -- system --------------------------------------------------------------------------------
    def save(self):
        self.device.save_startup()
        self.dirty = False
        return ("The configuration file primary.cfg already exists.\n"
                "Saving configuration on master ....... done!\nConfiguration saved to primary.cfg successfully.")

    def unconfigure_all(self):
        self.device.factory_reset()
        self.device.startup_config = None
        self.dirty = False
        return "Restoring factory defaults ... done. Reboot the switch to complete."

    def ping(self, host, count):
        return make_ping(self.device, host, ExosPing(self.write), count=count, size=56,
                         timeout=1000.0, interval=1000.0).start()

    def traceroute(self, host):
        return make_trace(self.device, host, ExosTrace(self.write)).start()

    # -- show ----------------------------------------------------------------------------------
    def _members(self, vid):
        u = [p for p in self.device.l2_ports() if self._untagged(p) == vid]
        t = [p for p in self.device.l2_ports() if vid in self._tagged(p)]
        return u, t

    def show_vlan(self, name):
        d = self.device
        if name is not None:
            vid, err = self._need_vlan(name)
            if err:
                return err
            u, t = self._members(vid)
            svi = d.svi_for_vlan(vid)
            active = [p for p in u + t if p.is_up]
            fwd = svi is not None and svi.name in d.extra.get("exos_fwd", [])
            lines = [f"VLAN Interface with name {d.vlans[vid]} created by user",
                     "    Admin State:         Enabled     Tagging:   802.1Q Tag " + str(vid),
                     "    Virtual router:      VR-Default",
                     f"    Primary IP:          {svi.ip.with_prefixlen if svi is not None and svi.ip else '-'}",
                     f"    IP Forwarding:       {'Enabled' if fwd else 'Disabled'}",
                     f"    Ports:               {len(u) + len(t)}. (Number of active ports={len(active)})",
                     f"       Untag:            {', '.join(('*' if p.is_up else '') + p.name for p in u) or '-'}",
                     f"       Tag:              {', '.join(('*' if p.is_up else '') + p.name for p in t) or '-'}",
                     "    Flags:    (*) Active, (!) Disabled, (g) Load Sharing port"]
            return "\n".join(lines)
        sep = "-" * 95
        lines = [sep, "Name            VID  Protocol Addr       Flags                         Proto  Ports  Virtual",
                 "                                                                              Active router",
                 "                                                                              /Total", sep]
        for vid, vname in sorted(d.vlans.items(), key=lambda kv: kv[1].lower() if kv[0] != 1 else ""):
            u, t = self._members(vid)
            svi = d.svi_for_vlan(vid)
            addr = (f"{str(svi.ip.ip):<13}/{svi.ip.network.prefixlen:<5}" if svi is not None and svi.ip
                    else "-" * 19)
            fwd = svi is not None and svi.name in d.extra.get("exos_fwd", [])
            flags = "-" * 11 + ("f" if fwd else "-") + "-" * 17
            act = sum(1 for p in u + t if p.is_up)
            lines.append(f"{vname:<16}{vid:<5}{addr} {flags} ANY    {act:>2} /{len(u) + len(t):<3}VR-Default")
        lines += [sep, "Flags : (f) IP Forwarding Enabled", f"Total number of VLAN(s) : {len(d.vlans)}"]
        return "\n".join(lines)

    def show_ports(self):
        d = self.device
        lines = ["Port Summary Monitor", "Port     Display              VLAN Name          Port  Link  Speed  Duplex",
                 "#        String               (or # VLANs)       State State Actual Actual", "=" * 82]
        for p in d.l2_ports():
            vl = [v for v in [self._untagged(p)] if v] + sorted(self._tagged(p))
            vname = d.vlans[vl[0]] if len(vl) == 1 else (f"({len(vl):04d})" if vl else "")
            st = "E" if p.admin_up else "D"
            link = "A" if p.is_up else ("R" if p.admin_up else "NP")
            spd = "1000   FULL" if p.is_up else ""
            lines.append(f"{p.name:<9}{p.description[:20]:<21}{vname[:18]:<19}{st:<6}{link:<6}{spd}")
        lines += ["=" * 82, "   Port State: D-Disabled, E-Enabled", "   Link State: A-Active, R-Ready, NP-Port Not Present"]
        return "\n".join(lines)

    def show_fdb(self):
        d = self.device
        entries = d.mac_entries()
        lines = ["MAC                     VLAN Name( Tag)  Age  Flags         Port / Virtual Port List",
                 "-" * 102]
        for e in entries:
            age = int((d.engine.now - e.learned_at) / 1000)
            vname = d.vlans.get(e.vlan, str(e.vlan))
            lines.append(f"{_mac(e.mac):<24}{vname[:9]}({e.vlan:04d}) {age:04d} d m           {e.port.name}")
        lines += ["", "Flags : d - Dynamic, s - Static, p - Permanent, m - MAC, i - IP,",
                  f"Total: {len(entries)} Static: 0  Perm: 0  Dyn: {len(entries)}  Dropped: 0  Locked: 0"]
        return "\n".join(lines)

    def show_iproute(self):
        d = self.device
        lines = ["Ori  Destination        Gateway         Mtr  Flags         VLAN       Duration"]
        n = 0
        for r in d.routing_table():
            if r.code == "L":
                continue
            ori = {"C": "#d", "S": "#s", "R": "#r", "B": "#bg"}.get(r.code)
            if ori is None:
                ori = "#oe" if "E" in r.code else ("#or" if "IA" in r.code else "#oa")
            gw = str(r.port.ip.ip) if r.code == "C" and r.port.ip else str(r.next_hop or "")
            flags = ("U-" if r.code == "C" else "UG") + "-" * 3 + ("S" if r.code == "S" else "-") + "-um--f-"
            mtr = r.metric if r.code not in ("C", "S") else 1
            lines.append(f"{ori:<5}{str(r.network):<19}{gw:<16}{mtr:<5}{flags:<14}{r.port.name[:10]:<11}"
                         f"{_dur(d.engine.now)}")
            n += 1
        lines += ["", "Origin(Ori): (bg) BGP, (d) Direct, (oa) OSPFIntra, (oe) OSPFAsExt, (or) OSPFInter,",
                  "             (r) RIP, (s) Static", "", f"Total number of routes = {n}"]
        return "\n".join(lines)

    def show_iparp(self):
        d = self.device
        entries = d.arp.entries(d.engine.now)
        lines = ["VR             Destination      Mac                Age  Static  VLAN          VID   Port"]
        for e in entries:
            vid = e.port.vlan if e.port.is_svi else 0
            phys = next((m.port.name for m in d.mac_entries() if m.mac == e.mac), "")
            age = int((d.engine.now - e.learned_at) / 60000)
            lines.append(f"{'VR-Default':<15}{str(e.ip):<17}{_mac(e.mac):<19}{age:<5}{'YES' if e.static else 'NO':<8}"
                         f"{e.port.name[:13]:<14}{vid:<6}{phys}")
        lines.append(f"\nDynamic Entries  : {len(entries):>10}    Static Entries   : {0:>10}")
        return "\n".join(lines)

    def show_ipconfig(self):
        d = self.device
        fwd = d.extra.get("exos_fwd", [])
        lines = [f"Router Interface on VLAN {s.name} is enabled and {'up' if s.is_up else 'down'}.\n"
                 f" inet {s.ip.ip}  netmask {s.ip.netmask}  broadcast {s.ip.network.broadcast_address}\n"
                 f" IP Forwarding: {'Enabled' if s.name in fwd else 'Disabled'}"
                 for s in d.svis() if s.ip is not None]
        return "\n\n".join(lines) or "No IP interfaces configured."

    def show_switch(self):
        d = self.device
        return (f"SysName:          {d.hostname}\nSysLocation:\nSysContact:       support@extremenetworks.com\n"
                f"System MAC:       {_mac(min(d.physical_ports(), key=lambda p: p.mac.value).mac)}\n"
                "System Type:      X440G2-24t-10G4\n\nSysHealth check: Enabled (Normal)\n"
                f"System UpTime:    {_dur(d.engine.now)}\n\nCurrent State:    OPERATIONAL\n"
                "Image Selected:   primary\nPrimary ver:      31.7.1.4\nConfig Selected:  primary.cfg")

    def show_version(self):
        return ("Switch      : 800578-00-06 1716G-01234 Rev 6.0 BootROM: 2.0.1.7    IMG: 31.7.1.4\n\n"
                "Image   : ExtremeXOS version 31.7.1.4 by release-manager\n"
                "BootROM : Default 2.0.1.7")

    def show_ospf(self):
        d = self.device
        if d.ospf is None:
            return "OSPF             : Disabled"
        cfg = d.ospf.cfg
        return (f"OSPF             : {'Enabled' if cfg.enabled else 'Disabled'}\n"
                f"RouterId         : {d.ospf.router_id or '0.0.0.0'}\n"
                f"Interfaces       : {', '.join(cfg.interfaces) or '-'}")

    def show_ospf_neighbor(self):
        d = self.device
        lines = ["Neighbor ID     Pri State               Up/Dead Time               Address         Interface",
                 "                                                                                BFD Session State",
                 "=" * 105]
        if d.ospf is not None:
            for nb in d.ospf.neighbors.values():
                role = "DR" if nb.dr == nb.ip else ("BDR" if nb.bdr == nb.ip else "OTHER")
                state = ("FULL /" + role) if nb.two_way else "INIT"
                lines.append(f"{str(nb.rid):<16}{nb.priority:<4}{state:<20}"
                             f"{'00:00:10:00/00:00:00:30':<27}{str(nb.ip):<16}{nb.port.name}")
        return "\n".join(lines)

    def show_stpd(self):
        d = self.device
        stp = d.stp
        root = stp.root_id or stp.bridge_id
        mode = {"stp": "802.1D", "rstp": "802.1W", "mstp": "MSTP"}.get(stp.mode, stp.mode)
        return (f"Stpd: s0\t\tStp: {'ENABLED' if stp.enabled else 'DISABLED'}\t\tNumber of Ports: "
                f"{len(d.physical_ports())}\nRstp: {'ENABLED' if stp.rapid else 'DISABLED'}\t\tTag: (none)\n"
                f"Operational Mode: {mode}\n802.1Q Tag: (none)\nBridge Priority: {stp.priority}\n"
                f"BridgeID:\t\t{stp.bridge_id[0]:04x}:{_mac(type(d.physical_ports()[0].mac)(stp.bridge_id[1]))}\n"
                f"Designated root:\t{root[0]:04x}:{_mac(type(d.physical_ports()[0].mac)(root[1]))}\n"
                f"RootPathCost: {stp.root_cost}\tRootPort: {stp.root_port or '----'}\n"
                f"Topology Change Count: {stp.topology_changes}")

    def show_stpd_ports(self):
        d = self.device
        stp = d.stp
        roles = {"root": "ROOT", "designated": "DSGN", "alternate": "ALTN", "edge": "DSGN", "disabled": "DSBL"}
        states = {"forwarding": "FORWARDING", "blocking": "BLOCKING", "listening": "LISTENING",
                  "learning": "LEARNING", "disabled": "DISABLED"}
        lines = ["Port   Mode   State      Cost  Flags    Priority Port ID Designated Bridge"]
        for p in d.physical_ports():
            if not p.is_up:
                continue
            st = states.get(stp.states.get(p.name, "forwarding"), "FORWARDING") if stp.enabled else "FORWARDING"
            role = roles.get(stp.roles.get(p.name), "DSGN") if stp.enabled else "DSBL"
            lines.append(f"{p.name:<7}{'EMISTP':<7}{st:<11}{stp.port_cost(p):<6}{'e' if p.name in stp.edge else 'p'}"
                         f"{role:<8}{'128':<9}{stp.port_id(p):04x}    {stp.bridge_id[0]:04x}")
        return "\n".join(lines)

    def show_config(self):
        d = self.device
        lines = ["#", "# Module devmgr configuration.", "#", f'configure snmp sysName "{d.hostname}"', "#",
                 "# Module vlan configuration.", "#"]
        default_members = [p for p in d.l2_ports() if self._untagged(p) == 1]
        if len(default_members) != len(d.l2_ports()):
            missing = [p for p in d.l2_ports() if p not in default_members]
            lines.append(f"configure vlan default delete ports {port_ranges(missing)}")
        for vid, vname in sorted(d.vlans.items()):
            if vid == 1:
                continue
            lines += [f'create vlan "{vname}"', f"configure vlan {vname} tag {vid}"]
        for vid, vname in sorted(d.vlans.items()):
            u, t = self._members(vid)
            if vid != 1 and u:
                lines.append(f"configure vlan {vname} add ports {port_ranges(u)} untagged")
            if t:
                lines.append(f"configure vlan {vname} add ports {port_ranges(t)} tagged")
        for p in d.physical_ports():
            if p.description:
                lines.append(f'configure ports {p.name} display-string "{p.description}"')
            if not p.admin_up:
                lines.append(f"disable ports {p.name}")
        for po in d.channels():
            mem = [int(m.name) for m in po.members]
            lacp = " lacp" if any(m.channel_mode == "active" for m in po.members) else ""
            lines.insert(7, f"enable sharing {po.group} grouping {port_ranges(po.members)} algorithm address-based L2{lacp}")
            _ = mem
        for svi in d.svis():
            if svi.ip:
                lines.append(f"configure vlan {svi.name} ipaddress {svi.ip.ip} {svi.ip.netmask}")
        for name in d.extra.get("exos_fwd", []):
            lines.append(f"enable ipforwarding vlan {name}")
        for g in d.fhrp.groups.values():
            lines.append(f"create vrrp vlan {g.port} vrid {g.group}")
            if g.priority != 100:
                lines.append(f"configure vrrp vlan {g.port} vrid {g.group} priority {g.priority}")
            if g.vip:
                lines.append(f"configure vrrp vlan {g.port} vrid {g.group} add {g.vip}")
            if not g.disabled:
                lines.append(f"enable vrrp vlan {g.port} vrid {g.group}")
        if d.static_routes:
            lines += ["#", "# Module rtmgr configuration.", "#"]
            for s in d.static_routes:
                tgt = "default" if s.network.prefixlen == 0 else f"{s.network.network_address} {s.network.netmask}"
                lines.append(f"configure iproute add {tgt} {s.next_hop}")
        stp = d.stp
        lines += ["#", "# Module stp configuration.", "#",
                  f"configure stpd s0 mode {({'stp': 'dot1d', 'rstp': 'dot1w'}).get(stp.mode, stp.mode)}"]
        if stp.priority != 32768:
            lines.append(f"configure stpd s0 priority {stp.priority}")
        if stp.edge:
            lines.append(f"configure stpd s0 ports link-type edge {port_ranges([d.ports[n] for n in stp.edge if n in d.ports])}")
        lines.append(f"{'enable' if stp.enabled else 'disable'} stpd s0")
        if d.ospf is not None:
            lines += ["#", "# Module ospf configuration.", "#"]
            if d.ospf.cfg.router_id:
                lines.append(f"configure ospf routerid {d.ospf.cfg.router_id}")
            for name, ic in d.ospf.cfg.interfaces.items():
                lines.append(f"configure ospf add vlan {name} area {ic.get('area', '0.0.0.0')}"
                             + (" passive" if ic.get("passive") else ""))
            if d.ospf.cfg.enabled:
                lines.append("enable ospf")
        return "\n".join(lines)


__all__ = ["ExosSession", "parse_ports", "is_valid_ip", "is_valid_mask"]
