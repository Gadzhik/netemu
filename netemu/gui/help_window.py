"""Built-in help: renders docs/<lang>/*.md (the same files as the docs folder)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QListWidgetItem, QSplitter, QTextBrowser

from ..i18n import language, tr

TOPICS = ("guide", "cisco", "mikrotik", "juniper", "huawei", "extreme", "pc", "ddos", "malware", "l2")


def docs_dir() -> Path:
    """Folder with the documentation: next to the executable when frozen, else the project root."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[2]
    return base / "docs"


def topic_for_device(dev) -> str:
    if dev.category in ("pc", "server"):
        return "pc"
    if dev.category == "attack":
        return "ddos"
    if dev.category in ("router", "switch") and dev.vendor in TOPICS:
        return dev.vendor
    return "guide"


class HelpWindow(QDialog):
    def __init__(self, parent=None, lang: str | None = None):
        super().__init__(parent)
        self.lang = lang or language()
        self.setWindowTitle(tr("Справка NetEmu"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(1100, 760)
        self.folder = docs_dir() / self.lang
        if not self.folder.is_dir():
            self.folder = docs_dir() / "ru"
        self.topics = QListWidget()
        self.topics.setMaximumWidth(220)
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        f = self.view.font()
        f.setPointSizeF(f.pointSizeF() + 1)
        self.view.setFont(f)
        self.view.document().setDefaultStyleSheet(
            "code, pre { font-family: Consolas, 'Courier New', monospace; } "
            "table { border-collapse: collapse; } td, th { padding: 2px 8px; border: 1px solid #999999; }")
        split = QSplitter()
        split.addWidget(self.topics)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(split)
        for key in TOPICS:
            it = QListWidgetItem(self._title(key))
            it.setData(Qt.UserRole, key)
            self.topics.addItem(it)
        bold = QFont(self.topics.font())
        bold.setBold(True)
        self.topics.item(0).setFont(bold)
        self.topics.currentItemChanged.connect(
            lambda cur, _prev: cur is not None and self._load(cur.data(Qt.UserRole)))
        self.current = None
        self.show_topic("guide")

    def _path(self, key: str) -> Path:
        return self.folder / f"{key}.md"

    def _title(self, key: str) -> str:
        try:
            first = self._path(key).read_text(encoding="utf-8").splitlines()[0]
            return first.lstrip("# ").strip()
        except (OSError, IndexError):
            return key

    def _load(self, key: str) -> None:
        path = self._path(key)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = "# NetEmu\n\n" + tr("Документация не найдена: {path}", path=path)
        self.current = key
        self.view.setMarkdown(text)
        self.view.verticalScrollBar().setValue(0)

    def show_topic(self, key: str | None) -> None:
        key = key if key in TOPICS else (self.current or "guide")
        row = TOPICS.index(key)
        if self.topics.currentRow() != row:
            self.topics.setCurrentRow(row)      # loads through currentItemChanged
        else:
            self._load(key)

    def text(self) -> str:
        return self.view.toPlainText()

    def _link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        name = Path(url.path()).stem
        if name in TOPICS:
            self.show_topic(name)
