"""Extra Windows commands for the PC prompt: route, netsh, netstat, getmac, arp -s, DNS cache ..."""
from __future__ import annotations

import shlex
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Interface, IPv6Network

from ..addresses import MacAddress, is_valid_ip, is_valid_mask

IF_INDEX = 11          # interface index of the NIC in "route print" / netsh


def split_args(line: str) -> list[str]:
    try:
        return shlex.split(line, posix=True)
    except ValueError:
        return line.split()


def win_mac(mac) -> str:
    return mac.windows()


class WindowsExtras:
    """Mixed into HostSession; ``self.device`` is the Host."""

    # -- route ----------------------------------------------------------------------------
    def route(self, args):
        d = self.device
        a = [x.lower() for x in args]
        persistent = "-p" in a
        a = [x for x in a if x not in ("-p", "-f")]
        if not a or a[0] == "print":
            only = a[1] if len(a) > 1 and a[1] in ("-4", "-6") else None
            return self.route_print(only)
        if a[0] == "add":
            rest = a[1:]
            try:
                dst = rest[0]
                mask = "255.255.255.255"
                i = 1
                if len(rest) > 2 and rest[1] == "mask":
                    mask = rest[2]
                    i = 3
                gw = rest[i]
            except IndexError:
                return self._route_usage()
            if ":" in dst:
                return self._route6_add(dst, gw)
            if not (is_valid_ip(dst) and is_valid_mask(mask) and is_valid_ip(gw)):
                return "Bad argument " + (dst if not is_valid_ip(dst) else mask if not is_valid_mask(mask) else gw)
            net = IPv4Network(f"{dst}/{mask}", strict=False)
            if str(net.network_address) != dst:
                return "The route addition failed: The specified mask parameter is invalid. (Destination & Mask) != Destination."
            gwa = IPv4Address(gw)
            if d.nic.ip is None or gwa not in d.nic.ip.network:
                return ("The route addition failed: Either the interface index is wrong or the gateway does not lie "
                        "on the same network as the interface. Check the IP Address Table for the machine.")
            d.add_route(net, gwa, persistent)
            d.engine.emit("device_changed", d)
            return " OK!"
        if a[0] == "delete":
            if len(a) < 2:
                return self._route_usage()
            if ":" in a[1]:
                before = len(d.static_routes6)
                net6 = IPv6Network(a[1], strict=False)
                d.static_routes6 = [r for r in d.static_routes6 if r.network != net6]
                return " OK!" if len(d.static_routes6) != before else "The route deletion failed: Element not found."
            try:
                mask = a[3] if len(a) > 3 and a[2] == "mask" else None
                net = IPv4Network(f"{a[1]}/{mask}" if mask else a[1] + ("/0" if a[1] == "0.0.0.0" else "/32"),
                                  strict=False)
            except ValueError:
                return f"Bad argument {a[1]}"
            if not d.del_route(net) and not (mask is None and d.del_route(_classful(a[1]))):
                return "The route deletion failed: Element not found."
            d.engine.emit("device_changed", d)
            return " OK!"
        if a[0] == "change":
            r = self.route(["delete"] + args[1:2])
            if "failed" in (r or ""):
                return r
            return self.route(["add"] + args[1:])
        return self._route_usage()

    @staticmethod
    def _route_usage():
        return ("\nManipulates network routing tables.\n\n"
                "ROUTE [-f] [-p] [-4|-6] command [destination]\n"
                "                  [MASK netmask]  [gateway] [METRIC metric]  [IF interface]\n\n"
                "  command      One of these:\n"
                "                 PRINT     Prints  a route\n"
                "                 ADD       Adds    a route\n"
                "                 DELETE    Deletes a route\n"
                "                 CHANGE    Modifies an existing route")

    def _route6_add(self, dst, gw):
        from ..stack.ipv6 import StaticRoute6
        d = self.device
        try:
            net = IPv6Network(dst, strict=False)
            g = IPv6Address(gw)
        except ValueError:
            return f"Bad argument {dst}"
        d.static_routes6 = [r for r in d.static_routes6 if r.network != net]
        d.static_routes6.append(StaticRoute6(net, g, d.nic.name))
        return " OK!"

    def route_print(self, only=None):
        d = self.device
        p = d.nic
        sep = "=" * 75
        mac = " ".join(f"{b:02x}" for b in p.mac.value.to_bytes(6, "big"))
        out = [sep, "Interface List", f" {IF_INDEX}...{mac} ......{p.name}",
               "  1...........................Software Loopback Interface 1", sep, ""]
        if only != "-6":
            rows = []
            me = str(p.ip.ip) if p.ip else None
            if p.ip is not None and p.is_up:
                if d.gateway is not None:
                    rows.append(("0.0.0.0", "0.0.0.0", str(d.gateway), me, 25 + 256 * 0))
                for net, gw, _ in d.routes:
                    rows.append((str(net.network_address), str(net.netmask), str(gw) if gw else "On-link", me, 26))
            rows += [("127.0.0.0", "255.0.0.0", "On-link", "127.0.0.1", 331),
                     ("127.0.0.1", "255.255.255.255", "On-link", "127.0.0.1", 331),
                     ("127.255.255.255", "255.255.255.255", "On-link", "127.0.0.1", 331)]
            if p.ip is not None and p.is_up:
                n = p.ip.network
                rows += [(str(n.network_address), str(n.netmask), "On-link", me, 281),
                         (me, "255.255.255.255", "On-link", me, 281),
                         (str(n.broadcast_address), "255.255.255.255", "On-link", me, 281)]
            rows.sort(key=lambda r: (int(IPv4Address(r[0])), int(IPv4Address(r[1]))))
            out += ["IPv4 Route Table", sep, "Active Routes:",
                    "Network Destination        Netmask          Gateway       Interface  Metric"]
            out += [f"{r[0]:>17}{r[1]:>17}{r[2]:>17}{r[3]:>16}{r[4]:>7}" for r in rows]
            pers = [r for r in d.routes if r[2]]
            out += [sep, "Persistent Routes:"]
            if pers:
                out.append("  Network Address          Netmask  Gateway Address  Metric")
                out += [f"{str(n.network_address):>17}{str(n.netmask):>17}{str(g):>17}{'1':>8}" for n, g, _ in pers]
            else:
                out.append("  None")
            out.append("")
        if only != "-4":
            out += ["IPv6 Route Table", sep, "Active Routes:", " If Metric Network Destination      Gateway"]
            rows6 = [("1", "331", "::1/128", "On-link")]
            for r in d.routing_table6():
                if r.code == "L":
                    rows6.append((str(IF_INDEX), "281", str(r.network), "On-link"))
                elif r.code == "C":
                    rows6.append((str(IF_INDEX), "281", str(r.network), "On-link"))
                else:
                    rows6.append((str(IF_INDEX), "281", str(r.network), str(r.next_hop) if r.next_hop else "On-link"))
            ll = d.link_local(p)
            if ll is not None and p.is_up:
                rows6.append((str(IF_INDEX), "281", "fe80::/64", "On-link"))
                rows6.append((str(IF_INDEX), "281", f"{ll.ip}/128", "On-link"))
            out += [f"{a:>3}{b:>7} {c:<24} {g}" for a, b, c, g in rows6]
            out += [sep, "Persistent Routes:", "  None", ""]
        return "\n".join(out)

    # -- netstat -------------------------------------------------------------------------------
    def netstat(self, args):
        a = "".join(x.lower().lstrip("-/") for x in args)
        if "r" in a:
            return self.route_print()
        lines = ["", "Active Connections", "", "  Proto  Local Address          Foreign Address        State"]
        if "a" in a:
            for proto, port in self.device.listening():
                if proto == "TCP":
                    lines.append(f"  TCP    {'0.0.0.0:' + str(port):<23}{'0.0.0.0:0':<23}LISTENING")
                else:
                    lines.append(f"  UDP    {'0.0.0.0:' + str(port):<23}*:*")
        if "e" in a:
            p = self.device.nic
            return ("Interface Statistics\n\n                           Received            Sent\n\n"
                    f"Unicast packets          {p.rx_frames:>10}      {p.tx_frames:>10}")
        return "\n".join(lines)

    # -- getmac -------------------------------------------------------------------------------
    def getmac(self, args):
        p = self.device.nic
        transport = "\\Device\\Tcpip_{4D36E972-E325-11CE-BFC1-08002BE10318}" if p.is_up else "Media disconnected"
        if any(x.lower() == "/v" for x in args):
            return ("\nConnection Name Network Adapter Physical Address    Transport Name\n"
                    "=============== =============== =================== ==========================================================\n"
                    f"{p.name:<16}{'NetEmu Ethernet':<16}{win_mac(p.mac):<20}{transport}")
        return ("\nPhysical Address    Transport Name\n"
                "=================== ==========================================================\n"
                f"{win_mac(p.mac):<20}{transport}")

    # -- arp extras ---------------------------------------------------------------------------
    def arp_static(self, ip, mac):
        from ..stack.arp import ArpEntry
        d = self.device
        try:
            m = MacAddress.parse(mac)
        except ValueError:
            return "The ARP entry addition failed: The parameter is incorrect."
        if not is_valid_ip(ip):
            return "The ARP entry addition failed: The parameter is incorrect."
        d.arp._entries[IPv4Address(ip)] = ArpEntry(IPv4Address(ip), m, d.nic, d.engine.now, static=True)
        return None

    def arp_delete(self, ip):
        d = self.device
        if ip in (None, "*"):
            d.arp.clear()
            return None
        if not is_valid_ip(ip) or d.arp._entries.pop(IPv4Address(ip), None) is None:
            return "The specified entry was not found."
        return None

    # -- DNS cache ----------------------------------------------------------------------------
    def displaydns(self):
        d = self.device
        if not d.dns_cache:
            return "\nWindows IP Configuration\n\n Could not display the DNS Resolver Cache."
        lines = ["", "Windows IP Configuration", ""]
        for name, (ip, at) in d.dns_cache.items():
            ttl = max(0, 86400 - int((d.engine.now - at) / 1000))
            lines += [f"    {name}", "    " + "-" * 40, f"    Record Name . . . . . : {name}",
                      "    Record Type . . . . . : 1", f"    Time To Live  . . . . : {ttl}",
                      "    Data Length . . . . . : 4", "    Section . . . . . . . : Answer",
                      f"    A (Host) Record . . . : {ip}", ""]
        return "\n".join(lines)

    def flushdns(self):
        self.device.dns_cache.clear()
        return "\nWindows IP Configuration\n\nSuccessfully flushed the DNS Resolver Cache."

    # -- netsh --------------------------------------------------------------------------------
    def netsh(self, line: str):
        toks = split_args(line)[1:]
        low = [t.lower() for t in toks]
        if not low or low[0] in ("?", "help", "/?"):
            return ("\nUsage: netsh interface ipv4|ipv6 <command>\n\n"
                    "  netsh interface show interface\n"
                    "  netsh interface set interface \"<name>\" admin=enabled|disabled\n"
                    "  netsh interface ipv4 set address \"<name>\" static <ip> <mask> [<gateway>] | dhcp\n"
                    "  netsh interface ipv4 set dnsservers \"<name>\" static <ip> [primary] | dhcp\n"
                    "  netsh interface ipv4 add dnsservers \"<name>\" <ip>\n"
                    "  netsh interface ipv4 show config | addresses | route | dnsservers | neighbors\n"
                    "  netsh interface ipv6 add|delete address \"<name>\" <addr>[/len]\n"
                    "  netsh interface ipv6 add|delete route ::/0 \"<name>\" <gateway>\n"
                    "  netsh interface ipv6 set interface \"<name>\" routerdiscovery=enabled|disabled\n"
                    "  netsh interface ipv6 show address | route | neighbors")
        if low[0] == "wlan":
            return self._netsh_wlan(low[1:], toks[1:])
        if low[0] in ("int", "interface"):
            low, toks = low[1:], toks[1:]
        else:
            return f"The following command was not found: {' '.join(toks)}."
        if not low:
            return "The syntax supplied for this command is not valid. Check help for the correct syntax."
        if low[0] == "show" and low[1:2] in (["interface"], ["int"]):
            p = self.device.nic
            st = "Connected" if p.is_up else "Disconnected"
            return ("\nAdmin State    State          Type             Interface Name\n"
                    "-------------------------------------------------------------------------\n"
                    f"{'Enabled' if p.admin_up else 'Disabled':<15}{st:<15}{'Dedicated':<17}{p.name}")
        if low[0] == "set" and low[1:2] in (["interface"], ["int"]):
            kv = _kv(low[2:])
            if "admin" in kv:
                self.device.nic.set_admin(kv["admin"] in ("enabled", "enable"))
                self.device.engine.emit("device_changed", self.device)
                return "Ok."
            return "The syntax supplied for this command is not valid. Check help for the correct syntax."
        fam = low[0]
        if fam in ("ip", "ipv4"):
            return self._netsh4(low[1:])
        if fam == "ipv6":
            return self._netsh6(low[1:], toks[1:])
        return f"The following command was not found: interface {' '.join(toks)}."

    def _netsh_wlan(self, low, toks):
        from .base import wifi_connect, wifi_disconnect, wifi_status
        d = self.device
        if not getattr(d.nic, "is_wireless", False):
            return "The Wireless AutoConfig Service (wlansvc) is not running."
        auth = {"wpa2": "WPA2-Personal", "open": "Open"}
        if low[:2] == ["show", "interfaces"]:
            ap, sig, _ = wifi_status(d)
            lines = ["", "There is 1 interface on the system:", "",
                     "    Name                   : Wi-Fi", "    Description            : NetEmu Wireless Adapter",
                     f"    Physical address       : {d.nic.mac.windows().replace('-', ':').lower()}",
                     f"    State                  : {'connected' if ap else 'disconnected'}"]
            if ap:
                lines += [f"    SSID                   : {ap.wifi['ssid']}",
                          f"    BSSID                  : {ap.get_port('Ethernet0').mac.windows().replace('-', ':').lower()}",
                          "    Network type           : Infrastructure", "    Radio type             : 802.11n",
                          f"    Authentication         : {auth.get(ap.wifi['security'], 'WPA2-Personal')}",
                          f"    Channel                : {ap.wifi['channel']}", f"    Signal                 : {sig}%"]
            return "\n".join(lines)
        if low[:2] == ["show", "networks"]:
            _, _, nets = wifi_status(d)
            lines = ["", "Interface name : Wi-Fi", f"There are {len(nets)} networks currently visible.", ""]
            for i, (ssid, sec, ch, sig, ap) in enumerate(nets, 1):
                lines += [f"SSID {i} : {ssid}", "    Network type            : Infrastructure",
                          f"    Authentication          : {auth.get(sec, 'WPA2-Personal')}",
                          f"    Encryption              : {'CCMP' if sec != 'open' else 'None'}", ""]
            return "\n".join(lines)
        if low[:1] == ["connect"]:
            kv = {}
            for t in toks[1:]:
                k, _, v = t.partition("=")
                kv[k.lower()] = v
            ssid = kv.get("name") or kv.get("ssid")
            if not ssid:
                return "One or more parameters for the command are not correct or missing."
            r = wifi_connect(d, ssid, kv.get("key"))
            if r == "ok":
                return "Connection request was completed successfully."
            if r == "auth":
                return f'The network security key for "{ssid}" is incorrect or missing (use key=...).'
            return f'The network specified by profile "{ssid}" is not available to connect.'
        if low[:1] == ["disconnect"]:
            wifi_disconnect(d)
            return 'Disconnection request was completed successfully for interface "Wi-Fi".'
        return "The following command was not found: wlan " + " ".join(toks)

    def _netsh4(self, a):
        d = self.device
        if not a:
            return "The syntax supplied for this command is not valid. Check help for the correct syntax."
        verb, obj, rest = a[0], (a[1] if len(a) > 1 else ""), a[2:]
        pos = [x for x in rest if "=" not in x]
        kv = _kv(rest)
        if verb == "set" and obj == "address":
            pos = pos[1:] if pos and not is_valid_ip(pos[0]) and pos[0] not in ("static", "dhcp") else pos
            source = kv.get("source") or (pos[0] if pos else None)
            if source == "dhcp":
                d.start_dhcp()
                return None
            vals = [x for x in pos if is_valid_ip(x)]
            ip = kv.get("address") or (vals[0] if vals else None)
            mask = kv.get("mask") or (vals[1] if len(vals) > 1 else None)
            gw = kv.get("gateway") or (vals[2] if len(vals) > 2 else None)
            if not ip or not mask or not is_valid_ip(ip) or not is_valid_mask(mask) or (gw and not is_valid_ip(gw)):
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            if d.dhcp_job is not None and not d.dhcp_job.done:
                d.dhcp_job.cancel()
            d.dhcp_enabled = False
            d.set_ip(ip, mask)
            d.set_gateway(gw)
            d.engine.emit("device_changed", d)
            return None
        if verb in ("set", "add") and obj in ("dnsservers", "dns", "dnsserver"):
            pos = [x for x in pos if x not in ("primary", "both", "none", "validate=no")]
            source = kv.get("source") or next((x for x in pos if x in ("static", "dhcp")), None)
            ips = [x for x in pos if is_valid_ip(x)] + ([kv["address"]] if is_valid_ip(kv.get("address", "")) else [])
            if source == "dhcp" and not ips:
                return None
            if not ips:
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            if verb == "set":
                d.dns, d.dns_alt = IPv4Address(ips[0]), []
            elif d.dns is None:
                d.dns = IPv4Address(ips[0])
            else:
                d.dns_alt.append(IPv4Address(ips[0]))
            d.engine.emit("device_changed", d)
            return None
        if verb == "delete" and obj in ("dnsservers", "dns"):
            d.dns, d.dns_alt = None, []
            return None
        if verb == "add" and obj == "route":
            try:
                net = IPv4Network(pos[0], strict=False)
                gw = next(x for x in pos[1:] + [kv.get("nexthop", "")] if is_valid_ip(x))
            except (IndexError, StopIteration, ValueError):
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            d.add_route(net, gw, kv.get("store") != "active")
            return "Ok."
        if verb == "delete" and obj == "route":
            try:
                ok = d.del_route(IPv4Network(pos[0], strict=False))
            except (IndexError, ValueError):
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            return "Ok." if ok else "Element not found."
        if verb == "show":
            return self._netsh4_show(obj)
        return "The syntax supplied for this command is not valid. Check help for the correct syntax."

    def _netsh4_show(self, obj):
        d = self.device
        p = d.nic
        if obj in ("config", "addresses", "address", "ipaddresses"):
            lines = ["", f'Configuration for interface "{p.name}"',
                     f"    DHCP enabled:                         {'Yes' if d.dhcp_enabled else 'No'}"]
            if p.ip:
                lines += [f"    IP Address:                           {p.ip.ip}",
                          f"    Subnet Prefix:                        {p.ip.network} (mask {p.ip.netmask})"]
            if d.gateway:
                lines += [f"    Default Gateway:                      {d.gateway}", "    Gateway Metric:                       0"]
            lines.append("    InterfaceMetric:                      25")
            if obj == "config":
                srv = d.dns_server_list
                lab = "DNS servers configured through DHCP:" if d.dhcp_enabled else "Statically Configured DNS Servers:"
                lines.append(f"    {lab:<38}{srv[0] if srv else 'None'}")
                lines += [f"{'':<42}{s}" for s in srv[1:]]
            return "\n".join(lines)
        if obj in ("dnsservers", "dns"):
            srv = d.dns_server_list
            return (f'\nConfiguration for interface "{p.name}"\n'
                    f"    Statically Configured DNS Servers:    {srv[0] if srv else 'None'}"
                    + "".join(f"\n{'':<42}{s}" for s in srv[1:]))
        if obj == "route":
            lines = ["", "Publish  Type      Met  Prefix                    Idx  Gateway/Interface Name",
                     "-------  --------  ---  ------------------------  ---  ------------------------"]
            if p.ip:
                lines.append(f"No       System    256  {str(p.ip.network):<26}{IF_INDEX:<5}{p.name}")
            if d.gateway:
                lines.append(f"No       Manual    0    {'0.0.0.0/0':<26}{IF_INDEX:<5}{d.gateway}")
            for n, g, _ in d.routes:
                lines.append(f"No       Manual    1    {str(n):<26}{IF_INDEX:<5}{g or p.name}")
            return "\n".join(lines)
        if obj in ("neighbors", "neighbours"):
            lines = ["", f"Interface {IF_INDEX}: {p.name}", "",
                     "Internet Address                              Physical Address   Type",
                     "--------------------------------------------  -----------------  -----------"]
            for e in d.arp.entries(d.engine.now):
                lines.append(f"{str(e.ip):<46}{win_mac(e.mac):<19}{'Permanent' if e.static else 'Reachable'}")
            return "\n".join(lines)
        if obj in ("interfaces", "interface"):
            return ("\nIdx     Met         MTU          State                Name\n"
                    "---  ----------  ----------  ------------  ---------------------------\n"
                    f"  1          75  4294967295  connected     Loopback Pseudo-Interface 1\n"
                    f"{IF_INDEX:>3}          25        1500  {'connected' if p.is_up else 'disconnected':<14}{p.name}")
        return "The syntax supplied for this command is not valid. Check help for the correct syntax."

    def _netsh6(self, a, raw):
        from ..stack.ipv6 import StaticRoute6
        d = self.device
        p = d.nic
        if not a:
            return "The syntax supplied for this command is not valid. Check help for the correct syntax."
        verb, obj, rest = a[0], (a[1] if len(a) > 1 else ""), a[2:]
        kv = _kv(rest)
        pos = [x for x in rest if "=" not in x]
        if obj == "address" and verb in ("add", "delete", "set"):
            addr = kv.get("address") or next((x for x in pos if ":" in x), None)
            if addr is None:
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            try:
                ai = IPv6Interface(addr if "/" in addr else addr + "/64")
            except ValueError:
                return "The parameter is incorrect."
            if verb == "delete":
                if ai.ip not in [x.ip for x in p.ipv6]:
                    return "Element not found."
                p.ipv6 = [x for x in p.ipv6 if x.ip != ai.ip]
            else:
                if any(x.ip == ai.ip for x in p.ipv6):
                    return "The object already exists."
                p.ipv6.append(ai)
            d.engine.emit("device_changed", d)
            return None
        if obj == "route" and verb in ("add", "delete"):
            try:
                net = IPv6Network(pos[0], strict=False)
            except (IndexError, ValueError):
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            if verb == "delete":
                before = len(d.static_routes6)
                d.static_routes6 = [r for r in d.static_routes6 if r.network != net]
                return "Ok." if before != len(d.static_routes6) else "Element not found."
            gw = kv.get("nexthop") or next((x for x in pos[1:] if ":" in x), None)
            try:
                g = IPv6Address(gw) if gw else None
            except ValueError:
                return "The parameter is incorrect."
            d.static_routes6 = [r for r in d.static_routes6 if r.network != net]
            d.static_routes6.append(StaticRoute6(net, g, p.name))
            return "Ok."
        if verb == "set" and obj in ("interface", "int"):
            rd = kv.get("routerdiscovery")
            if rd is None:
                return "The syntax supplied for this command is not valid. Check help for the correct syntax."
            if rd.startswith("enable"):
                d.start_slaac()
            else:
                p.ipv6_auto = False
                d.ra_routers.clear()
                p.ipv6 = [x for x in p.ipv6 if x not in getattr(p, "_slaac", set())]
            return "Ok."
        if verb == "show":
            if obj in ("address", "addresses"):
                lines = ["", f"Interface {IF_INDEX}: {p.name}", "",
                         "Addr Type  DAD State   Valid Life Pref. Life Address",
                         "---------  ----------- ---------- ---------- ------------------------"]
                for x in p.ipv6:
                    kind = "Public" if x not in getattr(p, "_slaac", set()) else "Public"
                    life = "infinite" if x not in getattr(p, "_slaac", set()) else "29d23h59m"
                    lines.append(f"{('Manual' if life == 'infinite' else kind):<11}{'Preferred':<12}{life:<11}{life:<11}{x.ip}")
                ll = d.link_local(p)
                if ll is not None:
                    lines.append(f"{'Other':<11}{'Preferred':<12}{'infinite':<11}{'infinite':<11}{ll.ip}%{IF_INDEX}")
                return "\n".join(lines)
            if obj == "route":
                lines = ["", "Publish  Type      Met  Prefix                    Idx  Gateway/Interface Name",
                         "-------  --------  ---  ------------------------  ---  ------------------------"]
                for r in d.routing_table6():
                    typ = "System" if r.code in ("C", "L") else "Manual"
                    gw = str(r.next_hop) if r.next_hop and r.code not in ("C", "L") else p.name
                    lines.append(f"No       {typ:<10}{'256':<5}{str(r.network):<26}{IF_INDEX:<5}{gw}")
                return "\n".join(lines)
            if obj in ("neighbors", "neighbours"):
                lines = ["", f"Interface {IF_INDEX}: {p.name}", "",
                         "Internet Address                              Physical Address   Type",
                         "--------------------------------------------  -----------------  -----------"]
                for n in d.nd_cache.values():
                    lines.append(f"{str(n.ip):<46}{win_mac(n.mac):<19}{'Reachable (Router)' if n.router else 'Reachable'}")
                return "\n".join(lines)
        return "The syntax supplied for this command is not valid. Check help for the correct syntax."


def _kv(tokens):
    out = {}
    for t in tokens:
        if "=" in t:
            k, _, v = t.partition("=")
            out[k.lower()] = v.lower()
    return out


def _classful(ip: str):
    first = int(ip.split(".")[0])
    plen = 8 if first < 128 else 16 if first < 192 else 24
    return IPv4Network(f"{ip}/{plen}", strict=False)


def parse_ssh_args(args):
    """ssh [-l user] [-p port] [user@]host  ->  (user, host, port) or an error string."""
    user, port, host = "", None, None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-l", "-p") and i + 1 < len(args):
            if a == "-l":
                user = args[i + 1]
            else:
                if not args[i + 1].isdigit():
                    return f"Bad port '{args[i + 1]}'"
                port = int(args[i + 1])
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        host = a
        i += 1
    if host is None:
        return "usage: ssh [-l login_name] [-p port] [user@]hostname"
    if "@" in host:
        user, host = host.split("@", 1)
    return user, host, port
