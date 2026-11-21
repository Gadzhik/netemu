"""Device window: configuration forms + CLI / command prompt."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core.addresses import (IPv4Address, IPv4Network, default_mask_for, is_valid_ip, is_valid_mask,
                              make_interface)
from ..i18n import tr
from .terminal import TerminalWidget

CLI_TITLES = {"cisco": "CLI (Cisco IOS)", "huawei": "CLI (Huawei VRP)",
              "juniper": "CLI (Junos)", "mikrotik": "Терминал (RouterOS)", "extreme": "CLI (ExtremeXOS)"}


class ApConfig(QWidget):
    """Access point: SSID, security, passphrase, channel and range."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        lay = QVBoxLayout(self)
        box = QGroupBox(tr("Беспроводная сеть"))
        form = QFormLayout(box)
        self.name = QLineEdit()
        self.ssid = QLineEdit()
        self.security = QComboBox()
        self.security.addItem("WPA2-Personal", "wpa2")
        self.security.addItem(tr("Открытая (без пароля)"), "open")
        self.key = QLineEdit()
        self.channel = QComboBox()
        for ch in range(1, 14):
            self.channel.addItem(str(ch), ch)
        self.range = QComboBox()
        for r in (150, 250, 400, 600):
            self.range.addItem(f"{r}", r)
        form.addRow(tr("Имя устройства:"), self.name)
        form.addRow("SSID:", self.ssid)
        form.addRow(tr("Защита:"), self.security)
        form.addRow(tr("Пароль (WPA2):"), self.key)
        form.addRow(tr("Канал:"), self.channel)
        form.addRow(tr("Дальность на схеме:"), self.range)
        lay.addWidget(box)
        self.clients = QLabel()
        self.clients.setWordWrap(True)
        lay.addWidget(self.clients)
        btns = QHBoxLayout()
        btns.addStretch(1)
        apply_btn = QPushButton(tr("Применить"))
        apply_btn.clicked.connect(self.apply)
        btns.addWidget(apply_btn)
        lay.addLayout(btns)
        lay.addStretch(1)
        self.load()

    def load(self) -> None:
        d = self.dialog.device
        w = d.wifi
        self.name.setText(d.name)
        self.ssid.setText(w["ssid"])
        self.security.setCurrentIndex(max(0, self.security.findData(w["security"])))
        self.key.setText(w.get("key") or "")
        self.channel.setCurrentIndex(max(0, self.channel.findData(int(w["channel"]))))
        self.range.setCurrentIndex(max(0, self.range.findData(int(w["range"]))))
        names = [p.peer.device.name for p in d.radio_ports() if p.peer is not None]
        self.clients.setText(tr("Подключены: {names}", names=", ".join(names)) if names else tr("Клиентов нет."))

    def apply(self) -> bool:
        d = self.dialog.device
        ssid = self.ssid.text().strip()
        sec = self.security.currentData()
        key = self.key.text()
        if not ssid:
            return self.dialog.error(tr("Укажите SSID."))
        if sec == "wpa2" and len(key) < 8:
            return self.dialog.error(tr("Пароль WPA2 должен быть не короче 8 символов."))
        d.wifi.update(ssid=ssid, security=sec, key=key, channel=self.channel.currentData(), range=self.range.currentData())
        name = self.name.text().strip()
        if name and name != d.name:
            self.dialog.topology.rename(d, name)
            self.dialog.update_title()
        d.engine.emit("device_changed", d)
        self.load()
        return True


def cloud_info(device) -> QWidget:
    """Read-only page of the Internet cloud: hand-off addressing per port and the public services."""
    from ..core.devices.cloud import PORTS, SERVICES, handoff
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel(tr("Каждый порт облака — отдельное подключение к провайдеру. Настройте на своём "
                            "маршрутизаторе адрес клиента и маршрут по умолчанию через шлюз провайдера. "
                            "Провайдер отбрасывает пакеты с частных адресов, поэтому нужен NAT.")))
    lay.itemAt(0).widget().setWordWrap(True)
    t = QTableWidget(PORTS, 4)
    t.setHorizontalHeaderLabels([tr("Порт"), tr("Ваш адрес"), tr("Шлюз провайдера"), tr("Подключено")])
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    for n in range(PORTS):
        isp, cust = handoff(n)
        p = device.ports[f"ISP{n}"]
        peer = f"{p.peer.device.name} {p.peer.short}" if p.peer else "—"
        for c, text in enumerate((p.name, f"{cust} /30 (255.255.255.252)", str(isp.ip), peer)):
            it = QTableWidgetItem(text)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            t.setItem(n, c, it)
    lay.addWidget(t, 2)
    lay.addWidget(QLabel(tr("Публичные службы:")))
    s = QTableWidget(len(SERVICES), 3)
    s.setHorizontalHeaderLabels([tr("Имя"), tr("Адрес"), tr("Служба")])
    s.verticalHeader().setVisible(False)
    s.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    s.horizontalHeader().setStretchLastSection(True)
    for r, (name, (ip, kind)) in enumerate(SERVICES.items()):
        for c, text in enumerate((name, ip, "DNS" if kind == "DNS" else "HTTP (www)")):
            it = QTableWidgetItem(text)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            s.setItem(r, c, it)
    lay.addWidget(s, 1)
    return w


