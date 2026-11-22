"""End devices: PC and server."""
from __future__ import annotations

from ..addresses import IPv4Address, IPv4Interface, IPv4Network
from ..packets import EthernetFrame
from ..port import Port
from ..stack.ipv4 import IpStack
from ..stack.ipv6 import Ip6Stack, StaticRoute6
from .base import Device


class Host(IpStack, Ip6Stack, Device):
    category = "pc"
    model = "PC"
    default_hostname = "PC"
    default_ttl = 128
    NIC = ("FastEthernet0", "Fa0")
    os = "windows"
    infection: dict | None = None                 # malware training (security/malware.py)
    hostsec: dict | None = None

    def __init__(self, engine, name, device_id=None):
        Device.__init__(self, engine, name, device_id)
        self._init_ip()
        self._init_ip6()
        self.hostname = name
        self.nic = self.add_port(*self.NIC)
        self.nic.ipv6_enabled = True                 # Windows: link-local address always present
        self.nic._slaac = set()
        self.gateway: IPv4Address | None = None
        self.dns: IPv4Address | None = None
        self.dns_alt: list[IPv4Address] = []
        self.dhcp_enabled = False
        self.dhcp_job = None
        self.dhcp_info: dict = {}
        self.routes: list[tuple[IPv4Network, IPv4Address | None, bool]] = []   # (net, gw, persistent)
        self.dns_cache: dict[str, tuple[IPv4Address, float]] = {}
        self.dns_expiry: dict[str, float] = {}      # name -> virtual ms when its TTL runs out

    def deliver_protocol(self, pkt, port) -> None:
        from ..security.malware import on_packet
        if on_packet(self, pkt):                     # an exploit attempt (malware training)
            return
        super().deliver_protocol(pkt, port)

    def receive(self, frame: EthernetFrame, port: Port) -> None:
        if frame.ethertype == "EAPOL":
            self._eapol_rx(frame)
            return
        self.handle_l3_frame(frame, port)

    # -- 802.1X supplicant -------------------------------------------------------------------
    dot1x: dict | None = None                     # {"user", "password"} when 802.1X is turned on
    dot1x_state = "off"                           # off | connecting | authenticated | failed

    def _eapol_start(self) -> None:
        from ..addresses import MacAddress
        from ..services.aaa import EAPOL_MAC, EapolMsg
        if not self.dot1x or not self.nic.is_up:
            return
        self.dot1x_state = "connecting"
        dst = MacAddress(EAPOL_MAC)
        self.nic.send(EthernetFrame(self.nic.mac, dst, "EAPOL", EapolMsg("start")))
        self.nic.send(EthernetFrame(self.nic.mac, dst, "EAPOL",
                                    EapolMsg("response", self.dot1x.get("user", ""), self.dot1x.get("password", ""))))

    def _eapol_rx(self, frame) -> None:
        m = frame.payload
        kind = getattr(m, "kind", "")
        if kind == "success":
            self.dot1x_state = "authenticated"
            if self.dhcp_enabled and (self.nic.ip is None or self.dhcp_info.get("apipa")):
                self.engine.schedule(100.0, self.start_dhcp)
        elif kind == "failure":
            self.dot1x_state = "failed"
        self.engine.emit("device_changed", self)

    # -- DHCP client ------------------------------------------------------------------
    def start_dhcp(self, on_result=None):
        from ..services.dhcp import DhcpClient
        if self.dhcp_job is not None and not self.dhcp_job.done:
            self.dhcp_job.cancel()
        self.dhcp_enabled = True
        self.dhcp_info = {}
        self.dhcp_job = DhcpClient(self, on_result).start()
        return self.dhcp_job

    def release_dhcp(self) -> None:
        from ..services.dhcp import DhcpMessage
        if self.dhcp_info.get("server") and self.nic.ip is not None:
            self.send_udp(self.dhcp_info["server"], 68, 67,
                          DhcpMessage("RELEASE", 0, self.nic.mac, ciaddr=self.nic.ip.ip))
        self.dhcp_info = {}
        self.engine.schedule(50.0, self._drop_address)       # let the RELEASE leave first

    def _drop_address(self) -> None:
        self.nic.ip = None
        self.gateway = None
        self.arp.clear()
        self.engine.emit("device_changed", self)

    def dhcp_bound(self, result, msg) -> None:
        iface, router, dns = result
        self.nic.ip = iface
        self.gateway = router
        if dns is not None:
            self.dns = dns
        self.dhcp_info = {"server": msg.siaddr, "lease": msg.lease, "obtained": self.engine.now,
                          "domain": msg.domain}
        self.arp.clear()
        self.engine.emit("device_changed", self)

    def dhcp_failed(self) -> None:
        """Windows behaviour: no server answered -> APIPA 169.254.x.y/16."""
        v = self.nic.mac.value
        self.nic.ip = IPv4Interface(f"169.254.{(v >> 8) & 0xFF or 1}.{v & 0xFF or 1}/16")
        self.gateway = None
        self.dhcp_info = {"apipa": True}
        self.engine.emit("device_changed", self)

    @property
    def dns_server_list(self) -> list:
        return ([self.dns] if self.dns else []) + list(getattr(self, "dns_alt", []))

    def add_route(self, net, gw, persistent=False) -> None:
        net = IPv4Network(str(net), strict=False)
        gw = IPv4Address(str(gw)) if gw else None
        if net.prefixlen == 0:
            self.gateway = gw
            return
        self.routes = [r for r in self.routes if r[0] != net] + [(net, gw, persistent)]

    def del_route(self, net) -> bool:
        net = IPv4Network(str(net), strict=False)
        if net.prefixlen == 0:
            had = self.gateway is not None
            self.gateway = None
            return had
        before = len(self.routes)
        self.routes = [r for r in self.routes if r[0] != net]
        return len(self.routes) != before

    def listening(self) -> list[tuple[str, int]]:
        """(proto, port) of the services this host listens on (netstat / ss)."""
        return []

    # -- IPv6 ---------------------------------------------------------------------------
    @property
    def gateway6(self):
        s = next((r for r in self.static_routes6 if r.network.prefixlen == 0), None)
        return s.next_hop if s else None

    def set_gateway6(self, gw) -> None:
        from ipaddress import IPv6Address, IPv6Network
        self.static_routes6 = [r for r in self.static_routes6 if r.network.prefixlen != 0]
        if gw:
            self.static_routes6.append(StaticRoute6(IPv6Network("::/0"), IPv6Address(str(gw)), self.nic.name))

    def set_ipv6(self, addr) -> None:
        from ipaddress import IPv6Interface
        self.nic.ipv6 = [IPv6Interface(addr)] if addr else []
        self.nic._slaac = set()

    def start_dhcp6(self, on_result=None):
        from ..services.dhcp6 import Dhcp6Client
        return Dhcp6Client(self, on_result).start()

    def deliver6_protocol(self, pkt, port) -> None:
        from ..services.dhcp6 import Dhcp6Msg
        job = getattr(self, "_dhcp6", None)
        if isinstance(pkt.payload, Dhcp6Msg) and job is not None:
            job.receive(pkt)

    def start_slaac(self) -> None:
        from ..stack.ipv6 import ALL_ROUTERS, RS, Icmp6Packet, IPv6Packet, mcast_mac
        self.nic.ipv6_auto = True
        ll = self.link_local(self.nic)
        if ll is not None and self.nic.is_up:
            rs = Icmp6Packet(RS, lladdr=self.nic.mac)
            self._send6_on(self.nic, IPv6Packet(ll.ip, ALL_ROUTERS, 255, rs), mac=mcast_mac(ALL_ROUTERS))

    def on_ra(self, pkt, m, port) -> None:
        from ipaddress import IPv6Network
        from ..stack.ipv6 import eui64_address
        if not port.ipv6_auto:
            return
        changed = False
        for pfx in m.prefixes:
            addr = eui64_address(IPv6Network(pfx), port.mac)
            if addr not in port.ipv6:
                port.ipv6.append(addr)
                port._slaac.add(addr)
                changed = True
        if m.router_lifetime:
            self.ra_routers[port.name] = (pkt.src, self.engine.now + m.router_lifetime * 1000)
        if changed:
            self.engine.emit("device_changed", self)

    def link_state_changed(self, port) -> None:
        super().link_state_changed(port)
        if self.dot1x and port.is_up:
            self.engine.schedule(50.0, self._eapol_start)
        if port.ipv6_auto and port.is_up:
            self.engine.schedule(150.0, self.start_slaac)
        if self.dhcp_enabled and port.is_up and (self.dhcp_job is None or self.dhcp_job.done) \
                and (self.nic.ip is None or self.dhcp_info.get("apipa")):
            self.engine.schedule(200.0, self.start_dhcp)

    def set_ip(self, ip: str | None, mask: str | None = None) -> None:
        from ..addresses import make_interface
        self.nic.ip = make_interface(ip, mask) if ip else None
        self.arp.clear()

    def set_gateway(self, gw: str | None) -> None:
        self.gateway = IPv4Address(gw) if gw else None

    def route_lookup(self, dst: IPv4Address):
        p = self.nic
        if p.ip is None:
            return None
        if dst in p.ip.network or dst == IPv4Address("255.255.255.255"):
            return p, dst
        best = None
        for net, gw, _ in self.routes:
            if dst in net and (gw is None or gw in p.ip.network) and (best is None or net.prefixlen > best[0].prefixlen):
                best = (net, gw)
        if best is not None:
            return p, best[1] or dst
        if self.gateway is not None and self.gateway in p.ip.network:
            return p, self.gateway
        return None

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["gateway"] = str(self.gateway) if self.gateway else None
        cfg["dns"] = str(self.dns) if self.dns else None
        if self.dhcp_enabled:
            cfg["dhcp"] = True
        if self.dot1x:
            cfg["dot1x"] = dict(self.dot1x)
        if getattr(self, "mail", None):
            cfg["mail"] = dict(self.mail)
        if getattr(self, "inbox", None):
            cfg["inbox"] = list(self.inbox)
        if self.gateway6:
            cfg["gateway6"] = str(self.gateway6)
        if getattr(self.nic, "is_wireless", False):
            cfg["wifi"] = dict(getattr(self, "wifi", {}) or {})
        if self.routes:
            cfg["routes"] = [[str(n), str(g) if g else None, p] for n, g, p in self.routes]
        if self.dns_server_list[1:]:
            cfg["dns_alt"] = [str(x) for x in self.dns_server_list[1:]]
        from ..security import malware
        malware.to_config(self, cfg)
        return cfg

    def apply_config(self, cfg: dict) -> None:
        super().apply_config(cfg)
        self.set_gateway(cfg.get("gateway"))
        self.dns = IPv4Address(cfg["dns"]) if cfg.get("dns") else None
        self.dhcp_enabled = bool(cfg.get("dhcp"))
        self.dot1x = dict(cfg["dot1x"]) if cfg.get("dot1x") else None
        self.mail = dict(cfg["mail"]) if cfg.get("mail") else None
        self.inbox = list(cfg.get("inbox", []))
        self.set_gateway6(cfg.get("gateway6"))
        if getattr(self.nic, "is_wireless", False):
            self.wifi = dict(cfg.get("wifi") or {"ssid": None, "key": None})
        self.routes = [(IPv4Network(n), IPv4Address(g) if g else None, bool(p)) for n, g, p in cfg.get("routes", [])]
        self.dns_alt = [IPv4Address(x) for x in cfg.get("dns_alt", [])]
        from ..security import malware
        malware.apply_config(self, cfg)
        self.nic._slaac = set()
        if self.nic.ipv6_auto:
            self.engine.schedule(300.0, self.start_slaac)
        if self.dhcp_enabled:
            self.engine.schedule(300.0, self.start_dhcp)        # re-acquire the lease after loading


