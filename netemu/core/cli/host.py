"""Command prompt of end devices (Windows-like, as on Packet Tracer PCs)."""
from __future__ import annotations

from ..addresses import IPv4Address, is_valid_ip, is_valid_mask
from ..stack.icmp import Job, PingListener, PingSession, TraceListener, TracerouteSession
from .base import CliSession
from .windows import IF_INDEX, WindowsExtras, split_args

HELP = """Available Commands
?          Display the list of available commands
arp        Display or modify the ARP cache
           arp -a [ip] | arp -d [ip|*] | arp -s <ip> <mac>
cls        Clear the screen
curl       Fetch a web page: curl [-I] http://<host>[/page]
date       Display the date: date /t
echo       Display a message
exit       Quit the command prompt
getmac     Display the MAC address: getmac [/v]
help       Display the list of available commands
hostname   Display the host name
ipconfig   Display or set IP configuration
           ipconfig [/all]
           ipconfig <ip> <mask> [<gateway>]
           ipconfig /ipv6 <addr>/<len> [<gw>] | /ipv6 auto | /ipv6 off
           ipconfig /renew | /release   (DHCP)
           ipconfig /renew6   (DHCPv6)
           ipconfig /dns <server>
           ipconfig /displaydns | /flushdns
netsh      Configure the network adapter (netsh interface ipv4|ipv6 ..., netsh ?)
           netsh wlan show interfaces | show networks | connect name=<SSID> key=<key> | disconnect
netstat    Network statistics: netstat [-a] [-n] [-r] [-e]
nslookup   Query the DNS server: nslookup <name> [<server>]
pathping   Trace route and loss: pathping [-n] <ip or name>
ping       Send echo messages: ping [-n count] [-t] [-l size] [-i TTL] [-w timeout] [-4|-6] <ip or name>
route      Routing table: route print [-4|-6] | route [-p] add <net> mask <mask> <gw> | route delete <net>
time       Display the time: time /t
tracert    Trace route to destination: tracert [-d] [-h max_hops] [-w timeout] [-4|-6] <ip or name>
ssh        Secure shell client: ssh [-l user] [-p port] [user@]<ip>
telnet     Telnet client: telnet <ip> [port]
ver        Display the Windows version
whoami     Display the current user
"""


def curl_text(status, body, head, url) -> str:
    """curl output for an HttpGet result (shared by the Windows and Linux prompts)."""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    if status is None:
        b = (body or "").lower()
        if "resolve" in b or "non-existent" in b or "can't find" in b or "dns" in b:
            return f"curl: (6) Could not resolve host: {host}\n"
        if "no route" in b:
            return f"curl: (7) Failed to connect to {host} port 80 after 0 ms: No route to host\n"
        return f"curl: (28) Failed to connect to {host} port 80 after 4000 ms: Timeout was reached\n"
    reason = {200: "OK", 404: "Not Found"}.get(status, "")
    if head:
        return (f"HTTP/1.1 {status} {reason}\nServer: NetEmu-httpd\nContent-Type: text/html\n"
                f"Content-Length: {len(body.encode())}\n\n")
    if status != 200:
        return f"HTTP/1.1 {status} {reason}\n" + body + "\n"
    return body + "\n"


def _ms(rtt: float) -> str:
    return "<1ms" if rtt < 1 else f"={round(rtt)}ms"


class Chain(Job):
    """Resolve a name (DNS) first, then run the real job; Ctrl+C cancels whichever is active."""

    def __init__(self, host, name, write, make_job):
        super().__init__()
        self.host, self.name, self.write, self.make_job = host, name, write, make_job
        self.inner = None

    def start(self) -> "Chain":
        from ..services.dns import Resolve
        self.inner = Resolve(self.host, self.name, self._resolved).start()
        return self

    def _resolved(self, ip, err) -> None:
        if self.done:
            return
        if ip is None:
            self.write(f"Ping request could not find host {self.name}. Please check the name and try again.\n"
                       if err != "no DNS server configured" else
                       f"Ping request could not find host {self.name}. No DNS server is configured.\n")
            self.finish()
            return
        self.inner = self.make_job(ip)
        self.inner.on_done(self.finish)

    def cancel(self) -> None:
        if self.inner is not None and not self.inner.done:
            self.inner.cancel()
        super().cancel()