class HostConfig(QWidget):
    """IP configuration of a PC / server."""

    def __init__(self, dialog: "DeviceDialog"):
        super().__init__()
        self.dialog = dialog
        lay = QVBoxLayout(self)
        box = QGroupBox(tr("Настройка IP (статическая)"))
        form = QFormLayout(box)
        self.name = QLineEdit()
        self.ip = QLineEdit()
        self.mask = QLineEdit()
        self.gw = QLineEdit()
        self.dns = QLineEdit()
        self.port_on = QCheckBox(tr("Порт включён"))
        self.dhcp = QCheckBox(tr("Получать адрес по DHCP"))
        self.dhcp.toggled.connect(self._dhcp_toggled)
        for w, ph in ((self.ip, tr("например 192.168.1.10")), (self.mask, "255.255.255.0"),
                      (self.gw, tr("например 192.168.1.1")), (self.dns, tr("необязательно"))):
            w.setPlaceholderText(ph)
        self.ip.editingFinished.connect(self._auto_mask)
        form.addRow(tr("Имя устройства:"), self.name)
        form.addRow("", self.dhcp)
        form.addRow(tr("IPv4-адрес:"), self.ip)
        form.addRow(tr("Маска подсети:"), self.mask)
        form.addRow(tr("Шлюз по умолчанию:"), self.gw)
        form.addRow(tr("DNS-сервер:"), self.dns)
        form.addRow("", self.port_on)
        lay.addWidget(box)
        box6 = QGroupBox("IPv6")
        f6 = QFormLayout(box6)
        self.slaac = QCheckBox(tr("Автонастройка (SLAAC)"))
        self.slaac.toggled.connect(self._slaac_toggled)
        self.ip6 = QLineEdit()
        self.ip6.setPlaceholderText(tr("например 2001:db8:1::10/64"))
        self.gw6 = QLineEdit()
        self.gw6.setPlaceholderText(tr("например 2001:db8:1::1"))
        self.ll6 = QLabel()
        self.ll6.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f6.addRow("", self.slaac)
        f6.addRow(tr("IPv6-адрес/префикс:"), self.ip6)
        f6.addRow(tr("Шлюз IPv6:"), self.gw6)
        f6.addRow("Link-local:", self.ll6)
        lay.addWidget(box6)
        self.wifi_ssid = self.wifi_key = self.wifi_state = None
        if getattr(dialog.device.nic, "is_wireless", False):
            boxw = QGroupBox("Wi-Fi")
            fw = QFormLayout(boxw)
            self.wifi_ssid = QLineEdit()
            self.wifi_ssid.setPlaceholderText(tr("имя сети, например NetEmu"))
            self.wifi_key = QLineEdit()
            self.wifi_key.setEchoMode(QLineEdit.Password)
            self.wifi_key.setPlaceholderText(tr("пусто для открытой сети"))
            self.wifi_state = QLabel()
            fw.addRow("SSID:", self.wifi_ssid)
            fw.addRow(tr("Пароль:"), self.wifi_key)
            fw.addRow(tr("Состояние:"), self.wifi_state)
            lay.addWidget(boxw)
        box8 = QGroupBox(tr("Аутентификация 802.1X"))
        f8 = QFormLayout(box8)
        self.x_on = QCheckBox(tr("Включить 802.1X (EAP)"))
        self.x_user = QLineEdit()
        self.x_pass = QLineEdit()
        self.x_pass.setEchoMode(QLineEdit.Password)
        self.x_state = QLabel()
        f8.addRow(self.x_on)
        f8.addRow(tr("Пользователь:"), self.x_user)
        f8.addRow(tr("Пароль:"), self.x_pass)
        f8.addRow(tr("Состояние:"), self.x_state)
        lay.addWidget(box8)
        self.mac = QLabel()
        self.mac.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.mac)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.apply_btn = QPushButton(tr("Применить"))
        self.apply_btn.clicked.connect(self.apply)
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)
        lay.addStretch(1)
        self.load()

    def _dhcp_toggled(self, on: bool) -> None:
        for w in (self.ip, self.mask, self.gw):
            w.setReadOnly(on)

    def _slaac_toggled(self, on: bool) -> None:
        for w in (self.ip6, self.gw6):
            w.setReadOnly(on)

    def _apply_v6(self) -> bool:
        from ipaddress import IPv6Address, IPv6Interface
        d = self.dialog.device
        if self.slaac.isChecked():
            if not d.nic.ipv6_auto:
                d.set_ipv6(None)
                d.set_gateway6(None)
                d.start_slaac()
            return True
        ip6, gw6 = self.ip6.text().strip(), self.gw6.text().strip()
        try:
            addr = IPv6Interface(ip6 if "/" in ip6 else ip6 + "/64") if ip6 else None
        except ValueError:
            return self.dialog.error(tr("Неверный IPv6-адрес."))
        try:
            gw = IPv6Address(gw6) if gw6 else None
        except ValueError:
            return self.dialog.error(tr("Неверный адрес шлюза IPv6."))
        d.nic.ipv6_auto = False
        d.ra_routers.clear()
        d.set_ipv6(str(addr) if addr else None)
        d.set_gateway6(str(gw) if gw else None)
        return True

    def _auto_mask(self) -> None:
        if is_valid_ip(self.ip.text().strip()) and not self.mask.text().strip():
            self.mask.setText(default_mask_for(self.ip.text().strip()))

    def load(self) -> None:
        d = self.dialog.device
        self.name.setText(d.name)
        self.ip.setText(str(d.nic.ip.ip) if d.nic.ip else "")
        self.mask.setText(str(d.nic.ip.netmask) if d.nic.ip else "")
        self.gw.setText(str(d.gateway) if d.gateway else "")
        self.dns.setText(str(d.dns) if d.dns else "")
        self.port_on.setChecked(d.nic.admin_up)
        self.dhcp.blockSignals(True)
        self.dhcp.setChecked(d.dhcp_enabled)
        self.dhcp.blockSignals(False)
        self._dhcp_toggled(d.dhcp_enabled)
        self.mac.setText(tr("MAC-адрес {port}: {mac}", port=d.nic.name, mac=d.nic.mac.cisco()))
        x = getattr(d, "dot1x", None) or {}
        self.x_on.setChecked(bool(x))
        self.x_user.setText(x.get("user", ""))
        self.x_pass.setText(x.get("password", ""))
        self.x_state.setText(tr({"off": "выключено", "connecting": "проверка…", "authenticated": "доступ разрешён",
                                 "failed": "отказано"}.get(getattr(d, "dot1x_state", "off"), "выключено")))
        self.slaac.blockSignals(True)
        self.slaac.setChecked(d.nic.ipv6_auto)
        self.slaac.blockSignals(False)
        self._slaac_toggled(d.nic.ipv6_auto)
        self.ip6.setText(str(d.nic.ipv6[0]) if d.nic.ipv6 else "")
        self.gw6.setText(str(d.gateway6) if d.gateway6 else "")
        ll = d.link_local(d.nic)
        self.ll6.setText(str(ll.ip) if ll else "")
        if self.wifi_ssid is not None:
            w = getattr(d, "wifi", None) or {}
            self.wifi_ssid.setText(w.get("ssid") or "")
            self.wifi_key.setText(w.get("key") or "")
            link = d.nic.link
            if link is not None and getattr(link, "wireless", False):
                self.wifi_state.setText(tr("подключено к {ap}", ap=link.other(d.nic).device.name))
            else:
                self.wifi_state.setText(tr("не подключено"))

    def apply(self) -> bool:
        d = self.dialog.device
        if not self._apply_v6():
            return False
        if self.wifi_ssid is not None:
            d.wifi = {"ssid": self.wifi_ssid.text().strip() or None, "key": self.wifi_key.text() or None}
        new_x = {"user": self.x_user.text().strip(), "password": self.x_pass.text()} if self.x_on.isChecked() else None
        if new_x != getattr(d, "dot1x", None):
            d.dot1x = new_x
            d.dot1x_state = "off"
            if new_x and d.nic.is_up:
                d._eapol_start()
        if self.dhcp.isChecked():
            dns = self.dns.text().strip()
            if dns and not is_valid_ip(dns):
                return self.dialog.error(tr("Неверный адрес DNS-сервера."))
            d.nic.set_admin(self.port_on.isChecked())
            if not d.dhcp_enabled or d.nic.ip is None or d.dhcp_info.get("apipa"):
                d.start_dhcp()
            name = self.name.text().strip()
            if name and name != d.name:
                self.dialog.topology.rename(d, name)
                self.dialog.update_title()
            d.engine.emit("device_changed", d)
            return True
        if d.dhcp_enabled:
            if d.dhcp_job is not None and not d.dhcp_job.done:
                d.dhcp_job.cancel()
            d.dhcp_enabled = False
        ip, mask = self.ip.text().strip(), self.mask.text().strip()
        gw, dns = self.gw.text().strip(), self.dns.text().strip()
        if ip and not is_valid_ip(ip):
            return self.dialog.error(tr("Неверный IPv4-адрес."))
        if ip and not mask:
            mask = default_mask_for(ip)
            self.mask.setText(mask)
        if ip and not is_valid_mask(mask):
            return self.dialog.error(tr("Неверная маска подсети."))
        if gw and not is_valid_ip(gw):
            return self.dialog.error(tr("Неверный адрес шлюза."))
        if dns and not is_valid_ip(dns):
            return self.dialog.error(tr("Неверный адрес DNS-сервера."))
        if ip and gw and IPv4Address(gw) not in make_interface(ip, mask).network:
            return self.dialog.error(tr("Шлюз должен находиться в той же подсети, что и IP-адрес."))
        d.set_ip(ip or None, mask or None)
        d.set_gateway(gw or None)
        d.dns = IPv4Address(dns) if dns else None
        d.nic.set_admin(self.port_on.isChecked())
        name = self.name.text().strip()
        if name and name != d.name:
            self.dialog.topology.rename(d, name)
            self.dialog.update_title()
        d.engine.emit("device_changed", d)
        return True


