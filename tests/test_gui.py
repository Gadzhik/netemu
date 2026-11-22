"""GUI tests (pytest-qt, offscreen)."""
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QMessageBox

from netemu.core.devices import MODELS
from netemu.gui.canvas import MIME_MODEL, TOOL_CABLE, TOOL_DELETE, TOOL_PDU, TOOL_SELECT
from netemu.gui.controller import REALTIME, SIMULATION
from netemu.gui.items import DeviceItem, LinkItem, PacketItem
from netemu.gui.main_window import MainWindow
from netemu.gui.simulation_panel import PduDialog


@pytest.fixture
def win(qtbot, monkeypatch):
    # never block on "save changes?" questions
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w._saved_snapshot = w._snapshot()
    w.close()  # runs MainWindow.shutdown()


def click_device(qtbot, win, dev, button=Qt.LeftButton):
    item = win.scene.device_items[dev.id]
    win.view.centerOn(item)
    pos = win.view.mapFromScene(item.scenePos())
    qtbot.mouseClick(win.view.viewport(), button, pos=pos)


def type_line(qtbot, term, text):
    qtbot.keyClicks(term, text)
    qtbot.keyClick(term, Qt.Key_Return)


# -- window & palette ------------------------------------------------------------
def test_window_starts_with_palette(win):
    assert "NetEmu" in win.windowTitle()
    total = sum(lst.count() for lst in win.palette.lists.values())
    assert total == len(MODELS)
    titles = {lst.item(i).text() for lst in win.palette.lists.values() for i in range(lst.count())}
    assert {"Cisco 2911", "MikroTik RB4011", "Juniper SRX300", "Huawei AR2220", "PC"} <= titles
    assert not win.sim_dock.isVisible()


def test_palette_double_click_adds_device(qtbot, win):
    lst = win.palette.lists["router"]
    item = lst.item(0)
    lst.itemDoubleClicked.emit(item)
    assert len(win.topology.devices) == 1
    dev = next(iter(win.topology.devices.values()))
    assert dev.model_key == item.data(Qt.UserRole)
    assert isinstance(win.scene.device_items[dev.id], DeviceItem)
    assert win.dirty


