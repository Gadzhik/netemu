"""Linux shell of end hosts (Ubuntu-like): iproute2, net-tools, ping, traceroute, dig, dhclient ..."""
from __future__ import annotations

import shlex
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Interface, IPv6Network

from ..addresses import MacAddress, is_valid_ip, is_valid_mask
from ..stack.icmp import PingListener, PingSession, TraceListener, TracerouteSession
from .base import CliSession, make_ping
from .host import Chain, curl_text

HELP = """NetEmu Linux shell (Ubuntu 24.04 style). Changing the configuration needs root: prefix with sudo.
ip         iproute2:
           ip [-4|-6] [-br] addr [show] | ip addr add|del <addr>/<len> dev eth0 | ip addr flush dev eth0
           ip link [show] | ip link set eth0 up|down
           ip [-6] route [show] | ip route add|del default|<net> via <gw> [dev eth0] | ip route get <ip>
           ip [-6] neigh [show] | ip neigh flush all
ifconfig   ifconfig [eth0] | ifconfig eth0 <ip> netmask <mask> | ifconfig eth0 <ip>/<len> | ifconfig eth0 up|down
           ifconfig eth0 inet6 add|del <addr>/<len>
route      route -n | route add default gw <gw> | route add -net <net> netmask <mask> gw <gw> | route del ...
arp        arp -n | arp -a | arp -d <ip> | arp -s <ip> <mac>
ping       ping [-c count] [-s size] [-t ttl] [-i interval] [-W timeout] [-4|-6] <host>   (Ctrl+C stops)
ping6      ping -6
traceroute traceroute [-4|-6] [-n] [-m max_ttl] [-w wait] <host>     (also traceroute6, tracepath)
dig        dig [@server] <name> [+short]
host       host <name> [server]
nslookup   nslookup <name> [server]
curl       curl [-I] [-s] http://<host>[/page]
wget       wget http://<host>[/page]
dhclient   dhclient [-v] eth0 | dhclient -r eth0 | dhclient -6 [-v] eth0 (DHCPv6)
resolvectl resolvectl status | resolvectl dns eth0 <server>
cat        cat /etc/resolv.conf | /etc/hostname | /etc/hosts | /proc/sys/net/ipv4/ip_forward
echo       echo nameserver <ip> > /etc/resolv.conf   (also: ... | sudo tee /etc/resolv.conf)
ss         ss -tuln            (also netstat -tuln, netstat -rn, netstat -i)
sysctl     sysctl net.ipv4.ip_forward | sysctl -w net.ipv6.conf.eth0.accept_ra=0|1
ethtool    ethtool eth0
hostname   hostname [new-name] | hostnamectl [set-hostname <name>]
snmp       snmpget|snmpwalk -v2c -c <community> <agent> [oid] | snmpset -v2c -c <c> <agent> sysName.0 s <value>
syslog     cat /var/log/syslog   (Linux server: messages from network devices)
ssh        ssh [-l user] [-p port] [user@]<ip>  |  telnet <ip> [port]
wifi       nmcli dev status | nmcli dev wifi list | nmcli dev wifi connect <SSID> password <key> | iw dev wlan0 link
sshd       sudo systemctl start|stop|enable ssh | systemctl status ssh | passwd [user]
other      sudo, sudo -i, su, exit, logout, whoami, id, uname [-a], pwd, date, uptime, clear, help
"""
ROOT_CMDS = {"dhclient"}


def _mac(m) -> str:
    h = f"{m.value:012x}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