def vlan_text(p) -> str:
    if p.mode == "access":
        return str(p.pvid)
    if p.tagged is None:
        return "trunk"
    from ..core.cli.cisco import vlan_ranges
    return "trunk " + (vlan_ranges(p.tagged) or "none")


def parse_vlan_text(text: str):
    """'10' -> ("access", 10, None); 'trunk' -> ("trunk", None, None); 'trunk 10,20' -> ("trunk", None, {10, 20})."""
    from ..core.cli.parser import parse_vlan_list
    t = text.strip().lower()
    if t.isdigit() and 1 <= int(t) <= 4094:
        return ("access", int(t), None)
    if t == "trunk":
        return ("trunk", None, None)
    if t.startswith("trunk "):
        ids = parse_vlan_list(t[6:].replace(" ", ""))
        return ("trunk", None, ids) if ids else None
    return None


def apply_vlan(dev, p, spec) -> None:
    mode, vid, allowed = spec
    if mode == "access":
        dev.add_vlan(vid)
        p.mode, p.access_vlan, p.pvid, p.tagged = "access", vid, vid, None
    else:
        p.mode, p.pvid = "trunk", p.native_vlan
        p.tagged = None if allowed is None else frozenset(allowed)
        for v in allowed or ():
            dev.add_vlan(v)
    for key in [k for k, e in dev.mac_table.items() if e.port is p]:
        del dev.mac_table[key]


