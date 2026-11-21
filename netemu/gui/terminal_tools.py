"""Terminal helpers: syntax highlighting, the live parameter hint, and the command search window."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import QDialog, QHeaderView, QLineEdit, QTableWidget, QTableWidgetItem, QVBoxLayout

from ..i18n import tr


def _fmt(color: str, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


RULES = [
    (re.compile(r"^\S{1,40}?(?:\([\w\-./]+\))?[#>$]\s?(?=\S|$)|^\[[^\]]+\][#>]?|^<[^>]+>|^\S+@\S+[:>#]\s?"), _fmt("#7cb7ff", True)),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"), _fmt("#4dd0e1")),
    (re.compile(r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}(?:/\d{1,3})?\b"), _fmt("#4dd0e1")),
    (re.compile(r"\b(?:up|Up|UP|Established|FULL|Full|connected|reachable|Reply from|bytes from)\b"), _fmt("#81c784")),
    (re.compile(r"\b(?:down|Down|DOWN|administratively down|err-disabled|unreachable|timed out|Request timed out|"
                r"Destination host unreachable)\b"), _fmt("#e57373")),
    (re.compile(r"^%.*|^Error:.*|^error:.*|.*syntax error.*|.*command not found.*|^\s*\^$"), _fmt("#ffb74d")),
    (re.compile(r"!{3,}"), _fmt("#81c784", True)),
    (re.compile(r"(?<![!\w])\.{3,}(?!\w)"), _fmt("#e57373", True)),
]


def segments(line: str) -> list[tuple[str, QTextCharFormat | None]]:
    """Split one output line into (text, format) pieces; later rules win where they overlap.
    The terminal inserts the pieces with these formats as it prints (no QSyntaxHighlighter: re-highlighting
    during font/scale changes crashed Qt on Windows)."""
    owner: list[int] = [-1] * len(line)
    for i, (rx, _fmt) in enumerate(RULES):
        for m in rx.finditer(line):
            for k in range(m.start(), m.end()):
                owner[k] = i
    out: list[tuple[str, QTextCharFormat | None]] = []
    start = 0
    for k in range(1, len(line) + 1):
        if k == len(line) or owner[k] != owner[start]:
            out.append((line[start:k], RULES[owner[start]][1] if owner[start] >= 0 else None))
            start = k
    return out


def hint_for(session, line: str) -> str:
    """One line: what may follow what has been typed so far (the '?' help, compacted)."""
    if session is None or not hasattr(session, "help"):
        return ""
    try:
        text = session.help(line if line.endswith(" ") or not line else line + "?"[0:0])
    except Exception:                                      # a dialect without tree help
        return ""
    items = [l.split()[0] for l in text.splitlines() if l.strip() and not l.lstrip().startswith(("%", "^"))]
    if not items:
        return ""
    shown = ", ".join(items[:12])
    return shown + (", …" if len(items) > 12 else "")


def all_commands(session) -> list[tuple[str, str]]:
    """[(command, help)] for every mode of the session (or the shell help text)."""
    from ..core.cli import reference as R
    out = []
    if hasattr(session, "_menus"):
        return [(line, h) for _m, line, h in R.mikrotik_commands(session)]
    trees = getattr(session, "_trees", None)
    if trees:
        for mode in trees:
            for cmd, h in R.tree_commands(session, mode):
                out.append((cmd, f"[{mode}] {h}"))
        return out
    text = session.help("") if hasattr(session, "help") else ""
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if parts:
            out.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return out


class CommandSearch(QDialog):
    """Ctrl+F in a terminal: find a command by words in it or in its description; double-click inserts it."""

    def __init__(self, terminal):
        super().__init__(terminal)
        self.terminal = terminal
        self.setWindowTitle(tr("Поиск команды"))
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText(tr("например: ospf neighbor, vlan, nat, route"))
        self.query.textChanged.connect(self.filter)
        lay.addWidget(self.query)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Команда"), tr("Описание")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.cellDoubleClicked.connect(self.insert)
        lay.addWidget(self.table, 1)
        self.commands = all_commands(terminal.session)
        self.filter("")

    def filter(self, text: str) -> None:
        words = text.lower().split()
        rows = [(c, h) for c, h in self.commands if all(w in (c + " " + h).lower() for w in words)][:400]
        self.table.setRowCount(len(rows))
        for i, (c, h) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(c))
            self.table.setItem(i, 1, QTableWidgetItem(h))

    def insert(self, row: int, _col: int = 0) -> None:
        cmd = self.table.item(row, 0).text()
        cmd = re.sub(r"^\[\w+\]\s*", "", cmd)                 # drop the optional negate word
        cmd = cmd.split(" <")[0].split(" WORD")[0].split(" A.B.C.D")[0]
        self.terminal._set_input(cmd + " ")
        self.terminal.setFocus()
        self.accept()


__all__ = ["segments", "CommandSearch", "hint_for", "all_commands", "Qt"]
