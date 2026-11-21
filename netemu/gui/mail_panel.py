"""PC e-mail client (SMTP to send, POP3 to receive) — Packet Tracer style Mail tab."""
from __future__ import annotations

from PySide6.QtWidgets import (QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..i18n import tr


class MailPanel(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dev = dialog.device
        self.job = None
        lay = QVBoxLayout(self)
        acc = QGroupBox(tr("Учётная запись"))
        f = QFormLayout(acc)
        self.address = QLineEdit()
        self.address.setPlaceholderText("alice@netemu.lab")
        self.server = QLineEdit()
        self.server.setPlaceholderText("192.168.1.100")
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        for label, w in ((tr("Адрес:"), self.address), (tr("Почтовый сервер:"), self.server),
                         (tr("Пользователь:"), self.user), (tr("Пароль:"), self.password)):
            f.addRow(label, w)
        save = QPushButton(tr("Сохранить"))
        save.clicked.connect(self.save)
        f.addRow(save)
        lay.addWidget(acc)
        split = QSplitter()
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.inbox = QListWidget()
        self.inbox.currentRowChanged.connect(self.show_message)
        recv = QPushButton(tr("Получить почту"))
        recv.clicked.connect(self.receive)
        lv.addWidget(QLabel(tr("Входящие")))
        lv.addWidget(self.inbox, 1)
        lv.addWidget(recv)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        split.addWidget(left)
        split.addWidget(self.view)
        lay.addWidget(split, 1)
        comp = QGroupBox(tr("Новое письмо"))
        fc = QFormLayout(comp)
        self.to = QLineEdit()
        self.subject = QLineEdit()
        self.body = QPlainTextEdit()
        self.body.setMaximumHeight(80)
        fc.addRow(tr("Кому:"), self.to)
        fc.addRow(tr("Тема:"), self.subject)
        fc.addRow(self.body)
        row = QHBoxLayout()
        self.status = QLabel()
        send = QPushButton(tr("Отправить"))
        send.clicked.connect(self.send)
        row.addWidget(self.status, 1)
        row.addWidget(send)
        fc.addRow(row)
        lay.addWidget(comp)
        self.load()

    def load(self) -> None:
        m = getattr(self.dev, "mail", None) or {}
        self.address.setText(m.get("address", ""))
        self.server.setText(m.get("server", ""))
        self.user.setText(m.get("user", ""))
        self.password.setText(m.get("password", ""))
        self.inbox.clear()
        for msg in getattr(self.dev, "inbox", []) or []:
            self.inbox.addItem(f"{msg.get('from', '?')}: {msg.get('subject', '')}")

    def save(self) -> None:
        self.dev.mail = {"address": self.address.text().strip(), "server": self.server.text().strip(),
                         "user": self.user.text().strip(), "password": self.password.text()}

    def show_message(self, row: int) -> None:
        msgs = getattr(self.dev, "inbox", []) or []
        if 0 <= row < len(msgs):
            m = msgs[row]
            self.view.setPlainText(f"{tr('От')}: {m.get('from')}\n{tr('Кому')}: {m.get('to')}\n"
                                   f"{tr('Тема')}: {m.get('subject')}\n\n{m.get('body')}")

    def send(self) -> None:
        from ..core.services.files import send_mail
        self.save()
        self.status.setText(tr("Отправка…"))
        self.job = send_mail(self.dev, self.to.text().strip(), self.subject.text(), self.body.toPlainText(), self._sent)

    def _sent(self, ok, data) -> None:
        try:
            self.status.setText(tr("Письмо отправлено.") if ok else tr("Ошибка: {e}", e=data))
            if ok:
                self.body.clear()
        except RuntimeError:
            pass

    def receive(self) -> None:
        from ..core.services.files import fetch_mail
        self.save()
        self.status.setText(tr("Получение…"))
        self.job = fetch_mail(self.dev, self._received)

    def _received(self, ok, data) -> None:
        try:
            self.status.setText(tr("Новых писем: {n}", n=len(data or [])) if ok else tr("Ошибка: {e}", e=data))
            self.load()
        except RuntimeError:
            pass
