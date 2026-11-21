"""Packet analyzer (Wireshark-style): capture on one cable or all, filter, inspect, save as .pcap."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ..i18n import tr

MAX_PACKETS = 5000


def frame_addresses(frame):
    p = frame.payload
    src, dst = getattr(p, "src", None), getattr(p, "dst", None)
    if src is None and hasattr(p, "sender_ip"):
        src, dst = p.sender_ip, p.target_ip
    if src is None and hasattr(p, "inner"):
        src, dst = getattr(p.inner, "src", None), getattr(p.inner, "dst", None)
    return str(src or frame.src.cisco()), str(dst or frame.dst.cisco())


def matches(text: str, frame) -> bool:
    """Display filter: a protocol name (icmp, arp, ospf ...), ip.addr==A, ip.src==A, ip.dst==A, vlan==N."""
    t = text.strip().lower()
    if not t:
        return True
    src, dst = frame_addresses(frame)
    for cond in (c.strip() for c in t.replace("&&", " and ").split(" and ")):
        if "==" in cond:
            k, v = (x.strip() for x in cond.split("==", 1))
            if k == "ip.addr" and v not in (src, dst):
                return False
            if k == "ip.src" and v != src:
                return False
            if k == "ip.dst" and v != dst:
                return False
            if k == "vlan" and str(frame.vlan) != v:
                return False
        elif cond and cond not in frame.protocol.lower():
            return False
    return True


class CapturePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.packets: list = []                 # (time ms, frame, from port, to port)
        self.capturing = False
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.where = QComboBox()
        self.start_btn = QPushButton(tr("Начать захват"))
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self.toggle)
        clear = QPushButton(tr("Очистить"))
        clear.clicked.connect(self.clear)
        save = QPushButton(tr("Сохранить .pcap…"))
        save.clicked.connect(lambda: self.save())
        row.addWidget(QLabel(tr("Где:")))
        row.addWidget(self.where, 1)
        for w in (self.start_btn, clear, save):
            row.addWidget(w)
        lay.addLayout(row)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("Фильтр: icmp, arp, ospf, ip.addr==192.168.1.10, vlan==10"))
        self.filter.textChanged.connect(self.refilter)
        lay.addWidget(self.filter)
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["No.", tr("Время, с"), tr("Источник"), tr("Получатель"), tr("Протокол"),
                                              tr("Информация")])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.currentCellChanged.connect(lambda r, *_: self.show_details(r))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        split.addWidget(self.table)
        split.addWidget(self.details)
        lay.addWidget(split, 1)
        self.status = QLabel()
        lay.addWidget(self.status)
        self.window.ctl.engine.subscribe("frame_delivered", self._on_frame)
        self.refresh_links()

    def shutdown(self) -> None:
        self.window.ctl.engine.unsubscribe("frame_delivered", self._on_frame)

    def refresh_links(self) -> None:
        cur = self.where.currentData()
        self.where.blockSignals(True)
        self.where.clear()
        self.where.addItem(tr("Все кабели"), None)
        for link in self.window.topology.links:
            self.where.addItem(f"{link.a.device.name} {link.a.short} ↔ {link.b.device.name} {link.b.short}", id(link))
        i = self.where.findData(cur)
        self.where.setCurrentIndex(max(0, i))
        self.where.blockSignals(False)

    def toggle(self, on: bool) -> None:
        self.capturing = on
        self.start_btn.setText(tr("Остановить") if on else tr("Начать захват"))
        if on:
            self.refresh_links()
        self._status()

    def _on_frame(self, ev) -> None:
        if not self.capturing or len(self.packets) >= MAX_PACKETS:
            return
        info = ev.info
        a, b = info["src"], info["dst"]
        want = self.where.currentData()
        if want is not None and not any(id(l) == want and {l.a, l.b} == {a, b} for l in self.window.topology.links):
            return
        self.packets.append((ev.time, info["frame"], a, b))
        if matches(self.filter.text(), info["frame"]):
            self._add_row(len(self.packets) - 1)
        self._status()

    def _add_row(self, i: int) -> None:
        t, frame, a, b = self.packets[i]
        src, dst = frame_addresses(frame)
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, text in enumerate((str(i + 1), f"{t / 1000:.3f}", src, dst, frame.protocol, frame.summary())):
            it = QTableWidgetItem(text)
            it.setData(Qt.UserRole, i)
            self.table.setItem(r, c, it)

    def refilter(self) -> None:
        self.table.setRowCount(0)
        for i, (_, frame, _a, _b) in enumerate(self.packets):
            if matches(self.filter.text(), frame):
                self._add_row(i)
        self._status()

    def show_details(self, row: int) -> None:
        it = self.table.item(row, 0)
        if it is None:
            return
        t, frame, a, b = self.packets[it.data(Qt.UserRole)]
        lines = [f"{a.device.name} {a.name} → {b.device.name} {b.name}, t = {t / 1000:.3f} s", ""]
        for title, body in frame.layers():
            lines.append(title)
            lines += [f"    {x}" for x in body]
        from ..core.pcap import frame_bytes
        data = frame_bytes(frame)
        lines += ["", tr("Байты ({n}):", n=len(data))]
        for off in range(0, min(len(data), 256), 16):
            chunk = data[off:off + 16]
            lines.append(f"{off:04x}  {' '.join(f'{x:02x}' for x in chunk):<48}  "
                         + "".join(chr(x) if 32 <= x < 127 else "." for x in chunk))
        self.details.setPlainText("\n".join(lines))

    def clear(self) -> None:
        self.packets.clear()
        self.table.setRowCount(0)
        self.details.clear()
        self._status()

    def save(self, path: str | None = None) -> int:
        from ..core.pcap import write_pcap
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить захват"), "capture.pcap", "pcap (*.pcap)")
            if not path:
                return 0
        shown = [self.packets[self.table.item(r, 0).data(Qt.UserRole)] for r in range(self.table.rowCount())]
        return write_pcap(path, [(t, f) for t, f, _a, _b in shown])

    def _status(self) -> None:
        self.status.setText(tr("Захвачено: {n}, показано: {m}", n=len(self.packets), m=self.table.rowCount()) +
                            (" · " + tr("идёт захват") if self.capturing else ""))