class WinPing(PingListener):
    def __init__(self, write, name=None):
        self.w = write
        self.name = name

    def on_start(self, s):
        self.dst = s.dst
        who = f"{self.name} [{s.dst}]" if self.name else str(s.dst)
        self.w(f"\nPinging {who} with {s.size} bytes of data:\n\n")

    def on_reply(self, seq, src, rtt, ttl, size):
        if ":" in str(src):
            self.w(f"Reply from {src}: time{_ms(rtt)}\n")
        else:
            self.w(f"Reply from {src}: bytes={size} time{_ms(rtt)} TTL={ttl}\n")

    def on_timeout(self, seq):
        self.w("Request timed out.\n")

    def on_send_fail(self, seq):
        self.w("Request timed out.\n")

    def on_error(self, seq, kind, src):
        text = "Destination host unreachable." if kind == "unreachable" else "TTL expired in transit."
        self.w(f"Reply from {src}: {text}\n")

    def on_finish(self, st):
        self.w(f"\nPing statistics for {self.dst}:\n"
               f"    Packets: Sent = {st.sent}, Received = {st.received}, Lost = {st.lost} "
               f"({st.loss_percent}% loss),\n")
        if st.received:
            self.w("Approximate round trip times in milli-seconds:\n"
                   f"    Minimum = {st.rtt('min')}ms, Maximum = {st.rtt('max')}ms, "
                   f"Average = {st.rtt('avg')}ms\n")


class WinTrace(TraceListener):
    def __init__(self, write):
        self.w = write

    def on_start(self, s):
        self.w(f"\nTracing route to {s.dst} over a maximum of {s.max_hops} hops: \n\n")

    def on_hop(self, hop):
        cols = "".join(f"{'*':<10}" if r is None else f"{(str(round(r)) + ' ms') if r >= 1 else '<1 ms':<10}"
                       for r in hop.probes)
        if hop.address is None:
            tail = "Request timed out."
        elif hop.unreachable:
            tail = f"{hop.address} reports: Destination host unreachable."
        else:
            tail = str(hop.address)
        self.w(f"{hop.ttl:>3}   {cols}{tail}\n")

    def on_finish(self, hops, reached):
        self.w("\nTrace complete.\n")


