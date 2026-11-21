"""IP phone tab: display, keypad, dial / answer / hang up, call history."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout,
                               QWidget)

from ..i18n import tr

STATES = {"unregistered": "Не зарегистрирован", "idle": "Готов", "dialing": "Набор…", "ringing": "Входящий вызов",
          "ringback": "Вызов абонента…", "connected": "Разговор"}


class PhonePanel(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dev = dialog.device
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.display = QLabel()
        self.display.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.display.setMinimumHeight(120)
        self.display.setStyleSheet("QLabel { background: #23313f; color: #d8f3ff; font-family: Consolas, monospace;"
                                   " font-size: 13px; padding: 10px; border-radius: 6px; }")
        left.addWidget(self.display)
        self.number = QLineEdit()
        self.number.setPlaceholderText(tr("Номер"))
        self.number.returnPressed.connect(self.dial)
        left.addWidget(self.number)
        grid = QGridLayout()
        for i, key in enumerate("123456789*0#"):
            b = QPushButton(key)
            b.setFixedHeight(34)
            b.clicked.connect(lambda _=False, k=key: self.number.insert(k))
            grid.addWidget(b, i // 3, i % 3)
        left.addLayout(grid)
        row = QHBoxLayout()
        self.b_dial = QPushButton(tr("Вызов"))
        self.b_answer = QPushButton(tr("Ответить"))
        self.b_hang = QPushButton(tr("Отбой"))
        self.b_dial.clicked.connect(self.dial)
        self.b_answer.clicked.connect(self.answer)
        self.b_hang.clicked.connect(self.hangup)
        for b in (self.b_dial, self.b_answer, self.b_hang):
            row.addWidget(b)
        left.addLayout(row)
        left.addStretch(1)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        right.addWidget(QLabel(tr("Журнал вызовов")))
        self.history = QListWidget()
        right.addWidget(self.history, 1)
        hint = QLabel(tr("Порт Switch подключите к коммутатору, порт PC к компьютеру. Телефон узнаёт voice VLAN от "
                         "коммутатора, получает адрес по DHCP и регистрируется на CME (опция 150 или шлюз)."))
        hint.setWordWrap(True)
        right.addWidget(hint)
        lay.addLayout(right, 1)
        eng, fn = self.dev.engine, self._changed
        eng.subscribe("device_changed", fn)
        self.destroyed.connect(lambda *_: eng.unsubscribe("device_changed", fn))
        self.refresh()

    def _changed(self, dev, *rest) -> None:
        if dev is self.dev:
            try:
                self.refresh()
            except RuntimeError:
                pass

    def refresh(self) -> None:
        d = self.dev
        peer = d.call["peer"] if d.call else ""
        lines = [f"{d.hostname}    DN {d.number or '—'}", "", tr(STATES.get(d.state, d.state)) + (f"  {peer}" if peer else "")]
        if d.state == "connected":
            lines.append(f"RTP: rx {d.rtp_rx} / tx {d.rtp_tx}")
        lines += ["", f"VLAN {d.voice_vlan or '—'}   IP {d.nic.ip.ip if d.nic.ip else '—'}", f"CME {d.cme or '—'}"]
        self.display.setText("\n".join(lines))
        self.b_dial.setEnabled(d.state == "idle")
        self.b_answer.setEnabled(d.state == "ringing")
        self.b_hang.setEnabled(d.state in ("dialing", "ringing", "ringback", "connected"))
        self.history.clear()
        self.history.addItems(list(reversed(d.history)))

    def dial(self) -> None:
        n = self.number.text().strip()
        if n:
            self.dev.dial(n)
            self.refresh()

    def answer(self) -> None:
        self.dev.answer()

    def hangup(self) -> None:
        self.dev.hangup()
