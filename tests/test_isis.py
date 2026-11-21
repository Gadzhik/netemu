"""IS-IS level 2 across Cisco, Huawei and Juniper: adjacencies, LSDB flooding, SPF routes, failover."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CISCO = """enable
configure terminal
interface gi0/0
ip address 192.168.1.1 255.255.255.0
ip router isis
no shutdown
interface gi0/1
ip address 10.0.12.1 255.255.255.252
ip router isis
no shutdown
interface gi0/2
ip address 10.0.13.1 255.255.255.252
ip router isis
isis metric 50
no shutdown
exit
router isis
net 49.0001.0000.0000.0001.00
is-type level-2-only
passive-interface gi0/0
end"""
HUAWEI = """system-view
isis 1
network-entity 49.0002.0000.0000.0002.00
is-level level-2
quit
interface GigabitEthernet0/0/1
ip address 10.0.12.2 30
isis enable 1
interface GigabitEthernet0/0/2
ip address 10.0.23.1 30
isis enable 1
return"""
JUNIPER = """configure
set interfaces ge-0/0/0 unit 0 family inet address 192.168.3.1/24
set interfaces ge-0/0/1 unit 0 family inet address 10.0.23.2/30
set interfaces ge-0/0/1 unit 0 family iso
set interfaces ge-0/0/2 unit 0 family inet address 10.0.13.2/30
set interfaces ge-0/0/2 unit 0 family iso
set interfaces lo0 unit 0 family iso address 49.0003.0000.0000.0003.00
set protocols isis interface ge-0/0/0.0 passive
set protocols isis interface ge-0/0/1.0
set protocols isis interface ge-0/0/2.0 level 2 metric 50
set protocols isis level 1 disable
commit and-quit"""


def build():
    t = Topology()
    c, h, j = t.add_device("cisco-2911", "R1"), t.add_device("huawei-ar2220", "R2"), t.add_device("juniper-srx300", "R3")
    p1, p3 = t.add_device("pc"), t.add_device("pc")
    t.connect(c.get_port("gi0/1"), h.get_port("GE0/0/1"))
    t.connect(h.get_port("GE0/0/2"), j.get_port("ge-0/0/1"))
    t.connect(c.get_port("gi0/2"), j.get_port("ge-0/0/2"))
    t.connect(p1.nic, c.get_port("gi0/0"))
    t.connect(p3.nic, j.get_port("ge-0/0/0"))
    for dev, script in ((c, CISCO), (h, HUAWEI), (j, JUNIPER)):
        out = run_all(dev.create_cli(), script)
        assert not any(e in out for e in ("Invalid", "Error", "error:", "syntax error")), out
    p1.set_ip("192.168.1.10", "255.255.255.0")
    p1.set_gateway("192.168.1.1")
    p3.set_ip("192.168.3.10", "255.255.255.0")
    p3.set_gateway("192.168.3.1")
    t.engine.run(max_time=20000)
    return t, c, h, j, p1, p3


def via(r, net):
    return [(str(x.next_hop), x.metric) for x in r.routing_table() if str(x.network) == net and x.code == "i L2"]


def test_isis_multivendor():
    t, c, h, j, p1, p3 = build()
    s = c.create_cli()
    run(s, "enable")
    nb = run(s, "show isis neighbors")
    assert nb.count("UP") == 2, nb
    assert "LSP Seq Num" in run(s, "show isis database") and f"{j.hostname}.00-00" in run(s, "show isis database")
    assert via(c, "192.168.3.0/24") == [("10.0.12.2", 30)]        # 10 + 10 + 10 beats the metric-50 link
    assert "i L2" in run(s, "show ip route isis")
    assert "ISIS-L2" in run(h.create_cli(), "display ip routing-table")
    js = j.create_cli()
    assert "Up" in run(js, "show isis adjacency") and "IS-IS" in run(js, "show route")
    assert _ping(None, p1, "192.168.3.10").received == 4
    # the Huawei router goes away: traffic falls back to the direct, more expensive link
    h.get_port("GE0/0/1").set_admin(False)
    t.engine.run(max_time=t.engine.now + 15000)
    assert via(c, "192.168.3.0/24") == [("10.0.13.2", 60)]
    assert _ping(None, p1, "192.168.3.10").received == 4
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=20000)
    assert via(t2.devices[c.id], "192.168.3.0/24")
    assert "router isis" in run(s, "show running-config") and " ip router isis" in run(s, "show running-config")
