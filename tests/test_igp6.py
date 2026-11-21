"""EIGRP for IPv6 and IS-IS for IPv6 (multi-vendor): adjacencies, routes, metrics, persistence."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_ipv6_core import ping6


def _ok(out):
    assert not any(e in out for e in ("Invalid", "Error", "error:", "syntax error", "% ")), out


def _pcs(t, r_left, port_left, r_right, port_right):
    a, b = t.add_device("pc", "A"), t.add_device("pc", "B")
    t.connect(a.nic, r_left.get_port(port_left))
    t.connect(b.nic, r_right.get_port(port_right))
    a.set_ipv6("2001:db8:a::10/64")
    a.set_gateway6("2001:db8:a::1")
    b.set_ipv6("2001:db8:b::10/64")
    b.set_gateway6("2001:db8:b::1")
    return a, b


EIGRP = """enable
configure terminal
ipv6 unicast-routing
ipv6 router eigrp 10
eigrp router-id {rid}
exit
interface gi0/0
ipv6 address {lan}
ipv6 eigrp 10
no shutdown
interface gi0/1
ipv6 address {w1}
ipv6 eigrp 10
no shutdown
interface gi0/2
ipv6 address {w2}
ipv6 eigrp 10
no shutdown
end"""


def test_eigrp6_triangle_prefers_fast_path_and_fails_over():
    t = Topology()
    r1, r2, r3 = (t.add_device("cisco-2911", n) for n in ("R1", "R2", "R3"))
    t.connect(r1.get_port("gi0/1"), r2.get_port("gi0/1"))
    t.connect(r2.get_port("gi0/2"), r3.get_port("gi0/1"))
    t.connect(r1.get_port("gi0/2"), r3.get_port("gi0/2"))
    _ok(run_all(r1.create_cli(), EIGRP.format(rid="1.1.1.1", lan="2001:db8:a::1/64", w1="2001:db8:12::1/64",
                                              w2="2001:db8:13::1/64")))
    _ok(run_all(r2.create_cli(), EIGRP.format(rid="2.2.2.2", lan="2001:db8:2::1/64", w1="2001:db8:12::2/64",
                                              w2="2001:db8:23::2/64")))
    _ok(run_all(r3.create_cli(), EIGRP.format(rid="3.3.3.3", lan="2001:db8:b::1/64", w1="2001:db8:23::3/64",
                                              w2="2001:db8:13::3/64")))
    s3 = r3.create_cli()
    run(s3, "enable")
    run_all(s3, "configure terminal\ninterface gi0/2\nbandwidth 10000\nend")   # the direct R1-R3 link is slow
    a, b = _pcs(t, r1, "gi0/0", r3, "gi0/0")
    t.engine.run(max_time=20000)
    s1 = r1.create_cli()
    run(s1, "enable")
    nb = run(s1, "show ipv6 eigrp neighbors")
    assert nb.count("Link-local") == 2, nb
    rt = run(s1, "show ipv6 route eigrp")
    assert "D  2001:DB8:B::/64 [90/" in rt and "GigabitEthernet0/1" in rt, rt     # via R2 (gigabit all the way)
    assert ping6(t, a, "2001:db8:b::10").ok == 4
    # another AS on R2: no neighbour there, traffic takes the slow link
    run_all(r2.create_cli(), "enable\nconfigure terminal\nno ipv6 router eigrp 10\nend")
    t.engine.run(max_time=t.engine.now + 20000)
    rt = run(s1, "show ipv6 route eigrp")
    assert "2001:DB8:B::/64" in rt and "GigabitEthernet0/2" in rt, rt
    assert ping6(t, a, "2001:db8:b::10").ok == 4
    cfg = run(s1, "show running-config")
    assert "ipv6 router eigrp 10" in cfg and " ipv6 eigrp 10" in cfg and " eigrp router-id 1.1.1.1" in cfg
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=20000)
    assert any(r.code == "D" for r in t2.devices[r1.id].routing_table6())


ISIS_CISCO = """enable
configure terminal
ipv6 unicast-routing
router isis
net 49.0001.0000.0000.0001.00
exit
interface gi0/0
ipv6 address 2001:db8:a::1/64
ipv6 router isis
no shutdown
interface gi0/1
ip address 10.0.12.1 255.255.255.252
ipv6 address 2001:db8:12::1/64
ip router isis
ipv6 router isis
no shutdown
end"""
ISIS_HUAWEI = """system-view
ipv6
isis 1
network-entity 49.0002.0000.0000.0002.00
ipv6 enable
quit
interface GigabitEthernet0/0/1
ipv6 enable
ip address 10.0.12.2 30
ipv6 address 2001:db8:12::2/64
isis enable 1
isis ipv6 enable 1
interface GigabitEthernet0/0/2
ipv6 enable
ipv6 address 2001:db8:23::2/64
isis ipv6 enable 1
return"""
ISIS_JUNIPER = """configure
set interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:b::1/64
set interfaces ge-0/0/1 unit 0 family inet6 address 2001:db8:23::3/64
set interfaces ge-0/0/1 unit 0 family iso
set interfaces lo0 unit 0 family iso address 49.0003.0000.0000.0003.00
set protocols isis interface ge-0/0/0.0 passive
set protocols isis interface ge-0/0/1.0
set protocols isis level 1 disable
commit and-quit"""


def test_isis_ipv6_multivendor():
    t = Topology()
    c, h, j = t.add_device("cisco-2911", "R1"), t.add_device("huawei-ar2220", "R2"), t.add_device("juniper-srx300", "R3")
    t.connect(c.get_port("gi0/1"), h.get_port("GE0/0/1"))
    t.connect(h.get_port("GE0/0/2"), j.get_port("ge-0/0/1"))
    for dev, script in ((c, ISIS_CISCO), (h, ISIS_HUAWEI), (j, ISIS_JUNIPER)):
        _ok(run_all(dev.create_cli(), script))
    a, b = _pcs(t, c, "gi0/0", j, "ge-0/0/0")
    t.engine.run(max_time=20000)
    s = c.create_cli()
    run(s, "enable")
    assert run(s, "show isis neighbors").count("UP") == 1
    rt = run(s, "show ipv6 route isis")
    assert "I2 2001:DB8:B::/64" in rt and "I2 2001:DB8:23::/64" in rt, rt
    via = [r for r in c.routing_table6() if str(r.network) == "2001:db8:b::/64"][0]
    assert via.next_hop.is_link_local and via.metric == 30          # 10 + 10 + prefix 10
    assert "IPv6 prefix 2001:db8:b::/64" in "\n".join(
        x for f in [c.isis] for x in __import__("netemu.core.routing.isis", fromlist=["x"]).IsisPdu(
            "lsp", "x", lsps=tuple(f._lsp_tuple(sid) for sid in f.lsdb)).layer_lines())
    assert ping6(t, a, "2001:db8:b::10").ok == 4
    assert any(str(r.network) == "2001:db8:a::/64" for r in j.routing_table6())
    hc = run_all(h.create_cli(), "display current-configuration")
    assert " isis ipv6 enable 1" in hc and "network-entity 49.0002.0000.0000.0002.00" in hc
    assert "isis {" in run(j.create_cli(), "show configuration")
    assert " ipv6 router isis" in run(s, "show running-config")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=20000)
    assert any(r.code == "I2" for r in t2.devices[c.id].routing_table6())
