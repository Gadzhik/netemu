"""DDoS training in the GUI: the botnet's attack editor, the provider's protection, the server's guard
and the load monitor (cables and servers under attack)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.security.ddos import KINDS, TITLES, Attack, human_bps, human_pps
from ..i18n import tr

KIND_HELP = {
    "udp": "Объёмная атака: поток UDP забивает канал до сервера.",
    "icmp": "Объёмная атака эхо-запросами ICMP (ping flood).",
    "syn": "Протокольная атака: SYN без завершения рукопожатия переполняют очередь полуоткрытых соединений.",
    "http": "Атака прикладного уровня: настоящие HTTP-запросы исчерпывают производительность сервера.",
    "slowloris": "Медленная атака: боты держат соединения открытыми, занимая все слоты сервера при малом трафике.",
    "dns-amp": "Отражение и усиление: запросы с подменённым адресом жертвы к открытым DNS-резолверам (×54).",
    "ntp-amp": "Отражение и усиление через NTP monlist (×556).",
}
UNIT = {"pps": "пакетов/с на бота", "rps": "запросов/с на бота", "conn": "соединений на бота"}


class AttackPanel(QWidget):
    """Botnet: a list of attacks, each with its kind, target, power; start / stop."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Атака"), tr("Цель"), tr("Мощность"), tr("К жертве"), tr("Состояние")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.currentCellChanged.connect(lambda r, *_: self._edit(r))
        lay.addWidget(self.table, 2)
        box = QGroupBox(tr("Параметры атаки"))
        form = QFormLayout(box)
        self.kind = QComboBox()
        for k in KINDS:
            self.kind.addItem(TITLES[k], k)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.target = QLineEdit()
        self.target.setPlaceholderText("203.0.113.130 / shop.example")
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.bots = QSpinBox()
        self.bots.setRange(1, 10_000_000)
        self.bots.setValue(1000)
        self.rate = QSpinBox()
        self.rate.setRange(1, 1_000_000)
        self.rate.setValue(100)
        self.rate_unit = QLabel()
        self.size = QSpinBox()
        self.size.setRange(40, 9000)
        self.spoof = QCheckBox(tr("Подменять адрес источника (spoofing)"))
        self.reflectors = QLineEdit()
        self.reflectors.setPlaceholderText("198.51.100.53, 198.51.100.123")
        self.total = QLabel()
        for w in (self.bots, self.rate, self.size):
            w.valueChanged.connect(self._update_total)
        rate_row = QHBoxLayout()
        rate_row.addWidget(self.rate)
        rate_row.addWidget(self.rate_unit, 1)
        form.addRow(tr("Тип:"), self.kind)
        form.addRow("", self.about)
        form.addRow(tr("Жертва (IP или имя):"), self.target)
        form.addRow(tr("Порт:"), self.port)
        form.addRow(tr("Ботов:"), self.bots)
        form.addRow(tr("Интенсивность:"), rate_row)
        form.addRow(tr("Размер пакета, байт:"), self.size)
        form.addRow("", self.spoof)
        form.addRow(tr("Отражатели:"), self.reflectors)
        form.addRow(tr("Итого:"), self.total)
        lay.addWidget(box)
        row = QHBoxLayout()
        for text, fn in ((tr("Добавить"), self.add), (tr("Сохранить изменения"), self.save),
                         (tr("Удалить"), self.remove), (tr("Запустить"), lambda: self._run(True)),
                         (tr("Остановить"), lambda: self._run(False)), (tr("Остановить все"), self.stop_all)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        lay.addLayout(row)
        hint = QLabel(tr("Один значок ботнета изображает тысячи заражённых машин. Атака моделируется потоком: раз в "
                         "секунду по сети идёт пакет-проба, несущий всю интенсивность, поэтому ACL, NAT, uRPF, "
                         "полисинг, null-маршруты и защита провайдера действуют на весь поток. Нагрузку на кабели "
                         "показывает «Инструменты → Нагрузка и DDoS»."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._kind_changed()
        self.load()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.load)
        self._timer.start(1000)

    # -- form ---------------------------------------------------------------------------------------
    def _kind_changed(self, *_):
        k = self.kind.currentData()
        proto, port, size, amp, unit, layer = KINDS[k]
        self.about.setText(f"{layer}. " + tr(KIND_HELP[k]))
        self.port.setValue(port)
        self.size.setValue(size)
        self.rate_unit.setText(tr(UNIT[unit]))
        self.spoof.setEnabled(k not in ("http", "slowloris"))
        self.reflectors.setEnabled(k in ("dns-amp", "ntp-amp"))
        self._update_total()

    def _from_form(self) -> Attack:
        k = self.kind.currentData()
        return Attack(k, self.target.text().strip(), self.port.value(), self.bots.value(), float(self.rate.value()),
                      self.size.value(), self.spoof.isChecked() and k not in ("http", "slowloris"),
                      [x.strip() for x in self.reflectors.text().replace(";", ",").split(",") if x.strip()])

    def _update_total(self, *_):
        a = self._from_form()
        text = f"{human_bps(a.total_bps)}, {human_pps(a.total_pps)}"
        if a.kind in ("dns-amp", "ntp-amp"):
            text += " → " + tr("у жертвы до {bps}", bps=human_bps(a.amplified_bps))
        if a.kind == "http":
            text = f"{a.total_pps:.0f} " + tr("запросов/с") + f", {human_bps(a.total_bps)}"
        if a.kind == "slowloris":
            text = f"{a.total_pps:.0f} " + tr("открытых соединений") + f", {human_bps(a.total_bps)}"
        self.total.setText(text)

    def _edit(self, row: int) -> None:
        if not (0 <= row < len(self.dev.attacks)):
            return
        a = self.dev.attacks[row]
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, self.kind.findData(a.kind)))
        self.kind.blockSignals(False)
        self._kind_changed()
        self.target.setText(a.target)
        self.port.setValue(a.dport)
        self.bots.setValue(a.bots)
        self.rate.setValue(int(a.rate))
        self.size.setValue(a.packet_size)
        self.spoof.setChecked(a.spoof)
        self.reflectors.setText(", ".join(a.reflectors))

    def _valid(self, a: Attack) -> bool:
        from ipaddress import IPv4Address
        import re
        try:
            IPv4Address(a.target)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", a.target):
                return self.dialog.error(tr("Укажите IPv4-адрес или доменное имя жертвы."))
            if getattr(self.dev, "dns", None) is None:
                return self.dialog.error(tr("Чтобы атаковать по имени, задайте ботнету DNS-сервер (вкладка «Настройка»)."))
        try:
            for r in a.reflectors:
                IPv4Address(r)
        except ValueError:
            return self.dialog.error(tr("Укажите IPv4-адреса отражателей."))
        if a.kind in ("dns-amp", "ntp-amp") and not a.reflectors:
            return self.dialog.error(tr("Для атаки с усилением нужны адреса отражателей."))
        return True

    def add(self) -> None:
        a = self._from_form()
        if self._valid(a):
            self.dev.attacks.append(a)
            self.load()
            self.table.selectRow(len(self.dev.attacks) - 1)

    def save(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.attacks):
            a = self._from_form()
            if self._valid(a):
                a.running = self.dev.attacks[r].running
                self.dev.attacks[r] = a
                self.load()

    def remove(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.dev.attacks):
            del self.dev.attacks[r]
            self.load()

    def _run(self, on: bool) -> None:
        r = self.table.currentRow()
        if not (0 <= r < len(self.dev.attacks)):
            return
        (self.dev.start if on else self.dev.stop)(r)
        self.load()

    def stop_all(self) -> None:
        self.dev.stop()
        self.load()

    def load(self) -> None:
        try:
            atts = self.dev.attacks
            cur = self.table.currentRow()
            self.table.setRowCount(len(atts))
            from ..core.security.ddos import monitor_of
            m = monitor_of(self.dev)
            for i, a in enumerate(atts):
                power = f"{human_bps(a.total_bps)} / {human_pps(a.total_pps)}" if a.kind not in ("http", "slowloris") \
                    else f"{a.total_pps:.0f} {'rps' if a.kind == 'http' else tr('соединений')}"
                victim = self.dialog.topology.devices.values()
                hit = "—"
                addr = str(self.dev._names.get(a.target.lower(), a.target))
                if m is not None and a.running:
                    for d in victim:
                        if any(p.ip is not None and str(p.ip.ip) == addr for p in d.ports.values()):
                            hit = human_bps(m.attack_bps_at(d))
                shown = a.target if addr == a.target else f"{a.target} ({addr})"
                cells = (TITLES[a.kind], shown, power, hit, tr("идёт") if a.running else tr("остановлена"))
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    if c == 4 and a.running:
                        it.setForeground(Qt.red)
                    self.table.setItem(i, c, it)
            if 0 <= cur < len(atts):
                self.table.selectRow(cur)
        except RuntimeError:
            self._timer.stop()