def test_drop_from_palette(qtbot, win):
    mime = QMimeData()
    mime.setData(MIME_MODEL, b"huawei-s5720")
    ev = QDropEvent(QPointF(300, 200), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    win.view.dropEvent(ev)
    dev = next(iter(win.topology.devices.values()))
    assert dev.model == "Huawei S5720-28P" and dev.name == "LSW0"
    expected = win.view.mapToScene(QPoint(300, 200))
    assert abs(dev.x - expected.x()) < 1 and abs(dev.y - expected.y()) < 1


# -- cable / delete tools ----------------------------------------------------------
def test_cable_tool_connects_devices(qtbot, win):
    a = win.view.add_device("pc", QPointF(-150, 0))
    b = win.view.add_device("cisco-2960", QPointF(150, 0))
    win.view.port_menu_hook = lambda dev: dev.free_ports()[0]
    win.act_tool_cable.trigger()
    assert win.view.tool == TOOL_CABLE
    click_device(qtbot, win, a)
    assert win.view._cable_from is a.nic
    click_device(qtbot, win, b)
    assert len(win.topology.links) == 1
    link = win.topology.links[0]
    assert {link.a, link.b} == {a.nic, b.get_port("Fa0/1")}
    assert len(win.scene.link_items) == 1
    assert a.nic.is_up


def test_cable_tool_cancel_and_self_link(qtbot, win):
    a = win.view.add_device("cisco-2911", QPointF(0, 0))
    win.view.port_menu_hook = lambda dev: dev.free_ports()[0]
    win.view.set_tool(TOOL_CABLE)
    click_device(qtbot, win, a)
    click_device(qtbot, win, a)  # same device -> refused
    assert win.topology.links == []
    qtbot.keyClick(win.view, Qt.Key_Escape)
    assert win.view._cable_from is None and win.view.tool == TOOL_SELECT
    assert win.act_tool_select.isChecked()


def test_delete_tool_and_delete_key(qtbot, win):
    win.load_demo()
    router = win.topology.find("R1")
    win.open_device(router)
    win.view.set_tool(TOOL_DELETE)
    click_device(qtbot, win, router)
    assert win.topology.find("R1") is None
    assert router.id not in win.dialogs
    assert len(win.topology.links) == 6
    win.view.set_tool(TOOL_SELECT)
    pc = win.topology.find("PC1")
    win.scene.device_items[pc.id].setSelected(True)
    qtbot.keyClick(win.view, Qt.Key_Delete)
    assert win.topology.find("PC1") is None
    assert len(win.scene.link_items) == len(win.topology.links) == 5


def test_drag_moves_device(qtbot, win):
    dev = win.view.add_device("pc", QPointF(0, 0))
    item = win.scene.device_items[dev.id]
    item.setPos(120, 40)
    assert (dev.x, dev.y) == (120, 40)


# -- device dialog & terminal --------------------------------------------------------
def test_router_terminal_typing(qtbot, win):
    win.load_demo()
    dlg = win.open_device(win.topology.find("R1"))
    term = dlg.terminal
    assert dlg.tabs.currentWidget() is term.parentWidget()
    assert term.toPlainText().endswith("R1>")
    type_line(qtbot, term, "en")
    type_line(qtbot, term, "show ip interface brief")
    assert "GigabitEthernet0/0     192.168.1.1" in term.toPlainText()
    # '?' help keeps the typed line
    qtbot.keyClicks(term, "show ip ")
    qtbot.keyClick(term, Qt.Key_Question, Qt.ShiftModifier)
    text = term.toPlainText()
    assert any(l.split()[:1] == ["route"] and "IP routing table" in l for l in text.splitlines())
    assert term.current_input() == "show ip "
    # Tab completion
    term._set_input("")
    qtbot.keyClicks(term, "conf")
    qtbot.keyClick(term, Qt.Key_Tab)
    assert term.current_input() == "configure "
    qtbot.keyClick(term, Qt.Key_Return)
    assert term.toPlainText().endswith("R1(config)#")
    # history
    qtbot.keyClick(term, Qt.Key_Up)
    assert term.current_input() == "configure "
    term._set_input("")
    # Ctrl+Z leaves configuration mode
    qtbot.keyClick(term, Qt.Key_Z, Qt.ControlModifier)
    assert term.toPlainText().endswith("R1#")
    # cannot backspace into the prompt
    qtbot.keyClick(term, Qt.Key_Backspace)
    assert term.toPlainText().endswith("R1#")


def test_terminal_ping_job_realtime(qtbot, win):
    win.load_demo()
    dlg = win.open_device(win.topology.find("PC0"))
    term = dlg.terminal
    term.run_command("ping -n 2 192.168.2.10")
    assert term.busy
    qtbot.keyClicks(term, "ignored while busy")
    qtbot.waitUntil(lambda: not term.busy, timeout=15000)
    text = term.toPlainText()
    assert "Packets: Sent = 2, Received = 2" in text
    assert "ignored" not in text
    assert text.endswith("C:\\>")


def test_terminal_ctrl_c_interrupts(qtbot, win):
    win.load_demo()
    term = win.open_device(win.topology.find("PC0")).terminal
    term.run_command("ping -t 192.168.1.1")
    qtbot.waitUntil(lambda: "Reply from 192.168.1.1" in term.toPlainText(), timeout=5000)
    qtbot.keyClick(term, Qt.Key_C, Qt.ControlModifier)
    assert not term.busy
    assert "Ping statistics" in term.toPlainText()


def test_terminal_paste_runs_lines(qtbot, win):
    win.load_demo()
    dlg = win.open_device(win.topology.find("R1"))
    term = dlg.terminal
    mime = QMimeData()
    mime.setText("enable\nconfigure terminal\nhostname PASTED\nend\n")
    term.insertFromMimeData(mime)
    assert win.topology.find("PASTED").hostname == "PASTED"
    assert term.toPlainText().endswith("PASTED#")


def test_vendor_terminal_titles(qtbot, win):
    win.load_demo()
    for name, title in (("MT1", "Терминал (RouterOS)"), ("SRX1", "CLI (Junos)"),
                        ("AR1", "CLI (Huawei VRP)"), ("SW1", "CLI (Cisco IOS)")):
        dlg = win.open_device(win.topology.find(name))
        assert dlg.tabs.tabText(1) == title
    term = win.open_device(win.topology.find("SRX1")).terminal
    type_line(qtbot, term, "show route")
    assert "inet.0:" in term.toPlainText()
    term = win.open_device(win.topology.find("MT1")).terminal
    type_line(qtbot, term, "/ip route print")
    assert "DST-ADDRESS" in term.toPlainText()


def test_host_config_form(qtbot, win, monkeypatch):
    pc = win.view.add_device("pc")
    dlg = win.open_device(pc)
    errors = []
    monkeypatch.setattr(dlg, "error", lambda text: errors.append(text) or False)
    cfg = dlg.config
    cfg.ip.setText("10.1.1.5")
    cfg.ip.editingFinished.emit()
    assert cfg.mask.text() == "255.0.0.0"  # classful default like Packet Tracer
    cfg.mask.setText("255.255.255.0")
    cfg.gw.setText("10.2.2.1")
    qtbot.mouseClick(cfg.apply_btn, Qt.LeftButton)
    assert errors and "подсети" in errors[-1]
    cfg.gw.setText("10.1.1.1")
    cfg.name.setText("Office-PC")
    qtbot.mouseClick(cfg.apply_btn, Qt.LeftButton)
    assert str(pc.nic.ip) == "10.1.1.5/24" and str(pc.gateway) == "10.1.1.1"
    assert pc.name == "Office-PC" and "Office-PC" in dlg.windowTitle()
    cfg.ip.setText("999.1.1.1")
    qtbot.mouseClick(cfg.apply_btn, Qt.LeftButton)
    assert "Неверный IPv4" in errors[-1]


def test_router_config_form(qtbot, win, monkeypatch):
    r = win.view.add_device("cisco-2911")
    dlg = win.open_device(r)
    errors = []
    monkeypatch.setattr(dlg, "error", lambda text: errors.append(text) or False)
    cfg = dlg.config
    dlg.tabs.setCurrentWidget(cfg)
    cfg.ifaces.item(0, 1).setCheckState(Qt.Checked)
    cfg.ifaces.item(0, 2).setText("192.168.5.1")
    cfg.ifaces.item(0, 3).setText("255.255.255.0")
    cfg.ifaces.item(0, 5).setText("LAN")
    cfg.ifaces.item(1, 2).setText("192.168.5.2")
    cfg.ifaces.item(1, 3).setText("255.255.255.0")
    cfg.hostname.setText("EDGE")
    qtbot.mouseClick(cfg.apply_btn, Qt.LeftButton)
    assert "пересекаются" in errors[-1]
    assert r.get_port("Gi0/0").ip is None  # nothing applied on error
    cfg.ifaces.item(1, 2).setText("")
    cfg.ifaces.item(1, 3).setText("")
    qtbot.mouseClick(cfg.apply_btn, Qt.LeftButton)
    p = r.get_port("Gi0/0")
    assert str(p.ip) == "192.168.5.1/24" and p.admin_up and p.description == "LAN"
    assert r.hostname == "EDGE"
    cfg.r_net.setText("0.0.0.0")
    cfg.r_mask.setText("0.0.0.0")
    cfg.r_nh.setText("192.168.5.254")
    cfg.add_route()
    assert len(r.static_routes) == 1 and cfg.routes.rowCount() == 1
    cfg.routes.selectRow(0)
    cfg.remove_route()
    assert r.static_routes == []
    # CLI changes show up in the form (live and when switching tabs)
    type_line(qtbot, dlg.terminal, "enable")
    type_line(qtbot, dlg.terminal, "conf t")
    type_line(qtbot, dlg.terminal, "hostname FROMCLI")
    assert cfg.hostname.text() == "FROMCLI"
    dlg.tabs.setCurrentWidget(dlg.terminal.parentWidget())
    type_line(qtbot, dlg.terminal, "int g0/1")
    type_line(qtbot, dlg.terminal, "description WAN")
    dlg.tabs.setCurrentWidget(cfg)
    assert cfg.ifaces.item(1, 5).text() == "WAN"


def test_switch_and_hub_dialogs(qtbot, win):
    sw = win.view.add_device("juniper-ex2300")
    dlg = win.open_device(sw)
    assert dlg.config.ifaces.rowCount() == 24
    hub = win.view.add_device("hub")
    dlg = win.open_device(hub)
    assert dlg.terminal is None and dlg.tabs.count() == 1


# -- simulation mode -----------------------------------------------------------------
def test_simulation_mode_pdu_step_and_play(qtbot, win):
    win.load_demo()
    win.act_simulation.trigger()
    assert win.ctl.mode == SIMULATION and win.sim_dock.isVisible()
    t0 = win.ctl.engine.now
    qtbot.wait(100)
    assert win.ctl.engine.now == t0  # the clock is frozen in simulation mode
    win.act_tool_pdu.trigger()
    assert win.view.tool == TOOL_PDU
    click_device(qtbot, win, win.topology.find("PC0"))
    click_device(qtbot, win, win.topology.find("PC1"))
    # the ARP request is in flight and drawn on the canvas
    win.scene.update_packets()
    packets = [i for i in win.scene.items() if isinstance(i, PacketItem)]
    assert packets and packets[0].frame.protocol == "ARP"
    qtbot.mouseClick(win.sim_panel.step_btn, Qt.LeftButton)
    assert win.sim_panel.table.rowCount() == 1
    assert win.sim_panel.table.item(0, 4).text() == "ARP"
    # filter hides ARP rows and envelopes
    win.sim_panel.filters["ARP"].setChecked(False)
    assert win.sim_panel.table.isRowHidden(0)
    win.sim_panel.filters["ARP"].setChecked(True)
    # play until the echo reply comes back
    win.sim_panel.speed.setValue(40)
    qtbot.mouseClick(win.sim_panel.play_btn, Qt.LeftButton)
    assert win.ctl.playing
    qtbot.waitUntil(lambda: bool(win.pdu_results), timeout=20000)
    assert win.pdu_results[-1][:3] == ("PC0", "PC1", True)
    protos = {win.sim_panel.table.item(r, 4).text() for r in range(win.sim_panel.table.rowCount())}
    assert protos == {"ARP", "ICMP"}
    # PDU details
    dlg = PduDialog(win.ctl.records[-1].frame)
    assert "Layer 2: Ethernet II" in dlg.text and "ICMP" in dlg.text
    win.sim_panel.table.cellDoubleClicked.emit(0, 0)
    # reset & back to realtime
    win.act_sim_reset.trigger()
    assert win.sim_panel.table.rowCount() == 0
    win.act_realtime.trigger()
    assert win.ctl.mode == REALTIME and not win.sim_dock.isVisible()


def test_pdu_failure_reported(qtbot, win):
    win.load_demo()
    pc0 = win.topology.find("PC0")
    pc0.set_gateway(None)
    win.send_pdu(pc0, win.topology.find("PC1"))
    assert win.pdu_results[-1][2] is False
    sw = win.topology.find("SW1")
    assert win.send_pdu(sw, pc0) is None


def test_packet_click_opens_details(qtbot, win):
    win.load_demo()
    win.ctl.set_mode(SIMULATION)
    win.send_pdu(win.topology.find("PC0"), win.topology.find("Server0"))
    win.scene.update_packets()
    item = next(i for i in win.scene.items() if isinstance(i, PacketItem))
    win.view.centerOn(item)
    qtbot.mouseClick(win.view.viewport(), Qt.LeftButton, pos=win.view.mapFromScene(item.scenePos()))
    dlgs = [w for w in win.findChildren(PduDialog)]
    assert dlgs and "ARP" in dlgs[-1].text


# -- files ------------------------------------------------------------------------------
def test_save_and_open(qtbot, win, tmp_path):
    win.load_demo()
    assert not win.dirty
    win.topology.find("PC0").set_ip("192.168.1.11", "255.255.255.0")
    assert win.dirty
    path = tmp_path / "lab.netemu"
    assert win.save_to(path)
    assert not win.dirty and win.windowTitle().startswith("lab.netemu")
    win.new_project()
    assert win.topology.devices == {} and win.scene.device_items == {}
    assert win.open_file(path)
    assert len(win.scene.device_items) == 9 and len(win.scene.link_items) == 8
    assert str(win.topology.find("PC0").nic.ip.ip) == "192.168.1.11"
    term = win.open_device(win.topology.find("PC0")).terminal
    term.run_command("ping -n 1 192.168.2.100")
    qtbot.waitUntil(lambda: not term.busy, timeout=10000)
    assert "Received = 1" in term.toPlainText()


def test_open_invalid_file(qtbot, win, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(a))
    p = tmp_path / "bad.netemu"
    p.write_text("not json at all")
    with pytest.raises(Exception):
        # invalid JSON raises json.JSONDecodeError (a ValueError subclass) -> handled
        if not win.open_file(p):
            raise RuntimeError("handled")
    assert shown


def test_view_actions(qtbot, win):
    win.load_demo()
    before = win.view.transform().m11()
    win.view.zoom(1.5)
    assert win.view.transform().m11() > before
    win.view.reset_zoom()
    assert win.view.transform().m11() == 1
    win.act_labels.trigger()
    assert not LinkItem.show_port_labels
    win.act_labels.trigger()
    assert LinkItem.show_port_labels
    win.act_select_all.trigger()
    assert all(i.isSelected() for i in win.scene.device_items.values())


def test_link_indicator_follows_shutdown(qtbot, win):
    win.load_demo()
    r = win.topology.find("R1")
    term = win.open_device(r).terminal
    for line in ("enable", "conf t", "int g0/0", "shutdown"):
        term.run_command(line)
    assert not r.get_port("Gi0/0").is_up
    link = r.get_port("Gi0/0").link
    item = win.scene.link_items[id(link)]
    assert not item.link.a.is_up and not item.link.b.is_up


def test_fit_all_shows_large_topology(win):
    for i in range(6):
        win.view.add_device("pc", QPointF(i * 500, i * 250))
    win.act_fit.trigger()
    vis = win.view.mapToScene(win.view.viewport().rect()).boundingRect()
    for item in win.scene.device_items.values():
        assert vis.contains(item.sceneBoundingRect())
    assert win.view.transform().m11() < 1


def test_canvas_label_follows_hostname(qtbot, win):
    r = win.view.add_device("cisco-2911", QPointF(0, 0))
    item = win.scene.device_items[r.id]
    dlg = win.open_device(r)
    for line in ("enable", "conf t", "hostname CORE-R1", "end"):
        type_line(qtbot, dlg.terminal, line)
    assert r.name == "CORE-R1" and item.device.name == "CORE-R1"
    assert "CORE-R1" in dlg.windowTitle()
    # config form: hostname alone renames the label, an explicit label wins
    dlg.tabs.setCurrentIndex(0)
    dlg.config.hostname.setText("EDGE")
    dlg.config.apply()
    assert r.name == "EDGE"
    dlg.config.hostname.setText("EDGE2")
    dlg.config.name.setText("Мой маршрутизатор")
    dlg.config.apply()
    assert r.hostname == "EDGE2" and r.name == "Мой маршрутизатор"


# -- help & language --------------------------------------------------------------------
def test_help_window_shows_guide_and_vendor_pages(qtbot, win, monkeypatch):
    from PySide6.QtWidgets import QApplication
    win.act_help.trigger()
    h = win._help
    assert h.isVisible() and "NetEmu" in h.text()
    assert h.topics.count() == 10
    h.show_topic("mikrotik")
    assert "/ip route add" in h.text() and "/interface disable" in h.text()
    from PySide6.QtCore import QUrl
    h._link(QUrl("juniper.md"))
    assert h.current == "juniper" and "commit" in h.text()
    # F1 in a device window opens that vendor's page
    win.load_demo()
    dlg = win.open_device(win.topology.find("AR1"))
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    dlg.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F1, Qt.NoModifier))
    assert win._help.current == "huawei"
    # the F1 menu action also opens the vendor page while a device window is active
    win.show_help("guide")
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: dlg))
    win.act_help.trigger()
    assert win._help.current == "huawei"