class HostSession(WindowsExtras, CliSession):
    def __init__(self, device):
        super().__init__(device)
        self.wants_clear = False

    @property
    def prompt(self) -> str:
        return "C:\\>"

    def banner(self) -> str:
        return "\nNetEmu PC Command Line 1.0\n"

    def help(self, line: str) -> str:
        return HELP

    def complete(self, line: str) -> str | None:
        cmds = ["arp", "cls", "curl", "date", "echo", "exit", "getmac", "help", "hostname", "ipconfig", "mpcmdrun",
                "netsh", "netstat", "nslookup", "pathping", "ping", "route", "sc", "shutdown", "ssh", "telnet", "time",
                "tracert", "ver", "wbadmin", "whoami", "wmic", "wusa"]
        if " " in line:
            return None
        m = [c for c in cmds if c.startswith(line.lower())]
        return m[0] + " " if len(m) == 1 else None

    def _execute(self, line: str):
        parts = split_args(line)
        if not parts:
            return None
        cmd, args = parts[0].lower(), parts[1:]
        if cmd.endswith(".exe"):
            cmd = cmd[:-4]
        d = self.device
        from .hostsec import NOT_MINE, windows as _sec
        r = _sec(self, cmd, args, line)
        if r is not NOT_MINE:
            return r
        if cmd in ("help", "?"):
            from .hostsec import WIN_HELP
            return HELP + "\n" + WIN_HELP
        if cmd == "cls":
            self.wants_clear = True
            return None
        if cmd == "exit":
            return None
        if cmd == "hostname":
            return d.hostname
        if cmd == "ipconfig":
            return self.ipconfig(args)
        if cmd == "arp":
            return self.arp(args)
        if cmd == "ping":
            return self.ping(args)
        if cmd in ("tracert", "traceroute", "pathping"):
            return self.tracert(args, pathping=cmd == "pathping")
        if cmd == "nslookup":
            return self.nslookup(args)
        if cmd in ("curl", "wget"):
            return self.curl(args)
        if cmd == "route":
            return self.route(args)
        if cmd == "netstat":
            return self.netstat(args)
        if cmd == "getmac":
            return self.getmac(args)
        if cmd == "netsh":
            return self.netsh(line)
        if cmd in ("ssh", "telnet"):
            return self.remote_cmd(cmd, args)
        if cmd == "ver":
            return "\nMicrosoft Windows [Version 10.0.22631.4460]"
        if cmd == "whoami":
            return f"{d.hostname.lower()}\\user"
        if cmd == "echo":
            return line.strip()[5:] if len(line.strip()) > 4 else "ECHO is on."
        if cmd in ("date", "time"):
            ms = int(d.engine.now)
            if cmd == "date":
                return "Fri 11/20/2026" if args[:1] == ["/t"] else "The current date is: Fri 11/20/2026"
            hh, mm, ss = (ms // 3_600_000) % 24, (ms // 60_000) % 60, (ms // 1000) % 60
            return f"{hh:02d}:{mm:02d}" if args[:1] == ["/t"] else f"The current time is: {hh:2d}:{mm:02d}:{ss:02d}.00"
        return (f"'{parts[0]}' is not recognized as an internal or external command,\n"
                "operable program or batch file.")

    def remote_cmd(self, cmd, args):
        from ..services.remote import connect
        from .windows import parse_ssh_args
        d = self.device
        if cmd == "telnet":
            if not args:
                return "Usage: telnet <host> [port]"
            host = args[0]
            port = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
            user = ""
        else:
            r = parse_ssh_args(args)
            if isinstance(r, str):
                return r
            user, host, port = r
            user = user or "user"
        if not is_valid_ip(host):
            return f"ssh: Could not resolve hostname {host}: No such host is known." if cmd == "ssh" else \
                f"Connecting To {host}...Could not open connection to the host, on port {port or 23}: Connect failed"
        if d.nic.ip is None:
            return f"ssh: connect to host {host} port {port or 22}: Network is unreachable"
        return connect(self, host, cmd, user, "windows", port)

    def tracert(self, args, pathping=False):
        d = self.device
        max_hops, timeout, target = 30, 2000.0, None
        i = 0
        while i < len(args):
            a = args[i].lower()
            if a in ("-h", "-w") and i + 1 < len(args) and args[i + 1].isdigit():
                if a == "-h":
                    max_hops = max(1, min(255, int(args[i + 1])))
                else:
                    timeout = float(int(args[i + 1]))
                i += 2
                continue
            if a in ("-d", "-n", "-4"):
                i += 1
                continue
            if a == "-6":
                i += 1
                continue
            target = args[i]
            i += 1
        if target is None:
            return "Usage: tracert [-d] [-h maximum_hops] [-w timeout] target_name"
        if ":" in target:
            from .base import make_trace
            return make_trace(d, target, WinTrace(self.write), max_hops=max_hops, timeout=timeout).start()
        if d.nic.ip is None:
            return "Transmit failed. General failure."
        make = lambda ip: TracerouteSession(d, ip, WinTrace(self.write), max_hops=max_hops, timeout=timeout).start()
        if is_valid_ip(target):
            return make(IPv4Address(target))
        return Chain(d, target, self.write, make).start()

    def nslookup(self, args):
        from ..services.dns import Resolve
        d = self.device
        if len(args) not in (1, 2) or (len(args) == 2 and not is_valid_ip(args[1])):
            return "Usage: nslookup <name> [<server>]"
        server = IPv4Address(args[1]) if len(args) == 2 else d.dns
        if server is None:
            return "*** Default servers are not available"
        name = args[0]
        self.write(f"Server:  {server}\nAddress:  {server}\n\n")

        def done(ip, err):
            if ip is None:
                self.write((err or f"*** {server} can't find {name}: Non-existent domain") + "\n")
            else:
                self.write(f"Non-authoritative answer:\nName:\t{name}\nAddress:  {ip}\n")
        return Resolve(d, name, done, server=server).start()

    def curl(self, args):
        from ..services.http import HttpGet
        d = self.device
        head = any(a in ("-I", "--head") for a in args)
        args = [a for a in args if not a.startswith("-")]
        if len(args) != 1:
            return "Usage: curl [-I] http://<host>[/page]"
        if d.nic.ip is None and not ("[" in args[0] and d.nic.ipv6):
            return "curl: (7) Failed to connect: network is unreachable"

        def done(status, body):
            self.write(curl_text(status, body, head, args[0]))
        return HttpGet(d, args[0], done).start()

    def ipconfig(self, args):
        d = self.device
        if args and args[0].lower() == "/renew":
            if not d.nic.is_up:
                return "An error occurred while renewing interface FastEthernet0 : media disconnected."

            def done(ok):
                if ok:
                    p = d.nic
                    self.write(f"\n   IP Address......................: {p.ip.ip}\n"
                               f"   Subnet Mask.....................: {p.ip.netmask}\n"
                               f"   Default Gateway.................: {d.gateway or '0.0.0.0'}\n"
                               f"   DNS Server......................: {d.dns or '0.0.0.0'}\n")
                else:
                    self.write("DHCP request failed.\n"
                               f"Autoconfiguration IPv4 Address . . : {d.nic.ip.ip}\n")
            return d.start_dhcp(done)
        if args and args[0].lower() == "/release":
            if not d.dhcp_enabled:
                return "The operation failed as no adapter is in the state permissible for this operation."
            d.release_dhcp()
            return ("\n   IP Address......................: 0.0.0.0\n"
                    "   Subnet Mask.....................: 0.0.0.0\n"
                    "   Default Gateway.................: 0.0.0.0\n")
        if args and args[0].lower() == "/renew6":
            def done6(m):
                if m is None:
                    self.write("An error occurred while renewing interface FastEthernet0 : unable to contact your DHCPv6 server.\n")
                else:
                    self.write(f"\n   IPv6 Address....................: {m.address.split('/')[0]}\n")
            return d.start_dhcp6(done6)
        if args and args[0].lower() == "/displaydns":
            return self.displaydns()
        if args and args[0].lower() == "/flushdns":
            return self.flushdns()
        if args and args[0].lower() == "/ipv6":
            return self._ipconfig_v6(args[1:])
        if args and args[0].lower() == "/dns":
            if len(args) != 2 or not is_valid_ip(args[1]):
                return "Usage: ipconfig /dns <server ip>"
            d.dns = IPv4Address(args[1])
            d.engine.emit("device_changed", d)
            return None
        if args and args[0].lower() not in ("/all",):
            if len(args) < 2 or not is_valid_ip(args[0]) or not is_valid_mask(args[1]) \
                    or (len(args) > 2 and not is_valid_ip(args[2])):
                return "Usage: ipconfig <ip address> <subnet mask> [<default gateway>]"
            if d.dhcp_job is not None and not d.dhcp_job.done:
                d.dhcp_job.cancel()
            d.dhcp_enabled = False
            d.set_ip(args[0], args[1])
            d.set_gateway(args[2] if len(args) > 2 else None)
            d.engine.emit("device_changed", d)
            return None
        p = d.nic
        lines = ["", f"{p.name} Connection:(default port)", ""]
        if args:
            lines.append(f"   Physical Address................: {p.mac.cisco().upper()}")
        lines += ["   Connection-specific DNS Suffix..: ",
                  *[f"   IPv6 Address....................: {a.ip}" for a in p.ipv6],
                  f"   Link-local IPv6 Address.........: {d.link_local(p).ip if d.link_local(p) else '::'}%1",
                  f"   IPv4 Address....................: {p.ip.ip if p.ip else '0.0.0.0'}",
                  f"   Subnet Mask.....................: {p.ip.netmask if p.ip else '0.0.0.0'}",
                  f"   Default Gateway.................: {d.gateway or '0.0.0.0'}"]
        gw6 = d.gateway6 or next((r for r, _ in d.ra_routers.values()), None)
        if gw6:
            lines.append(f"                                     {gw6}")
        if args:
            lines.append(f"   DNS Servers.....................: {d.dns or '0.0.0.0'}")
            lines.insert(4, f"   DHCP Enabled....................: {'Yes' if d.dhcp_enabled else 'No'}")
            if d.dhcp_enabled and d.dhcp_info.get("server"):
                lines.append(f"   DHCP Server.....................: {d.dhcp_info['server']}")
                lines.append(f"   Lease Obtained..................: {int(d.dhcp_info['lease'] / 3600)} hour(s)")
        if not p.is_up:
            lines.insert(3, "   Media State.....................: Media disconnected")
        return "\n".join(lines) + "\n"

    def arp(self, args):
        d = self.device
        op = args[0].lower() if args else ""
        if op == "-d":
            return self.arp_delete(args[1] if len(args) > 1 else None)
        if op == "-s":
            if len(args) < 3:
                return "Usage: arp -s <inet_addr> <eth_addr>"
            return self.arp_static(args[1], args[2])
        if op not in ("-a", "-g"):
            return ("\nDisplays and modifies the IP-to-Physical address translation tables used by\n"
                    "address resolution protocol (ARP).\n\n"
                    "ARP -s inet_addr eth_addr\nARP -d inet_addr\nARP -a [inet_addr]")
        entries = d.arp.entries(d.engine.now)
        if len(args) > 1 and is_valid_ip(args[1]):
            entries = [e for e in entries if str(e.ip) == args[1]]
        if not entries:
            return "No ARP Entries Found."
        me = d.nic.ip.ip if d.nic.ip else "0.0.0.0"
        lines = [f"\nInterface: {me} --- 0x{IF_INDEX:x}", "  Internet Address      Physical Address      Type"]
        for e in entries:
            lines.append(f"  {str(e.ip):<22}{e.mac.windows().lower():<22}{'static' if e.static else 'dynamic'}")
        return "\n".join(lines)

    def _ipconfig_v6(self, args):
        """netsh-free shortcut: ipconfig /ipv6 <addr/len> [gateway] | /ipv6 auto | /ipv6 off."""
        d = self.device
        from ipaddress import IPv6Address, IPv6Interface
        if len(args) == 1 and args[0].lower() == "auto":
            d.set_ipv6(None)
            d.set_gateway6(None)
            d.start_slaac()
            self.device.engine.emit("device_changed", d)
            return "IPv6 autoconfiguration (SLAAC) requested."
        if len(args) == 1 and args[0].lower() == "off":
            d.set_ipv6(None)
            d.set_gateway6(None)
            d.nic.ipv6_auto = False
            return ""
        try:
            addr = IPv6Interface(args[0] if "/" in args[0] else args[0] + "/64")
            gw = IPv6Address(args[1]) if len(args) > 1 else None
        except (ValueError, IndexError):
            return "Usage: ipconfig /ipv6 <address>/<prefix> [gateway] | /ipv6 auto | /ipv6 off"
        d.nic.ipv6_auto = False
        d.set_ipv6(str(addr))
        d.set_gateway6(str(gw) if gw else None)
        self.device.engine.emit("device_changed", d)
        return ""

    def ping(self, args):
        d = self.device
        count, size, ttl, timeout, family = 4, 32, None, 4000.0, None
        target = None
        i = 0
        while i < len(args):
            a = args[i].lower()
            nxt = args[i + 1] if i + 1 < len(args) else None
            if a in ("-n", "-l", "-i", "-w") and nxt is not None and nxt.isdigit():
                v = int(nxt)
                if a == "-n":
                    count = max(1, v)
                elif a == "-l":
                    if v > 65500:
                        return "Bad value for option -l, valid range is from 0 to 65500."
                    size = v
                elif a == "-i":
                    if not 1 <= v <= 255:
                        return "Bad value for option -i, valid range is from 1 to 255."
                    ttl = v
                else:
                    timeout = float(v)
                i += 2
                continue
            if a in ("-n", "-l", "-i", "-w"):
                return f"Value must be supplied for option {args[i]}."
            if a == "-t":
                count = 10_000_000
            elif a in ("-4", "-6"):
                family = a
            elif a in ("-a", "-f"):
                pass
            elif a.startswith("-") or a.startswith("/"):
                return f"Bad option {args[i]}."
            else:
                target = args[i]
            i += 1
        if target is None:
            return "Usage: ping [-t] [-a] [-n count] [-l size] [-i TTL] [-w timeout] [-4] [-6] target_name"
        if family == "-6" and ":" not in target:
            return f"Ping request could not find host {target}. Please check the name and try again."
        timeout = min(timeout, 4000.0)
        if ":" in target:
            from .base import make_ping
            return make_ping(d, target, WinPing(self.write), count=count, size=size,
                             timeout=timeout, interval=1000.0, ttl=ttl).start()
        if d.nic.ip is None:
            return "\nPinging %s with %d bytes of data:\n\nPING: transmit failed. General failure." % (target, size)
        name = None if is_valid_ip(target) else target
        make = lambda ip: PingSession(d, ip, WinPing(self.write, name), count=count, size=size,
                                      timeout=timeout, interval=1000.0, ttl=ttl).start()
        if is_valid_ip(target):
            return make(IPv4Address(target))
        return Chain(d, target, self.write, make).start()