class NetConfig(QWidget):
    """Hostname, interfaces and static routes of routers / switches."""

    def __init__(self, dialog: "DeviceDialog"):
        super().__init__()
        self.dialog = dialog
        dev = dialog.device
        self.is_router = dev.category == "router"
        lay = QVBoxLayout(self)

        top = QFormLayout()
        self.name = QLineEdit()
        self.hostname = QLineEdit()
        top.addRow(tr("Имя на схеме:"), self.name)
        top.addRow("Hostname:", self.hostname)
        lay.addLayout(top)

        lay.addWidget(QLabel(tr("Интерфейсы (двойной щелчок — правка):")))
        # switches: IP/mask are edited on VLAN interfaces (SVI), the VLAN cell on switchports
        cols = [tr(c) for c in ["Интерфейс", "Вкл", "IP-адрес", "Маска", "VLAN", "Описание", "Статус"]]
        # switch VLAN cell: "10" = access VLAN 10, "trunk" = all VLANs, "trunk 10,20" = allowed list
        self.vlan_editable = not self.is_router and dev.vendor != "mikrotik"
        self.ifaces = QTableWidget(0, len(cols))
        self.ifaces.setHorizontalHeaderLabels(cols)
        self.ifaces.verticalHeader().setVisible(False)
        self.ifaces.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.ifaces.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.ifaces, 3)

        if True:
            lay.addWidget(QLabel(tr("Статические маршруты:")))
            self.routes = QTableWidget(0, 4)
            self.routes.setHorizontalHeaderLabels([tr("Сеть"), tr("Маска"), tr("Следующий переход / интерфейс"),
                                                   tr("Дистанция")])
            self.routes.verticalHeader().setVisible(False)
            self.routes.horizontalHeader().setStretchLastSection(True)
            self.routes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            lay.addWidget(self.routes, 2)
            row = QHBoxLayout()
            self.r_net = QLineEdit()
            self.r_net.setPlaceholderText(tr("сеть, напр. 0.0.0.0"))
            self.r_mask = QLineEdit()
            self.r_mask.setPlaceholderText(tr("маска, напр. 0.0.0.0"))
            self.r_nh = QLineEdit()
            self.r_nh.setPlaceholderText(tr("следующий переход"))
            add = QPushButton(tr("Добавить"))
            add.clicked.connect(self.add_route)
            rem = QPushButton(tr("Удалить выбранный"))
            rem.clicked.connect(self.remove_route)
            for w in (self.r_net, self.r_mask, self.r_nh, add, rem):
                row.addWidget(w)
            lay.addLayout(row)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.apply_btn = QPushButton(tr("Применить"))
        self.apply_btn.clicked.connect(self.apply)
        btns.addWidget(self.apply_btn)
        lay.addLayout(btns)
        self.load()

    def _cell(self, text: str, editable: bool = True) -> QTableWidgetItem:
        it = QTableWidgetItem(text)
        if not editable:
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        return it

    def load(self) -> None:
        d = self.dialog.device
        self.name.setText(d.name)
        self._loaded_name = d.name
        self.hostname.setText(d.hostname)
        self.ifaces.setRowCount(0)
        for p in d.ports.values():
            r = self.ifaces.rowCount()
            self.ifaces.insertRow(r)
            self.ifaces.setItem(r, 0, self._cell(p.name, False))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked if p.admin_up else Qt.Unchecked)
            self.ifaces.setItem(r, 1, chk)
            status = "up" if p.is_up else (tr("выкл.") if not p.admin_up else "down")
            if p.link:
                status += f" → {p.peer.device.name} {p.peer.short}"
            l3 = self.is_router or p.is_svi or p.is_loopback
            if l3:
                vl = "" if not (p.is_sub or p.is_svi) else (str(p.vlan) if p.vlan is not None else "?")
                if p.is_svi:
                    vl = f"SVI {p.vlan}"
                self.ifaces.setItem(r, 4, self._cell(vl, False))
            else:
                self.ifaces.setItem(r, 4, self._cell(vlan_text(p), self.vlan_editable))
            self.ifaces.setItem(r, 2, self._cell(str(p.ip.ip) if p.ip else "", l3))
            self.ifaces.setItem(r, 3, self._cell(str(p.ip.netmask) if p.ip else "", l3))
            self.ifaces.setItem(r, 5, self._cell(p.description))
            self.ifaces.setItem(r, 6, self._cell(status, False))
        if True:
            self.routes.setRowCount(0)
            for s in d.static_routes:
                r = self.routes.rowCount()
                self.routes.insertRow(r)
                self.routes.setItem(r, 0, self._cell(str(s.network.network_address), False))
                self.routes.setItem(r, 1, self._cell(str(s.network.netmask), False))
                self.routes.setItem(r, 2, self._cell(str(s.next_hop) if s.next_hop else s.interface, False))
                self.routes.setItem(r, 3, self._cell(str(s.distance), False))

    def apply(self) -> bool:
        d = self.dialog.device
        plan = []
        for r, p in enumerate(d.ports.values()):
            up = self.ifaces.item(r, 1).checkState() == Qt.Checked
            if self.is_router or p.is_svi or p.is_loopback:
                ip = self.ifaces.item(r, 2).text().strip()
                mask = self.ifaces.item(r, 3).text().strip()
                desc = self.ifaces.item(r, 5).text().strip()
                if ip and not is_valid_ip(ip):
                    return self.dialog.error(tr("{port}: неверный IP-адрес «{ip}».", port=p.name, ip=ip))
                if ip and not mask:
                    mask = default_mask_for(ip)
                if ip and not is_valid_mask(mask):
                    return self.dialog.error(tr("{port}: неверная маска «{mask}».", port=p.name, mask=mask))
                plan.append((p, up, make_interface(ip, mask) if ip else None, desc))
            else:
                vlan = None
                if self.vlan_editable:
                    vlan = parse_vlan_text(self.ifaces.item(r, 4).text())
                    if vlan is None:
                        return self.dialog.error(tr("{port}: неверное значение VLAN «{v}». Примеры: 10, trunk, "
                                                    "trunk 10,20.", port=p.name, v=self.ifaces.item(r, 4).text()))
                plan.append((p, up, vlan, self.ifaces.item(r, 5).text().strip()))
        if True:
            nets = [(p, i) for p, _, i, _ in plan if i is not None and (self.is_router or p.is_svi or p.is_loopback)]
            for k, (p, i) in enumerate(nets):
                for q, j in nets[k + 1:]:
                    if i.network.overlaps(j.network):
                        return self.dialog.error(tr("Подсети {a} и {b} пересекаются.", a=p.name, b=q.name))
        for p, up, iface, desc in plan:
            l3 = self.is_router or p.is_svi or p.is_loopback
            if l3 and p.ip != iface:
                p.ip = iface
                d.arp.flush_port(p)
            if not l3 and iface is not None:
                apply_vlan(d, p, iface)
            p.description = desc
            p.set_admin(up)
        host = self.hostname.text().strip()
        name = self.name.text().strip()
        if host and host != d.hostname:
            typed_label = name != self._loaded_name
            d.hostname = host
            d.engine.emit("device_changed", d)   # the label follows the new hostname...
            if not typed_label:                   # ...unless a label was typed explicitly
                name = d.name
        if name and name != d.name:
            self.dialog.topology.rename(d, name)
            self.dialog.update_title()
        d.engine.emit("device_changed", d)
        self.load()
        return True

    def add_route(self) -> None:
        net, mask, nh = self.r_net.text().strip(), self.r_mask.text().strip(), self.r_nh.text().strip()
        if not is_valid_ip(net) or not is_valid_mask(mask):
            self.dialog.error(tr("Укажите сеть и маску в формате A.B.C.D."))
            return
        try:
            network = IPv4Network(f"{net}/{mask}")
        except ValueError:
            self.dialog.error(tr("Адрес сети не соответствует маске."))
            return
        d = self.dialog.device
        if is_valid_ip(nh):
            d.add_static_route(network, IPv4Address(nh), None, self._default_distance())
        elif d.get_port(nh) is not None:
            d.add_static_route(network, None, d.get_port(nh).name, self._default_distance())
        else:
            self.dialog.error(tr("Укажите IP следующего перехода или имя интерфейса."))
            return
        self.r_net.clear()
        self.r_mask.clear()
        self.r_nh.clear()
        self.load()

    def _default_distance(self) -> int:
        return {"huawei": 60, "juniper": 5}.get(self.dialog.device.vendor, 1)

    def remove_route(self) -> None:
        rows = sorted({i.row() for i in self.routes.selectedIndexes()}, reverse=True)
        d = self.dialog.device
        for r in rows:
            if r < len(d.static_routes):
                del d.static_routes[r]
        self.load()