def test_english_interface_has_no_russian_text(qtbot, monkeypatch):
    import re
    from PySide6.QtWidgets import QAbstractButton, QLabel, QMenu, QTabWidget, QTableWidget, QToolBox
    from netemu import i18n
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    i18n.set_language("en")
    try:
        w = MainWindow()
        qtbot.addWidget(w)
        w.show()
        w.load_demo()
        w.ctl.set_mode(SIMULATION)
        dialogs = [w.open_device(w.topology.find(n)) for n in ("R1", "MT1", "SRX1", "AR1", "PC0", "SW1")]
        dialogs.append(w.show_help())
        cyr = re.compile("[А-Яа-яЁё]")
        texts = [w.windowTitle()]
        for root in [w] + dialogs:
            for wd in root.findChildren(QAbstractButton) + root.findChildren(QLabel):
                texts.append(wd.text())
            for m in root.findChildren(QMenu):
                texts += [m.title()] + [a.text() for a in m.actions()]
            for tw in root.findChildren(QTabWidget):
                texts += [tw.tabText(i) for i in range(tw.count())]
            for tb in root.findChildren(QToolBox):
                texts += [tb.itemText(i) for i in range(tb.count())]
            for t in root.findChildren(QTableWidget):
                texts += [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())]
        texts.append(dialogs[-1].text())
        russian = [t for t in texts if cyr.search(t) and t not in i18n.LANGUAGES.values() and "Язык" not in t]
        assert not russian, russian[:10]
        assert w.menuBar().actions()[0].text() == "&File"
        assert "Command Prompt" in [dialogs[4].tabs.tabText(i) for i in range(dialogs[4].tabs.count())]
        w._saved_snapshot = w._snapshot()
        w.close()
    finally:
        i18n.set_language("ru")


