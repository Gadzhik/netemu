"""Generic command-tree parser used by the Cisco, Huawei and Junos dialects.

Features: unique-prefix abbreviations ("sh ip int br"), typed arguments,
context help ('?'), Tab completion and error positions for '^' markers.
"""
from __future__ import annotations

from ipaddress import IPv4Address

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..addresses import IPv4Network, is_valid_ip, is_valid_mask

_TOKEN_RE = re.compile(r'"[^"]*"?|\S+')


@dataclass
class Token:
    text: str
    pos: int

    @property
    def value(self) -> str:
        t = self.text
        if t.startswith('"'):
            t = t[1:-1] if t.endswith('"') and len(t) > 1 else t[1:]
        return t


def tokenize(line: str) -> list[Token]:
    return [Token(m.group(0), m.start()) for m in _TOKEN_RE.finditer(line)]


# -- argument types ------------------------------------------------------------
class Arg:
    """Base argument type.  ``match`` returns (value, tokens consumed) or None."""

    name = "WORD"
    rest = False  # consumes the rest of the line

    def __init__(self, name: str | None = None):
        if name:
            self.name = name

    def match(self, tokens: list[Token], i: int, session) -> tuple[Any, int] | None:
        return tokens[i].value, 1


class WordArg(Arg):
    name = "WORD"


class LineArg(Arg):
    name = "LINE"
    rest = True

    def match(self, tokens, i, session):
        return " ".join(t.value if t.text.startswith('"') else t.text for t in tokens[i:]), len(tokens) - i


class IpArg(Arg):
    name = "A.B.C.D"

    def match(self, tokens, i, session):
        return (tokens[i].text, 1) if is_valid_ip(tokens[i].text) else None


def is_ipv6(text: str) -> bool:
    from ipaddress import IPv6Address
    try:
        IPv6Address(text)
        return ":" in text
    except ValueError:
        return False


class Ipv6Arg(Arg):
    name = "X:X:X:X::X"

    def match(self, tokens, i, session):
        return (tokens[i].text, 1) if is_ipv6(tokens[i].text) else None


class AnyIpArg(Arg):
    """IPv4 or IPv6 address (ping/traceroute)."""
    name = "WORD"

    def match(self, tokens, i, session):
        t = tokens[i].text
        return (t, 1) if is_valid_ip(t) or is_ipv6(t) else None


class Ipv6PrefixArg(Arg):
    name = "X:X:X:X::X/<0-128>"

    def match(self, tokens, i, session):
        from ipaddress import IPv6Interface
        t = tokens[i].text
        if "/" not in t:
            return None
        try:
            IPv6Interface(t)
        except ValueError:
            return None
        return t, 1


class MaskArg(Arg):
    name = "A.B.C.D"

    def match(self, tokens, i, session):
        return (tokens[i].text, 1) if is_valid_mask(tokens[i].text) else None


class MaskOrLenArg(Arg):
    """Huawei style: either a dotted mask or a prefix length."""
    name = "MASK|LEN"

    def match(self, tokens, i, session):
        t = tokens[i].text
        if is_valid_mask(t):
            return t, 1
        if t.isdigit() and 0 <= int(t) <= 32:
            return str(IPv4Network(f"0.0.0.0/{t}").netmask), 1
        return None


class PrefixArg(Arg):
    """A.B.C.D/LEN (Junos, MikroTik)."""
    name = "A.B.C.D/LEN"

    def __init__(self, name=None, host_bits_ok: bool = True):
        super().__init__(name)
        self.host_bits_ok = host_bits_ok

    def match(self, tokens, i, session):
        t = tokens[i].text
        if "/" not in t:
            return None
        ip, _, ln = t.partition("/")
        if not (is_valid_ip(ip) and ln.isdigit() and 0 <= int(ln) <= 32):
            return None
        return t, 1


class NumArg(Arg):
    def __init__(self, lo: int, hi: int, name: str | None = None):
        super().__init__(name or f"<{lo}-{hi}>")
        self.lo, self.hi = lo, hi

    def match(self, tokens, i, session):
        t = tokens[i].text
        if t.isdigit() and self.lo <= int(t) <= self.hi:
            return int(t), 1
        return None


class IfaceArg(Arg):
    """Interface name; accepts 'g0/0', 'Gi0/0', 'GigabitEthernet 0/0'."""

    def __init__(self, name: str | None = None, ports=None):
        super().__init__(name or "IFACE")
        self.ports = ports  # optional callable(session) -> ports to consider

    def match(self, tokens, i, session):
        dev = session.device
        p = dev.get_port(tokens[i].text)
        if p is not None:
            return p, 1
        if i + 1 < len(tokens) and tokens[i].text.isalpha():
            p = dev.get_port(tokens[i].text + tokens[i + 1].text)
            if p is not None:
                return p, 2
        return None


