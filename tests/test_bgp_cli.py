"""BGP through the vendor CLIs."""
from cli_helpers import run, run_all
from test_bgp_core import ebgp_pair, ibgp_chain, run as converge
from test_ospf_cli import ok
from test_vlan import ping


def test_cisco_ebgp_show_commands():
    t, r1, r2, a, b = ebgp_pair()
    s1 = r1.create_cli()
    ok(run_all(s1, """
        enable
        configure terminal
        hostname R1
        interface loopback0
        ip address 1.1.1.1 255.255.255.255
        router bgp 65001
        bgp router-id 1.1.1.1
        neighbor 10.0.12.2 remote-as 65002
        neighbor 10.0.12.2 description ISP-B
        network 192.168.1.0
        network 1.1.1.1 mask 255.255.255.255
        end"""))
    ok(run_all(r2.create_cli(), """
        enable
        configure terminal
        hostname R2
        router bgp 65002
        neighbor 10.0.12.1 remote-as 65001
        network 192.168.2.0 mask 255.255.255.0
        end"""))
    converge(t)
    summ = run(s1, "show ip bgp summary")
    assert "BGP router identifier 1.1.1.1, local AS number 65001" in summ
    row = next(l for l in summ.splitlines() if l.startswith("10.0.12.2"))
    assert row.split()[2] == "65002" and row.split()[-1] == "1"
    tbl = run(s1, "show ip bgp")
    assert "*> 192.168.2.0" in tbl and "65002 i" in tbl and "*> 1.1.1.1/32" in tbl and "32768 i" in tbl
    rt = run(s1, "show ip route")
    assert "B    192.168.2.0/24 [20/0] via 10.0.12.2" in rt
    assert run(s1, "show ip route bgp").count("[20/0]") == 1
    nb = run(s1, "show ip bgp neighbors 10.0.12.2")
    assert "remote AS 65002, external link" in nb and "BGP state = Established" in nb and "ISP-B" in nb
    cfg = run(s1, "show running-config")
    assert "router bgp 65001" in cfg and " neighbor 10.0.12.2 remote-as 65002" in cfg
    assert " network 192.168.1.0" in cfg and " network 1.1.1.1 mask 255.255.255.255" in cfg
    assert ping(t, a, "192.168.2.10").received == 4
    assert "1.1.1.1/32" in run(r2.create_cli(), "show ip bgp")
    # wrong AS on the peer -> notification, session stays down
    run_all(r2.create_cli(), "enable\nconf t\nrouter bgp 65002\nno neighbor 10.0.12.1\nneighbor 10.0.12.1 remote-as 65009\nend")
    converge(t, 10)
    row = next(l for l in run(s1, "show ip bgp summary").splitlines() if l.startswith("10.0.12.2"))
    assert row.split()[-1] in ("Idle", "Active")
    run_all(s1, "conf t\nrouter bgp 65001\nneighbor 10.0.12.2 shutdown\nend")
    assert "Idle (Admin)" in run(s1, "show ip bgp summary")


def test_cisco_ibgp_next_hop_self_via_cli():
    t, r1, r2, r3, a, c = ibgp_chain(nhs=False)
    s2 = r2.create_cli()
    converge(t, 30)
    s3 = r3.create_cli()
    run(s3, "enable")
    assert "*>i" not in run(s3, "show ip bgp").split("192.168.1.0")[0][-6:]
    ok(run_all(s2, "enable\nconfigure terminal\nrouter bgp 65002\nneighbor 3.3.3.3 next-hop-self\nend\nclear ip bgp 3.3.3.3"))
    converge(t, 10)
    tbl = run(s3, "show ip bgp")
    line = next(l for l in tbl.splitlines() if "192.168.1.0" in l)
    assert line.startswith("*>i") and "2.2.2.2" in line and "100" in line and "65001 i" in line
    assert "B    192.168.1.0/24 [200/0] via 2.2.2.2" in run(s3, "show ip route")
    assert ping(t, c, "192.168.1.10").received == 4


