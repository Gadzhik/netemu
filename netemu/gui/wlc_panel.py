"""Wireless LAN controller tab: WLANs pushed to every joined lightweight AP, and the AP list."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..i18n import tr


class WlcPanel(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        box = QGroupBox(tr("Беспроводные сети (WLAN)"))
        v = QVBoxLayout(box)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["SSID", tr("Защита"), tr("Пароль"), tr("Включена")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        v.addWidget(self.table)
        row = QHBoxLayout()
        self.ssid = QLineEdit()
        self.ssid.setPlaceholderText("SSID")
        self.sec = QComboBox()
        self.sec.addItem("WPA2-Personal", "wpa2")
        self.sec.addItem(tr("Открытая (без пароля)"), "open")
        self.key = QLineEdit()
        self.key.setPlaceholderText(tr("пароль WPA2"))
        self.on = QCheckBox(tr("Включена"))
        self.on.setChecked(True)
        add, rem = QPushButton(tr("Добавить")), QPushButton(tr("Удалить выбранный"))
        add.clicked.connect(self.add)
        rem.clicked.connect(self.remove)
        for w in (self.ssid, self.sec, self.key, self.on, add, rem):
            row.addWidget(w)
        v.addLayout(row)
        lay.addWidget(box)
        lay.addWidget(QLabel(tr("Подключённые точки доступа:")))
        self.aps = QTableWidget(0, 2)
        self.aps.setHorizontalHeaderLabels([tr("Имя"), "MAC"])
        self.aps.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.aps.verticalHeader().setVisible(False)
        lay.addWidget(self.aps, 1)
        hint = QLabel(tr("Лёгкие точки доступа (Lightweight AP) в том же сегменте сети находят контроллер сами и "
                         "получают от него эти WLAN. Изменение здесь сразу применяется на всех точках."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        eng, fn = self.dev.engine, self._changed
        eng.subscribe("device_changed", fn)
        self.destroyed.connect(lambda *_: eng.unsubscribe("device_changed", fn))
        self.load()

    def _changed(self, dev, *rest) -> None:
        if dev is self.dev:
            try:
                self.load()
            except RuntimeError:
                pass

    def load(self) -> None:
        w = self.dev.wlans
        self.table.setRowCount(len(w))
        for r, x in enumerate(w):
            for c, text in enumerate((x["ssid"], "WPA2" if x.get("security") == "wpa2" else tr("открытая"),
                                      "•" * len(x.get("key") or ""), tr("да") if x.get("enabled", True) else tr("нет"))):
                self.table.setItem(r, c, QTableWidgetItem(text))
        aps = sorted(self.dev.aps.items())
        self.aps.setRowCount(len(aps))
        for r, (name, a) in enumerate(aps):
            self.aps.setItem(r, 0, QTableWidgetItem(name))
            self.aps.setItem(r, 1, QTableWidgetItem(a["mac"].cisco()))

    def add(self) -> None:
        ssid, sec, key = self.ssid.text().strip(), self.sec.currentData(), self.key.text()
        if not ssid:
            self.dialog.error(tr("Укажите SSID."))
            return
        if sec == "wpa2" and len(key) < 8:
            self.dialog.error(tr("Пароль WPA2 должен быть не короче 8 символов."))
            return
        self.dev.wlans = [x for x in self.dev.wlans if x["ssid"] != ssid] + [
            {"ssid": ssid, "security": sec, "key": key if sec == "wpa2" else None, "enabled": self.on.isChecked()}]
        self.dev.push()
        self.load()

    def remove(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.wlans):
            del self.dev.wlans[r]
            self.dev.push()
            self.load()