def parse_vlan_list(text: str) -> set[int] | None:
    """'10,20,30-32' -> {10, 20, 30, 31, 32}; None when malformed or out of 1-4094."""
    out: set[int] = set()
    for part in text.split(","):
        a, _, b = part.partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            return None
        lo, hi = int(a), int(b or a)
        if not 1 <= lo <= hi <= 4094:
            return None
        out.update(range(lo, hi + 1))
    return out


class VlanListArg(Arg):
    """VLAN list such as 10,20,30-35 (returns a set of ints)."""

    def __init__(self, name: str | None = None):
        super().__init__(name or "WORD")

    def match(self, tokens, i, session):
        v = parse_vlan_list(tokens[i].text)
        return (v, 1) if v else None


class LoopbackArg(Arg):
    """'loopback0', 'Lo0', 'LoopBack 1' (one or two tokens) -> loopback number."""

    _re = re.compile(r"^lo(?:o(?:p(?:b(?:a(?:c(?:k)?)?)?)?)?)?(\d+)$", re.I)

    def __init__(self, name: str | None = None):
        super().__init__(name or "<0-2147483647>")

    def match(self, tokens, i, session):
        m = self._re.match(tokens[i].text)
        if m:
            return int(m.group(1)), 1
        if i + 1 < len(tokens) and self._re.match(tokens[i].text + tokens[i + 1].text):
            return int(self._re.match(tokens[i].text + tokens[i + 1].text).group(1)), 2
        return None


class VlanIfArg(Arg):
    """'vlan10', 'Vl10', 'Vlan 10' (one or two tokens) -> VLAN number of a switch virtual interface."""

    def __init__(self, name: str | None = None, word: str = "vlan", min_prefix: int = 2, lo: int = 1, hi: int = 4094):
        super().__init__(name or "Vlan <1-4094>")
        self.word = word
        self.min_prefix = min_prefix
        self.lo, self.hi = lo, hi

    def _split(self, text):
        m = re.match(r"^([a-z-]+)(\d+)$", text, re.I)
        return (m.group(1).lower(), int(m.group(2))) if m else (None, None)

    def _word_ok(self, w):
        return w is not None and len(w) >= self.min_prefix and self.word.startswith(w)

    def match(self, tokens, i, session):
        w, n = self._split(tokens[i].text)
        if self._word_ok(w) and self.lo <= n <= self.hi:
            return n, 1
        if i + 1 < len(tokens) and self._word_ok(tokens[i].text.lower()) and tokens[i + 1].text.isdigit():
            n = int(tokens[i + 1].text)
            if self.lo <= n <= self.hi:
                return n, 2
        return None


class TunnelNameArg(Arg):
    """'Tunnel0/0/0', 'tunnel 0/0/0' (Huawei) -> '0/0/0'."""

    def __init__(self, name: str | None = None):
        super().__init__(name or "Tunnel <slot/card/port>")

    def match(self, tokens, i, session):
        t = tokens[i].text
        m = re.match(r"^tunnel(\d+(?:/\d+)*)$", t, re.I)
        if m:
            return m.group(1), 1
        if t.lower() == "tunnel" and i + 1 < len(tokens) and re.match(r"^\d+(?:/\d+)*$", tokens[i + 1].text):
            return tokens[i + 1].text, 2
        return None


class WildcardArg(Arg):
    """Wildcard mask such as 0.0.0.255 (contiguous)."""

    def __init__(self, name: str | None = None):
        super().__init__(name or "A.B.C.D")

    def match(self, tokens, i, session):
        t = tokens[i].text
        if not is_valid_ip(t):
            return None
        inv = int(IPv4Address(t)) ^ 0xFFFFFFFF
        if inv and (inv | (inv - 1)) & 0xFFFFFFFF != 0xFFFFFFFF:
            return None
        return t, 1


class SubIfaceArg(Arg):
    """A new or existing subinterface name: 'g0/0.10', 'GigabitEthernet0/0.10',
    'GE0/0/0.10'.  Returns (parent port, number)."""

    def __init__(self, name: str | None = None):
        super().__init__(name or "IFACE.N")

    def match(self, tokens, i, session):
        for n in (1, 2):
            if i + n > len(tokens):
                break
            text = "".join(t.text for t in tokens[i:i + n])
            base, dot, num = text.rpartition(".")
            if not dot or not num.isdigit() or not 1 <= int(num) <= 4294967295:
                continue
            parent = session.device.get_port(base)
            if parent is not None and not parent.is_sub:
                return (parent, int(num)), n
        return None


# -- tree ----------------------------------------------------------------------
Handler = Callable[..., Any]


