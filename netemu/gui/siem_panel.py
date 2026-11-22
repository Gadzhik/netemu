"""SIEM panel: Tools → Monitoring (SIEM). One pane of glass over the network's security alerts and events."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import siem
from ..i18n import tr

SEV_COLORS = {1: "#c62828", 2: "#ef6c00", 3: "#f9a825", 4: "#2e7d32"}
CAT_NAMES = {"ddos": "DDoS", "scan": "Сканирование", "worm": "Червь/вредонос", "c2": "Командный сервер",
             "arp-spoof": "Подмена ARP", "mac-flood": "Переполнение MAC", "stp": "Атака STP",
             "brute": "Подбор пароля", "ids": "IPS/IDS", "syslog": "Syslog"}
KIND_COLORS = {"ddos": "#c62828", "malware": "#6a1b9a", "l2": "#00695c", "syslog": "#546e7a"}


class SiemPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        self.alerts = QTableWidget(0, 4)
        self.alerts.setHorizontalHeaderLabels([tr("Важность"), tr("Категория"), tr("Источник"), tr("Что обнаружено")])
        self.alerts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.alerts.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.alerts.verticalHeader().setVisible(False)
        self.alerts.setEditTriggers(QTableWidget.NoEditTriggers)
        self.alerts.setSelectionBehavior(QTableWidget.SelectRows)
        self.alerts.itemSelectionChanged.connect(self._show_advice)
        row.addWidget(self.alerts, 3)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        row.addWidget(self.log, 2)
        lay.addLayout(row, 1)
        self.advice = QLabel()
        self.advice.setWordWrap(True)
        lay.addWidget(self.advice)
        self._alerts: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)

    def shutdown(self):
        self._timer.stop()

    def _show_advice(self):
        r = self.alerts.currentRow()
        if 0 <= r < len(self._alerts):
            self.advice.setText("💡 " + self._alerts[r].advice)

    def refresh(self):
        try:
            topo = self.window.topology
            self._alerts = siem.alerts(topo)
            self.alerts.setRowCount(len(self._alerts))
            for i, al in enumerate(self._alerts):
                cells = (siem.sev_name(al.severity), tr(CAT_NAMES.get(al.category, al.category)), al.source, al.text)
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(str(text))
                    if c == 0:
                        it.setForeground(QColor(SEV_COLORS.get(al.severity, "#2e7d32")))
                    self.alerts.setItem(i, c, it)
            counts = siem.alert_counts(topo)
            if any(counts.values()):
                self.summary.setText(tr("Тревоги: {c} критических, {h} высоких, {m} средних, {l} низких. "
                                        "Выберите строку — внизу появится рекомендация.",
                                        c=counts[1], h=counts[2], m=counts[3], l=counts[4]))
            else:
                self.summary.setText(tr("Угроз не обнаружено. SIEM собирает события со всех устройств и мониторов "
                                        "безопасности."))
            evs = siem.events(topo)[-200:]
            lines = []
            for e in evs:
                t = f"{e.ms / 1000:7.1f}s " if e.ms else "    syslog "
                lines.append(f"{t}{e.source}: {e.text}")
            text = "\n".join(lines)
            if self.log.toPlainText() != text:
                self.log.setPlainText(text)
                self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
            if not self._alerts:
                self.advice.setText("")
        except RuntimeError:
            self._timer.stop()