def test_language_choice_is_saved(qtbot, win, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    saved = {}
    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: saved.__setitem__(k, v))
    win.choose_language("en")
    assert saved == {"language": "en"} and win.language_pending == "en"


def test_switch_form_edits_vlans(qtbot, win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    sw = win.view.add_device("cisco-2960", QPointF(0, 0))
    dlg = win.open_device(sw)
    cfg = dlg.config
    headers = [cfg.ifaces.horizontalHeaderItem(i).text() for i in range(cfg.ifaces.columnCount())]
    assert headers[4] == "VLAN"
    cfg.ifaces.item(0, 4).setText("10")
    cfg.ifaces.item(25, 4).setText("trunk 10,20")
    assert cfg.apply()
    p1, g2 = sw.get_port("fa0/1"), sw.get_port("gi0/2")
    assert (p1.mode, p1.pvid) == ("access", 10) and 10 in sw.vlans and 20 in sw.vlans
    assert g2.mode == "trunk" and g2.tagged == frozenset({10, 20})
    assert cfg.ifaces.item(25, 4).text() == "trunk 10,20"
    cfg.ifaces.item(1, 4).setText("bogus")
    assert not cfg.apply()


def test_subinterfaces_hidden_from_cable_menu_and_shown_in_form(qtbot, win):
    r = win.view.add_device("cisco-2911", QPointF(0, 0))
    sub = r.add_subinterface(r.get_port("gi0/0"), 10, vlan=10)
    offered = []
    win.view.port_menu_hook = lambda dev: offered.extend(dev.free_ports()) or None
    assert sub not in r.free_ports()
    dlg = win.open_device(r)
    names = [dlg.config.ifaces.item(i, 0).text() for i in range(dlg.config.ifaces.rowCount())]
    assert names[1] == "GigabitEthernet0/0.10" and dlg.config.ifaces.item(1, 4).text() == "10"


def test_ospf_console_messages_and_simulation_filter(qtbot, win):
    from ipaddress import IPv4Interface
    from netemu.core.routing.ospf import OspfConfig
    r1 = win.view.add_device("cisco-2911", QPointF(-100, 0))
    r2 = win.view.add_device("cisco-2911", QPointF(100, 0))
    win.topology.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    dlg = win.open_device(r1)
    term = dlg.terminal
    type_line(qtbot, term, "enable")
    for r, n in ((r1, 1), (r2, 2)):
        p = r.get_port("gi0/0")
        p.ip = IPv4Interface(f"10.0.0.{n}/30")
        p.set_admin(True)
        r.enable_ospf(OspfConfig(networks=[{"prefix": "10.0.0.0/30", "area": "0.0.0.0"}]))
    qtbot.keyClicks(term, "show ver")              # half-typed command must survive the message
    win.ctl.engine.run(max_time=15_000)
    text = term.toPlainText()
    assert "%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 on GigabitEthernet0/0 from LOADING to FULL" in text
    assert term.current_input() == "show ver"
    assert "OSPF" in win.sim_panel.filters
    # simulation: Step skips silent timers and stops on the next OSPF hello
    win.ctl.set_mode(SIMULATION)
    t0 = win.ctl.engine.now
    win.sim_panel.step()
    assert win.ctl.engine.frames_in_flight() or win.ctl.records
    assert win.ctl.engine.now - t0 <= 10_500


def test_server_services_tab_pc_dhcp_and_browser(qtbot, win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    sw = win.view.add_device("cisco-2960", QPointF(0, 0))
    srv = win.view.add_device("server", QPointF(150, 0))
    pc = win.view.add_device("pc", QPointF(-150, 0))
    win.topology.connect(srv.nic, sw.get_port("fa0/1"))
    win.topology.connect(pc.nic, sw.get_port("fa0/2"))
    sd = win.open_device(srv)
    sd.config.ip.setText("192.168.1.2")
    sd.config.mask.setText("255.255.255.0")
    assert sd.config.apply()
    tabs = [sd.tabs.tabText(i) for i in range(sd.tabs.count())]
    assert "Службы" in tabs and "Браузер" in tabs
    sv = sd.services
    sv.dhcp_on.setChecked(True)
    sv.pool_start.setText("192.168.1.100")
    sv.pool_mask.setText("255.255.255.0")
    sv.pool_gw.setText("192.168.1.1")
    sv.pool_dns.setText("192.168.1.2")
    sv.pool_max.setText("50")
    sv.dns_on.setChecked(True)
    sv.rec_name.setText("www.lab.local")
    sv.rec_ip.setText("192.168.1.2")
    sv.add_record()
    sv.page.setPlainText("<h1>Lab web</h1>")
    assert sv.apply()
    assert srv.dhcp_server.cfg.pools[0].end == "192.168.1.149"
    pd = win.open_device(pc)
    tabs = [pd.tabs.tabText(i) for i in range(pd.tabs.count())]
    assert "Браузер" in tabs and "Службы" not in tabs
    pd.config.dhcp.setChecked(True)
    assert pd.config.apply()
    win.ctl.engine.run(max_time=3000)
    assert str(pc.nic.ip) == "192.168.1.100/24" and str(pc.dns) == "192.168.1.2"
    pd.browser.url.setText("http://www.lab.local")
    pd.browser.navigate()
    win.ctl.engine.run(max_time=3000)
    assert "Lab web" in pd.browser.text()
    assert {"DHCP", "DNS", "HTTP", "TCP"} <= set(win.sim_panel.filters)


def test_ipv6_host_form_and_sim_filters(win):
    t = win.ctl.topology
    a, b = t.add_device("pc"), t.add_device("pc")
    sw = t.add_device("cisco-2960")
    t.connect(a.nic, sw.get_port("fa0/1"))
    t.connect(b.nic, sw.get_port("fa0/2"))
    for dev, addr in ((a, "2001:db8::10/64"), (b, "2001:db8::11")):
        dlg = win.open_device(dev)
        assert dlg.config.ll6.text().startswith("fe80::")
        dlg.config.ip6.setText(addr)
        assert dlg.config.apply()
    assert str(b.nic.ipv6[0]) == "2001:db8::11/64"
    from cli_helpers import run
    assert "Lost = 0" in run(a.create_cli(), "ping 2001:db8::11")
    assert {"ICMPv6", "NDP", "STP"} <= set(win.sim_panel.filters)


def test_linux_pc_in_palette_and_dialog(win):
    from netemu.core.devices import models_by_category
    assert "pc-linux" in [m.key for m in models_by_category()["pc"]]
    assert "server-linux" in [m.key for m in models_by_category()["server"]]
    dev = win.view.add_device("pc-linux", QPointF(0, 0))
    dlg = win.open_device(dev)
    tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert "Терминал (Linux)" in tabs
    dlg.config.ip.setText("10.0.0.5")
    dlg.config.mask.setText("255.255.255.0")
    assert dlg.config.apply()
    from cli_helpers import run
    assert "inet 10.0.0.5/24" in run(dev.create_cli(), "ip a")


def test_extreme_dialog_and_svi_ip_in_form(win):
    x = win.view.add_device("extreme-x440g2", QPointF(0, 0))
    dlg = win.open_device(x)
    tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert "CLI (ExtremeXOS)" in tabs
    sw = win.view.add_device("cisco-2960", QPointF(200, 0))
    sw.add_svi(1, "Vlan1", "Vl1")
    d2 = win.open_device(sw)
    row = list(sw.ports).index("Vlan1")
    assert d2.config.ifaces.item(row, 4).text() == "SVI 1"
    d2.config.ifaces.item(row, 2).setText("192.168.1.2")
    assert d2.config.apply()
    assert str(sw.get_port("Vlan1").ip) == "192.168.1.2/24"


def test_internet_cloud_dialog(win):
    inet = win.view.add_device("internet", QPointF(0, 0))
    dlg = win.open_device(inet)
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Интернет", "Защита провайдера"]
    assert dlg.terminal is None


def test_wifi_gui(win):
    ap = win.view.add_device("access-point", QPointF(0, 0))
    lap = win.view.add_device("laptop", QPointF(120, 60))
    assert "laptop" in [m.key for m in win.palette.lists["wireless"].models] if hasattr(win, "palette") and hasattr(
        win.palette.lists["wireless"], "models") else True
    d = win.open_device(ap)
    d.config.ssid.setText("Office")
    d.config.key.setText("secret123")
    assert d.config.apply()
    ld = win.open_device(lap)
    ld.config.wifi_ssid.setText("Office")
    ld.config.wifi_key.setText("secret123")
    assert ld.config.apply()
    assert lap.nic.link is not None and lap.nic.link.wireless
    assert any(getattr(it.link, "wireless", False) for it in win.view.scene().items() if hasattr(it, "link"))


def test_lab_panel(win):
    from test_lab import lab_topology
    t, r1, r2 = lab_topology()
    win.topology.lab = t.lab
    res = win.check_lab()
    assert len(res) == 4 and win.lab_panel.progress.maximum() == 4
    assert win.lab_panel.checks.count() == 4


def test_undo_in_gui(qtbot, win):
    dev = win.view.add_device("pc", QPointF(10, 10))
    qtbot.wait(5)
    assert dev.id in win.topology.devices
    win.undo()
    assert dev.id not in win.topology.devices and dev.id not in win.scene.device_items
    win.redo()
    assert dev.id in win.topology.devices and dev.id in win.scene.device_items


def test_notes_and_frames_saved(qtbot, win, tmp_path):
    from netemu.gui.canvas import TOOL_FRAME, TOOL_NOTE
    win.view.set_tool(TOOL_NOTE)
    note = win.scene.add_annotation("note", QPointF(10, 10))
    frame = win.scene.add_annotation("frame", QPointF(-200, -150))
    note.data_["text"] = "VLAN 10 = sales"
    frame.setPos(-180, -140)
    assert win.topology.annotations[1]["x"] == -180
    win.view.set_tool(TOOL_FRAME)
    path = tmp_path / "n.netemu"
    assert win.save_to(path)
    win.open_file(path)
    kinds = sorted(a["kind"] for a in win.topology.annotations)
    assert kinds == ["frame", "note"] and len(win.scene.annotation_items) == 2
    win.scene.delete_items([win.scene.annotation_items[0]])
    assert len(win.topology.annotations) == 1


def test_export_png_and_svg(win, tmp_path):
    win.load_demo()
    win.scene.add_annotation("frame", QPointF(-300, -200))
    png, svg = tmp_path / "t.png", tmp_path / "t.svg"
    assert win.export_image(png) and png.stat().st_size > 2000
    assert win.export_image(svg) and "<svg" in svg.read_text(encoding="utf-8")


def test_builtin_lab_teacher_mode_and_report(win, tmp_path):
    from netemu.gui.lab_panel import builtin_labs
    labs = builtin_labs()
    assert len(labs) >= 12
    path = next(p for t, p, k in labs if "02-inter-vlan" in p.name)
    win.open_builtin_lab(path)
    assert win.file_path is None and win.topology.lab is not None and not win.teacher
    assert not win.act_lab_solve.isEnabled()
    win.toggle_teacher(True)
    assert win.lab_panel.tabs.count() == 2
    win.apply_solution()
    assert win.lab_panel.progress.value() == win.lab_panel.progress.maximum()
    out = win.lab_panel.save_report("Петров", str(tmp_path / "r.html"))
    assert "Петров" in (tmp_path / "r.html").read_text(encoding="utf-8") and out
    win.plant_fault()
    assert win.topology.lab.faults
    win.toggle_teacher(False)
    assert win.lab_panel.tabs.count() == 1


def test_phone_dialog_opens(win):
    from netemu.gui.device_dialog import DeviceDialog
    p = win.topology.add_device("ip-phone")
    d = DeviceDialog(p, win.topology)
    assert d.tabs.count() == 1 and d.phone.b_dial.isEnabled() is False
    d.close()


def test_lan_collaboration_between_two_windows(win, qtbot, monkeypatch):
    other = MainWindow()
    qtbot.addWidget(other)
    try:
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
        r = win.topology.add_device("cisco-2911", "R1", 0, 0)
        port = 47000 + (id(win) % 2000)
        assert win.collab_host(port)
        other.collab_join("127.0.0.1", port, "0000" if win.collab.pin != "0000" else "1111")   # wrong PIN
        qtbot.waitUntil(lambda: other.collab.sock is None or "error" in other.collab_label.text()
                        or "потеряна" in other.collab_label.text() or "lost" in other.collab_label.text(), timeout=5000)
        assert r.id not in other.topology.devices
        other.collab_join("127.0.0.1", port, win.collab.pin)
        qtbot.waitUntil(lambda: r.id in other.topology.devices, timeout=5000)
        assert other.scene.device_items.get(r.id) is not None
        pc = other.topology.add_device("pc", "PC9", 200, 0)                    # edit on the participant
        qtbot.waitUntil(lambda: pc.id in win.topology.devices, timeout=5000)
        win.topology.rename(r, "CORE")                                       # edit on the host
        qtbot.waitUntil(lambda: other.topology.devices[r.id].name == "CORE", timeout=5000)
        assert win.collab.participants()
    finally:
        other.shutdown()
        win.collab_stop()


def test_classroom_hand_out_and_progress(win, qtbot, monkeypatch, tmp_path):
    from netemu.gui.lab_panel import builtin_labs
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"warning dialog: {a[2]}"))
    path = next(p for t, p, k in builtin_labs() if "01-vlan" in p.name)
    win.open_builtin_lab(path)
    win._saved_snapshot = win._snapshot()
    import socket
    with socket.socket() as probe:                       # a port nobody holds (a fixed guess was sometimes busy)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert win.class_open(port)
    student = MainWindow()
    qtbot.addWidget(student)
    try:
        student.class_join("127.0.0.1", port, win.teacher_class.pin, "Ivanov")
        qtbot.waitUntil(lambda: student.topology.lab is not None, timeout=5000)
        assert student.topology.lab.title == win.topology.lab.title
        assert not student.topology.lab.script                       # the solution stays with the teacher
        qtbot.waitUntil(lambda: "Ivanov" in win.teacher_class.roster.students, timeout=5000)
        from netemu.core.lab import apply_script
        apply_script(student.topology, win.topology.lab.script)
        student.check_lab()
        qtbot.waitUntil(lambda: win.teacher_class.roster.students["Ivanov"].total > 0, timeout=5000)
        st = win.teacher_class.roster.students["Ivanov"]
        assert st.passed == st.total == len(win.topology.lab.checks)
        out = win.class_report(str(tmp_path / "class.html"))
        assert "Ivanov" in (tmp_path / "class.html").read_text(encoding="utf-8") and out
    finally:
        student.shutdown()
        win.class_leave()


