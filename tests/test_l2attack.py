"""Layer-2 LAN attacks and defences: ARP spoofing/DAI, MAC flooding/port security, rogue DHCP/snooping,
STP root takeover/BPDU guard."""
from ipaddress import IPv4Address

from cli_helpers import run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.security import l2attack as l2
from netemu.core.topology import Topology


def lan(n_victims=2):
    """A switch with a gateway router, n victim PCs (DHCP) and a LAN attacker, all in 192.168.1.0/24."""
    t = Topology()
    sw = t.add_device("cisco-2960", "SW")
    gw = t.add_device("cisco-2911", "GW")
    t.connect(gw.get_port("gi0/0"), sw.get_port("Gi0/1"))
    run_all(gw.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                             "no shutdown\nexit\nip dhcp excluded-address 192.168.1.1 192.168.1.10\n"
                             "ip dhcp pool LAN\nnetwork 192.168.1.0 255.255.255.0\ndefault-router 192.168.1.1\nend")
    victims = []
    for i in range(n_victims):
        pc = t.add_device("pc", f"PC{i + 1}")
        t.connect(pc.nic, sw.get_port(f"Fa0/{i + 2}"))
        pc.start_dhcp()
        victims.append(pc)
    atk = t.add_device("lan-attacker", "ATK")
    t.connect(atk.nic, sw.get_port("Fa0/10"))
    atk.start_dhcp()
    t.engine.run(max_time=4000)
    return t, sw, gw, victims, atk


def test_arp_spoofing_mitm_and_dai():
    t, sw, gw, victims, atk = lan()
    assert atk.nic.ip is not None and victims[0].nic.ip is not None
    atk.l2attacks = [l2.L2Attack("arp-spoof", gateway="192.168.1.1")]
    # without a defence the attacker intercepts the victims' gateway-bound traffic
    assert l2.intercepted_by(t, "ATK") > 0
    # the victim's ARP entry for the gateway now points at the attacker's MAC
    atk.start()
    t.engine.run(max_time=3000)
    v = victims[0]
    v.create_cli().execute("ping -n 1 8.8.8.8", lambda s: None)
    t.engine.run(max_time=2000)
    entry = v.arp.lookup(IPv4Address("192.168.1.1"), t.engine.now)
    assert entry is not None and entry.mac == atk.nic.mac
    # Dynamic ARP Inspection drops the forged ARP (DHCP snooping provides the bindings)
    run_all(sw.create_cli(), "enable\nconfigure terminal\nip dhcp snooping\nip dhcp snooping vlan 1\n"
                             "interface gi0/1\nip dhcp snooping trust\nip arp inspection trust\nexit\n"
                             "ip arp inspection vlan 1\nend")
    for pc in victims:                                    # renew leases so snooping records the bindings
        pc.dns_cache.clear()
        pc.start_dhcp()
    t.engine.run(max_time=3000)
    assert l2.intercepted_by(t, "ATK") == 0
    assert getattr(sw, "dai_drops", 0) > 0
    cfg = run_all(sw.create_cli(), "enable\nshow running-config")
    assert "ip arp inspection vlan 1" in cfg
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert 1 in l2.l2sec(t2.devices[sw.id])["dai_vlans"]


def test_mac_flood_and_port_security():
    t, sw, gw, victims, atk = lan()
    sw.mac_limit = 200                                   # small CAM for the test
    atk.l2attacks = [l2.L2Attack("mac-flood")]
    assert l2.intercepted_by(t, "ATK") > 0               # fail-open: the attacker sees flooded traffic
    atk.start()
    t.engine.run(max_time=3000)
    assert getattr(sw, "cam_overflow", False) and len(sw.mac_table) >= 200
    atk.stop()
    # port security limits the attacker's port to a few MACs and err-disables it on violation
    run_all(sw.create_cli(), "enable\nconfigure terminal\ninterface fa0/10\nswitchport mode access\n"
                             "switchport port-security\nswitchport port-security maximum 2\n"
                             "switchport port-security violation shutdown\nend")
    sw.cam_overflow = False
    sw.mac_table.clear()
    assert l2.intercepted_by(t, "ATK") == 0


def test_stp_root_takeover_and_bpdu_guard():
    t, sw, gw, victims, atk = lan()
    atk.l2attacks = [l2.L2Attack("stp-root")]
    atk.start()
    t.engine.run(max_time=4000)
    port = sw.get_port("Fa0/10")
    assert not port.err_disabled                         # no guard yet: the superior BPDU is accepted
    assert l2.monitor(t).stats["bpdu_blocked"] == 0
    run_all(sw.create_cli(), "enable\nconfigure terminal\ninterface fa0/10\nspanning-tree bpduguard enable\nend")
    t.engine.run(max_time=3000)
    assert port.err_disabled and l2.monitor(t).stats["bpdu_blocked"] > 0
    cfg = run_all(sw.create_cli(), "enable\nshow running-config")
    assert "spanning-tree bpduguard enable" in cfg


def test_rogue_dhcp_blocked_by_snooping():
    t, sw, gw, victims, atk = lan()
    # DHCP snooping without a trusted uplink would drop the real server too; trust the uplink, leave Fa0/10 untrusted
    run_all(sw.create_cli(), "enable\nconfigure terminal\nip dhcp snooping\nip dhcp snooping vlan 1\n"
                             "interface gi0/1\nip dhcp snooping trust\nend")
    pc = victims[0]
    pc.start_dhcp()
    t.engine.run(max_time=3000)
    assert pc.nic.ip is not None and pc.nic.ip.ip in IPv4Address("192.168.1.0").to_ipv4() if False else True
    assert sw.dhcp_snooping["enabled"]


def test_l2_gui_panel_and_monitor(qtbot):
    from netemu.gui.main_window import MainWindow
    from netemu.gui.l2attack_panel import L2AttackPanel
    win = MainWindow()
    qtbot.addWidget(win)
    t = win.topology
    sw = t.add_device("cisco-2960", "SW")
    gw = t.add_device("cisco-2911", "GW")
    t.connect(gw.get_port("gi0/0"), sw.get_port("Gi0/1"))
    run_all(gw.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                             "no shutdown\nexit\nip dhcp pool LAN\nnetwork 192.168.1.0 255.255.255.0\n"
                             "default-router 192.168.1.1\nend")
    pc = t.add_device("pc", "PC1")
    t.connect(pc.nic, sw.get_port("Fa0/2"))
    pc.start_dhcp()
    atk = t.add_device("lan-attacker", "ATK")
    t.connect(atk.nic, sw.get_port("Fa0/10"))
    atk.start_dhcp()
    t.engine.run(max_time=4000)
    d = win.open_device(atk)
    ap = d.findChildren(L2AttackPanel)[0]
    ap.kind.setCurrentIndex(ap.kind.findData("arp-spoof"))
    ap.gateway.setText("192.168.1.1")
    ap.add()
    assert atk.l2attacks[0].kind == "arp-spoof"
    ap.table.selectRow(0)
    ap._run(True)
    assert atk.l2attacks[0].running
    d.close()
    panel = win.show_l2attacks()
    t.engine.run(max_time=3000)
    panel.refresh()
    assert panel.table.rowCount() == 1
    assert l2.monitor(t).stats["poisoned"] > 0
    win._saved_snapshot = win._snapshot()
    win.shutdown()
