"""Command reference generated from the CLI dialects themselves.

The documentation in ``docs/<lang>/<vendor>.md`` embeds the output of
``markdown_reference`` between marker comments; ``tools/gen_docs.py`` refreshes
it and a test fails when it is out of date, so the docs always list exactly the
commands the simulator understands.
"""
from __future__ import annotations

import re

from . import parser as P

END = "<!-- END GENERATED COMMANDS -->"
_BLOCK = re.compile(r"(<!-- BEGIN GENERATED COMMANDS (\w+) -->\n).*?(" + re.escape(END) + ")", re.S)
DOC_VENDORS = {"cisco.md": "cisco", "mikrotik.md": "mikrotik", "juniper.md": "juniper",
               "huawei.md": "huawei", "extreme.md": "extreme", "pc.md": "host"}

# mode key -> (prompt example, {lang: title})
MODE_TITLES = {
    "cisco": {
        "user": ("{h}>", {"ru": "Пользовательский режим", "en": "User EXEC mode"}),
        "priv": ("{h}#", {"ru": "Привилегированный режим (enable)", "en": "Privileged EXEC mode (enable)"}),
        "config": ("{h}(config)#", {"ru": "Глобальная конфигурация (configure terminal)",
                                       "en": "Global configuration (configure terminal)"}),
        "config-if": ("{h}(config-if)#", {"ru": "Конфигурация интерфейса (interface …)",
                                             "en": "Interface configuration (interface …)"}),
    },
    "huawei": {
        "user": ("<{h}>", {"ru": "Пользовательский вид", "en": "User view"}),
        "system": ("[{h}]", {"ru": "Системный вид (system-view)", "en": "System view (system-view)"}),
        "interface": ("[{h}-GigabitEthernet0/0/1]", {"ru": "Вид интерфейса (interface …)",
                                                        "en": "Interface view (interface …)"}),
    },
    "juniper": {
        "oper": ("root@{h}>", {"ru": "Операционный режим", "en": "Operational mode"}),
        "config": ("root@{h}#", {"ru": "Режим конфигурации (configure)", "en": "Configuration mode (configure)"}),
    },
    "extreme": {
        "user": ("{h}.1 #", {"ru": "Все команды (у EXOS нет режимов конфигурации)",
                             "en": "All commands (EXOS has no configuration modes)"}),
    },
}

NEGATE = {"cisco": "no", "huawei": "undo"}

# MikroTik arguments are key=value pairs parsed inside the handlers; documented here.
MIKROTIK_ARGS = {
    ("interface", "enable"): "<name>",
    ("interface", "disable"): "<name>",
    ("interface", "set"): "<name> [comment=<text>] [disabled=yes/no]",
    ("interface", "comment"): "<name> comment=<text>",
    ("interface", "ethernet", "enable"): "<name>",
    ("interface", "ethernet", "disable"): "<name>",
    ("interface", "ethernet", "set"): "<name> [comment=<text>] [disabled=yes/no]",
    ("system", "identity", "set"): "name=<name>",
    ("ip", "address", "add"): "address=<A.B.C.D/len> interface=<name> [comment=<text>]",
    ("ip", "address", "remove"): "<number>",
    ("ip", "route", "add"): "[dst-address=<A.B.C.D/len>] gateway=<A.B.C.D> [distance=<n>] [comment=<text>]",
    ("ip", "route", "remove"): "<number>",
    ("interface", "vlan", "add"): "name=<name> vlan-id=<1-4094> interface=<name>",
    ("interface", "vlan", "remove"): "<number>",
    ("interface", "bridge", "set"): "bridge vlan-filtering=yes/no",
    ("interface", "bridge", "port", "set"): "<name>/[find interface=<name>] pvid=<1-4094>",
    ("interface", "bridge", "vlan", "add"): "bridge=bridge vlan-ids=<list> [tagged=<ports>] [untagged=<ports>]",
    ("interface", "bridge", "vlan", "remove"): "<number>",
    ("ping",): "<A.B.C.D> [count=<n>] [size=<bytes>] [ttl=<n>]",
    ("traceroute",): "<A.B.C.D> [count=<n>] [max-hops=<n>]",
    ("tool", "traceroute"): "<A.B.C.D> [count=<n>] [max-hops=<n>]",
}