def test_huawei_ebgp_with_cisco():
    from test_bgp_core import ip, lan
    from netemu.core.topology import Topology
    t = Topology()
    hw, cs = t.add_device("huawei-ar2220"), t.add_device("cisco-2911")
    t.connect(hw.get_port("GE0/0/0"), cs.get_port("gi0/0"))
    ip(cs, "gi0/0", "10.0.12.2/30")
    a = lan(t, hw, "GE0/0/1", 1)
    lan(t, cs, "gi0/1", 2)
    s = hw.create_cli()
    ok(run_all(s, """
        system-view
        interface GigabitEthernet0/0/0
        ip address 10.0.12.1 30
        quit
        bgp 65001
        router-id 1.1.1.1
        peer 10.0.12.2 as-number 65002
        ipv4-family unicast
        network 192.168.1.0 255.255.255.0
        peer 10.0.12.2 enable
        return"""))
    ok(run_all(cs.create_cli(), "enable\nconf t\nrouter bgp 65002\nneighbor 10.0.12.1 remote-as 65001\n"
                                "network 192.168.2.0\nend"))
    converge(t)
    peer = run(s, "display bgp peer")
    assert "10.0.12.2" in peer and "65002" in peer and "Established" in peer and "Peers in established state : 1" in peer
    tbl = run(s, "display bgp routing-table")
    assert "*>   192.168.2.0" in tbl and "65002i" in tbl and "*>   192.168.1.0" in tbl
    rt = run(s, "display ip routing-table")
    assert "192.168.2.0/24 EBGP    255" in rt
    cur = run(s, "display current-configuration")
    assert "bgp 65001" in cur and " peer 10.0.12.2 as-number 65002" in cur and "  network 192.168.1.0 255.255.255.0" in cur
    assert ping(t, a, "192.168.2.10").received == 4
    assert "another AS" in run_all(s, "system-view\nbgp 65001\npeer 10.0.12.2 as-number 65099")


def test_junos_ebgp_with_export_policy():
    from test_bgp_core import ip, lan
    from netemu.core.topology import Topology
    t = Topology()
    jn, cs = t.add_device("juniper-srx300"), t.add_device("cisco-2911")
    t.connect(jn.get_port("ge-0/0/0"), cs.get_port("gi0/0"))
    ip(cs, "gi0/0", "10.0.23.1/30")
    lan(t, cs, "gi0/1", 2)
    hc = t.add_device("pc")
    hc.set_ip("192.168.3.10", "255.255.255.0")
    hc.set_gateway("192.168.3.1")
    t.connect(hc.nic, jn.get_port("ge-0/0/1"))
    s = jn.create_cli()
    out = ok(run_all(s, """
        configure
        set interfaces ge-0/0/0 unit 0 family inet address 10.0.23.2/30
        set interfaces ge-0/0/1 unit 0 family inet address 192.168.3.1/24
        set routing-options router-id 3.3.3.3
        set routing-options autonomous-system 65003
        set policy-options policy-statement ADV term 1 from protocol direct
        set policy-options policy-statement ADV term 1 from route-filter 192.168.3.0/24 exact
        set policy-options policy-statement ADV term 1 then accept
        set protocols bgp group EXT type external
        set protocols bgp group EXT peer-as 65002
        set protocols bgp group EXT neighbor 10.0.23.1
        set protocols bgp group EXT export ADV
        commit and-quit"""))
    assert "commit complete" in out
    ok(run_all(cs.create_cli(), "enable\nconf t\nrouter bgp 65002\nneighbor 10.0.23.2 remote-as 65003\n"
                                "network 192.168.2.0\nend"))
    converge(t)
    summ = run(s, "show bgp summary")
    assert "Groups: 1 Peers: 1 Down peers: 0" in summ and "10.0.23.1" in summ and "1/1/1/0" in summ
    rt = run(s, "show route protocol bgp")
    assert "192.168.2.0/24" in rt and "[BGP/170]" in rt and "AS path: 65002 I" in rt
    assert "Type: External    State: Established" in run(s, "show bgp neighbor 10.0.23.1")
    cs_bgp = run(cs.create_cli(), "show ip bgp")
    assert "192.168.3.0" in cs_bgp and "65003 i" in cs_bgp and "10.0.23.0" not in cs_bgp   # route-filter
    cfg = run(s, "show configuration")
    assert "autonomous-system 65003;" in cfg and "group EXT {" in cfg and "export ADV;" in cfg
    assert "from route-filter 192.168.3.0/24 exact;" in cfg
    assert ping(t, hc, "192.168.2.10").received == 4
    err = run_all(s, "configure\nset protocols bgp group X neighbor 1.2.3.4\ncommit")
    assert "peer-as is not configured" in err


