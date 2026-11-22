"""The Internet: an ISP cloud with public services, for labs that need "a way out".

Every port is an ISP hand-off with its own /30 from 203.0.113.0/24 (TEST-NET-3):
port N has the ISP address 203.0.113.(4N+1) and gives the customer 203.0.113.(4N+2)
with the gateway 203.0.113.(4N+1).  Behind the cloud live public services:

* DNS resolvers 8.8.8.8 and 1.1.1.1 (www.example.com, www.netemu.lab, ...);
* web sites www.example.com (93.184.216.34) and www.netemu.lab (198.51.100.80);
* every service address answers ping and appears at the end of a traceroute.

Like a real provider the cloud drops packets whose source is a private (RFC 1918)
address, so a lab network reaches the Internet only through NAT.

DDoS training: two badly configured public servers live in the cloud and can be abused as
reflectors (an open DNS resolver 198.51.100.53 and an NTP server with ``monlist`` 198.51.100.123).
Every customer port can buy the provider's protection (``protect``): traffic scrubbing (attack
flows to the customer are removed, legitimate traffic passes), RTBH (everything to the listed
addresses is dropped inside the ISP) and FlowSpec rules ("udp src 53", "icmp", "tcp dst 80"
...) that drop matching traffic before it reaches the customer's link.

DNS switching: the provider hosts customers' domains (``zones``: name -> A address and TTL), answered by
the public resolvers.  A zone can be switched to the provider's DDoS protection service (``shield``):
the resolvers then answer with the service's address 198.51.100.200, a reverse proxy that absorbs
floods, filters bots and fetches pages from the customer's origin address.  Clients move over when the
TTL of their cached record runs out; attacks aimed at the origin's IP address are not affected."""
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
    "open-resolver.example": ("198.51.100.53", "DNS (open resolver)"),
    "ntp.example": ("198.51.100.123", "NTP (monlist)"),
    "shield.example": ("198.51.100.200", "DDoS protection (reverse proxy)"),
    "update.example": ("198.51.100.150", "Antivirus and OS updates"),
    "c2.example": ("198.51.100.66", "Malware command-and-control server (C2)"),
}
SHIELD_IP = "198.51.100.200"
DEFAULT_TTL = 300
REFLECTORS = {"198.51.100.53": 53, "198.51.100.123": 123}
DNS_RECORDS = {
    "dns.google": "8.8.8.8", "one.one.one.one": "1.1.1.1",
    "example.com": "93.184.216.34", "www.example.com": "93.184.216.34",
    "netemu.lab": "198.51.100.80", "www.netemu.lab": "198.51.100.80",
    "update.example": "198.51.100.150", "c2.example": "198.51.100.66",
}
PAGES = {
    "93.184.216.34": {"index.html": "<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1>"
                           "<p>This domain is for use in illustrative examples in documents.</p></body></html>"},
    "198.51.100.80": {"index.html": "<html><head><title>NetEmu</title></head><body><h1>Hello from the Internet!</h1>"
                           "<p>Your lab reached a public web server through the ISP cloud.</p></body></html>",
                      "about": "<html><body><h1>NetEmu</h1><p>Network emulator for labs.</p></body></html>"},
    "198.51.100.150": {"index.html": "<html><body><h1>Update server</h1><p>Antivirus signatures and OS patches.</p>"
                                     "</body></html>", "defs": "signatures version 3"},
    "198.51.100.66": {"index.html": "<html><body><h1>It works!</h1></body></html>", "gate.php": "OK"},
}