_HEAD = {"ru": ("Команда", "Описание"), "en": ("Command", "Description")}


def _walk(node: P.Node, prefix: list[str], help_text: str, out: list[tuple[str, str]]) -> None:
    if node.hidden:
        return
    words = prefix + [node.label]
    text = node.help or help_text
    if node.handler is not None:
        out.append((" ".join(words), text))
    for ch in node.children:
        _walk(ch, words, text, out)


def tree_commands(session, mode: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    neg = session.negate_word if mode in getattr(session, "negate_modes", ()) else None
    for root in session.roots(mode):
        start = len(out)
        _walk(root, [], root.help, out)
        if neg and root.negatable:
            out[start:] = [(f"[{neg}] {s}", h) for s, h in out[start:]]
    seen, uniq = set(), []
    for s, h in out:
        if s not in seen:
            seen.add(s)
            uniq.append((s, h))
    return uniq


def mikrotik_commands(session) -> list[tuple[str, str, str]]:
    """(menu path, command, help) for every command of every menu."""
    out = []
    for path in sorted(session._menus):
        for cmd, (help_text, _fn) in sorted(session._menus[path].items()):
            args = MIKROTIK_ARGS.get(path + (cmd,), "")
            menu = "/" + " ".join(path) if path else "/"
            line = (f"/{' '.join(path)} {cmd}" if path else f"/{cmd}") + (f" {args}" if args else "")
            out.append((menu, line, help_text))
    return out


def _table(rows, lang) -> list[str]:
    a, b = _HEAD[lang]
    lines = [f"| {a} | {b} |", "|---|---|"]
    for s, h in rows:
        # a "|" would split the Markdown table cell: alternatives are shown as "a/b"
        lines.append(f"| `{s.replace('|', '/')}` | {h.replace('|', '/')} |")
    return lines


def markdown_reference(vendor: str, category: str, lang: str) -> str:
    """Markdown command list for a vendor's router or switch (or the PC prompt)."""
    from ..devices import MODELS  # local import: avoid cycles
    from ..topology import Topology

    if vendor == "host":
        if category == "linux":
            from .linux import HELP as LINUX_HELP
            return "```\n" + LINUX_HELP.strip("\n") + "\n```"
        from .host import HELP
        return "```\n" + HELP.strip("\n") + "\n```"
    key = next(m.key for m in MODELS.values() if m.vendor == vendor and m.category == category)
    dev = Topology().add_device(key)
    session = dev.create_cli()
    lines: list[str] = []
    if vendor == "mikrotik":
        current = None
        rows: list[tuple[str, str]] = []
        for menu, line, h in mikrotik_commands(session):
            if menu != current:
                if rows:
                    lines += _table(rows, lang) + [""]
                    rows = []
                lines.append(f"**`{menu}`**")
                lines.append("")
                current = menu
            rows.append((line, h))
        if rows:
            lines += _table(rows, lang)
        return "\n".join(lines)
    for mode, (prompt, titles) in MODE_TITLES[vendor].items():
        rows = tree_commands(session, mode)
        if not rows:
            continue
        lines.append(f"#### {titles[lang]} — `{prompt.format(h=dev.hostname)}`")
        lines.append("")
        lines += _table(rows, lang)
        lines.append("")
    return "\n".join(lines).rstrip()


def refresh_doc(text: str, vendor: str, lang: str) -> str:
    """Replace every generated block of a doc page with the current command list."""
    def block(m):
        category = m.group(2)
        body = markdown_reference(vendor, "pc" if category == "host" else category, lang)
        return f"{m.group(1)}{body}\n{m.group(3)}"
    return _BLOCK.sub(block, text)