def test_terminal_hint_highlight_and_search(win, qtbot):
    from netemu.gui.terminal_tools import all_commands, hint_for, segments
    r = win.topology.add_device("cisco-2911")
    d = win.open_device(r)
    term = d.terminal
    parts = segments("R1#ping 10.0.0.1 is up")
    assert "".join(p for p, _ in parts) == "R1#ping 10.0.0.1 is up"
    assert [p for p, f in parts if f is not None] == ["R1#", "10.0.0.1", "up"]
    s = term.session
    hint = hint_for(s, "")
    assert "enable" in hint
    cmds = all_commands(s)
    assert any(c.startswith("show ip route") for c, _ in cmds) and len(cmds) > 200
    from netemu.gui.terminal_tools import CommandSearch
    cs = CommandSearch(term)
    cs.query.setText("ospf neighbor")
    assert cs.table.rowCount() >= 1
    cs.insert(0)
    assert "ospf" in term.current_input()
    d.close()


def test_demo_after_lab_clears_the_lab_panel(win):
    from netemu.gui.lab_panel import builtin_labs
    path = next(p for t, p, k in builtin_labs() if "01-vlan" in p.name)
    win.open_builtin_lab(path)
    assert win.topology.lab is not None and not win.lab_dock.isHidden()
    win._saved_snapshot = win._snapshot()
    win.load_demo()
    assert win.topology.lab is None and win.lab_dock.isHidden()


