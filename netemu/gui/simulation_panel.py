"""Simulation mode panel: event list, playback controls, protocol filter."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSlider, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from .controller import SimController, SimRecord
from .items import PROTO_COLORS

PROTOCOLS = ("ARP", "STP", "ICMP", "ICMPv6", "NDP", "SSH", "TELNET", "LACP", "PAgP", "HSRP", "VRRP", "OSPFv3", "RIPng", "DHCPv6", "RIP", "OSPF", "BGP", "DHCP", "DNS", "HTTP", "TCP")


class PduDialog(QDialog):
    """Shows a frame's headers layer by layer (like PT's 'PDU Information')."""

    def __init__(self, frame, where: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Информация о PDU"))
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        view = QTextBrowser()
        html = [f"<h3>{frame.summary()}</h3>"]
        if where:
            html.append(f"<p><i>{where}</i></p>")
        for title, lines in frame.layers():
            html.append(f"<p><b>{title}</b><br>" + "<br>".join(f"&nbsp;&nbsp;{l}" for l in lines) + "</p>")
        view.setHtml("".join(html))
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.text = view.toPlainText()


class SimulationPanel(QWidget):
    filterChanged = Signal(object)  # set of visible protocols

    def __init__(self, ctl: SimController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", tr("Время, мс"), tr("Откуда"), tr("Куда"), tr("Тип")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_row)
        lay.addWidget(QLabel(tr("Список событий (двойной щелчок — содержимое PDU):")))
        lay.addWidget(self.table, 1)

        ctl_row = QHBoxLayout()
        self.play_btn = QPushButton(tr("▶ Воспроизвести"))
        self.play_btn.setCheckable(True)
        self.play_btn.toggled.connect(ctl.set_playing)
        self.step_btn = QPushButton(tr("⏭ Шаг"))
        self.step_btn.clicked.connect(self.step)
        self.reset_btn = QPushButton(tr("⟲ Сброс"))
        self.reset_btn.clicked.connect(ctl.reset_simulation)
        for b in (self.play_btn, self.step_btn, self.reset_btn):
            ctl_row.addWidget(b)
        lay.addLayout(ctl_row)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel(tr("Скорость:")))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 40)
        self.speed.setValue(8)
        self.speed.valueChanged.connect(self._speed_changed)
        speed_row.addWidget(self.speed)
        lay.addLayout(speed_row)
        self._speed_changed(self.speed.value())

        lay.addWidget(QLabel(tr("Фильтр:")))
        flt = QGridLayout()                      # a grid, so the panel stays narrow
        flt.setHorizontalSpacing(10)
        flt.setVerticalSpacing(2)
        self.filters: dict[str, QCheckBox] = {}
        for i, proto in enumerate(PROTOCOLS):
            cb = QCheckBox(proto)
            cb.setChecked(True)
            cb.toggled.connect(self._filter_changed)
            self.filters[proto] = cb
            flt.addWidget(cb, i // 6, i % 6)
        flt.setColumnStretch(6, 1)
        lay.addLayout(flt)

        self.status = QLabel()
        lay.addWidget(self.status)

        ctl.recordAdded.connect(self._add_record)
        ctl.recordsCleared.connect(lambda: self.table.setRowCount(0))
        ctl.playingChanged.connect(self._playing_changed)
        ctl.tick.connect(self._update_status)

    def visible_protocols(self) -> set[str]:
        return {p for p, cb in self.filters.items() if cb.isChecked()}

    def _filter_changed(self) -> None:
        vis = self.visible_protocols()
        for r in range(self.table.rowCount()):
            proto = self.table.item(r, 4).text()
            self.table.setRowHidden(r, proto not in vis)
        self.filterChanged.emit(vis)

    def _speed_changed(self, v: int) -> None:
        # 1 ms of link delay takes from ~1.25 s (slow) to ~30 ms (fast)
        self.ctl.speed = v * 0.0008

    def _playing_changed(self, playing: bool) -> None:
        self.play_btn.blockSignals(True)
        self.play_btn.setChecked(playing)
        self.play_btn.blockSignals(False)
        self.play_btn.setText(tr("⏸ Пауза") if playing else tr("▶ Воспроизвести"))

    def step(self) -> None:
        self.ctl.set_playing(False)
        self.ctl.step()

    def _add_record(self, rec: SimRecord) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        vals = [str(rec.index + 1), f"{rec.time:.3f}", rec.last_device, f"{rec.at_device} ({rec.at_port})",
                rec.protocol]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            it.setToolTip(rec.summary)
            self.table.setItem(r, c, it)
        color = PROTO_COLORS.get(rec.protocol)
        if color is not None:
            self.table.item(r, 4).setBackground(color.lighter(130))
        self.table.setRowHidden(r, rec.protocol not in self.visible_protocols())
        self.table.scrollToBottom()

    def _open_row(self, row: int, col: int) -> None:
        rec = self.ctl.records[row]
        dlg = PduDialog(rec.frame, tr("Время {t} мс: {a} → {b}", t=f"{rec.time:.3f}", a=rec.last_device, b=
                                   f"{rec.at_device} ({rec.at_port})"), self)
        dlg.show()

    def _update_status(self) -> None:
        e = self.ctl.engine
        n = len(e.frames_in_flight())
        self.status.setText(tr("Время: {t} с · пакетов в пути: {n}", t=f"{e.now / 1000:.3f}", n=n))
