"""Wi-Fi: association by SSID/key/range, traffic through the AP, persistence."""
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received


def lab():
    t = Topology()
    ap = t.add_device("access-point", x=0, y=0)
    sw = t.add_device("cisco-2960", x=0, y=200)
    srv = t.add_device("server", x=100, y=300)
    t.connect(ap.get_port("Ethernet0"), sw.get_port("fa0/1"))
    t.connect(srv.nic, sw.get_port("fa0/2"))
    lap = t.add_device("laptop", x=120, y=-80)
    srv.set_ip("192.168.1.2", "255.255.255.0")
    lap.set_ip("192.168.1.50", "255.255.255.0")
    return t, ap, srv, lap


def test_association_needs_ssid_key_and_range():
    t, ap, srv, lap = lab()
    assert lap.nic.link is None
    lap.wifi = {"ssid": "NetEmu", "key": "wrong"}
    t.rescan_wireless()
    assert lap.nic.link is None
    lap.wifi = {"ssid": "NetEmu", "key": "netemu123"}
    t.rescan_wireless()
    assert lap.nic.link is not None and lap.nic.link.wireless and lap.nic.is_up
    t.engine.run(max_time=3000)
    assert ping(lap, "192.168.1.2") == 4
    lap.x = 1000                                       # walks out of range
    t.rescan_wireless()
    assert lap.nic.link is None and not ap.radio_ports()
    lap.x = 50
    ap.wifi["key"] = "changed"
    t.rescan_wireless()
    assert lap.nic.link is None
    ap.wifi["security"] = "open"
    t.rescan_wireless()
    assert lap.nic.link is not None


def test_wireless_persists():
    t, ap, srv, lap = lab()
    lap.wifi = {"ssid": "NetEmu", "key": "netemu123"}
    t.rescan_wireless()
    data = topology_to_dict(t)
    assert not any("Radio0" in l["a"][1] or "Radio0" in l["b"][1] for l in data["links"])
    t2 = topology_from_dict(data, Topology())
    lap2 = t2.devices[lap.id]
    assert lap2.nic.link is not None and lap2.nic.is_wireless
    t2.engine.run(max_time=3000)
    assert ping(lap2, "192.168.1.2") == 4


from cli_helpers import run  # noqa: E402


def test_windows_and_linux_wifi_commands():
    t, ap, srv, lap = lab()
    w = lap.create_cli()
    assert "NetEmu" in run(w, "netsh wlan show networks")
    assert "incorrect" in run(w, "netsh wlan connect name=NetEmu key=bad")
    assert "completed successfully" in run(w, "netsh wlan connect name=NetEmu key=netemu123")
    out = run(w, "netsh wlan show interfaces")
    assert "State                  : connected" in out and "SSID                   : NetEmu" in out
    lx = t.add_device("laptop-linux", x=-60, y=40)
    lc = lx.create_cli()
    assert "NetEmu" in run(lc, "nmcli dev wifi list")
    assert "Secrets were required" in run(lc, "nmcli dev wifi connect NetEmu")
    assert "successfully activated" in run(lc, "nmcli dev wifi connect NetEmu password netemu123")
    assert "SSID: NetEmu" in run(lc, "iw dev wlan0 link")
    assert len(ap.radio_ports()) == 2
    assert "successfully disconnected" in run(lc, "nmcli dev disconnect wlan0")
    assert "Not connected." in run(lc, "iw dev wlan0 link")