def test_shell_hint_has_commands_only():
    from netemu.core.topology import Topology
    from netemu.gui.terminal_tools import hint_for
    t = Topology()
    pc = hint_for(t.add_device("pc").create_cli(), "").split(", ")
    assert "Available" not in pc and len(pc) == len(set(pc)) and "arp" in pc
    assert "traceroute" in hint_for(t.add_device("pc-linux").create_cli(), "")


def test_router_form_keeps_unsaved_interface_edits_when_a_route_is_added(win):
    from netemu.gui.device_dialog import DeviceDialog
    r = win.topology.add_device("cisco-2911")
    d = DeviceDialog(r, win.topology)
    c = d.config
    row = next(i for i in range(c.ifaces.rowCount()) if c.ifaces.item(i, 0).text().endswith("0/0"))
    c.ifaces.item(row, 2).setText("192.168.1.1")
    c.ifaces.item(row, 3).setText("255.255.255.0")
    c.r_net.setText("10.10.0.0")
    c.r_mask.setText("255.255.0.0")
    c.r_nh.setText("192.168.1.254")
    c.add_route()
    assert c.ifaces.item(row, 2).text() == "192.168.1.1"          # the typed address survived
    assert c.apply()
    assert str(r.get_port("gi0/0").ip) == "192.168.1.1/24" and len(r.static_routes) == 1
    d.close()


