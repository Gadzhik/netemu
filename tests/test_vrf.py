"""VRF-lite on Cisco: overlapping customer networks on one router, separate tables, ping vrf."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def build():
    t = Topology()
    r = t.add_device("cisco-2911", "PE")
    a1, a2 = t.add_device("pc", "A1"), t.add_device("pc", "A2")      # customer A: 10.1.0.0/24 and 10.2.0.0/24
    b1 = t.add_device("pc", "B1")                                   # customer B reuses 10.1.0.0/24
    t.connect(a1.nic, r.get_port("gi0/0"))
    t.connect(a2.nic, r.get_port("gi0/1"))
    t.connect(b1.nic, r.get_port("gi0/2"))
    out = run_all(r.create_cli(), """enable
configure terminal
ip vrf CUST-A
rd 65000:1
exit
ip vrf CUST-B
rd 65000:2
exit
interface gi0/0
ip address 10.1.0.1 255.255.255.0
ip vrf forwarding CUST-A
ip address 10.1.0.1 255.255.255.0
no shutdown
interface gi0/1
ip vrf forwarding CUST-A
ip address 10.2.0.1 255.255.255.0
no shutdown
interface gi0/2
ip vrf forwarding CUST-B
ip address 10.1.0.1 255.255.255.0
no shutdown
exit
ip route vrf CUST-B 0.0.0.0 0.0.0.0 10.1.0.99
end""")
    assert "Invalid" not in out and "removed due to enabling VRF CUST-A" in out, out
    for pc, ip in ((a1, "10.1.0.10"), (a2, "10.2.0.10"), (b1, "10.1.0.20")):
        pc.set_ip(ip, "255.255.255.0")
        pc.set_gateway(ip.rsplit(".", 1)[0] + ".1")
    t.engine.run(max_time=500)
    return t, r, a1, a2, b1


def test_vrfs_are_isolated_and_overlap():
    t, r, a1, a2, b1 = build()
    assert _ping(None, a1, "10.2.0.10").received == 4                 # inside CUST-A
    assert _ping(None, b1, "10.2.0.10").received == 0                 # CUST-B cannot reach CUST-A
    s = r.create_cli()
    run(s, "enable")
    assert "Routing Table: CUST-A" in run(s, "show ip route vrf CUST-A")
    rt_a = run(s, "show ip route vrf CUST-A")
    assert "10.2.0.0/24 is directly connected" in rt_a and "S*" not in rt_a
    assert "S*" in run(s, "show ip route vrf CUST-B")
    assert "10.1.0.0" not in run(s, "show ip route")                   # global table is empty
    assert "!!!!!" in run(s, "ping vrf CUST-A 10.2.0.10")
    assert "!!!!!" in run(s, "ping vrf CUST-B 10.1.0.20")
    assert "CUST-A" in run(s, "show ip vrf") and "65000:1" in run(s, "show ip vrf")
    rc = run(s, "show running-config")
    assert "ip vrf CUST-A" in rc and " ip vrf forwarding CUST-B" in rc and "ip route vrf CUST-B 0.0.0.0" in rc
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=500)
    a1b = next(d for d in t2.devices.values() if d.name == "A1")
    assert _ping(None, a1b, "10.2.0.10").received == 4
