"""Parsing and printing of Cisco IOS ACL entries (also reused for named ACL modes)."""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from ..addresses import is_valid_ip
from .acl import ANY, ICMP_TYPES, AclRule, wildcard_net

PORT_NAMES = {"www": 80, "http": 80, "https": 443, "telnet": 23, "ssh": 22, "ftp": 21, "ftp-data": 20,
              "smtp": 25, "domain": 53, "bootps": 67, "bootpc": 68, "tftp": 69, "pop3": 110, "snmp": 161,
              "ntp": 123, "syslog": 514}
PORT_TEXT = {v: k for k, v in PORT_NAMES.items() if k not in ("http",)}
EXT_PROTOS = ("ip", "icmp", "tcp", "udp", "ospf")


class AclSyntaxError(ValueError):
    def __init__(self, pos: int):
        super().__init__(pos)
        self.pos = pos


def _addr(words, i):
    """address spec -> (network, next index)."""
    if i >= len(words):
        raise AclSyntaxError(i)
    w = words[i].lower()
    if w == "any":
        return ANY, i + 1
    if w == "host":
        if i + 1 >= len(words) or not is_valid_ip(words[i + 1]):
            raise AclSyntaxError(i + 1)
        return IPv4Network(f"{words[i + 1]}/32"), i + 2
    if is_valid_ip(words[i]):
        if i + 1 < len(words) and is_valid_ip(words[i + 1]):
            return wildcard_net(words[i], words[i + 1]), i + 2
        return IPv4Network(f"{words[i]}/32"), i + 1
    raise AclSyntaxError(i)


def _port(words, i):
    if i >= len(words):
        raise AclSyntaxError(i)
    w = words[i].lower()
    if w.isdigit() and 0 <= int(w) <= 65535:
        return int(w)
    if w in PORT_NAMES:
        return PORT_NAMES[w]
    raise AclSyntaxError(i)


def _port_cond(words, i):
    if i < len(words) and words[i].lower() in ("eq", "neq", "gt", "lt"):
        return (words[i].lower(), _port(words, i + 1)), i + 2
    if i < len(words) and words[i].lower() == "range":
        return ("range", _port(words, i + 1), _port(words, i + 2)), i + 3
    return None, i


def parse_entry(action: str, rest: str, extended: bool) -> AclRule:
    """'192.168.1.0 0.0.0.255' (standard) or 'tcp any host 1.1.1.1 eq 80' (extended)."""
    words = rest.split()
    if not extended:
        src, i = _addr(words, 0)
        if i < len(words) and words[i].lower() != "log":
            raise AclSyntaxError(i)
        return AclRule(action, "ip", src)
    if not words or words[0].lower() not in EXT_PROTOS:
        raise AclSyntaxError(0)
    proto = words[0].lower()
    src, i = _addr(words, 1)
    sport = dport = None
    if proto in ("tcp", "udp"):
        sport, i = _port_cond(words, i)
    dst, i = _addr(words, i)
    icmp_type = None
    if proto in ("tcp", "udp"):
        dport, i = _port_cond(words, i)
    elif proto == "icmp" and i < len(words) and words[i].lower() in ICMP_TYPES:
        icmp_type = ICMP_TYPES[words[i].lower()]
        i += 1
    while i < len(words) and words[i].lower() in ("log", "established"):
        i += 1
    if i < len(words):
        raise AclSyntaxError(i)
    return AclRule(action, proto, src, dst, sport, dport, icmp_type)


def _addr_text(net: IPv4Network, show: bool) -> str:
    if net.prefixlen == 0:
        return "any"
    if net.prefixlen == 32:
        return f"host {net.network_address}"
    wc = IPv4Address(int(net.hostmask))
    return f"{net.network_address}, wildcard bits {wc}" if show else f"{net.network_address} {wc}"


def _port_text(cond) -> str:
    if cond is None:
        return ""
    name = lambda p: PORT_TEXT.get(p, str(p))
    if cond[0] == "range":
        return f" range {name(cond[1])} {name(cond[2])}"
    return f" {cond[0]} {name(cond[1])}"


def entry_text(r: AclRule, extended: bool, show: bool = False) -> str:
    """Text after the sequence number: 'permit 192.168.1.0, wildcard bits 0.0.0.255' etc."""
    if not extended:
        src = _addr_text(r.src, show)
        if r.src.prefixlen == 32 and show:
            src = str(r.src.network_address)
        return f"{r.action} {src}"
    text = f"{r.action} {r.proto} {_addr_text(r.src, False)}{_port_text(r.sport)} {_addr_text(r.dst, False)}"
    text += _port_text(r.dport)
    if r.icmp_type is not None:
        text += " " + {8: "echo", 0: "echo-reply", 3: "unreachable", 11: "time-exceeded"}.get(r.icmp_type,
                                                                                              str(r.icmp_type))
    return text


def numbered_kind(num: int) -> str | None:
    if 1 <= num <= 99 or 1300 <= num <= 1999:
        return "standard"
    if 100 <= num <= 199 or 2000 <= num <= 2699:
        return "extended"
    return None