def terminal_page(term) -> QWidget:
    """The terminal with a one-line hint of what may come next (and Ctrl+F: command search)."""
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    v.addWidget(term, 1)
    hint = QLabel(tr("Подсказка появится при наборе. Ctrl+F — поиск команды, ? — справка, Tab — дополнение."))
    hint.setStyleSheet("color: #78909c; padding: 2px 6px;")
    hint.setWordWrap(True)
    term.hintChanged.connect(lambda text: hint.setText(tr("Дальше: {items}", items=text) if text else ""))
    v.addWidget(hint)
    return page


class DeviceDialog(QDialog):
    def __init__(self, device, topology, parent=None):
        super().__init__(parent)
        self.device = device
        self.topology = topology
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(760, 560)
        self.update_title()
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.config = None
        self.terminal = None
        cat = device.category
        if cat in ("pc", "server"):
            from .services_panel import ServicesPanel, WebBrowser
            self.config = HostConfig(self)
            self.tabs.addTab(self.config, tr("Настройка"))
            self.terminal = TerminalWidget(device.create_cli())
            self.tabs.addTab(terminal_page(self.terminal), tr("Терминал (Linux)") if getattr(device, "os", "") == "linux"
                             else tr("Командная строка"))
            self.browser = WebBrowser(self)
            self.tabs.addTab(self.browser, tr("Браузер"))
            from .mail_panel import MailPanel
            self.mail = MailPanel(self)
            self.tabs.addTab(self.mail, tr("Почта"))
            if cat == "server":
                self.services = ServicesPanel(self)
                self.tabs.addTab(self.services, tr("Службы"))
        elif cat in ("router", "switch"):
            self.config = NetConfig(self)
            self.tabs.addTab(self.config, tr("Настройка"))
            self.terminal = TerminalWidget(device.create_cli())
            self.tabs.addTab(terminal_page(self.terminal), tr(CLI_TITLES.get(device.vendor, "CLI")))
        elif cat == "cloud":
            self.tabs.addTab(cloud_info(device), tr("Интернет"))
        elif cat == "iot":
            self.config = HostConfig(self)
            self.tabs.addTab(self.config, tr("Настройка"))
            from .iot_panel import IotPanel
            self.iot = IotPanel(self)
            self.tabs.addTab(self.iot, tr("Устройство IoT"))
        elif cat == "wlc":
            self.config = HostConfig(self)
            self.tabs.addTab(self.config, tr("Настройка"))
            from .wlc_panel import WlcPanel
            self.wlc = WlcPanel(self)
            self.tabs.addTab(self.wlc, tr("Контроллер"))
        elif cat == "phone":
            from .phone_panel import PhonePanel
            self.phone = PhonePanel(self)
            self.tabs.addTab(self.phone, tr("Телефон"))
        elif cat == "wireless" and device.model_key == "lap-1142":
            c = device.controller
            info = QLabel(tr("Лёгкая точка доступа: настройки Wi-Fi приходят от контроллера.") + "\n\n" +
                          (tr("Контроллер: {wlc}. SSID: {ssid}.", wlc=c[1], ssid=device.wifi.get("ssid") or "—") if c
                           else tr("Контроллер не найден: радио выключено.")))
            info.setWordWrap(True)
            self.tabs.addTab(info, tr("Устройство"))
        elif cat == "wireless":
            self.config = ApConfig(self)
            self.tabs.addTab(self.config, tr("Настройка"))
        else:
            info = QLabel(tr("{model}: {n} портов.\nКонцентратор повторяет каждый кадр во все порты и не настраивается.",
                             model=device.model, n=len(device.ports)))
            info.setAlignment(Qt.AlignCenter)
            self.tabs.addTab(info, tr("Устройство"))
        self.tabs.currentChanged.connect(self._tab_changed)
        if self.terminal is not None and getattr(self.terminal.session, "shows_console", False):
            engine, term, dev = device.engine, self.terminal, device

            def on_console(d, message):
                if d is not dev:
                    return
                try:
                    term.console_message(message)
                except RuntimeError:          # the window was already destroyed
                    engine.unsubscribe("console", on_console)
            engine.subscribe("console", on_console)
            self.destroyed.connect(lambda *_: engine.unsubscribe("console", on_console))
            self._console_sub = on_console
        if self.terminal is not None and cat in ("router", "switch"):
            self.tabs.setCurrentWidget(self.terminal.parentWidget())

    help_hook = None   # set by the main window: opens this vendor's help page

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_F1 and self.help_hook is not None:
            self.help_hook()
            return
        super().keyPressEvent(e)

    def update_title(self) -> None:
        self.setWindowTitle(f"{self.device.name} — {self.device.model}")

    def _tab_changed(self, index: int) -> None:
        w = self.tabs.widget(index)
        if w is self.config and self.config is not None:
            self.config.load()
        if self.terminal is not None and w is self.terminal.parentWidget():
            self.terminal.setFocus()

    def refresh(self) -> None:
        if self.config is not None and self.tabs.currentWidget() is self.config:
            self.config.load()

    def error(self, text: str) -> bool:
        QMessageBox.warning(self, tr("Ошибка настройки"), text)
        return False
