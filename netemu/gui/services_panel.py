"""Server 'Services' tab (DHCP / DNS / HTTP / Syslog / NTP) and the PC web browser (Packet Tracer style)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.addresses import IPv4Network, is_valid_ip, is_valid_mask, mask_to_prefix
from ..core.services.dhcp import DhcpPool
from ..i18n import tr


class ServicesPanel(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)

        dh = QGroupBox("DHCP")
        f = QFormLayout(dh)
        self.dhcp_on = QCheckBox(tr("Служба включена"))
        self.pool_name = QLineEdit()
        self.pool_gw = QLineEdit()
        self.pool_dns = QLineEdit()
        self.pool_start = QLineEdit()
        self.pool_mask = QLineEdit()
        self.pool_max = QLineEdit()
        f.addRow(self.dhcp_on)
        f.addRow(tr("Имя пула:"), self.pool_name)
        f.addRow(tr("Шлюз по умолчанию:"), self.pool_gw)
        f.addRow(tr("DNS-сервер:"), self.pool_dns)
        f.addRow(tr("Начальный адрес:"), self.pool_start)
        f.addRow(tr("Маска подсети:"), self.pool_mask)
        f.addRow(tr("Число адресов:"), self.pool_max)
        lay.addWidget(dh)

        dn = QGroupBox("DNS")
        v = QVBoxLayout(dn)
        self.dns_on = QCheckBox(tr("Служба включена"))
        v.addWidget(self.dns_on)
        self.records = QTableWidget(0, 2)
        self.records.setHorizontalHeaderLabels([tr("Имя"), tr("Адрес")])
        self.records.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.records.verticalHeader().setVisible(False)
        v.addWidget(self.records)
        row = QHBoxLayout()
        self.rec_name, self.rec_ip = QLineEdit(), QLineEdit()
        self.rec_name.setPlaceholderText("www.example.com")
        self.rec_ip.setPlaceholderText("192.168.1.10")
        add, rem = QPushButton(tr("Добавить")), QPushButton(tr("Удалить выбранный"))
        add.clicked.connect(self.add_record)
        rem.clicked.connect(self.remove_record)
        for w in (self.rec_name, self.rec_ip, add, rem):
            row.addWidget(w)
        v.addLayout(row)
        lay.addWidget(dn)

        ht = QGroupBox("HTTP")
        v = QVBoxLayout(ht)
        self.http_on = QCheckBox(tr("Служба включена"))
        v.addWidget(self.http_on)
        v.addWidget(QLabel("index.html:"))
        self.page = QPlainTextEdit()
        self.page.setMaximumHeight(120)
        v.addWidget(self.page)
        lay.addWidget(ht)

        aa = QGroupBox(tr("AAA: RADIUS и TACACS+"))
        fa = QFormLayout(aa)
        row = QHBoxLayout()
        self.radius_on = QCheckBox("RADIUS (UDP 1812)")
        self.tacacs_on = QCheckBox("TACACS+ (TCP 49)")
        row.addWidget(self.radius_on)
        row.addWidget(self.tacacs_on)
        row.addStretch(1)
        fa.addRow(row)
        self.aaa_secret = QLineEdit()
        fa.addRow(tr("Общий ключ (secret):"), self.aaa_secret)
        self.aaa_users = QPlainTextEdit()
        self.aaa_users.setPlaceholderText(tr("пользователь:пароль, по одному в строке"))
        self.aaa_users.setMaximumHeight(70)
        fa.addRow(tr("Пользователи:"), self.aaa_users)
        self.aaa_log = QPlainTextEdit()
        self.aaa_log.setReadOnly(True)
        self.aaa_log.setMaximumHeight(60)
        fa.addRow(tr("Журнал:"), self.aaa_log)
        lay.addWidget(aa)

        fs = QGroupBox(tr("Файлы и почта: TFTP, FTP, SMTP/POP3"))
        ff = QFormLayout(fs)
        row = QHBoxLayout()
        self.tftp_on = QCheckBox("TFTP (UDP 69)")
        self.ftp_on = QCheckBox("FTP (TCP 21)")
        self.mail_on = QCheckBox(tr("Почта (SMTP 25, POP3 110)"))
        for w in (self.tftp_on, self.ftp_on, self.mail_on):
            row.addWidget(w)
        row.addStretch(1)
        ff.addRow(row)
        self.ftp_users = QLineEdit()
        self.ftp_users.setPlaceholderText("cisco:cisco")
        ff.addRow(tr("Пользователи FTP:"), self.ftp_users)
        self.mail_domain = QLineEdit()
        ff.addRow(tr("Почтовый домен:"), self.mail_domain)
        self.mail_users = QLineEdit()
        self.mail_users.setPlaceholderText("alice:pass1, bob:pass2")
        ff.addRow(tr("Почтовые ящики:"), self.mail_users)
        self.files_list = QLabel()
        self.files_list.setWordWrap(True)
        ff.addRow(tr("Файлы:"), self.files_list)
        lay.addWidget(fs)

        it = QGroupBox(tr("Интернет вещей (IoT)"))
        fi = QFormLayout(it)
        self.iot_on = QCheckBox(tr("IoT-сервер (CoAP, UDP 5683)"))
        fi.addRow(self.iot_on)
        self.iot_devices = QLabel()
        self.iot_devices.setWordWrap(True)
        fi.addRow(tr("Устройства:"), self.iot_devices)
        self.iot_rules = QPlainTextEdit()
        self.iot_rules.setPlaceholderText("IF Temp1 > 30 THEN Fan1 on\nIF Motion1 = yes THEN Lamp1 on")
        self.iot_rules.setMaximumHeight(70)
        fi.addRow(tr("Правила:"), self.iot_rules)
        lay.addWidget(it)

        sl = QGroupBox("Syslog / NTP")
        v = QVBoxLayout(sl)
        row = QHBoxLayout()
        self.syslog_on = QCheckBox(tr("Syslog-сервер (UDP 514)"))
        self.ntp_on = QCheckBox(tr("NTP-сервер (UDP 123)"))
        clear = QPushButton(tr("Очистить журнал"))
        clear.clicked.connect(self.clear_log)
        for w in (self.syslog_on, self.ntp_on):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(clear)
        v.addLayout(row)
        self.log = QTableWidget(0, 3)
        self.log.setHorizontalHeaderLabels([tr("Время"), tr("Устройство"), tr("Сообщение")])
        self.log.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.log.verticalHeader().setVisible(False)
        self.log.setMaximumHeight(140)
        v.addWidget(self.log)
        lay.addWidget(sl)
        eng, fn = self.dev.engine, self._on_changed
        eng.subscribe("device_changed", fn)
        self.destroyed.connect(lambda *_: eng.unsubscribe("device_changed", fn))

        from .ddos_panel import ServerGuard
        self.guard = ServerGuard(self.dev)
        lay.addWidget(self.guard)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.apply_btn = QPushButton(tr("Применить"))
        self.apply_btn.clicked.connect(lambda _c=False: self.apply())
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)
        self.load()

    def load(self) -> None:
        d = self.dev
        cfg = d.dhcp_server.cfg
        self.dhcp_on.setChecked(cfg.enabled)
        p = cfg.pools[0] if cfg.pools else None
        net = p.net() if p else None
        self.pool_name.setText(p.name if p else "serverPool")
        self.pool_gw.setText(p.gateway or "" if p else "")
        self.pool_dns.setText(p.dns or "" if p else "")
        start = p.start if p and p.start else (str(net.network_address + 1) if net else "")
        self.pool_start.setText(start)
        self.pool_mask.setText(str(net.netmask) if net else "")
        if p and net:
            from ipaddress import IPv4Address
            end = int(IPv4Address(p.end)) if p.end else int(net.broadcast_address) - 1
            self.pool_max.setText(str(end - int(IPv4Address(start)) + 1))
        else:
            self.pool_max.setText("")
        self.dns_on.setChecked(d.dns_server.enabled)
        self._load_records()
        self.http_on.setChecked(d.http_server.enabled)
        self.page.setPlainText(d.http_server.page("/")[1])
        io = d.iot_service
        self.iot_on.setChecked(io.enabled)
        self.iot_devices.setText(", ".join(f"{n} = {v['value']}" for n, v in sorted(io.devices.items())) or "—")
        if not self.iot_rules.hasFocus():
            self.iot_rules.setPlainText("\n".join(io.rules))
        fsv = d.file_server
        self.tftp_on.setChecked(fsv.tftp)
        self.ftp_on.setChecked(fsv.ftp)
        self.mail_on.setChecked(fsv.mail)
        self.ftp_users.setText(", ".join(f"{u}:{p}" for u, p in fsv.ftp_users.items()))
        self.mail_domain.setText(fsv.domain)
        self.mail_users.setText(", ".join(f"{u}:{p}" for u, p in fsv.mail_users.items()))
        self.files_list.setText(", ".join(f"{n} ({len(t)} B)" for n, t in sorted(fsv.files.items())) or "—")
        a = d.aaa_server
        self.radius_on.setChecked(a.radius)
        self.tacacs_on.setChecked(a.tacacs)
        self.aaa_secret.setText(a.secret)
        self.aaa_users.setPlainText("\n".join(f"{u}:{p}" for u, p in a.users.items()))
        self.aaa_log.setPlainText("\n".join(a.log[-20:]))
        self.syslog_on.setChecked(d.syslog_server.enabled)
        self.ntp_on.setChecked(d.ntp_server.enabled)
        self._load_log()

    def _load_log(self) -> None:
        msgs = self.dev.syslog_server.messages
        self.log.setRowCount(len(msgs))
        for r, row in enumerate(msgs):
            for c, val in enumerate(row):
                self.log.setItem(r, c, QTableWidgetItem(val))
        self.log.scrollToBottom()

    def _on_changed(self, dev, *rest) -> None:
        if dev is self.dev:
            try:
                self._load_log()
            except RuntimeError:            # widget already destroyed
                pass

    def clear_log(self) -> None:
        self.dev.syslog_server.messages.clear()
        self._load_log()

    def _load_records(self) -> None:
        d = self.dev
        self.records.setRowCount(0)
        for name, ip in sorted(d.dns_server.records.items()):
            r = self.records.rowCount()
            self.records.insertRow(r)
            self.records.setItem(r, 0, QTableWidgetItem(name))
            self.records.setItem(r, 1, QTableWidgetItem(ip))

    def add_record(self) -> None:
        name, ip = self.rec_name.text().strip().lower(), self.rec_ip.text().strip()
        if not name or not is_valid_ip(ip):
            self.dialog.error(tr("Укажите имя и IPv4-адрес записи."))
            return
        self.dev.dns_server.records[name] = ip
        self.rec_name.clear()
        self.rec_ip.clear()
        self._load_records()

    def remove_record(self) -> None:
        r = self.records.currentRow()
        if r >= 0:
            self.dev.dns_server.records.pop(self.records.item(r, 0).text(), None)
            self._load_records()

    def apply(self) -> bool:
        self.guard.apply()
        from ipaddress import IPv4Address
        d = self.dev
        cfg = d.dhcp_server.cfg
        cfg.enabled = self.dhcp_on.isChecked()
        start, mask = self.pool_start.text().strip(), self.pool_mask.text().strip()
        gw, dns = self.pool_gw.text().strip(), self.pool_dns.text().strip()
        if start or mask:
            if not is_valid_ip(start) or not is_valid_mask(mask):
                return self.dialog.error(tr("Неверный начальный адрес или маска пула DHCP."))
            for v in (gw, dns):
                if v and not is_valid_ip(v):
                    return self.dialog.error(tr("Неверный адрес шлюза или DNS-сервера в пуле DHCP."))
            net = IPv4Network(f"{start}/{mask_to_prefix(mask)}", strict=False)
            count = self.pool_max.text().strip()
            end = None
            if count.isdigit() and int(count) > 0:
                end = str(min(IPv4Address(int(IPv4Address(start)) + int(count) - 1), net.broadcast_address - 1))
            cfg.pools = [DhcpPool(self.pool_name.text().strip() or "serverPool", str(net), gw or None, dns or None,
                                  start=start, end=end)]
        d.dns_server.enabled = self.dns_on.isChecked()
        d.http_server.enabled = self.http_on.isChecked()
        d.http_server.pages["index.html"] = self.page.toPlainText()
        errs = d.iot_service.check_rules(self.iot_rules.toPlainText())
        if errs:
            return self.dialog.error(tr("Неверные правила IoT:") + "\n" + "\n".join(errs))
        d.iot_service.enabled = self.iot_on.isChecked()
        d.iot_service.rules = [x.strip() for x in self.iot_rules.toPlainText().splitlines() if x.strip()]
        fsv = d.file_server
        fsv.tftp, fsv.ftp, fsv.mail = self.tftp_on.isChecked(), self.ftp_on.isChecked(), self.mail_on.isChecked()

        def pairs(text):
            out = {}
            for item in text.split(","):
                u, sep, pw = item.strip().partition(":")
                if u and sep:
                    out[u.strip()] = pw
            return out
        fsv.ftp_users = pairs(self.ftp_users.text())
        fsv.mail_users = pairs(self.mail_users.text())
        fsv.domain = self.mail_domain.text().strip() or "netemu.lab"
        a = d.aaa_server
        a.radius, a.tacacs = self.radius_on.isChecked(), self.tacacs_on.isChecked()
        a.secret = self.aaa_secret.text()
        a.users = {}
        for line in self.aaa_users.toPlainText().splitlines():
            u, sep, pw = line.strip().partition(":")
            if u and sep:
                a.users[u.strip()] = pw
        d.syslog_server.enabled = self.syslog_on.isChecked()
        d.ntp_server.enabled = self.ntp_on.isChecked()
        d.engine.emit("device_changed", d)
        self.load()
        return True


class WebBrowser(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.url = QLineEdit("http://")
        self.go = QPushButton(tr("Перейти"))
        self.go.clicked.connect(self.navigate)
        self.url.returnPressed.connect(self.navigate)
        row.addWidget(QLabel("URL:"))
        row.addWidget(self.url, 1)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.view = QTextBrowser()
        lay.addWidget(self.view, 1)
        self.job = None

    def navigate(self) -> None:
        from ..core.services.http import HttpGet
        if self.job is not None and not self.job.done:
            self.job.cancel()
        self.view.setHtml(f"<p><i>{tr('Загрузка…')}</i></p>")
        if self.dev.nic.ip is None:
            self.view.setHtml(f"<h3>{tr('Нет IP-адреса')}</h3>")
            return

        def done(status, body):
            try:
                if status is None:
                    self.view.setHtml(f"<h3>{tr('Не удалось открыть страницу')}</h3><p>{body}</p>")
                else:
                    self.view.setHtml(body)
            except RuntimeError:
                pass
        self.job = HttpGet(self.dev, self.url.text(), done).start()

    def text(self) -> str:
        return self.view.toPlainText()
