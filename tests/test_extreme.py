"""ExtremeXOS switch: VLANs by name, ports by number, VLAN router interfaces, ipforwarding, OSPF, STP."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

ERR = ("%%", "Error:")


def ok(out):
    assert not any(e in out for e in ERR), out
    return out


CFG = """create vlan v10 tag 10
create vlan v20 tag 20
configure vlan default delete ports 1-2
configure vlan v10 add ports 1 untagged
configure vlan v20 add ports 2 untagged
configure vlan v10 ipaddress 10.0.10.1 255.255.255.0
configure vlan v20 ipaddress 10.0.20.1/24"""


def lab():
    t = Topology()
    sw = t.add_device("extreme-x440g2")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("1"))
    t.connect(b.nic, sw.get_port("2"))
    a.set_ip("10.0.10.5", "255.255.255.0")
    a.set_gateway("10.0.10.1")
    b.set_ip("10.0.20.5", "255.255.255.0")
    b.set_gateway("10.0.20.1")
    return t, sw, a, b


def test_exos_vlans_and_routing():
    t, sw, a, b = lab()
    s = sw.create_cli()
    assert s.prompt == "X440G2-24t.1 # " and not sw.stp.enabled
    ok(run_all(s, CFG))
    assert s.prompt.startswith("* X440G2-24t.")
    pa = a.create_cli()
    assert "Received = 4" in run(pa, "ping 10.0.10.1")
    assert "Received = 0" in run(pa, "ping 10.0.20.5")          # ipforwarding off by default
    ok(run(s, "enable ipforwarding"))
    assert "Received = 4" in run(pa, "ping 10.0.20.5")
    vl = run(s, "show vlan")
    assert "v10" in vl and "10.0.10.1" in vl and "Default" in vl
    assert "Untag:            *1" in run(s, "show vlan v10")
    rt = run(s, "show iproute")
    assert "#d   10.0.10.0/24" in rt and "#d   10.0.20.0/24" in rt
    assert "10.0.10.5" in run(s, "show iparp")
    assert "v10" in run(s, "show fdb")
    cfg = run(s, "show configuration")
    for line in ('create vlan "v10"', "configure vlan v10 tag 10", "configure vlan v10 add ports 1 untagged",
                 "configure vlan v10 ipaddress 10.0.10.1 255.255.255.0", "enable ipforwarding vlan v10",
                 "configure vlan default delete ports 1-2"):
        assert line in cfg, line
    assert "saved" in run(s, "save") and not s.dirty
    assert "Error: Protocol conflict" in run(s, "configure vlan v20 add ports 1 untagged")
    assert "%% Invalid input" in run(s, "confgure vlan")
    assert "4 packets transmitted, 4 packets received" in run(s, "ping 10.0.20.5")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    pa2 = t2.devices[a.id].create_cli()
    assert "Received = 4" in run(pa2, "ping 10.0.20.5")


def test_exos_tagged_trunk_static_route_and_stp():
    t = Topology()
    x = t.add_device("extreme-x440g2")
    c = t.add_device("cisco-2960")
    t.connect(x.get_port("24"), c.get_port("gi0/1"))
    s = x.create_cli()
    ok(run_all(s, "create vlan v10 tag 10\nconfigure vlan v10 add ports 24 tagged\n"
                  "configure vlan v10 ipaddress 10.0.10.1/24\nconfigure iproute add default 10.0.10.254\n"
                  "configure snmp sysName CORE1\nenable stpd s0\nconfigure stpd s0 mode dot1w\n"
                  "configure stpd s0 priority 4096"))
    assert x.hostname == "CORE1" and x.stp.enabled and x.stp.mode == "rstp"
    p = x.get_port("24")
    assert p.mode == "trunk" and 10 in p.tagged and p.pvid == 1
    assert "#s   0.0.0.0/0" in run(s, "show iproute")
    t.engine.run(max_time=31_000)
    assert "ENABLED" in run(s, "show stpd s0")
    assert "configure iproute add default 10.0.10.254" in run(s, "show configuration")
    ok(run(s, "configure vlan v10 delete ports 24"))
    assert p.mode == "access" and p.pvid == 1
    ok(run(s, "delete vlan v10"))
    assert 10 not in x.vlans and not x.svis()


def test_exos_ospf_with_cisco_router():
    t = Topology()
    x = t.add_device("extreme-x440g2")
    r = t.add_device("cisco-2911")
    t.connect(x.get_port("1"), r.get_port("gi0/0"))
    run_all(r.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 10.0.0.2 255.255.255.0\n"
                            "no shutdown\ninterface loopback0\nip address 2.2.2.2 255.255.255.255\nrouter ospf 1\n"
                            "network 10.0.0.0 0.0.0.255 area 0\nnetwork 2.2.2.2 0.0.0.0 area 0\nend")
    s = x.create_cli()
    ok(run_all(s, "create vlan transit tag 100\nconfigure vlan default delete ports 1,5\n"
                  "configure vlan transit add ports 1 untagged\nconfigure vlan transit ipaddress 10.0.0.1/24\n"
                  "create vlan users tag 10\nconfigure vlan users ipaddress 10.0.10.1/24\n"
                  "configure vlan users add ports 5 untagged\nenable ipforwarding\n"
                  "configure ospf routerid 1.1.1.1\nconfigure ospf add vlan transit area 0.0.0.0\n"
                  "enable ospf"))
    t.engine.run(max_time=60_000)
    assert "FULL" in run(s, "show ospf neighbor")
    assert "#oa  2.2.2.2/32" in run(s, "show iproute")
    assert "enable ospf" in run(s, "show configuration")


import pytest  # noqa: E402


@pytest.mark.parametrize("kind,cmd", [("ospf6", "configure ospfv3 add vlan {v} area 0.0.0.0\nenable ospfv3"),
                                      ("ripng", "configure ripng add vlan {v}\nenable ripng")])
def test_exos_ipv6_igp(kind, cmd):
    from test_ipv6_core import ping6
    t = Topology()
    x1, x2 = t.add_device("extreme-x440g2"), t.add_device("extreme-x440g2")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(x1.get_port("24"), x2.get_port("24"))
    t.connect(a.nic, x1.get_port("1"))
    t.connect(b.nic, x2.get_port("1"))
    for i, x in ((1, x1), (2, x2)):
        ok(run_all(x.create_cli(), "create vlan lan tag 10\ncreate vlan core tag 99\n"
                                   "configure vlan default delete ports 1,24\nconfigure vlan lan add ports 1 untagged\n"
                                   "configure vlan core add ports 24 untagged\n"
                                   f"configure vlan lan ipaddress 2001:db8:{i}::1/64\n"
                                   f"configure vlan core ipaddress 2001:db8:99::{i}/64\nenable ipforwarding ipv6\n"
                                   f"configure ospfv3 routerid {i}.{i}.{i}.{i}\n"
                                   + cmd.format(v="lan") + "\n" + cmd.format(v="core").split("\n")[0]))
    a.set_ipv6("2001:db8:1::10/64")
    a.set_gateway6("2001:db8:1::1")
    b.set_ipv6("2001:db8:2::10/64")
    b.set_gateway6("2001:db8:2::1")
    t.engine.run(max_time=12000)
    assert ping6(t, a, "2001:db8:2::10").ok == 4
    if kind == "ospf6":
        assert "FULL" in run(x1.create_cli(), "show ospfv3 neighbor")


def test_exos_limit_learning_and_dhcp_snooping():
    t = Topology()
    x = t.add_device("extreme-x440g2")
    hub = t.add_device("hub")
    a, b, c = t.add_device("pc"), t.add_device("pc"), t.add_device("pc")
    t.connect(hub.ports["Port0"], x.get_port("1"))
    t.connect(a.nic, hub.ports["Port1"])
    t.connect(b.nic, hub.ports["Port2"])
    t.connect(c.nic, x.get_port("2"))
    s = x.create_cli()
    ok(run_all(s, "configure ports 1 vlan Default limit-learning 1 action blackhole\n"
                  "enable ip-security dhcp-snooping vlan Default ports all violation-action drop-packet\n"
                  "configure trusted-ports 24 trust-for dhcp-server"))
    for h, ip in ((a, "10.0.0.1"), (b, "10.0.0.2"), (c, "10.0.0.3")):
        h.set_ip(ip, "255.255.255.0")
    t.engine.run(max_time=1000)
    from test_vlan import ping as vping
    assert vping(None, a, "10.0.0.3").received == 4
    assert vping(None, b, "10.0.0.3").received == 0          # second MAC on port 1 is blackholed
    assert "blackhole" in run(s, "show fdb limit-learning")
    assert x.dhcp_snooping["enabled"] and x.get_port("24").dhcp_trust