class IspProtectPanel(QWidget):
    """Internet cloud: protection the provider sells to each customer port, DNS hosting and the switch of a
    domain to the protection service."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("Защита от DDoS на стороне провайдера (для каждого порта-клиента):")))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Порт"), tr("Очистка трафика"), "RTBH", "FlowSpec"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 2)
        hint = QLabel(tr("Очистка трафика (scrubbing) убирает потоки атак L3/L4 до канала клиента, легитимный трафик "
                         "проходит. RTBH — адреса (через запятую), весь трафик к которым отбрасывается у провайдера: "
                         "жертва недоступна, но канал и остальные адреса работают. FlowSpec — правила через «;», "
                         "например «udp src 53; udp src 123; icmp»."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addWidget(QLabel(tr("DNS-хостинг доменов клиентов (отвечают резолверы 8.8.8.8 и 1.1.1.1):")))
        self.zones = QTableWidget(0, 4)
        self.zones.setHorizontalHeaderLabels([tr("Домен"), tr("Адрес сайта (A)"), "TTL, " + tr("с"),
                                              tr("Через сервис защиты")])
        self.zones.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.zones.verticalHeader().setVisible(False)
        lay.addWidget(self.zones, 1)
        zb = QHBoxLayout()
        for text, fn in ((tr("Добавить домен"), self._add_zone), (tr("Удалить домен"), self._del_zone)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, f=fn: f())
            zb.addWidget(b)
        zb.addStretch(1)
        lay.addLayout(zb)
        zhint = QLabel(tr("Переключение DNS: отметьте «Через сервис защиты» — резолверы начнут отвечать адресом "
                          "сервиса защиты 198.51.100.200. Это обратный прокси: он поглощает флуд, отсеивает ботов и "
                          "забирает страницы с адреса сайта. Клиенты и боты переходят, когда истекает TTL записи в их "
                          "кэше. Атаки прямо на IP-адрес сайта переключение не останавливает: адрес надо скрывать "
                          "(или сменить) и принимать HTTP только от сервиса защиты. Тем же способом сайт переводят "
                          "на резервную площадку — достаточно поменять адрес A."))
        zhint.setWordWrap(True)
        lay.addWidget(zhint)
        apply_btn = QPushButton(tr("Применить"))
        apply_btn.clicked.connect(lambda _c=False: self.apply())
        lay.addWidget(apply_btn, 0, Qt.AlignRight)
        self.load()

    def _zone_row(self, i, name, z) -> None:
        self.zones.setItem(i, 0, QTableWidgetItem(name))
        self.zones.setItem(i, 1, QTableWidgetItem(z.get("a", "")))
        self.zones.setItem(i, 2, QTableWidgetItem(str(z.get("ttl", 300))))
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        chk.setCheckState(Qt.Checked if z.get("shield") else Qt.Unchecked)
        self.zones.setItem(i, 3, chk)

    def _add_zone(self) -> None:
        i = self.zones.rowCount()
        self.zones.setRowCount(i + 1)
        self._zone_row(i, "shop.example", {"a": "203.0.113.2", "ttl": 300})
        self.zones.setCurrentCell(i, 0)

    def _del_zone(self) -> None:
        r = self.zones.currentRow()
        if r >= 0:
            self.zones.removeRow(r)

    def load(self) -> None:
        zones = getattr(self.dev, "zones", {})
        self.zones.setRowCount(len(zones))
        for i, (name, z) in enumerate(sorted(zones.items())):
            self._zone_row(i, name, z)
        ports = sorted((p for p in self.dev.ports.values() if not p.is_loopback), key=lambda p: p.name)
        self.table.setRowCount(len(ports))
        for i, p in enumerate(ports):
            prot = self.dev.protect.get(p.name, {})
            self.table.setItem(i, 0, QTableWidgetItem(p.name))
            self.table.item(i, 0).setFlags(Qt.ItemIsEnabled)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if prot.get("scrub") else Qt.Unchecked)
            self.table.setItem(i, 1, chk)
            self.table.setItem(i, 2, QTableWidgetItem(", ".join(prot.get("rtbh", []))))
            self.table.setItem(i, 3, QTableWidgetItem("; ".join(prot.get("flowspec", []))))

    def apply(self) -> bool:
        out = {}
        for i in range(self.table.rowCount()):
            name = self.table.item(i, 0).text()
            prot = {"scrub": self.table.item(i, 1).checkState() == Qt.Checked,
                    "rtbh": [x.strip() for x in self.table.item(i, 2).text().split(",") if x.strip()],
                    "flowspec": [x.strip() for x in self.table.item(i, 3).text().split(";") if x.strip()]}
            if prot["scrub"] or prot["rtbh"] or prot["flowspec"]:
                out[name] = prot
        from ipaddress import IPv4Address
        zones = {}
        for i in range(self.zones.rowCount()):
            name = (self.zones.item(i, 0).text() if self.zones.item(i, 0) else "").strip().lower().rstrip(".")
            if not name:
                continue
            a = self.zones.item(i, 1).text().strip()
            try:
                IPv4Address(a)
                ttl = int(self.zones.item(i, 2).text().strip())
            except ValueError:
                return self.dialog.error(tr("Домен {name}: нужен IPv4-адрес и TTL в секундах.", name=name))
            zones[name] = {"a": a, "ttl": max(1, ttl), "shield": self.zones.item(i, 3).checkState() == Qt.Checked}
        self.dev.protect = out
        self.dev.zones = zones
        return True


class ServerGuard(QGroupBox):
    """Server: protections against SYN floods, HTTP floods, Slowloris and being used as a reflector."""

    def __init__(self, dev):
        super().__init__(tr("Защита от DDoS"))
        from ..core.security.ddos import guard
        self.g = guard(dev)
        form = QFormLayout(self)
        self.syncookies = QCheckBox(tr("SYN cookies (не хранить полуоткрытые соединения)"))
        self.http_limit = QSpinBox()
        self.http_limit.setRange(0, 100000)
        self.http_limit.setSpecialValueText(tr("нет"))
        self.challenge = QCheckBox(tr("Проверка браузера (JS-challenge) для всех запросов"))
        self.conn_limit = QSpinBox()
        self.conn_limit.setRange(0, 10000)
        self.conn_limit.setSpecialValueText(tr("нет"))
        self.open_resolver = QCheckBox(tr("DNS: рекурсия для любых адресов (открытый резолвер)"))
        self.monlist = QCheckBox(tr("NTP: отвечать на monlist"))
        form.addRow(self.syncookies)
        form.addRow(tr("Лимит запросов с одного IP, в секунду:"), self.http_limit)
        form.addRow(self.challenge)
        form.addRow(tr("Лимит соединений с одного IP:"), self.conn_limit)
        form.addRow(self.open_resolver)
        form.addRow(self.monlist)
        self.load()

    def load(self) -> None:
        g = self.g
        self.syncookies.setChecked(bool(g["syncookies"]))
        self.http_limit.setValue(int(g["http_limit"] or 0))
        self.challenge.setChecked(bool(g["challenge"]))
        self.conn_limit.setValue(int(g["conn_limit"] or 0))
        self.open_resolver.setChecked(bool(g["open_resolver"]))
        self.monlist.setChecked(bool(g["ntp_monlist"]))

    def apply(self) -> None:
        self.g.update(syncookies=self.syncookies.isChecked(), http_limit=self.http_limit.value(),
                      challenge=self.challenge.isChecked(), conn_limit=self.conn_limit.value(),
                      open_resolver=self.open_resolver.isChecked(), ntp_monlist=self.monlist.isChecked())


class LoadMonitor(QWidget):
    """Tools → Load and DDoS: cable directions by utilisation, servers under pressure."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.links = QTableWidget(0, 5)
        self.links.setHorizontalHeaderLabels([tr("Кабель (направление)"), tr("Атака"), tr("Загрузка"),
                                              tr("Пакетов/с"), tr("Типы")])
        self.links.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.links.verticalHeader().setVisible(False)
        self.links.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.links, 2)
        self.servers = QPlainTextEdit()
        self.servers.setReadOnly(True)
        self.servers.setMaximumHeight(140)
        lay.addWidget(self.servers)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self.refresh()

    def shutdown(self) -> None:
        self._timer.stop()

    def refresh(self) -> None:
        from ..core.security.ddos import server_pressure
        topo = self.window.topology
        m = getattr(topo, "ddos", None)
        rows = m.rows() if m is not None else []
        self.links.setRowCount(len(rows))
        for i, (link, port, bps, pps, util, kinds) in enumerate(rows):
            other = link.other(port)
            cells = (f"{port.device.name} {port.short} → {other.device.name} {other.short}", human_bps(bps),
                     f"{util * 100:.0f} %", human_pps(pps), ", ".join(TITLES.get(k, k) for k in kinds))
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 2:
                    it.setForeground(Qt.red if util > 1 else (Qt.darkYellow if util > 0.7 else Qt.darkGreen))
                self.links.setItem(i, c, it)
        lines = []
        for d in topo.devices.values():
            if d.category != "server":
                continue
            p = server_pressure(d)
            parts = []
            for key, title in (("syn", "SYN"), ("http", "HTTP"), ("conn", tr("соединения"))):
                load, cap = p[key]
                if load:
                    parts.append(f"{title} {load:.0f}/{cap}" + (" ⚠" if load > cap or (key == "conn" and load >= cap)
                                                                  else ""))
            if parts:
                lines.append(f"{d.name}: " + ", ".join(parts))
        if m is not None:
            from ..core.security.ddos import firewall_syn_protected
            for d in topo.devices.values():
                if getattr(d, "pps_limit", None) and m.device_pps(d):
                    pps = m.device_pps(d)
                    over = pps > d.pps_limit
                    lines.append(tr("{dev}: пересылка {pps} из {cap}", dev=d.name, pps=human_pps(pps),
                                    cap=human_pps(d.pps_limit)) + (" ⚠" if over else ""))
                cap = getattr(d, "conn_max", None)
                syn = m.received(d).get("syn", (0, 0, 0, 0))[1] if cap else 0
                if cap and syn:
                    over = syn > cap and not firewall_syn_protected(d)
                    lines.append(tr("{dev}: полуоткрытых соединений {syn} из {cap}", dev=d.name,
                                    syn=human_pps(syn).replace("pps", "").strip(), cap=cap) + (" ⚠" if over else ""))
        self.servers.setPlainText("\n".join(lines) or tr("Серверы работают в штатном режиме."))
        running = sum(1 for d in topo.devices.values() for a in getattr(d, "attacks", []) if a.running)
        drops = m.drops if m is not None else 0
        self.summary.setText(tr("Идёт атак: {n}. Потеряно легитимных кадров на перегруженных кабелях: {d}.",
                                n=running, d=drops))