def test_mikrotik_v7_bgp_with_huawei():
    from test_bgp_core import ip, lan
    from netemu.core.topology import Topology
    t = Topology()
    mt, hw = t.add_device("mikrotik-rb4011"), t.add_device("huawei-ar2220")
    t.connect(mt.get_port("ether1"), hw.get_port("GE0/0/0"))
    ip(hw, "GE0/0/0", "10.0.45.2/30")
    lan(t, hw, "GE0/0/1", 5)
    hm = t.add_device("pc")
    hm.set_ip("192.168.4.10", "255.255.255.0")
    hm.set_gateway("192.168.4.1")
    t.connect(hm.nic, mt.get_port("ether2"))
    s = mt.create_cli()
    ok(run_all(s, """
        /ip address add address=10.0.45.1/30 interface=ether1
        /ip address add address=192.168.4.1/24 interface=ether2
        /ip firewall address-list add list=bgp-networks address=192.168.4.0/24
        /routing bgp connection add name=to-hw remote.address=10.0.45.2 remote.as=65005 as=65004 local.role=ebgp router-id=4.4.4.4 output.network=bgp-networks
        """))
    ok(run_all(hw.create_cli(), "system-view\nbgp 65005\nrouter-id 5.5.5.5\npeer 10.0.45.1 as-number 65004\n"
                                "network 192.168.5.0 24\nreturn"))
    converge(t)
    sess = run(s, "/routing bgp session print")
    assert " E " in sess and "remote.address=10.0.45.2 .as=65005" in sess and "prefix-count=1" in sess
    rt = run(s, "/ip route print")
    assert "DAb" in rt and "192.168.5.0/24" in rt and "10.0.45.2" in rt
    assert "192.168.4.0/24" in run(s, "/routing bgp advertisements print")
    assert "192.168.4.0" in run(hw.create_cli(), "display bgp routing-table")
    exp = run(s, "/export")
    assert "/routing bgp connection" in exp and "remote.as=65005" in exp and "list=bgp-networks" in exp
    assert ping(t, hm, "192.168.5.10").received == 4
    run(s, "/routing bgp connection remove to-hw")
    converge(t, 3)
    assert mt.bgp is None and not any(r.code == "B" for r in mt.routing_table())


def test_example_bgp_lab_converges_after_loading():
    from pathlib import Path
    from netemu.core import persistence
    from netemu.core.topology import Topology
    t = persistence.load(Path(__file__).resolve().parents[1] / "examples" / "bgp-lab.netemu", Topology())
    converge(t, 40)
    pcs = sorted((d for d in t.devices.values() if d.category == "pc"), key=lambda d: str(d.nic.ip))
    for target in ("192.168.2.10", "192.168.3.10"):
        ping(t, pcs[0], target)
        assert ping(t, pcs[0], target).received == 4, target
    r1 = t.find("R1")
    assert all(p.state == "Established" for p in r1.bgp.peers.values()) and len(r1.bgp.peers) == 3
    assert "Established" in run(t.find("SRX1").create_cli(), "show bgp neighbor")