@dataclass
class Node:
    word: str | None = None
    help: str = ""
    arg: Arg | None = None
    handler: Handler | None = None
    children: list["Node"] = field(default_factory=list)
    negatable: bool = False
    hidden: bool = False

    def add(self, *nodes: "Node") -> "Node":
        self.children.extend(nodes)
        return self

    @property
    def label(self) -> str:
        return self.word if self.word else self.arg.name


def kw(word: str, help: str = "", *children: Node, handler: Handler | None = None,
       negatable: bool = False, hidden: bool = False) -> Node:
    return Node(word, help, None, handler, list(children), negatable, hidden)


def arg(a: Arg, help: str = "", *children: Node, handler: Handler | None = None,
        negatable: bool = False) -> Node:
    return Node(None, help, a, handler, list(children), negatable)


@dataclass
class ParseResult:
    status: str                # ok | invalid | ambiguous | incomplete | empty
    pos: int = 0               # character offset of the offending token
    handler: Handler | None = None
    args: list = field(default_factory=list)
    words: list[str] = field(default_factory=list)  # keywords taken (full spelling)
    node: Node | None = None
    token: str = ""


def _keyword_matches(children: list[Node], text: str) -> list[Node]:
    low = text.lower()
    exact = [c for c in children if c.word and c.word.lower() == low]
    if exact:
        return exact
    return [c for c in children if c.word and c.word.lower().startswith(low)]


def parse(roots: list[Node], tokens: list[Token], session, line_len: int = 0) -> ParseResult:
    if not tokens:
        return ParseResult("empty")
    children = roots
    node: Node | None = None
    values: list = []
    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        kws = _keyword_matches(children, tok.text)
        if len(kws) == 1:
            node = kws[0]
            words.append(node.word)
            i += 1
        elif len(kws) > 1:
            return ParseResult("ambiguous", tok.pos, token=tok.text)
        else:
            for c in children:
                if c.arg is None:
                    continue
                r = c.arg.match(tokens, i, session)
                if r is not None:
                    node = c
                    values.append(r[0])
                    i += r[1]
                    break
            else:
                return ParseResult("invalid", tok.pos, token=tok.text)
        children = node.children
    if node is None or node.handler is None:
        return ParseResult("incomplete", line_len)
    return ParseResult("ok", handler=node.handler, args=values, words=words, node=node)


def help_for(roots: list[Node], line: str, session) -> tuple[str, list[tuple[str, str]]]:
    """Context help for *line* (text before '?').

    Returns (status, [(label, help)]) where status is 'ok' or 'invalid'.
    """
    tokens = tokenize(line)
    partial = ""
    if line and not line[-1].isspace() and tokens:
        partial = tokens.pop().text
    children = roots
    node = None
    i = 0
    while i < len(tokens):
        kws = _keyword_matches(children, tokens[i].text)
        if len(kws) == 1:
            node = kws[0]
            i += 1
        elif len(kws) > 1:
            return "ambiguous", []
        else:
            for c in children:
                if c.arg is not None:
                    r = c.arg.match(tokens, i, session)
                    if r is not None:
                        node = c
                        i += r[1]
                        break
            else:
                return "invalid", []
        children = node.children
        if node.arg is not None and node.arg.rest:
            return "ok", [("LINE", node.help)] + ([("<cr>", "")] if node.handler else [])
    if partial:
        items = [(c.word, c.help) for c in children
                 if c.word and not c.hidden and c.word.lower().startswith(partial.lower())]
        return ("ok" if items else "invalid"), items
    items = [(c.label, c.help) for c in children if not c.hidden]
    if node is not None and node.handler is not None:
        items.append(("<cr>", ""))
    return "ok", items


def complete(roots: list[Node], line: str, session) -> str | None:
    """Tab completion: returns the completed line or None."""
    if not line or line[-1].isspace():
        return None
    tokens = tokenize(line)
    partial = tokens.pop()
    children = roots
    i = 0
    while i < len(tokens):
        kws = _keyword_matches(children, tokens[i].text)
        if len(kws) == 1:
            node = kws[0]
            i += 1
        else:
            for c in children:
                if c.arg is not None:
                    r = c.arg.match(tokens, i, session)
                    if r is not None:
                        node = c
                        i += r[1]
                        break
            else:
                return None
        children = node.children
    kws = _keyword_matches(children, partial.text)
    if len(kws) != 1:
        return None
    return line[:partial.pos] + kws[0].word + " "


def format_help(items: list[tuple[str, str]], indent: int = 2) -> str:
    if not items:
        return ""
    width = max(len(l) for l, _ in items)
    return "\n".join(f"{' ' * indent}{l:<{width}}  {h}".rstrip() for l, h in items) + "\n"
