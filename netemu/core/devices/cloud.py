"""The Internet: an ISP cloud with public services, for labs that need "a way out".

Every port is an ISP hand-off with its own /30 from 203.0.113.0/24 (TEST-NET-3):
port N has the ISP address 203.0.113.(4N+1) and gives the customer 203.0.113.(4N+2)
with the gateway 203.0.113.(4N+1).  Behind the cloud live public services:

* DNS resolvers 8.8.8.8 and 1.1.1.1 (www.example.com, www.netemu.lab, ...);
* web sites www.example.com (93.184.216.34) and www.netemu.lab (198.51.100.80);
* every service address answers ping and appears at the end of a traceroute.

Like a real provider the cloud drops packets whose source is a private (RFC 1918)
address, so a lab network reaches the Internet only through NAT."""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from ..packets import TcpSegment, UdpDatagram
from .router import Router

PORTS = 8
HANDOFF = IPv4Network("203.0.113.0/24")
PRIVATE = [IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]

SERVICES = {                       # loopback name -> (address, description)
    "dns.google": ("8.8.8.8", "DNS"),
    "one.one.one.one": ("1.1.1.1", "DNS"),
    "www.example.com": ("93.184.216.34", "WEB"),
    "www.netemu.lab": ("198.51.100.80", "WEB"),
}
DNS_RECORDS = {
    "dns.google": "8.8.8.8", "one.one.one.one": "1.1.1.1",
    "example.com": "93.184.216.34", "www.example.com": "93.184.216.34",
    "netemu.lab": "198.51.100.80", "www.netemu.lab": "198.51.100.80",
}
PAGES = {
    "93.184.216.34": {"index.html": "<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1>"
                           "<p>This domain is for use in illustrative examples in documents.</p></body></html>"},
    "198.51.100.80": {"index.html": "<html><head><title>NetEmu</title></head><body><h1>Hello from the Internet!</h1>"
                           "<p>Your lab reached a public web server through the ISP cloud.</p></body></html>",
                      "about": "<html><body><h1>NetEmu</h1><p>Network emulator for labs.</p></body></html>"},
}


def handoff(n: int) -> tuple[IPv4Interface, IPv4Address]:
    """(ISP side interface, customer address) of port *n*."""
    base = int(HANDOFF.network_address) + 4 * n
    return IPv4Interface(f"{IPv4Address(base + 1)}/30"), IPv4Address(base + 2)


def is_private(ip) -> bool:
    return any(ip in n for n in PRIVATE)


class Internet(Router):
    category = "cloud"
    vendor = "isp"
    model = "Internet (ISP)"
    default_hostname = "Internet"
    default_ttl = 64

    def __init__(self, engine, name, device_id=None):
        super().__init__(engine, name, device_id)
        self.hostname = "Internet"
        from ..services.dns import DnsServer
        from ..services.http import HttpServer
        for n in range(PORTS):
            p = self.add_port(f"ISP{n}", f"ISP{n}")
            p.ip = handoff(n)[0]
        for name, (ip, _) in SERVICES.items():
            self.add_loopback(name).ip = IPv4Interface(f"{ip}/32")
        self.dns_server = DnsServer(self, DNS_RECORDS)
        self.http = {ip: HttpServer(self, pages) for ip, pages in PAGES.items()}
        self.dropped_private = 0

    # -- behaviour --------------------------------------------------------------------------------
    def ingress_filter(self, pkt, port):
        if not port.is_loopback and is_private(pkt.src):
            self.dropped_private += 1           # no route back to a private address: drop silently
            return None
        return pkt

    def deliver_protocol(self, pkt, port) -> None:
        l4 = pkt.payload
        if isinstance(l4, UdpDatagram) and l4.dport == 53 and str(pkt.dst) in ("8.8.8.8", "1.1.1.1"):
            self.dns_server.handle(pkt, port)
        elif isinstance(l4, TcpSegment) and l4.dport == 80 and str(pkt.dst) in self.http:
            self.http[str(pkt.dst)].handle(pkt, port)
        else:
            super().deliver_protocol(pkt, port)

    # -- the cloud is not configurable: it always comes back the same ------------------------------
    def apply_config(self, cfg: dict) -> None:
        for n in range(PORTS):
            p = self.ports.get(f"ISP{n}")
            if p is not None:
                p.ip = handoff(n)[0]
                p.set_admin(True)

    def factory_reset(self) -> None:
        self.apply_config({})

    def create_cli(self):
        return None