class Server(Host):
    """PC with services (Packet Tracer style): DHCP, DNS and HTTP, configured on the Services tab."""

    category = "server"
    model = "Server"
    default_hostname = "Server"

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        from ..services.dhcp import DhcpServer, DhcpServerConfig
        from ..services.dns import DnsServer
        from ..services.http import HttpServer
        self.dhcp_server = DhcpServer(self, DhcpServerConfig(enabled=False))
        self.dns_server = DnsServer(self, enabled=False)
        self.http_server = HttpServer(self, enabled=True)
        from ..services.aaa import AaaServer
        self.aaa_server = AaaServer()
        from ..services.files import FileServer
        self.file_server = FileServer()
        from .iot import IotService
        self.iot_service = IotService()
        from ..services.mgmt import NtpServer, SyslogServer
        self.syslog_server = SyslogServer(self, enabled=True)
        self.ntp_server = NtpServer(self, enabled=True)

    def listening(self) -> list[tuple[str, int]]:
        out = []
        if self.syslog_server.enabled:
            out.append(("UDP", 514))
        if self.file_server.tftp:
            out.append(("UDP", 69))
        if self.file_server.ftp:
            out.append(("TCP", 21))
        if self.file_server.mail:
            out += [("TCP", 25), ("TCP", 110)]
        if self.aaa_server.radius:
            out.append(("UDP", 1812))
        if self.aaa_server.tacacs:
            out.append(("TCP", 49))
        if self.ntp_server.enabled:
            out.append(("UDP", 123))
        if self.http_server.enabled:
            out.append(("TCP", 80))
        if self.dns_server.enabled:
            out.append(("UDP", 53))
        if self.dhcp_server.cfg.enabled:
            out.append(("UDP", 67))
        return out

    def deliver_protocol(self, pkt, port) -> None:
        from ..packets import TcpSegment, UdpDatagram
        from ..security.malware import on_packet
        l4 = pkt.payload
        if on_packet(self, pkt):                              # an exploit attempt (malware training)
            return
        if getattr(pkt, "flood", None) is not None:          # DDoS probe: resources, reflection, nothing else
            from ..security.ddos import guard, reflect
            g = guard(self)
            if isinstance(l4, UdpDatagram) and l4.dport == 53 and self.dns_server.enabled:
                reflect(self, pkt, g["open_resolver"])
            elif isinstance(l4, UdpDatagram) and l4.dport == 123 and self.ntp_server.enabled:
                reflect(self, pkt, g["ntp_monlist"])
            return
        if isinstance(l4, UdpDatagram) and l4.dport == 67:
            self.dhcp_server.handle(pkt, port)
        elif isinstance(l4, UdpDatagram) and l4.dport == 53:
            self.dns_server.handle(pkt, port)
        elif isinstance(l4, TcpSegment) and l4.dport == 80:
            self.http_server.handle(pkt, port)
        elif isinstance(l4, UdpDatagram) and l4.dport == 5683:
            self.iot_service.handle(self, pkt)
        elif (isinstance(l4, UdpDatagram) and l4.dport == 69) or \
                (isinstance(l4, TcpSegment) and l4.dport in (21, 25, 110)):
            self.file_server.handle(self, pkt)
        elif (isinstance(l4, UdpDatagram) and l4.dport == 1812) or (isinstance(l4, TcpSegment) and l4.dport == 49):
            self.aaa_server.handle(self, pkt)
        elif isinstance(l4, UdpDatagram) and l4.dport == 514:
            self.syslog_server.handle(pkt, port)
        elif isinstance(l4, UdpDatagram) and l4.dport == 123:
            self.ntp_server.handle(pkt, port)
        else:
            super().deliver_protocol(pkt, port)

    def to_config(self) -> dict:
        cfg = super().to_config()
        cfg["services"] = {"dhcp": self.dhcp_server.cfg.to_dict(),
                           "dns": {"enabled": self.dns_server.enabled, "records": dict(self.dns_server.records)},
                           "http": {"enabled": self.http_server.enabled, "pages": dict(self.http_server.pages)},
                           "syslog": {"enabled": self.syslog_server.enabled},
                           "ntp": {"enabled": self.ntp_server.enabled},
                           "aaa": self.aaa_server.to_dict(), "files": self.file_server.to_dict(),
                           "iot": self.iot_service.to_dict()}
        from ..security.ddos import DEFAULT_GUARD, guard
        if guard(self) != DEFAULT_GUARD:
            cfg["services"]["ddos"] = dict(guard(self))
        return cfg

    def apply_config(self, cfg: dict) -> None:
        from ..services.dhcp import DhcpServerConfig
        super().apply_config(cfg)
        sv = cfg.get("services", {})
        self.dhcp_server.cfg = DhcpServerConfig.from_dict(sv.get("dhcp", {"enabled": False}))
        self.dhcp_server.bindings.clear()
        dns = sv.get("dns", {})
        self.dns_server.enabled = dns.get("enabled", False)
        self.dns_server.records = dict(dns.get("records", {}))
        http = sv.get("http", {})
        self.http_server.enabled = http.get("enabled", True)
        self.http_server.pages = dict(http.get("pages", {}))
        self.syslog_server.enabled = sv.get("syslog", {}).get("enabled", True)
        self.syslog_server.messages.clear()
        self.ntp_server.enabled = sv.get("ntp", {}).get("enabled", True)
        from ..services.aaa import AaaServer
        self.aaa_server = AaaServer.from_dict(sv.get("aaa"))
        from ..services.files import FileServer
        self.file_server = FileServer.from_dict(sv.get("files"))
        from .iot import IotService
        self.iot_service = IotService.from_dict(sv.get("iot"))
        from ..security.ddos import DEFAULT_GUARD
        self.ddos = dict(DEFAULT_GUARD, **(sv.get("ddos") or {}))


__all__ = ["Host", "Server", "IPv4Interface"]


class _LinuxMixin:
    """Linux end host: eth0, TTL 64, SLAAC on by default (accept_ra=1), no APIPA after a failed DHCP."""

    NIC = ("eth0", "eth0")
    os = "linux"
    default_ttl = 64

    def _linux_init(self) -> None:
        self.nic.ipv6_auto = True
        self.user_root = False

    def dhcp_failed(self) -> None:
        self.nic.ip = None
        self.gateway = None
        self.dhcp_info = {"failed": True}
        self.engine.emit("device_changed", self)


class LinuxHost(_LinuxMixin, Host):
    model = "PC (Linux)"

    def __init__(self, engine, name, device_id=None):
        Host.__init__(self, engine, name, device_id)
        self._linux_init()


class LinuxServer(_LinuxMixin, Server):
    model = "Server (Linux)"

    def __init__(self, engine, name, device_id=None):
        Server.__init__(self, engine, name, device_id)
        self._linux_init()