class _CloudDns:
    """The public resolvers: static records plus the customers' zones hosted at the provider."""

    def __new__(cls, owner, records):
        from ..services.dns import DnsServer

        class CloudDns(DnsServer):
            def lookup(self, name):
                from ipaddress import IPv4Address
                z = owner.zones.get(name.lower().rstrip("."))
                if z is not None:
                    ip = SHIELD_IP if z.get("shield") else z.get("a")
                    return IPv4Address(ip) if ip else None
                return super().lookup(name)

            def ttl_of(self, name):
                z = owner.zones.get(name.lower().rstrip("."))
                return int(z.get("ttl") or DEFAULT_TTL) if z is not None else None

        return CloudDns(owner, records)


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
        from ..services.http import HttpServer
        for n in range(PORTS):
            p = self.add_port(f"ISP{n}", f"ISP{n}")
            p.ip = handoff(n)[0]
        for name, (ip, _) in SERVICES.items():
            self.add_loopback(name).ip = IPv4Interface(f"{ip}/32")
        self.dns_server = _CloudDns(self, DNS_RECORDS)
        self.zones: dict = {}               # customer domain -> {"a": origin IP, "ttl": s, "shield": bool}
        self.shield_stats = {"proxied": 0, "failed": 0}
        self.http = {ip: HttpServer(self, pages) for ip, pages in PAGES.items()}
        self.dropped_private = 0
        self.protect: dict = {}             # customer port -> {"scrub": bool, "rtbh": [ip], "flowspec": [rule]}

    # -- behaviour --------------------------------------------------------------------------------
    def ingress_filter(self, pkt, port):
        if not port.is_loopback and is_private(pkt.src):
            self.dropped_private += 1           # no route back to a private address: drop silently
            return None
        return pkt

    # -- DDoS: provider-side protection of a customer port --------------------------------------------
    def _isp_drops(self, pkt, out_port) -> bool:
        prot = self.protect.get(out_port.name)
        if not prot:
            return False
        if str(pkt.dst) in prot.get("rtbh", []):
            return True                                    # RTBH: blackholed inside the provider
        tag = getattr(pkt, "flood", None)
        if prot.get("scrub") and tag is not None and tag.kind not in ("http", "slowloris"):
            return True                                    # scrubbing centre removes L3/L4 attack flows
        return any(flowspec_match(rule, pkt) for rule in prot.get("flowspec", []))

    def egress_filter(self, pkt, in_port, out_port):
        pkt = super().egress_filter(pkt, in_port, out_port)
        if pkt is None or self._isp_drops(pkt, out_port):
            return None
        return pkt

    def send_ip(self, pkt, in_port=None) -> bool:
        if in_port is None and self.protect:               # traffic the cloud itself sends (reflectors)
            r = self.route_lookup(pkt.dst)
            if r is not None and self._isp_drops(pkt, r[0]):
                return True
        return super().send_ip(pkt, in_port)

    def deliver_protocol(self, pkt, port) -> None:
        l4 = pkt.payload
        if getattr(pkt, "flood", None) is not None:
            if isinstance(l4, UdpDatagram) and REFLECTORS.get(str(pkt.dst)) == l4.dport:
                from ..security.ddos import reflect
                reflect(self, pkt, True)                   # the misconfigured public reflectors answer anyone
            return
        if isinstance(l4, TcpSegment) and l4.dport == 80 and str(pkt.dst) == SHIELD_IP:
            self._shield(pkt)
            return
        if isinstance(l4, UdpDatagram) and l4.dport == 53 and str(pkt.dst) in ("8.8.8.8", "1.1.1.1"):
            self.dns_server.handle(pkt, port)
        elif isinstance(l4, TcpSegment) and l4.dport == 80 and str(pkt.dst) in self.http:
            self.http[str(pkt.dst)].handle(pkt, port)
        else:
            super().deliver_protocol(pkt, port)

    # -- DNS hosting and the protection service -----------------------------------------------------
    def set_zone(self, name: str, a: str | None = None, ttl: int | None = None, shield: bool | None = None) -> dict:
        z = self.zones.setdefault(name.lower().rstrip("."), {"a": "", "ttl": DEFAULT_TTL, "shield": False})
        if a is not None:
            z["a"] = a
        if ttl is not None:
            z["ttl"] = int(ttl)
        if shield is not None:
            z["shield"] = bool(shield)
        return z

    def _shield(self, pkt) -> None:
        """The reverse proxy: floods end here (absorbed by the service's capacity, bots fail the browser
        check); real clients get the page fetched from the customer's origin."""
        from ..services.http import HttpGet, HttpMessage
        seg = pkt.payload
        if getattr(pkt, "flood", None) is not None:
            return
        if "SYN" in seg.flags and "ACK" not in seg.flags:
            self.send_tcp(pkt.src, 80, seg.sport, None, "SYN,ACK", src=SHIELD_IP)
            return
        if not (isinstance(seg.data, HttpMessage) and seg.data.request):
            return
        z = self.zones.get(seg.data.host.lower())
        client, cport = pkt.src, seg.sport

        def reply(status, body):
            if status is None:
                self.shield_stats["failed"] += 1
                code, title = (504, "Gateway Timeout") if body == "Request Timeout" else (502, "Bad Gateway")
                status, body = code, f"<html><body><h1>{code} {title}</h1><p>The origin did not answer.</p></body></html>"
            else:
                self.shield_stats["proxied"] += 1
            self.send_tcp(client, 80, cport, HttpMessage(False, status=status, body=body), "PSH,ACK,FIN",
                          src=SHIELD_IP)

        if not z or not z.get("shield") or not z.get("a"):
            reply(404, "<html><body><h1>404</h1><p>This site is not protected by the service.</p></body></html>")
            return
        job = HttpGet(self, f"http://{z['a']}{seg.data.path}", reply, src=SHIELD_IP)
        job.host_header = seg.data.host
        job.TIMEOUT_MS = 2500.0                 # give up before the client does and answer 504
        job.start()

    # -- the cloud is not configurable: it always comes back the same ------------------------------
    def to_config(self) -> dict:
        cfg = super().to_config()
        if any(self.protect.values()):
            cfg["isp_protect"] = {k: dict(v) for k, v in self.protect.items() if v}
        if self.zones:
            cfg["isp_zones"] = {k: dict(v) for k, v in self.zones.items()}
        return cfg

    def apply_config(self, cfg: dict) -> None:
        self.protect = {k: dict(v) for k, v in (cfg.get("isp_protect") or {}).items()}
        self.zones = {k: dict(v) for k, v in (cfg.get("isp_zones") or {}).items()}
        for n in range(PORTS):
            p = self.ports.get(f"ISP{n}")
            if p is not None:
                p.ip = handoff(n)[0]
                p.set_admin(True)

    def factory_reset(self) -> None:
        self.apply_config({})

    def create_cli(self):
        return None


def flowspec_match(rule: str, pkt) -> bool:
    """'udp src 53', 'udp dst 80', 'tcp dst 80', 'icmp', 'udp' (all UDP)."""
    from ..packets import PROTO_ICMP, PROTO_TCP, PROTO_UDP
    w = rule.lower().split()
    if not w:
        return False
    proto = {"udp": PROTO_UDP, "tcp": PROTO_TCP, "icmp": PROTO_ICMP}.get(w[0])
    if proto is None or pkt.protocol != proto:
        return False
    if len(w) >= 3 and w[1] in ("src", "dst") and w[2].isdigit():
        port = getattr(pkt.payload, "sport" if w[1] == "src" else "dport", None)
        return port == int(w[2])
    return True