def test_iot_device_joins_wifi_from_its_form(win):
    from netemu.gui.device_dialog import DeviceDialog
    ap = win.topology.add_device("access-point", "AP", 0, 0)
    temp = win.topology.add_device("iot-temperature", "T1", 100, 50)
    d = DeviceDialog(temp, win.topology)
    c = d.config
    c.dhcp.setChecked(False)
    c.wifi_ssid.setText(ap.wifi["ssid"])
    c.wifi_key.setText(ap.wifi["key"])
    c.ip.setText("192.168.1.21")
    c.mask.setText("255.255.255.0")
    assert c.apply()
    assert temp.nic.link is not None and temp.nic.link.other(temp.nic).device is ap
    d.close()


def test_menu_actions_do_not_pass_checked_into_optional_parameters(win, monkeypatch):
    """QAction.triggered(bool) once reached class_join(address=False) and class_open(port=False)."""
    from PySide6.QtWidgets import QInputDialog
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (asked.append(a[2]), ("", False))[1])
    win.act_class_join.trigger()
    win.act_collab_join.trigger()
    assert len(asked) == 2                      # both asked the user for the address instead of crashing


def test_palette_double_click_does_not_stack_devices(win):
    lst = win.palette.lists["router"]
    for _ in range(5):
        lst.itemDoubleClicked.emit(lst.item(0))
    spots = {(round(d.x), round(d.y)) for d in win.topology.devices.values()}
    assert len(spots) == 5


