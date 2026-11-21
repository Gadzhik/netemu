"""The Internet cloud: hand-off addressing, public DNS/web, private sources dropped (NAT needed)."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

EDGE = """enable
configure terminal
interface gi0/0
ip address 192.168.1.1 255.255.255.0
no shutdown
interface gi0/1
ip address 203.0.113.2 255.255.255.252
no shutdown
exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
end"""
NAT = """configure terminal
access-list 1 permit 192.168.1.0 0.0.0.255
interface gi0/0
ip nat inside
interface gi0/1
ip nat outside
exit
ip nat inside source list 1 interface gi0/1 overload
end"""


def lab():
    t = Topology()
    inet = t.add_device("internet")
    r = t.add_device("cisco-2911")
    pc = t.add_device("pc")
    t.connect(r.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(pc.nic, r.get_port("gi0/0"))
    rc = r.create_cli()
    run_all(rc, EDGE)
    pc.set_ip("192.168.1.10", "255.255.255.0")
    pc.set_gateway("192.168.1.1")
    pc.dns = __import__("ipaddress").IPv4Address("8.8.8.8")
    return t, inet, r, rc, pc


def test_router_reaches_internet_but_lan_needs_nat():
    t, inet, r, rc, pc = lab()
    assert "!!!!!" in run(rc, "ping 8.8.8.8")                     # router's own public address works
    w = pc.create_cli()
    assert "Received = 0" in run(w, "ping 8.8.8.8")                # private source dropped by the ISP
    assert inet.dropped_private > 0
    run_all(rc, NAT)
    assert "Received = 4" in run(w, "ping 8.8.8.8")
    out = run(w, "nslookup www.example.com")
    assert "93.184.216.34" in out
    assert "Example Domain" in run(w, "curl http://www.example.com")
    assert "Hello from the Internet" in run(w, "curl http://www.netemu.lab/")
    tr = run(w, "tracert -d 1.1.1.1")
    assert "192.168.1.1" in tr and "  2 " in tr and "1.1.1.1" in tr


def test_cloud_is_fixed_and_persists():
    t, inet, r, rc, pc = lab()
    assert inet.create_cli() is None
    assert str(inet.get_port("ISP3").ip) == "203.0.113.13/30"
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    inet2 = next(d for d in t2.devices.values() if d.category == "cloud")
    assert str(inet2.get_port("ISP0").ip) == "203.0.113.1/30"
    r2 = t2.devices[r.id]
    s = r2.create_cli()
    run(s, "enable")
    assert "!!!!!" in run(s, "ping 1.1.1.1")