class LinuxPing(PingListener):
    def __init__(self, write, name=None, v6=False):
        self.w, self.name, self.v6 = write, name, v6
        self.failed = False
        self.session = None

    def _who(self, src):
        return f"{self.name} ({src})" if self.name else str(src)

    def on_start(self, s):
        self.session = s
        self.dst = s.dst
        self.size = s.size
        label = f"{self.name} ({s.dst})" if self.name else f"{s.dst} ({s.dst})"
        if self.v6:
            self.w(f"PING {self.name or s.dst}({s.dst}) {s.size} data bytes\n")
        else:
            self.w(f"PING {label} {s.size}({s.size + 28}) bytes of data.\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        self.w(f"{size + 8} bytes from {self._who(src)}: icmp_seq={seq + 1} ttl={ttl} time={max(rtt, 0.05):.3f} ms\n")

    def on_error(self, seq, kind, src):
        text = "Destination Host Unreachable" if kind == "unreachable" else "Time to live exceeded"
        self.w(f"From {src} icmp_seq={seq + 1} {text}\n")

    def on_send_fail(self, seq):
        if not self.failed:
            self.failed = True
            self.w("ping: connect: Network is unreachable\n")
            if self.session is not None:
                self.session.engine.schedule(0.0, self.session.cancel)

    def on_finish(self, st):
        if self.failed:
            return
        loss = st.loss_percent
        total = int(max(0, st.sent - 1) * 1000 + 1)
        self.w(f"\n--- {self.name or self.dst} ping statistics ---\n"
               f"{st.sent} packets transmitted, {st.received} received, {loss}% packet loss, time {total}ms\n")
        if st.rtts:
            mn, mx = min(st.rtts), max(st.rtts)
            avg = sum(st.rtts) / len(st.rtts)
            mdev = (sum((r - avg) ** 2 for r in st.rtts) / len(st.rtts)) ** 0.5
            self.w(f"rtt min/avg/max/mdev = {max(mn, 0.05):.3f}/{max(avg, 0.05):.3f}/{max(mx, 0.05):.3f}/{mdev:.3f} ms\n")


class LinuxTrace(TraceListener):
    def __init__(self, write, name=None, numeric=False):
        self.w, self.name, self.numeric = write, name, numeric

    def on_start(self, s):
        self.w(f"traceroute to {self.name or s.dst} ({s.dst}), {s.max_hops} hops max, 60 byte packets\n")

    def on_hop(self, hop):
        if hop.address is None:
            self.w(f"{hop.ttl:>2}  * * *\n")
            return
        who = str(hop.address) if self.numeric else f"{hop.address} ({hop.address})"
        mark = " !H" if hop.unreachable else ""
        times = "  ".join("*" if r is None else f"{max(r, 0.05):.3f} ms{mark}" for r in hop.probes)
        self.w(f"{hop.ttl:>2}  {who}  {times}\n")


class LinuxSession(CliSession):
    def __init__(self, device):
        super().__init__(device)
        self.wants_clear = False
        self.root = False

    @property
    def user(self) -> str:
        return "root" if self.root else getattr(self, "user_name", "user")

    @property
    def prompt(self) -> str:
        home = "~"
        return f"{self.user}@{self.device.hostname}:{home}{'#' if self.root else '$'} "

    def banner(self) -> str:
        return "\nWelcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.0-45-generic x86_64) — NetEmu\nType 'help' for the list of commands.\n"

    def help(self, line: str) -> str:
        return HELP

    CMDS = ["arp", "cat", "clear", "curl", "date", "dhclient", "dig", "echo", "ethtool", "exit", "help", "host",
            "hostname", "hostnamectl", "id", "ifconfig", "ip", "logout", "netstat", "nslookup", "ping", "ping6", "pwd",
            "iw", "nmcli", "passwd", "resolvectl", "route", "ss", "ssh", "su", "sudo", "sysctl", "systemctl", "telnet", "tracepath", "traceroute", "traceroute6", "uname", "uptime", "wget",
            "whoami"]

    def complete(self, line: str) -> str | None:
        if " " in line:
            return None
        m = [c for c in self.CMDS if c.startswith(line)]
        return m[0] + " " if len(m) == 1 else None

    # -- dispatch ------------------------------------------------------------------------------
    def _execute(self, line: str):
        text = line.strip()
        if not text or text.startswith("#"):
            return None
        tee = None
        redirect = None
        if "|" in text:
            left, right = text.split("|", 1)
            rt = _split(right)
            if rt[:1] == ["sudo"]:
                rt = rt[1:]
            if rt[:1] == ["tee"]:
                tee = (rt[-1], right.strip().startswith("sudo") or self.root)
                text = left.strip()
            elif rt[:1] in (["grep"],):
                pass
        if ">" in text and text.split()[0] == "echo":
            text, _, redirect = text.partition(">")
            redirect = redirect.strip().lstrip(">").strip()
        args = _split(text)
        if not args:
            return None
        sudo = False
        while args and args[0] == "sudo":
            sudo = True
            args = args[1:]
            if not args:
                return "usage: sudo -h | -K | -k | -V\nusage: sudo [-u user] command"
        if args[0] in ("-i", "-s", "su") and sudo:
            self.root = True
            return None
        root = self.root or sudo
        cmd, a = args[0], args[1:]
        if cmd == "echo":
            msg = " ".join(a)
            target = redirect or (tee[0] if tee else None)
            if target:
                can = root if redirect else tee[1]
                out = self._write_file(target, msg, can)
                return (msg + "\n" + out).strip() if tee and not out.startswith(("tee", "-bash")) else out or (
                    msg if tee else None)
            return msg
        if "|" in line and not tee:
            pass
        fn = getattr(self, "c_" + cmd.replace("-", "_"), None)
        if fn is None:
            if cmd in ("exit", "logout"):
                if self.root:
                    self.root = False
                    return "logout"
                return None
            return f"{cmd}: command not found"
        return fn(a, root)

    @staticmethod
    def _need_root(what="Operation not permitted"):
        return what

    # -- files ---------------------------------------------------------------------------------
    def _write_file(self, path, text, root):
        d = self.device
        if path != "/etc/resolv.conf":
            return f"-bash: {path}: Permission denied" if not root else ""
        if not root:
            return "-bash: /etc/resolv.conf: Permission denied"
        w = text.split()
        if len(w) >= 2 and w[0] == "nameserver" and is_valid_ip(w[1]):
            d.dns, d.dns_alt = IPv4Address(w[1]), []
            d.engine.emit("device_changed", d)
        return ""

    def c_cat(self, a, root):
        d = self.device
        out = []
        for path in a:
            if path == "/etc/resolv.conf":
                srv = d.dns_server_list
                lines = ["# This is /run/systemd/resolve/resolv.conf managed by NetEmu"]
                lines += [f"nameserver {s}" for s in srv]
                if d.dhcp_info.get("domain"):
                    lines.append(f"search {d.dhcp_info['domain']}")
                out.append("\n".join(lines))
            elif path == "/var/log/syslog" and hasattr(d, "syslog_server"):
                out.append("\n".join(f"{t} {ip} {m}" for t, ip, m in d.syslog_server.messages))
            elif path == "/etc/hostname":
                out.append(d.hostname)
            elif path == "/etc/hosts":
                out.append(f"127.0.0.1 localhost\n127.0.1.1 {d.hostname}\n\n::1     ip6-localhost ip6-loopback")
            elif path == "/proc/sys/net/ipv4/ip_forward":
                out.append("0")
            elif path in ("/etc/os-release",):
                out.append('PRETTY_NAME="Ubuntu 24.04.1 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"')
            else:
                out.append(f"cat: {path}: No such file or directory")
        return "\n".join(out) if a else None

    # -- misc ----------------------------------------------------------------------------------
    def c_help(self, a, root):
        return HELP

    def c_clear(self, a, root):
        self.wants_clear = True
        return None

    def c_whoami(self, a, root):
        return "root" if root else "user"

    def c_id(self, a, root):
        return ("uid=0(root) gid=0(root) groups=0(root)" if root else
                "uid=1000(user) gid=1000(user) groups=1000(user),4(adm),27(sudo)")

    def c_pwd(self, a, root):
        return "/root" if self.root else "/home/user"

    def c_su(self, a, root):
        if root:
            self.root = True
            return None
        return "Password: \nsu: Authentication failure   (use: sudo -i)"

    def c_uname(self, a, root):
        if "-a" in a:
            return f"Linux {self.device.hostname} 6.8.0-45-generic #45-Ubuntu SMP PREEMPT_DYNAMIC x86_64 x86_64 x86_64 GNU/Linux"
        if "-r" in a:
            return "6.8.0-45-generic"
        return "Linux"

    def c_date(self, a, root):
        ms = int(self.device.engine.now)
        return f"Fri Nov 20 {(ms // 3_600_000) % 24:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d} UTC 2026"

    def c_uptime(self, a, root):
        ms = int(self.device.engine.now)
        return f" {(ms // 3_600_000) % 24:02d}:{(ms // 60_000) % 60:02d}:{(ms // 1000) % 60:02d} up {ms // 60_000} min,  1 user,  load average: 0.00, 0.00, 0.00"

    def c_hostname(self, a, root):
        d = self.device
        if not a:
            return d.hostname
        if a[0] in ("-I", "-i"):
            return " ".join([str(d.nic.ip.ip)] if d.nic.ip else []) + (" " if d.nic.ip else "")
        if not root:
            return "hostname: you must be root to change the host name"
        return self._set_hostname(a[0])

    def c_hostnamectl(self, a, root):
        d = self.device
        if a[:1] == ["set-hostname"] and len(a) == 2:
            if not root:
                return "Could not set hostname: Access denied"
            return self._set_hostname(a[1])
        return (f" Static hostname: {d.hostname}\n       Icon name: computer-desktop\n"
                f"Operating System: Ubuntu 24.04.1 LTS\n          Kernel: Linux 6.8.0-45-generic\n    Architecture: x86-64")

    def _set_hostname(self, name):
        d = self.device
        d.hostname = name
        d.name = name
        d.engine.emit("device_changed", d)
        return None

    def c_sudo(self, a, root):           # only reached for "sudo" alone (handled in _execute)
        return "usage: sudo [-u user] command"

    # -- ip ------------------------------------------------------------------------------------
    def c_ip(self, a, root):
        fam, brief = None, False
        while a and a[0].startswith("-"):
            o = a.pop(0)
            if o in ("-4", "-6"):
                fam = o
            elif o in ("-br", "-brief"):
                brief = True
            elif o in ("-c", "-color", "-s", "-d"):
                pass
            else:
                return f'Option "{o}" is unknown, try "ip -help".'
        if not a:
            return ("Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n"
                    "where  OBJECT := { address | link | neighbor | route }\n"
                    "       OPTIONS := { -4 | -6 | -br[ief] }")
        obj, rest = a[0], a[1:]
        if "address".startswith(obj) and obj:
            return self._ip_addr(rest, fam, brief, root)
        if "link".startswith(obj):
            return self._ip_link(rest, brief, root)
        if "route".startswith(obj) and obj not in ("ro",) or obj in ("r", "ro"):
            return self._ip_route(rest, fam, root)
        if obj in ("n", "ne", "nei", "neigh", "neighbor", "neighbour"):
            return self._ip_neigh(rest, fam, root)
        return f'Object "{obj}" is unknown, try "ip help".'

    def _state(self, p):
        if not p.admin_up:
            return "<BROADCAST,MULTICAST>", "DOWN"
        if not p.is_up:
            return "<NO-CARRIER,BROADCAST,MULTICAST,UP>", "DOWN"
        return "<BROADCAST,MULTICAST,UP,LOWER_UP>", "UP"

    def _ip_addr(self, rest, fam, brief, root):
        d = self.device
        p = d.nic
        verb = rest[0] if rest else "show"
        if verb in ("show", "list", "ls", "s", "sh") or verb == "dev":
            if brief:
                flags, st = self._state(p)
                addrs = ([] if fam == "-6" else ([p.ip.with_prefixlen] if p.ip else []))
                addrs += [] if fam == "-4" else [str(x) for x in self._v6(p)]
                return (f"{'lo':<17}{'UNKNOWN':<15}127.0.0.1/8 ::1/128\n"
                        f"{p.name:<17}{st:<15}{' '.join(addrs)}")
            return "\n".join(self._addr_lines(fam))
        if verb in ("add", "a", "del", "delete", "d", "replace", "change"):
            if not root:
                return "RTNETLINK answers: Operation not permitted"
            kv = _opts(rest[1:])
            addr = rest[1] if len(rest) > 1 else None
            if addr is None or kv.get("dev", p.name) != p.name:
                return f'Cannot find device "{kv.get("dev")}"' if addr else "Not enough information: \"dev\" argument is required."
            if "dev" not in kv:
                return 'Not enough information: "dev" argument is required.'
            if ":" in addr:
                try:
                    a6 = IPv6Interface(addr if "/" in addr else addr + "/128")
                except ValueError:
                    return f'Error: inet6 prefix is expected rather than "{addr}".'
                if verb.startswith("d"):
                    if a6 not in p.ipv6:
                        return "RTNETLINK answers: Cannot assign requested address"
                    p.ipv6.remove(a6)
                else:
                    if a6 in p.ipv6:
                        return "RTNETLINK answers: File exists"
                    p.ipv6.append(a6)
                d.engine.emit("device_changed", d)
                return None
            try:
                a4 = IPv4Interface(addr if "/" in addr else addr + "/32")
            except ValueError:
                return f'Error: any valid prefix is expected rather than "{addr}".'
            if verb.startswith("d"):
                if p.ip != a4:
                    return "RTNETLINK answers: Cannot assign requested address"
                p.ip = None
            else:
                if p.ip == a4 and verb in ("add", "a"):
                    return "RTNETLINK answers: File exists"
                self._stop_dhcp()
                p.ip = a4
                d.arp.clear()
            d.engine.emit("device_changed", d)
            return None
        if verb == "flush":
            if not root:
                return "Failed to send flush request: Operation not permitted"
            self._stop_dhcp()
            if fam != "-6":
                p.ip = None
                d.gateway = None
                d.routes = []
            if fam != "-4":
                p.ipv6 = []
            d.engine.emit("device_changed", d)
            return None
        return f'Command "{verb}" is unknown, try "ip address help".'

    def _v6(self, p):
        out = list(p.ipv6)
        ll = self.device.link_local(p)
        if ll is not None:
            out.append(ll)
        return out

    def _addr_lines(self, fam=None):
        d = self.device
        p = d.nic
        flags, st = self._state(p)
        lines = ["1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000",
                 "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00"]
        if fam != "-6":
            lines += ["    inet 127.0.0.1/8 scope host lo", "       valid_lft forever preferred_lft forever"]
        if fam != "-4":
            lines += ["    inet6 ::1/128 scope host noprefixroute", "       valid_lft forever preferred_lft forever"]
        lines += [f"2: {p.name}: {flags} mtu 1500 qdisc fq_codel state {st} group default qlen 1000",
                  f"    link/ether {_mac(p.mac)} brd ff:ff:ff:ff:ff:ff"]
        if p.ip and fam != "-6":
            dyn = " dynamic" if d.dhcp_enabled and d.dhcp_info.get("server") else ""
            lft = f"{int(d.dhcp_info.get('lease', 86400))}sec" if dyn else "forever"
            lines += [f"    inet {p.ip.with_prefixlen} brd {p.ip.network.broadcast_address} scope global{dyn} {p.name}",
                      f"       valid_lft {lft} preferred_lft {lft}"]
        if fam != "-4":
            slaac = getattr(p, "_slaac", set())
            for x in p.ipv6:
                dyn = " dynamic mngtmpaddr noprefixroute" if x in slaac else ""
                lft = "2591999sec" if x in slaac else "forever"
                lines += [f"    inet6 {x} scope global{dyn}", f"       valid_lft {lft} preferred_lft {lft}"]
            ll = d.link_local(p)
            if ll is not None:
                lines += [f"    inet6 {ll} scope link", "       valid_lft forever preferred_lft forever"]
        return lines

    def _ip_link(self, rest, brief, root):
        d = self.device
        p = d.nic
        verb = rest[0] if rest else "show"
        if verb in ("show", "list", "ls", "sh"):
            flags, st = self._state(p)
            if brief:
                return f"{'lo':<17}{'UNKNOWN':<15}00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>\n{p.name:<17}{st:<15}{_mac(p.mac)} {flags}"
            return "\n".join([
                "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000",
                "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00",
                f"2: {p.name}: {flags} mtu 1500 qdisc fq_codel state {st} mode DEFAULT group default qlen 1000",
                f"    link/ether {_mac(p.mac)} brd ff:ff:ff:ff:ff:ff"])
        if verb == "set":
            if len(rest) < 3:
                return 'Not enough information: "dev" argument is required.'
            dev = rest[2] if rest[1] == "dev" else rest[1]
            if dev != p.name:
                return f'Cannot find device "{dev}"'
            if not root:
                return "RTNETLINK answers: Operation not permitted"
            if "up" in rest:
                p.set_admin(True)
            elif "down" in rest:
                p.set_admin(False)
            else:
                return 'Error: argument "{}" is wrong: unknown link option'.format(rest[-1])
            d.engine.emit("device_changed", d)
            return None
        return f'Command "{verb}" is unknown, try "ip link help".'

    def _ip_route(self, rest, fam, root):
        d = self.device
        verb = rest[0] if rest else "show"
        if verb in ("show", "list", "ls", "sh"):
            return self._route_lines(fam)
        if verb == "get":
            if len(rest) < 2:
                return 'need at least a destination address'
            return self._route_get(rest[1])
        if verb in ("add", "del", "delete", "replace", "change", "append"):
            if not root:
                return "RTNETLINK answers: Operation not permitted"
            if len(rest) < 2:
                return "Error: an inet prefix is expected."
            dest = rest[1]
            kv = _opts(rest[2:])
            gw = kv.get("via")
            dev = kv.get("dev")
            if dev is not None and dev != d.nic.name:
                return f'Cannot find device "{dev}"'
            six = fam == "-6" or ":" in dest or (gw is not None and ":" in gw)
            if six:
                return self._route6_change(verb, dest, gw)
            try:
                net = IPv4Network("0.0.0.0/0" if dest == "default" else (dest if "/" in dest else dest + "/32"),
                                  strict=False)
            except ValueError:
                return f'Error: inet prefix is expected rather than "{dest}".'
            if verb.startswith("del"):
                if not d.del_route(net):
                    return "RTNETLINK answers: No such process"
                d.engine.emit("device_changed", d)
                return None
            if gw is None and dev is None:
                return "Error: Device for nexthop is not up." if net.prefixlen else "RTNETLINK answers: No such device"
            if gw is not None:
                if not is_valid_ip(gw):
                    return f'Error: inet address is expected rather than "{gw}".'
                if d.nic.ip is None or IPv4Address(gw) not in d.nic.ip.network:
                    return "Error: Nexthop has invalid gateway."
            if verb in ("add", "append"):
                exists = (net.prefixlen == 0 and d.gateway is not None) or any(r[0] == net for r in d.routes)
                if exists:
                    return "RTNETLINK answers: File exists"
            d.add_route(net, gw)
            d.engine.emit("device_changed", d)
            return None
        if verb == "flush":
            if not root:
                return "Failed to send flush request: Operation not permitted"
            d.routes = []
            d.gateway = None
            return None
        return f'Command "{verb}" is unknown, try "ip route help".'

    def _route6_change(self, verb, dest, gw):
        from ..stack.ipv6 import StaticRoute6
        d = self.device
        try:
            net = IPv6Network("::/0" if dest == "default" else dest, strict=False)
            g = IPv6Address(gw) if gw else None
        except ValueError:
            return f'Error: inet6 prefix is expected rather than "{dest}".'
        if verb.startswith("del"):
            before = len(d.static_routes6)
            d.static_routes6 = [r for r in d.static_routes6 if r.network != net]
            return None if len(d.static_routes6) != before else "RTNETLINK answers: No such process"
        if verb in ("add", "append") and any(r.network == net for r in d.static_routes6):
            return "RTNETLINK answers: File exists"
        d.static_routes6 = [r for r in d.static_routes6 if r.network != net]
        d.static_routes6.append(StaticRoute6(net, g, d.nic.name))
        d.engine.emit("device_changed", d)
        return None

    def _route_lines(self, fam):
        d = self.device
        p = d.nic
        dev = p.name
        if fam == "-6":
            lines = []
            for r in d.routing_table6():
                if r.code == "L":
                    continue
                if r.code == "C":
                    lines.append(f"{r.network} dev {dev} proto kernel metric 256 pref medium")
                elif r.code == "ND":
                    lines.append(f"default via {r.next_hop} dev {dev} proto ra metric 1024 expires 1799sec hoplimit 64 pref medium")
                else:
                    tgt = "default" if r.network.prefixlen == 0 else str(r.network)
                    lines.append(f"{tgt} via {r.next_hop} dev {dev} metric 1024 pref medium")
            if d.link_local(p) is not None and p.is_up:
                lines.append(f"fe80::/64 dev {dev} proto kernel metric 256 pref medium")
            return "\n".join(lines)
        if p.ip is None or not p.is_up:
            return ""
        proto = " proto dhcp" if d.dhcp_enabled and d.dhcp_info.get("server") else " proto static"
        lines = []
        if d.gateway:
            lines.append(f"default via {d.gateway} dev {dev}{proto} metric 100")
        for net, gw, _ in sorted(d.routes, key=lambda r: (int(r[0].network_address), r[0].prefixlen)):
            lines.append(f"{net} via {gw} dev {dev}" if gw else f"{net} dev {dev} scope link")
        lines.append(f"{p.ip.network} dev {dev} proto kernel scope link src {p.ip.ip}"
                     + (" metric 100" if proto == " proto dhcp" else ""))
        return "\n".join(lines)

    def _route_get(self, dst):
        d = self.device
        p = d.nic
        if ":" in dst:
            try:
                r = d.route_lookup6(IPv6Address(dst))
            except ValueError:
                return f'Error: any valid prefix is expected rather than "{dst}".'
            if r is None:
                return "RTNETLINK answers: Network is unreachable"
            port, nh = r
            _, src = d.source_for6(IPv6Address(dst))
            via = f" via {nh}" if str(nh) != dst else ""
            return f"{dst} from ::{via} dev {port.name} proto kernel src {src} metric 256 pref medium"
        if not is_valid_ip(dst):
            return f'Error: any valid prefix is expected rather than "{dst}".'
        r = d.route_lookup(IPv4Address(dst))
        if r is None:
            return "RTNETLINK answers: Network is unreachable"
        port, nh = r
        via = f" via {nh}" if str(nh) != dst else ""
        return f"{dst}{via} dev {port.name} src {p.ip.ip} uid {0 if self.root else 1000}\n    cache"

    def _ip_neigh(self, rest, fam, root):
        d = self.device
        verb = rest[0] if rest else "show"
        if verb == "flush":
            if not root:
                return "Failed to send flush request: Operation not permitted"
            d.arp.clear()
            d.nd_cache.clear()
            return None
        if verb in ("show", "list", "ls", "sh"):
            lines = []
            if fam != "-6":
                for e in d.arp.entries(d.engine.now):
                    lines.append(f"{e.ip} dev {e.port.name} lladdr {_mac(e.mac)} {'PERMANENT' if e.static else 'REACHABLE'}")
            if fam != "-4":
                for n in d.nd_cache.values():
                    lines.append(f"{n.ip} dev {n.port.name} lladdr {_mac(n.mac)}{' router' if n.router else ''} REACHABLE")
            return "\n".join(lines)
        if verb in ("add", "replace") and len(rest) >= 4:
            if not root:
                return "RTNETLINK answers: Operation not permitted"
            kv = _opts(rest[2:])
            return self._arp_add(rest[1], kv.get("lladdr", ""))
        if verb in ("del", "delete") and len(rest) >= 2:
            if not root:
                return "RTNETLINK answers: Operation not permitted"
            if d.arp._entries.pop(IPv4Address(rest[1]), None) is None if is_valid_ip(rest[1]) else True:
                return "RTNETLINK answers: No such file or directory"
            return None
        return f'Command "{verb}" is unknown, try "ip neigh help".'

    def _arp_add(self, ip, mac):
        from ..stack.arp import ArpEntry
        d = self.device
        try:
            m = MacAddress.parse(mac)
        except ValueError:
            return f"{mac}: invalid link-layer address"
        if not is_valid_ip(ip):
            return f"{ip}: Unknown host"
        d.arp._entries[IPv4Address(ip)] = ArpEntry(IPv4Address(ip), m, d.nic, d.engine.now, static=True)
        return None

    def _stop_dhcp(self):
        d = self.device
        if d.dhcp_job is not None and not d.dhcp_job.done:
            d.dhcp_job.cancel()
        d.dhcp_enabled = False

    # -- net-tools -----------------------------------------------------------------------------
    def c_ifconfig(self, a, root):
        d = self.device
        p = d.nic
        if not a or a == ["-a"] or a == [p.name]:
            return self._ifconfig_text()
        if a[0] != p.name:
            return f"{a[0]}: error fetching interface information: Device not found"
        if not root:
            return "SIOCSIFADDR: Operation not permitted"
        rest = a[1:]
        if rest == ["up"] or rest == ["down"]:
            p.set_admin(rest[0] == "up")
            d.engine.emit("device_changed", d)
            return None
        if rest[:1] == ["inet6"] and len(rest) >= 3:
            try:
                a6 = IPv6Interface(rest[2] if "/" in rest[2] else rest[2] + "/64")
            except ValueError:
                return f"{rest[2]}: Unknown host"
            if rest[1] == "add":
                if a6 not in p.ipv6:
                    p.ipv6.append(a6)
            elif a6 in p.ipv6:
                p.ipv6.remove(a6)
            d.engine.emit("device_changed", d)
            return None
        addr = rest[0] if rest else ""
        if rest[:1] == ["inet"]:
            rest = rest[1:]
            addr = rest[0] if rest else ""
        mask = None
        if "netmask" in rest:
            i = rest.index("netmask")
            mask = rest[i + 1] if i + 1 < len(rest) else None
            if mask is None or not is_valid_mask(mask):
                return f"{mask}: Unknown host"
        try:
            if "/" in addr:
                iface = IPv4Interface(addr)
            else:
                if not is_valid_ip(addr):
                    return f"{addr}: Unknown host\nifconfig: `--help' gives usage information."
                from ..addresses import default_mask_for
                iface = IPv4Interface(f"{addr}/{mask or default_mask_for(addr)}")
        except ValueError:
            return f"{addr}: Unknown host"
        self._stop_dhcp()
        p.ip = iface
        d.arp.clear()
        if "down" in rest:
            p.set_admin(False)
        elif "up" in rest:
            p.set_admin(True)
        d.engine.emit("device_changed", d)
        return None

    def _ifconfig_text(self):
        d = self.device
        p = d.nic
        fl = 4163 if p.is_up else (4099 if p.admin_up else 4098)
        names = {4163: "UP,BROADCAST,RUNNING,MULTICAST", 4099: "UP,BROADCAST,MULTICAST", 4098: "BROADCAST,MULTICAST"}
        lines = [f"{p.name}: flags={fl}<{names[fl]}>  mtu 1500"]
        if p.ip:
            lines.append(f"        inet {p.ip.ip}  netmask {p.ip.netmask}  broadcast {p.ip.network.broadcast_address}")
        for x in self._v6(p):
            scope = "0x20<link>" if x.ip.is_link_local else "0x0<global>"
            lines.append(f"        inet6 {x.ip}  prefixlen {x.network.prefixlen}  scopeid {scope}")
        lines += [f"        ether {_mac(p.mac)}  txqueuelen 1000  (Ethernet)",
                  f"        RX packets {p.rx_frames}  bytes {p.rx_frames * 98} ({p.rx_frames * 98 / 1000:.1f} KB)",
                  "        RX errors 0  dropped 0  overruns 0  frame 0",
                  f"        TX packets {p.tx_frames}  bytes {p.tx_frames * 98} ({p.tx_frames * 98 / 1000:.1f} KB)",
                  "        TX errors 0  dropped 0 overruns 0  carrier 0  collisions 0", "",
                  "lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536",
                  "        inet 127.0.0.1  netmask 255.0.0.0", "        inet6 ::1  prefixlen 128  scopeid 0x10<host>",
                  "        loop  txqueuelen 1000  (Local Loopback)", ""]
        return "\n".join(lines)

    def c_route(self, a, root):
        d = self.device
        p = d.nic
        if not a or set(a) <= {"-n", "-e", "-v"}:
            num = "-n" in a
            name = lambda ip: str(ip) if num or str(ip) != "0.0.0.0" else "default"
            gwname = lambda ip: str(ip) if num or str(ip) != "0.0.0.0" else "0.0.0.0"
            rows = []
            if p.ip and p.is_up:
                if d.gateway:
                    rows.append(("0.0.0.0", str(d.gateway), "0.0.0.0", "UG", 100))
                for net, gw, _ in d.routes:
                    rows.append((str(net.network_address), str(gw) if gw else "0.0.0.0", str(net.netmask),
                                 ("UGH" if net.prefixlen == 32 else "UG") if gw else "U", 0))
                rows.append((str(p.ip.network.network_address), "0.0.0.0", str(p.ip.netmask), "U", 100))
            lines = ["Kernel IP routing table",
                     "Destination     Gateway         Genmask         Flags Metric Ref    Use Iface"]
            for dst, gw, mask, fl, met in rows:
                lines.append(f"{name(dst):<16}{gwname(gw) if gw != '0.0.0.0' or num else '0.0.0.0':<16}{mask:<16}"
                             f"{fl:<6}{met:<7}{0:<7}{0:>3} {p.name}")
            return "\n".join(lines)
        if a[0] in ("add", "del"):
            if not root:
                return "SIOCADDRT: Operation not permitted" if a[0] == "add" else "SIOCDELRT: Operation not permitted"
            rest = a[1:]
            kv = _opts([x for x in rest if x not in ("-net", "-host", "default")])
            gw = kv.get("gw")
            if rest[:1] == ["default"]:
                net = IPv4Network("0.0.0.0/0")
            else:
                try:
                    dst = rest[1] if rest[0] in ("-net", "-host") else rest[0]
                    mask = kv.get("netmask") or ("255.255.255.255" if rest[0] == "-host" else None)
                    net = IPv4Network(dst if "/" in dst else f"{dst}/{mask or '255.255.255.255'}", strict=False)
                except (IndexError, ValueError):
                    return "Usage: route add -net <net> netmask <mask> gw <gw> | route add default gw <gw>"
            if a[0] == "del":
                return None if d.del_route(net) else "SIOCDELRT: No such process"
            if gw is not None:
                if not is_valid_ip(gw):
                    return f"{gw}: Unknown host"
                if p.ip is None or IPv4Address(gw) not in p.ip.network:
                    return "SIOCADDRT: Network is unreachable"
            elif net.prefixlen == 0:
                return "SIOCADDRT: No such device"
            d.add_route(net, gw)
            d.engine.emit("device_changed", d)
            return None
        return "Usage: route [-n] | route {add|del} [-net|-host] target [netmask Nm] [gw Gw] [dev If]"

    def c_arp(self, a, root):
        d = self.device
        if a[:1] == ["-d"]:
            if not root:
                return "SIOCDARP(dontpub): Operation not permitted"
            if len(a) < 2 or not is_valid_ip(a[1]) or d.arp._entries.pop(IPv4Address(a[1]), None) is None:
                return f"No ARP entry for {a[1] if len(a) > 1 else ''}"
            return None
        if a[:1] == ["-s"]:
            if not root:
                return "SIOCSARP: Operation not permitted"
            if len(a) < 3:
                return "arp: need host name"
            return self._arp_add(a[1], a[2])
        entries = d.arp.entries(d.engine.now)
        if "-a" in a:
            return "\n".join(f"? ({e.ip}) at {_mac(e.mac)} [ether] {'PERM ' if e.static else ''}on {e.port.name}"
                             for e in entries)
        lines = ["Address                  HWtype  HWaddress           Flags Mask            Iface"]
        for e in entries:
            lines.append(f"{str(e.ip):<25}{'ether':<8}{_mac(e.mac):<20}{'CM' if e.static else 'C':<6}{'':<16}{e.port.name}")
        return "\n".join(lines)

    def c_netstat(self, a, root):
        flags = "".join(x.lstrip("-") for x in a)
        if "r" in flags:
            return self.c_route(["-n"], root).replace("Kernel IP routing table", "Kernel IP routing table", 1)
        if "i" in flags:
            p = self.device.nic
            return ("Kernel Interface table\nIface      MTU    RX-OK RX-ERR RX-DRP RX-OVR    TX-OK TX-ERR TX-DRP TX-OVR Flg\n"
                    f"{p.name:<10}1500 {p.rx_frames:>8}      0      0 0      {p.tx_frames:>8}      0      0      0 BMRU\n"
                    f"{'lo':<10}65536       0      0      0 0             0      0      0      0 LRU")
        return self._sockets("netstat")

    def c_ss(self, a, root):
        return self._sockets("ss")

    def _sockets(self, style):
        socks = [("udp" if p == "UDP" else "tcp", n) for p, n in self.device.listening()]
        socks.append(("tcp", 22))
        socks.append(("udp", 68)) if self.device.dhcp_enabled else None
        if style == "ss":
            lines = ["Netid State  Recv-Q Send-Q Local Address:Port  Peer Address:Port"]
            for proto, port in sorted(socks):
                st = "LISTEN" if proto == "tcp" else "UNCONN"
                lines.append(f"{proto:<6}{st:<7}{0:<7}{128 if proto == 'tcp' else 0:<7}{'0.0.0.0:' + str(port):<19}0.0.0.0:*")
            return "\n".join(lines)
        lines = ["Active Internet connections (only servers)",
                 "Proto Recv-Q Send-Q Local Address           Foreign Address         State"]
        for proto, port in sorted(socks):
            lines.append(f"{proto:<6}{0:>6}{0:>7} {'0.0.0.0:' + str(port):<24}{'0.0.0.0:*':<24}"
                         f"{'LISTEN' if proto == 'tcp' else ''}")
        return "\n".join(lines)

    def c_ethtool(self, a, root):
        p = self.device.nic
        if not a or a[0] != p.name:
            return f"netlink error: no device matches name ({a[0] if a else ''})" if a else "ethtool: bad command line argument(s)"
        return (f"Settings for {p.name}:\n\tSupported ports: [ TP ]\n\tSpeed: {'100Mb/s' if p.is_up else 'Unknown!'}\n"
                f"\tDuplex: {'Full' if p.is_up else 'Unknown! (255)'}\n\tPort: Twisted Pair\n\tAuto-negotiation: on\n"
                f"\tLink detected: {'yes' if p.is_up else 'no'}")

    def c_sysctl(self, a, root):
        d = self.device
        p = d.nic
        write = "-w" in a
        keys = [x for x in a if not x.startswith("-")]
        if not keys:
            return "sysctl: no variables specified"
        out = []
        for k in keys:
            name, _, val = k.partition("=")
            if name == "net.ipv4.ip_forward":
                if val and val != "0":
                    return "sysctl: NetEmu end hosts do not forward packets: use a router for routing"
                out.append("net.ipv4.ip_forward = 0")
            elif name in (f"net.ipv6.conf.{p.name}.accept_ra", f"net.ipv6.conf.{p.name}.autoconf",
                          "net.ipv6.conf.all.accept_ra", "net.ipv6.conf.default.accept_ra"):
                if val:
                    if not (write or "=" in k):
                        return f'sysctl: "{k}" must be of the form name=value'
                    if not root:
                        return f"sysctl: permission denied on key \"{name}\""
                    if val == "0":
                        p.ipv6_auto = False
                        d.ra_routers.clear()
                        p.ipv6 = [x for x in p.ipv6 if x not in getattr(p, "_slaac", set())]
                    else:
                        d.start_slaac()
                out.append(f"{name} = {1 if p.ipv6_auto else 0}")
            elif name in ("net.ipv6.conf.all.disable_ipv6", f"net.ipv6.conf.{p.name}.disable_ipv6"):
                out.append(f"{name} = 0")
            else:
                out.append(f"sysctl: cannot stat /proc/sys/{name.replace('.', '/')}: No such file or directory")
        return "\n".join(out)

    # -- DHCP / DNS ----------------------------------------------------------------------------
    def c_dhclient(self, a, root):
        d = self.device
        if not root:
            return "Can't create /var/run/dhclient.pid: Permission denied"
        verbose = "-v" in a
        if "-6" in a:
            def done6(m):
                if m is None:
                    self.write("No DHCPv6 server answered.\n")
                elif verbose:
                    self.write(f"XMT: Solicit on {d.nic.name}\nRCV: Advertise message on {d.nic.name} from {m.server}\n"
                               f"XMT: Request on {d.nic.name}\nRCV: Reply message on {d.nic.name} from {m.server}\n"
                               f"RCV:  | | X-- IAADDR {m.address.split('/')[0]}\n")
            return d.start_dhcp6(done6)
        if "-r" in a or "-x" in a:
            if d.dhcp_enabled:
                if verbose:
                    self.write(f"Listening on LPF/{d.nic.name}/{_mac(d.nic.mac)}\n")
                d.release_dhcp()
                if verbose and d.dhcp_info == {}:
                    pass
                d.dhcp_enabled = False
                return "DHCPRELEASE of " + (str(d.nic.ip.ip) if d.nic.ip else "0.0.0.0") + f" on {d.nic.name}" if verbose else None
            return None
        names = [x for x in a if not x.startswith("-")]
        if names and names[0] != d.nic.name:
            return f"Cannot find device \"{names[0]}\""
        if not d.nic.is_up:
            return None if not verbose else f"{d.nic.name}: link is down"
        if verbose:
            self.write("Internet Systems Consortium DHCP Client 4.4.3-P1\n"
                       f"Listening on LPF/{d.nic.name}/{_mac(d.nic.mac)}\n"
                       f"Sending on   LPF/{d.nic.name}/{_mac(d.nic.mac)}\n"
                       f"DHCPDISCOVER on {d.nic.name} to 255.255.255.255 port 67 interval 3\n")

        def done(ok):
            if not verbose:
                return
            if ok:
                srv = d.dhcp_info.get("server")
                self.write(f"DHCPOFFER of {d.nic.ip.ip} from {srv}\nDHCPREQUEST for {d.nic.ip.ip} on {d.nic.name} "
                           f"to 255.255.255.255 port 67\nDHCPACK of {d.nic.ip.ip} from {srv}\n"
                           f"bound to {d.nic.ip.ip} -- renewal in {int(d.dhcp_info.get('lease', 86400) / 2000)} seconds.\n")
            else:
                self.write("No DHCPOFFERS received.\nNo working leases in persistent database - sleeping.\n")
        return d.start_dhcp(done)

    def c_resolvectl(self, a, root):
        d = self.device
        if a[:1] == ["dns"] and len(a) >= 3:
            if not root:
                return "Failed to set DNS configuration: Access denied"
            ips = [x for x in a[2:] if is_valid_ip(x)]
            if not ips:
                return f"Failed to parse DNS server address: {a[2]}"
            d.dns, d.dns_alt = IPv4Address(ips[0]), [IPv4Address(x) for x in ips[1:]]
            d.engine.emit("device_changed", d)
            return None
        if a[:1] == ["flush-caches"]:
            d.dns_cache.clear()
            return None
        srv = d.dns_server_list
        return ("Global\n       Protocols: -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported\n resolv.conf mode: stub\n\n"
                f"Link 2 ({d.nic.name})\n    Current Scopes: {'DNS' if srv else 'none'}\n"
                f"         Protocols: +DefaultRoute -LLMNR -mDNS -DNSOverTLS DNSSEC=no/unsupported\n"
                + (f"Current DNS Server: {srv[0]}\n       DNS Servers: {' '.join(map(str, srv))}" if srv else ""))

    def _resolve_then(self, name, on_ip, on_fail, server=None):
        """Resolve *name* (or take it as an address) then call on_ip(ip) which may return a job."""
        from ..services.dns import Resolve
        d = self.device

        class _Chain(Chain):
            def _resolved(self_, ip, err):
                if self_.done:
                    return
                if ip is None:
                    on_fail(err)
                    self_.finish()
                    return
                self_.inner = on_ip(ip)
                if self_.inner is None:
                    self_.finish()
                else:
                    self_.inner.on_done(self_.finish)

            def start(self_):
                self_.inner = Resolve(d, name, self_._resolved, server=server).start()
                return self_
        return _Chain(d, name, self.write, None).start()

    @staticmethod
    def _resolve_err(name, err):
        if err == "no DNS server configured" or "timed out" in (err or "") or "route" in (err or ""):
            return f"{name}: Temporary failure in name resolution"
        return f"{name}: Name or service not known"

    def c_dig(self, a, root):
        d = self.device
        server = next((x[1:] for x in a if x.startswith("@")), None)
        short = "+short" in a
        names = [x for x in a if not x.startswith(("@", "+", "-")) and x.upper() not in ("A", "IN")]
        if not names:
            names = ["."]
        name = names[0].rstrip(".")
        srv = IPv4Address(server) if server and is_valid_ip(server) else d.dns
        if srv is None:
            return ";; no servers could be reached"
        if not short:
            self.write(f"\n; <<>> DiG 9.18.28-0ubuntu0.24.04.1-Ubuntu <<>> {' '.join(a)}\n;; global options: +cmd\n")
        t0 = d.engine.now

        def done(ip, err):
            if short:
                if ip is not None:
                    self.write(f"{ip}\n")
                elif "timed out" in (err or "") or "route" in (err or ""):
                    self.write(f";; communications error to {srv}#53: timed out\n;; no servers could be reached\n")
                return
            if ip is None and ("timed out" in (err or "") or "route" in (err or "")):
                self.write(f";; communications error to {srv}#53: timed out\n;; communications error to {srv}#53: "
                           f"timed out\n\n; <<>> DiG 9.18.28 <<>> {name}\n;; global options: +cmd\n"
                           ";; no servers could be reached\n")
                return
            status = "NOERROR" if ip is not None else "NXDOMAIN"
            out = [";; Got answer:", f";; ->>HEADER<<- opcode: QUERY, status: {status}, id: {int(t0) % 65536}",
                   f";; flags: qr rd ra; QUERY: 1, ANSWER: {1 if ip else 0}, AUTHORITY: 0, ADDITIONAL: 1", "",
                   ";; QUESTION SECTION:", f";{name}.\t\t\tIN\tA", ""]
            if ip is not None:
                out += [";; ANSWER SECTION:", f"{name}.\t\t86400\tIN\tA\t{ip}", ""]
            out += [f";; Query time: {max(1, int(d.engine.now - t0))} msec",
                    f";; SERVER: {srv}#53({srv}) (UDP)", f";; WHEN: {self.c_date([], False)}",
                    f";; MSG SIZE  rcvd: {47 if ip else 31}", ""]
            self.write("\n".join(out) + "\n")
        from ..services.dns import Resolve
        return Resolve(d, name, done, server=srv).start()

    def c_host(self, a, root):
        d = self.device
        if not a:
            return "Usage: host [-t type] hostname [server]"
        name = a[0]
        srv = IPv4Address(a[1]) if len(a) > 1 and is_valid_ip(a[1]) else d.dns
        if srv is None:
            return ";; connection timed out; no servers could be reached"
        if len(a) > 1:
            self.write(f"Using domain server:\nName: {srv}\nAddress: {srv}#53\nAliases: \n\n")

        def done(ip, err):
            if ip is not None:
                self.write(f"{name} has address {ip}\n")
            elif "timed out" in (err or "") or "route" in (err or ""):
                self.write(";; connection timed out; no servers could be reached\n")
            else:
                self.write(f"Host {name} not found: 3(NXDOMAIN)\n")
        from ..services.dns import Resolve
        return Resolve(d, name, done, server=srv).start()

    def c_nslookup(self, a, root):
        d = self.device
        if not a:
            return "Usage: nslookup <name> [server]"
        name = a[0]
        srv = IPv4Address(a[1]) if len(a) > 1 and is_valid_ip(a[1]) else d.dns
        if srv is None:
            return ";; connection timed out; no servers could be reached"

        def done(ip, err):
            if ip is None and ("timed out" in (err or "") or "route" in (err or "")):
                self.write(";; connection timed out; no servers could be reached\n")
                return
            self.write(f"Server:\t\t{srv}\nAddress:\t{srv}#53\n\n")
            if ip is not None:
                self.write(f"Non-authoritative answer:\nName:\t{name}\nAddress: {ip}\n")
            else:
                self.write(f"** server can't find {name}: NXDOMAIN\n")
        from ..services.dns import Resolve
        return Resolve(d, name, done, server=srv).start()

    # -- ping / traceroute ---------------------------------------------------------------------
    # -- remote access -----------------------------------------------------------------------
    # -- net-snmp tools ---------------------------------------------------------------------------
    def _snmp_args(self, a, prog):
        comm, rest, i = None, [], 0
        while i < len(a):
            if a[i] == "-c" and i + 1 < len(a):
                comm, i = a[i + 1], i + 2
            elif a[i] in ("-v", "-t", "-r") and i + 1 < len(a):
                i += 2
            elif a[i].startswith(("-v", "-O")):
                i += 1
            else:
                rest.append(a[i])
                i += 1
        if comm is None:
            return f"{prog}: No community name specified."
        if len(rest) < (1 if prog == "snmpwalk" else 2):
            usage = f"USAGE: {prog} [OPTIONS] AGENT OID" + (" TYPE VALUE" if prog == "snmpset" else "")
            return f"{prog}: Missing object name\n{usage}"
        if not is_valid_ip(rest[0]):
            return f"{prog}: Unknown host ({rest[0]})"
        return comm, IPv4Address(rest[0]), rest[1:]

    def _snmp(self, a, prog, op):
        from ..services.mgmt import SnmpRequest, format_row, oid_name, parse_oid
        r = self._snmp_args(a, prog)
        if isinstance(r, str):
            return r
        comm, agent, rest = r
        oid = parse_oid(rest[0] if rest else "mib-2")
        if oid is None:
            return f"{rest[0]}: Unknown Object Identifier"
        value = None
        if op == "set":
            if len(rest) < 3:
                return f"{prog}: Missing type/value for variable"
            value = " ".join(rest[2:]).strip("'\"")
        out = []
        name = oid_name(oid)

        def row(o, typ, v):
            self.write(format_row(o, typ, v) + "\n")
            out.append(o)

        def done(err):
            if err.startswith("Timeout"):
                self.write(err + "\n")
            elif err == "noSuchObject":
                self.write(f"{name} = No Such Object available on this agent at this OID\n")
            elif err in ("noAccess", "notWritable"):
                self.write(f"Error in packet.\nReason: {err}\nFailed object: {name}\n")
            elif op == "walk" and not out:
                self.write(f"{name} = No more variables left in this MIB View\n")
        return SnmpRequest(self.device, agent, comm, op, oid, row, done, value).start()

    def c_snmpget(self, a, root):
        return self._snmp(a, "snmpget", "get")

    def c_snmpwalk(self, a, root):
        return self._snmp(a, "snmpwalk", "walk")

    def c_snmpset(self, a, root):
        return self._snmp(a, "snmpset", "set")

    def c_ssh(self, a, root):
        from ..services.remote import connect
        from .windows import parse_ssh_args
        r = parse_ssh_args(a)
        if isinstance(r, str):
            return r
        user, host, port = r
        if not is_valid_ip(host):
            return f"ssh: Could not resolve hostname {host}: Name or service not known"
        if self.device.nic.ip is None:
            return f"ssh: connect to host {host} port {port or 22}: Network is unreachable"
        return connect(self, host, "ssh", user or self.user, "linux", port)

    def c_telnet(self, a, root):
        from ..services.remote import connect
        if not a:
            return "usage: telnet [-l user] host [port]"
        host = a[0]
        if not is_valid_ip(host):
            return f"telnet: could not resolve {host}/telnet: Name or service not known"
        self.write(f"Trying {host}...\n")
        port = int(a[1]) if len(a) > 1 and a[1].isdigit() else None
        return connect(self, host, "telnet", "", "linux", port)

    def c_systemctl(self, a, root):
        from ..services.remote import access
        if len(a) < 2:
            return "systemctl: usage: systemctl start|stop|enable|disable|status ssh"
        verb, unit = a[0], a[-1].removesuffix(".service")
        if unit not in ("ssh", "sshd"):
            return f"Failed to {verb} {unit}.service: Unit {unit}.service not found."
        acc = access(self.device)
        if verb == "status":
            st = "active (running)" if acc.get("ssh") else "inactive (dead)"
            return (f"● ssh.service - OpenBSD Secure Shell server\n     Loaded: loaded (/usr/lib/systemd/system/ssh.service)\n"
                    f"     Active: {st}")
        if not root:
            return f"Failed to {verb} ssh.service: Access denied\nSee system logs and 'systemctl status ssh.service' for details."
        if verb in ("start", "enable", "restart") or "--now" in a:
            acc["ssh"] = True
            if verb == "enable":
                return ("Synchronizing state of ssh.service with SysV service script.\n"
                        "Created symlink /etc/systemd/system/multi-user.target.wants/ssh.service → /usr/lib/systemd/system/ssh.service.")
        elif verb in ("stop", "disable"):
            acc["ssh"] = False
        return None

    def c_passwd(self, a, root):
        from ..services.remote import access
        user = a[0] if a else self.user
        if a and not root and user != self.user:
            return "passwd: You may not view or modify password information for " + user + "."
        acc = access(self.device)
        first = {}

        def second(pw):
            if pw != first["pw"]:
                return "Sorry, passwords do not match.\npasswd: Authentication token manipulation error\npasswd: password unchanged"
            acc.setdefault("users", {})[user] = pw
            return "passwd: password updated successfully"

        def one(pw):
            first["pw"] = pw
            self.ask("Retype new password: ", second)
            return None
        self.ask("New password: ", one)
        return None

    # -- Wi-Fi ------------------------------------------------------------------------------
    def c_nmcli(self, a, root):
        from .base import wifi_connect, wifi_disconnect, wifi_status
        d = self.device
        wl = getattr(d.nic, "is_wireless", False)
        words = [x for x in a if not x.startswith("-")]
        if words[:2] in (["dev", "status"], ["device", "status"]) or not words:
            ap, _, _ = wifi_status(d) if wl else (None, 0, [])
            typ = "wifi" if wl else "ethernet"
            st = "connected" if d.nic.is_up else "disconnected"
            conn = ap.wifi["ssid"] if ap else ("Wired connection 1" if d.nic.is_up else "--")
            return (f"DEVICE  TYPE      STATE         CONNECTION\n{d.nic.name:<8}{typ:<10}{st:<14}{conn}\n"
                    "lo      loopback  connected (externally)  lo")
        if words[:3] in (["dev", "wifi", "list"], ["device", "wifi", "list"]):
            if not wl:
                return ""
            ap, _, nets = wifi_status(d)
            lines = ["IN-USE  BSSID              SSID        MODE   CHAN  RATE        SIGNAL  BARS  SECURITY"]
            for ssid, sec, ch, sig, n_ap in nets:
                bars = "▂▄▆█" if sig > 75 else ("▂▄▆_" if sig > 50 else ("▂▄__" if sig > 25 else "▂___"))
                mac = ":".join(f"{n_ap.get_port('Ethernet0').mac.value:012X}"[i:i + 2] for i in range(0, 12, 2))
                lines.append(f"{'*' if n_ap is ap else ' ':<8}{mac:<19}{ssid:<12}{'Infra':<7}{ch:<6}{'130 Mbit/s':<12}"
                             f"{sig:<8}{bars:<6}{'WPA2' if sec != 'open' else '--'}")
            return "\n".join(lines)
        if words[:3] in (["dev", "wifi", "connect"], ["device", "wifi", "connect"]) and len(words) >= 4:
            if not wl:
                return "Error: No Wi-Fi device found."
            ssid = words[3]
            key = words[words.index("password") + 1] if "password" in words and words.index("password") + 1 < len(words) else None
            r = wifi_connect(d, ssid, key)
            if r == "ok":
                return f"Device '{d.nic.name}' successfully activated with '6f1c2e9a-{abs(hash(ssid)) % 10**4:04d}-4b7e-9c1d-netemu'."
            if r == "auth":
                return "Error: Connection activation failed: Secrets were required, but not provided."
            return f"Error: No network with SSID '{ssid}' found."
        if words[:2] in (["dev", "disconnect"], ["device", "disconnect"]):
            wifi_disconnect(d)
            return f"Device '{d.nic.name}' successfully disconnected."
        return "Error: argument not understood. Try: nmcli dev status | nmcli dev wifi list | nmcli dev wifi connect SSID password KEY"

    def c_iw(self, a, root):
        from .base import wifi_status
        d = self.device
        if not getattr(d.nic, "is_wireless", False):
            return "command failed: No such device (-19)"
        ap, sig, _ = wifi_status(d)
        if not ap:
            return "Not connected."
        mac = ":".join(f"{ap.get_port('Ethernet0').mac.value:012x}"[i:i + 2] for i in range(0, 12, 2))
        freq = 2407 + 5 * int(ap.wifi["channel"])
        return (f"Connected to {mac} (on {d.nic.name})\n\tSSID: {ap.wifi['ssid']}\n\tfreq: {freq}\n"
                f"\tsignal: {-30 - (100 - sig) // 2} dBm\n\ttx bitrate: 130.0 MBit/s")

    def c_ping6(self, a, root):
        return self.c_ping(["-6"] + a, root)

    def c_ping(self, a, root):
        d = self.device
        count, size, ttl, interval, timeout, fam = 10_000_000, 56, None, 1000.0, 2000.0, None
        target = None
        i = 0
        while i < len(a):
            o = a[i]
            nxt = a[i + 1] if i + 1 < len(a) else None
            if o in ("-c", "-s", "-t", "-i", "-W", "-w"):
                if nxt is None:
                    return f"ping: option requires an argument -- '{o[1:]}'"
                try:
                    v = float(nxt)
                except ValueError:
                    return f"ping: invalid argument: '{nxt}'"
                if o == "-c":
                    if v < 1:
                        return f"ping: invalid argument: '{nxt}': out of range: 1 <= value <= 9223372036854775807"
                    count = int(v)
                elif o == "-s":
                    size = int(v)
                elif o == "-t":
                    if not 1 <= v <= 255:
                        return f"ping: invalid argument: '{nxt}': out of range: 1 <= value <= 255"
                    ttl = int(v)
                elif o == "-i":
                    if v < 0.2 and not root:
                        return "ping: cannot flood; minimal interval allowed for user is 200ms"
                    interval = v * 1000
                else:
                    timeout = v * 1000
                i += 2
                continue
            if o in ("-4", "-6"):
                fam = o
            elif o in ("-n", "-q", "-v", "-D", "-O"):
                pass
            elif o.startswith("-"):
                return f"ping: invalid option -- '{o.lstrip('-')[:1]}'"
            else:
                target = o
            i += 1
        if target is None:
            return "ping: usage error: Destination address required"
        kw = dict(count=count, size=size, timeout=timeout, interval=interval, ttl=ttl)
        if ":" in target or fam == "-6":
            try:
                IPv6Address(target)
            except ValueError:
                return f"ping: {target}: Address family for hostname not supported"
            return make_ping(d, target, LinuxPing(self.write, v6=True), **kw).start()
        if is_valid_ip(target):
            if d.nic.ip is None and IPv4Address(target) != IPv4Address("127.0.0.1"):
                return "ping: connect: Network is unreachable"
            return PingSession(d, IPv4Address(target), LinuxPing(self.write), **kw).start()
        return self._resolve_then(
            target, lambda ip: PingSession(d, ip, LinuxPing(self.write, name=target), **kw).start(),
            lambda err: self.write(f"ping: {self._resolve_err(target, err)}\n"))

    def c_traceroute(self, a, root):
        d = self.device
        max_hops, wait, numeric, target = 30, 5000.0, False, None
        i = 0
        while i < len(a):
            o = a[i]
            if o in ("-m", "-w", "-q") and i + 1 < len(a):
                try:
                    v = float(a[i + 1])
                except ValueError:
                    return f"Cannot handle \"{o}\" option with arg \"{a[i + 1]}\""
                if o == "-m":
                    max_hops = int(v)
                elif o == "-w":
                    wait = v * 1000
                i += 2
                continue
            if o == "-n":
                numeric = True
            elif o in ("-I", "-4", "-T", "-U"):
                pass
            elif o == "-6":
                pass
            elif o.startswith("-"):
                return f"Bad option `{o}' (argc 1)"
            else:
                target = o
            i += 1
        if target is None:
            return "Usage:\n  traceroute [ -46dFITnreAUDV ] [ -m max_ttl ] [ -w waittime ] host"
        if ":" in target:
            from .base import make_trace
            return make_trace(d, target, LinuxTrace(self.write, numeric=numeric), max_hops=max_hops,
                              timeout=min(wait, 5000.0)).start()
        wait = min(wait, 5000.0)
        if is_valid_ip(target):
            if d.nic.ip is None:
                return "connect: Network is unreachable"
            return TracerouteSession(d, IPv4Address(target), LinuxTrace(self.write, numeric=numeric),
                                     max_hops=max_hops, timeout=wait).start()
        return self._resolve_then(
            target, lambda ip: TracerouteSession(d, ip, LinuxTrace(self.write, name=target, numeric=numeric),
                                                 max_hops=max_hops, timeout=wait).start(),
            lambda err: self.write(f"{target}: {self._resolve_err(target, err).split(': ', 1)[1]}\n"
                                   "Cannot handle \"host\" cmdline arg `" + target + "' on position 1 (argc 1)\n"))

    def c_traceroute6(self, a, root):
        return self.c_traceroute(["-6"] + a, root)

    def c_tracepath(self, a, root):
        return self.c_traceroute(["-n"] + [x for x in a if x != "-n"], root)

    # -- web -----------------------------------------------------------------------------------
    def c_curl(self, a, root):
        from ..services.http import HttpGet
        d = self.device
        head = any(x in ("-I", "--head") for x in a)
        urls = [x for x in a if not x.startswith("-")]
        if not urls:
            return "curl: try 'curl --help' or 'curl --manual' for more information"
        url = urls[0]
        if d.nic.ip is None and not ("[" in url and d.nic.ipv6):
            host = url.split("//", 1)[-1].split("/", 1)[0]
            return f"curl: (7) Failed to connect to {host} port 80 after 0 ms: Couldn't connect to server"

        def done(status, body):
            self.write(curl_text(status, body, head, url))
        return HttpGet(d, url, done).start()

    def c_wget(self, a, root):
        from ..services.http import HttpGet
        d = self.device
        urls = [x for x in a if not x.startswith("-")]
        if not urls:
            return "wget: missing URL\nUsage: wget [OPTION]... [URL]..."
        url = urls[0] if "://" in urls[0] else "http://" + urls[0]
        host = url.split("//", 1)[-1].split("/", 1)[0]
        path = "/" + url.split("//", 1)[-1].partition("/")[2]
        fname = path.rsplit("/", 1)[-1] or "index.html"
        self.write(f"--{self.c_date([], False)}--  {url}\n")

        def done(status, body):
            if status is None:
                if "resolve" in (body or "").lower() or "can't find" in (body or "") or "dns" in (body or "").lower():
                    self.write(f"Resolving {host} ({host})... failed: Name or service not known.\n"
                               f"wget: unable to resolve host address ‘{host}’\n")
                else:
                    self.write(f"Connecting to {host} ({host})|:80... failed: Connection timed out.\n")
                return
            reason = {200: "OK", 404: "Not Found"}.get(status, "")
            self.write(f"Connecting to {host} ({host})|:80... connected.\n"
                       f"HTTP request sent, awaiting response... {status} {reason}\n")
            if status != 200:
                self.write(f"{self.c_date([], False)} ERROR {status}: {reason}.\n")
                return
            n = len(body.encode())
            self.write(f"Length: {n} [text/html]\nSaving to: ‘{fname}’\n\n"
                       f"{fname:<20}100%[===================>]  {n}  --.-KB/s    in 0s\n\n"
                       f"‘{fname}’ saved [{n}/{n}]\n")
        return HttpGet(d, url, done).start()


def _split(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _opts(tokens) -> dict:
    """iproute2/net-tools style "key value" pairs: via X dev Y metric Z / gw X netmask Y."""
    out = {}
    i = 0
    while i < len(tokens):
        k = tokens[i]
        if k in ("via", "dev", "metric", "src", "proto", "gw", "netmask", "lladdr", "nud", "table", "scope") \
                and i + 1 < len(tokens):
            out[k] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return out


