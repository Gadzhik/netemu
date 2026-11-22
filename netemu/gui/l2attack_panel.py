"""Layer-2 attack training in the GUI: the attacker's attack list and the LAN attack monitor."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.security import l2attack as l2
from ..i18n import tr

HELP = {
    "arp-spoof": "Подмена ARP: атакующий выдаёт себя за шлюз, трафик жертв идёт через него (MITM). Защита — Dynamic ARP Inspection (DAI).",
    "mac-flood": "Переполнение MAC-таблицы: коммутатор начинает рассылать кадры во все порты, атакующий видит чужой трафик. Защита — port security.",
    "stp-root": "Захват корня STP: лучшие BPDU делают атакующего корнем дерева, трафик идёт через него. Защита — BPDU Guard / Root Guard.",
}


class L2AttackPanel(QWidget):
    """A LAN attacker: a list of L2 attacks (kind, target, gateway) with Start / Stop."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Атака"), tr("Цель"), tr("Шлюз/VLAN"), tr("Состояние")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._load_form)
        lay.addWidget(self.table, 1)

        form = QHBoxLayout()
        self.kind = QComboBox()
        for k in l2.KINDS:
            self.kind.addItem(l2.name(k), k)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.target = QLineEdit()
        self.target.setPlaceholderText(tr("жертва (пусто = вся подсеть)"))
        self.gateway = QLineEdit()
        self.gateway.setPlaceholderText(tr("шлюз (пусто = свой)"))
        form.addWidget(self.kind, 2)
        form.addWidget(self.target, 2)
        form.addWidget(self.gateway, 2)
        lay.addLayout(form)

        btns = QHBoxLayout()
        for text, fn in ((tr("Добавить"), self.add), (tr("Сохранить"), self.save), (tr("Удалить"), self.remove),
                         (tr("Запустить"), lambda: self._run(True)), (tr("Остановить"), lambda: self._run(False)),
                         (tr("Остановить все"), self.stop_all)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, f=fn: f())
            btns.addWidget(b)
        lay.addLayout(btns)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        note = QLabel(tr("Атаки идут настоящими кадрами (ARP, DHCP, BPDU), поэтому на них действуют VLAN, port security, "
                         "DHCP snooping, DAI и BPDU Guard на коммутаторе. Перехват и защиту видно в «Инструменты → Атаки "
                         "в сети»."))
        note.setWordWrap(True)
        lay.addWidget(note)
        self._kind_changed()
        self.load()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.load)
        self._timer.start(1000)

    def _kind_changed(self, *_):
        k = self.kind.currentData()
        self.gateway.setEnabled(k == "arp-spoof")
        self.hint.setText(tr(HELP.get(k, "")))

    def _from_form(self):
        return l2.L2Attack(self.kind.currentData(), self.target.text().strip(), self.gateway.text().strip())

    def _load_form(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.l2attacks):
            a = self.dev.l2attacks[r]
            self.kind.setCurrentIndex(self.kind.findData(a.kind))
            self.target.setText(a.target)
            self.gateway.setText(a.gateway)

    def add(self):
        self.dev.l2attacks.append(self._from_form())
        self.load()
        self.table.selectRow(len(self.dev.l2attacks) - 1)

    def save(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.l2attacks):
            a = self._from_form()
            a.running = self.dev.l2attacks[r].running
            self.dev.l2attacks[r] = a
            self.load()

    def remove(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.l2attacks):
            del self.dev.l2attacks[r]
            self.load()

    def _run(self, on):
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.l2attacks):
            (self.dev.start if on else self.dev.stop)(r)
            self.load()

    def stop_all(self):
        self.dev.stop()
        self.load()

    def load(self):
        try:
            cur = self.table.currentRow()
            atts = self.dev.l2attacks
            self.table.setRowCount(len(atts))
            for i, a in enumerate(atts):
                extra = a.gateway if a.kind == "arp-spoof" else ""
                cells = (l2.name(a.kind), a.target or tr("вся подсеть"), extra,
                         tr("идёт") if a.running else tr("остановлена"))
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    if c == 3 and a.running:
                        it.setForeground(Qt.red)
                    self.table.setItem(i, c, it)
            if 0 <= cur < len(atts):
                self.table.selectRow(cur)
        except RuntimeError:
            self._timer.stop()


class L2Monitor(QWidget):
    """Tools → LAN attacks: who is attacking, how much is intercepted and what defences fired."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Атакующий"), tr("Атаки"), tr("Перехвачено кадров")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        row.addWidget(self.table, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        row.addWidget(self.log, 3)
        lay.addLayout(row, 1)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)

    def shutdown(self):
        self._timer.stop()

    def refresh(self):
        try:
            topo = self.window.topology
            m = l2.monitor(topo)
            attackers = m.attackers()
            self.table.setRowCount(len(attackers))
            for i, d in enumerate(attackers):
                kinds = ", ".join(l2.name(a.kind) for a in d.l2attacks if a.running) or "—"
                cells = (d.name, kinds, str(getattr(d, "_intercepted", 0)))
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    if c == 2 and getattr(d, "_intercepted", 0) > 0:
                        it.setForeground(Qt.red)
                    self.table.setItem(i, c, it)
            st = m.stats
            self.summary.setText(tr("Отравлено ARP: {p}. Перехвачено кадров: {i}. Переполнений CAM: {c}. "
                                    "DAI отбросил: {d}. BPDU Guard/Root Guard: {b}.",
                                    p=st["poisoned"], i=st["intercepted"], c=st["cam_flood"], d=st["dai_drops"],
                                    b=st["bpdu_blocked"]))
            lines = [f"{t / 1000:8.1f} s  {name}: {text}" for t, name, text in m.events[-200:]]
            text = "\n".join(lines)
            if self.log.toPlainText() != text:
                self.log.setPlainText(text)
                self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        except RuntimeError:
            self._timer.stop()