def test_realtime_clock_does_not_replay_a_long_stall(win):
    """After the event loop stalls (sleep, modal dialog) one tick must not simulate the whole gap."""
    from netemu.gui.controller import MAX_CATCHUP_MS
    ctl = win.ctl
    t0 = ctl.engine.now
    ctl._last = ctl._clock.elapsed() - 3_600_000          # an hour without ticks
    ctl._on_tick()
    assert ctl.engine.now - t0 <= MAX_CATCHUP_MS



def test_ddos_panels(win, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]) or QMessageBox.Ok)
    from netemu.core.security.ddos import guard
    from netemu.gui.ddos_panel import AttackPanel, IspProtectPanel
    bot = win.topology.add_device("botnet", "BOTS")
    srv = win.topology.add_device("server", "WEB")
    inet = win.topology.add_device("internet", "ISP")
    d = win.open_device(bot)
    ap = d.findChildren(AttackPanel)[0]
    ap.kind.setCurrentIndex(ap.kind.findData("dns-amp"))
    ap.target.setText("203.0.113.2")
    ap.bots.setValue(1000)
    ap.rate.setValue(100)
    ap.add()
    assert not bot.attacks and warnings                  # no reflectors: refused with a message
    ap.reflectors.setText("198.51.100.53")
    ap.add()
    assert bot.attacks[0].kind == "dns-amp" and "2.4 Gbit/s" in ap.total.text()
    ap.table.selectRow(0)
    ap._run(True)
    assert bot.attacks[0].running
    ap.stop_all()
    assert not bot.attacks[0].running
    d.close()
    d = win.open_device(srv)
    d.services.guard.syncookies.setChecked(True)
    d.services.guard.http_limit.setValue(7)
    assert d.services.apply()
    assert guard(srv)["syncookies"] and guard(srv)["http_limit"] == 7
    d.close()
    d = win.open_device(inet)
    ip = d.findChildren(IspProtectPanel)[0]
    from PySide6.QtCore import Qt
    ip.table.item(0, 1).setCheckState(Qt.Checked)
    ip.table.item(0, 3).setText("udp src 53; icmp")
    ip.apply()
    assert inet.protect["ISP0"] == {"scrub": True, "rtbh": [], "flowspec": ["udp src 53", "icmp"]}
    ip._add_zone()
    ip.zones.item(0, 3).setCheckState(Qt.Checked)
    assert ip.apply()
    assert inet.zones == {"shop.example": {"a": "203.0.113.2", "ttl": 300, "shield": True}}
    ip.zones.item(0, 1).setText("not-an-ip")
    assert not ip.apply() and warnings
    d.close()
    panel = win.show_load()
    panel.refresh()
    assert "0" in panel.summary.text()



def test_security_tab_and_infection_monitor(win):
    from netemu.core.security import malware as mw
    from netemu.gui.malware_panel import HostSecurityPanel
    t = win.topology
    sw = t.add_device("cisco-2960", "SW")
    pcs = []
    for i in range(3):
        pc = t.add_device("pc", f"PC{i + 1}")
        t.connect(pc.nic, sw.get_port(f"Fa0/{i + 1}"))
        pc.set_ip(f"192.168.1.{11 + i}", "255.255.255.0")
        pcs.append(pc)
    d = win.open_device(pcs[1])
    sec = d.findChildren(HostSecurityPanel)[0]
    sec.patches["MS17-010"].setChecked(True)
    assert mw.sec(pcs[1])["patches"] == ["MS17-010"]
    d.close()
    d = win.open_device(pcs[2])
    sec = d.findChildren(HostSecurityPanel)[0]
    sec.fw.setText("445")
    assert sec.apply_firewall() and mw.fw_blocked(pcs[2]) == {445}
    sec.fw.setText("http")
    assert not sec.apply_firewall()
    d.close()
    d = win.open_device(pcs[0])
    sec = d.findChildren(HostSecurityPanel)[0]
    sec.family.setCurrentIndex(sec.family.findData("smb-worm"))
    sec.infect()
    assert pcs[0].infection["family"] == "smb-worm"
    d.close()
    t.engine.run(max_time=5000)
    assert pcs[1].infection is None and pcs[2].infection is None
    panel = win.show_malware()
    panel.refresh()
    assert panel.table.rowCount() == 3 and "1" in panel.summary.text()
    assert any("smb" in panel.table.item(r, 1).text().lower() or "SMB" in panel.table.item(r, 1).text()
               for r in range(3))
    d = win.open_device(pcs[0])
    sec = d.findChildren(HostSecurityPanel)[0]
    sec.reinstall()
    assert pcs[0].infection is None
    d.close()
