"""WLC + lightweight APs: discovery/join, WLANs pushed from the controller, radio off without it."""
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def build():
    t = Topology()
    sw = t.add_device("cisco-2960", "SW1", 400, 100)
    wlc = t.add_device("wlc-2504", "WLC1", 200, 100)
    ap1, ap2 = t.add_device("lap-1142", "LAP1", 300, 300), t.add_device("lap-1142", "LAP2", 900, 300)
    srv = t.add_device("server", "SRV", 600, 100)
    lt = t.add_device("laptop", "Laptop1", 320, 380)
    t.connect(wlc.nic, sw.get_port("Fa0/1"))
    t.connect(ap1.get_port("Ethernet0"), sw.get_port("Fa0/2"))
    t.connect(ap2.get_port("Ethernet0"), sw.get_port("Fa0/3"))
    t.connect(srv.nic, sw.get_port("Fa0/10"))
    wlc.set_ip("10.0.0.2", "255.255.255.0")
    srv.set_ip("10.0.0.100", "255.255.255.0")
    lt.set_ip("10.0.0.50", "255.255.255.0")
    lt.wifi = {"ssid": "Corp", "key": "corp12345"}
    return t, wlc, ap1, ap2, lt


def test_lightweight_aps_follow_the_controller():
    t, wlc, ap1, ap2, lt = build()
    assert ap1.wifi["ssid"] is None                                # no controller yet: radio off
    t.engine.run(max_time=8000)
    assert set(wlc.aps) == {"LAP1", "LAP2"} and ap1.controller and ap1.wifi["ssid"] == "Corp"
    assert lt.nic.link is not None and lt.nic.link.other(lt.nic).device is ap1
    assert _ping(None, lt, "10.0.0.100").received == 4
    # one change on the controller reconfigures every AP; the laptop loses the old network
    wlc.wlans = [{"ssid": "Guest", "security": "open", "key": None, "enabled": True}]
    wlc.push()
    t.engine.run(max_time=t.engine.now + 500)
    assert ap1.wifi["ssid"] == ap2.wifi["ssid"] == "Guest" and lt.nic.link is None
    lt.wifi = {"ssid": "Guest", "key": None}
    from netemu.core.devices.wireless import rescan
    rescan(t)
    assert _ping(None, lt, "10.0.0.100").received == 4
    # the controller disappears: APs time out and switch the radio off
    wlc.nic.set_admin(False)
    t.engine.run(max_time=t.engine.now + 45000)
    assert ap1.controller is None and ap1.wifi["ssid"] is None and lt.nic.link is None


def test_wlc_persistence():
    t, wlc, ap1, ap2, lt = build()
    wlc.wlans.append({"ssid": "Lab", "security": "wpa2", "key": "lab-pass1", "enabled": False})
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    w2 = t2.devices[wlc.id]
    assert [w["ssid"] for w in w2.wlans] == ["Corp", "Lab"]
    t2.engine.run(max_time=8000)
    assert t2.devices[ap1.id].wifi["ssid"] == "Corp"           # only enabled WLANs go to the APs


def test_second_wlan_is_broadcast_too():
    t, wlc, ap1, ap2, lt = build()
    wlc.wlans.append({"ssid": "Guest", "security": "wpa2", "key": "guest12345", "enabled": True})
    t.engine.run(max_time=8000)
    from netemu.core.devices.wireless import ap_networks
    assert [n[0] for n in ap_networks(ap1)] == ["Corp", "Guest"]
    lt.wifi = {"ssid": "Guest", "key": "guest12345"}
    t.rescan_wireless()
    assert lt.nic.link is not None
    lt.wifi = {"ssid": "Guest", "key": "wrong-key"}
    t.rescan_wireless()
    assert lt.nic.link is None
