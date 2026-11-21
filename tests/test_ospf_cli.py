"""OSPF configured through each vendor CLI; multi-vendor adjacency and show commands."""

from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_vlan import ping

BAD = ("Invalid", "Error", "error", "bad command", "syntax", "Incomplete", "failure")


def ok(out):
    assert not any(b in out for b in BAD), out
    return out


def converge(t, seconds=15):
    t.engine.run(max_time=seconds * 1000)


def pc(t, ip, gw):
    h = t.add_device("pc")
    h.set_ip(ip, "255.255.255.0")
    h.set_gateway(gw)
    return h


def test_cisco_ospf_two_routers():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    a, b = pc(t, "192.168.1.10", "192.168.1.1"), pc(t, "192.168.2.10", "192.168.2.1")
    t.connect(a.nic, r1.get_port("gi0/1"))
    t.connect(b.nic, r2.get_port("gi0/1"))
    for r, n, lan in ((r1, 1, "192.168.1.1"), (r2, 2, "192.168.2.1")):
        ok(run_all(r.create_cli(), f"""
            enable
            configure terminal
            hostname R{n}
            interface loopback0
            ip address {n}.{n}.{n}.{n} 255.255.255.255
            interface gi0/0
            ip address 10.0.12.{n} 255.255.255.252
            no shutdown
            interface gi0/1
            ip address {lan} 255.255.255.0
            no shutdown
            router ospf 1
            network 10.0.12.0 0.0.0.3 area 0
            network {lan.rsplit('.', 1)[0]}.0 0.0.0.255 area 0
            network {n}.{n}.{n}.{n} 0.0.0.0 area 0
            passive-interface gi0/1
            end"""))
    converge(t)
    s = r1.create_cli()
    run(s, "enable")
    nb = run(s, "show ip ospf neighbor")
    assert "2.2.2.2" in nb and "FULL/" in nb and "10.0.12.2" in nb and "GigabitEthernet0/0" in nb
    rt = run(s, "show ip route")
    assert "O       192.168.2.0/24 [110/2] via 10.0.12.2" in rt or "O    192.168.2.0 [110/2]" in rt \
        or "O    192.168.2.0/24 [110/2] via 10.0.12.2" in rt
    assert "2.2.2.2/32 [110/2] via 10.0.12.2" in rt
    assert ping(t, a, "192.168.2.10").received == 4
    br = run(s, "show ip ospf interface brief")
    assert "Gi0/0" in br and "Lo0" in br and "LOOP" in br
    assert "Passive" in run(s, "show ip ospf interface")
    db = run(s, "show ip ospf database")
    assert "Router Link States (Area 0)" in db and "2.2.2.2" in db
    pr = run(s, "show ip protocols")
    assert 'Routing Protocol is "ospf 1"' in pr and "10.0.12.0 0.0.0.3 area 0" in pr
    cfg = run(s, "show running-config")
    assert "router ospf 1" in cfg and " network 10.0.12.0 0.0.0.3 area 0" in cfg
    assert "interface Loopback0" in cfg and " passive-interface GigabitEthernet0/1" in cfg
    assert run(s, "show ip route ospf").count("[110/") == 2
    # interface cost changes the metric
    run_all(s, "conf t\ninterface gi0/0\nip ospf cost 50\nend")
    converge(t, 3)
    assert "[110/51] via 10.0.12.2" in run(s, "show ip route")
    # default route originated by R2
    out = run_all(r2.create_cli(), "enable\nconf t\nip route 0.0.0.0 0.0.0.0 gi0/1\nrouter ospf 1\n"
                                   "default-information originate\nend")
    assert r2.static_routes and r2.ospf.cfg.default_originate == "yes", out
    converge(t, 3)
    rt = run(s, "show ip route")
    assert "O*E2  0.0.0.0/0 [110/1] via 10.0.12.2" in rt and "Gateway of last resort is 10.0.12.2" in rt
    # no router ospf removes the routes but keeps ip ospf cost
    run_all(s, "conf t\nno router ospf 1\nend")
    assert "[110/" not in run(s, "show ip route")
    assert " ip ospf cost 50" in run(s, "show running-config")


def test_cisco_ospf_mismatched_area_no_adjacency():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    for r, n, area in ((r1, 1, 0), (r2, 2, 1)):
        ok(run_all(r.create_cli(), f"enable\nconf t\ninterface gi0/0\nip address 10.0.0.{n} 255.255.255.0\n"
                                   f"no shutdown\nrouter ospf 1\nnetwork 10.0.0.0 0.0.0.255 area {area}\nend"))
    converge(t)
    assert run(r1.create_cli(), "show ip ospf neighbor").count("10.0.0.2") == 0


def test_huawei_ospf_with_cisco_neighbor():
    t = Topology()
    hw, cs = t.add_device("huawei-ar2220"), t.add_device("cisco-2911")
    t.connect(hw.get_port("GE0/0/0"), cs.get_port("gi0/0"))
    a, b = pc(t, "192.168.1.10", "192.168.1.1"), pc(t, "192.168.2.10", "192.168.2.1")
    t.connect(a.nic, hw.get_port("GE0/0/1"))
    t.connect(b.nic, cs.get_port("gi0/1"))
    s = hw.create_cli()
    ok(run_all(s, """
        system-view
        sysname AR1
        interface LoopBack0
        ip address 1.1.1.1 32
        interface GigabitEthernet0/0/0
        ip address 10.0.12.1 30
        ospf cost 5
        quit
        interface GigabitEthernet0/0/1
        ip address 192.168.1.1 24
        quit
        ospf 1 router-id 1.1.1.1
        silent-interface GigabitEthernet0/0/1
        area 0
        network 10.0.12.0 0.0.0.3
        network 192.168.1.0 0.0.0.255
        network 1.1.1.1 0.0.0.0
        return"""))
    ok(run_all(cs.create_cli(), """
        enable
        configure terminal
        interface gi0/0
        ip address 10.0.12.2 255.255.255.252
        no shutdown
        interface gi0/1
        ip address 192.168.2.1 255.255.255.0
        no shutdown
        router ospf 1
        router-id 2.2.2.2
        network 10.0.0.0 0.255.255.255 area 0
        network 192.168.2.0 0.0.0.255 area 0
        end"""))
    converge(t)
    peer = run(s, "display ospf peer brief")
    assert "0.0.0.0" in peer and "GigabitEthernet0/0/0" in peer and "2.2.2.2" in peer and "Full" in peer
    assert "Address: 10.0.12.2" in run(s, "display ospf peer")
    rt = run(s, "display ip routing-table")
    assert "192.168.2.0/24 OSPF    10   6" in rt           # Huawei preference 10, cost 5 + 1
    assert "DR" in run(s, "display ospf interface") and "Router" in run(s, "display ospf lsdb")
    cur = run(s, "display current-configuration")
    assert "ospf 1 router-id 1.1.1.1" in cur and "  network 10.0.12.0 0.0.0.3" in cur
    assert " ospf cost 5" in cur and " silent-interface GigabitEthernet0/0/1" in cur
    assert "interface LoopBack0" in cur
    assert ping(t, a, "192.168.2.10").received == 4
    cs_rt = run(cs.create_cli(), "show ip route")
    assert "1.1.1.1/32 [110/2] via 10.0.12.1" in cs_rt
    run_all(s, "system-view\nundo ospf 1\nreturn")
    assert hw.ospf is None or not hw.ospf.running


def test_junos_ospf_with_export_policy():
    t = Topology()
    jn, cs = t.add_device("juniper-srx300"), t.add_device("cisco-2911")
    t.connect(jn.get_port("ge-0/0/0"), cs.get_port("gi0/0"))
    a = pc(t, "192.168.3.10", "192.168.3.1")
    t.connect(a.nic, jn.get_port("ge-0/0/1"))
    s = jn.create_cli()
    out = ok(run_all(s, """
        configure
        set system host-name SRX1
        set interfaces lo0 unit 0 family inet address 3.3.3.3/32
        set interfaces ge-0/0/0 unit 0 family inet address 10.0.23.1/30
        set interfaces ge-0/0/1 unit 0 family inet address 192.168.3.1/24
        set routing-options router-id 3.3.3.3
        set routing-options static route 172.16.0.0/16 next-hop 192.168.3.10
        set protocols ospf area 0.0.0.0 interface ge-0/0/0.0
        set protocols ospf area 0.0.0.0 interface ge-0/0/1.0 passive
        set protocols ospf area 0.0.0.0 interface lo0.0 passive
        set protocols ospf export STATIC
        set policy-options policy-statement STATIC term 1 from protocol static
        set policy-options policy-statement STATIC term 1 then accept
        commit and-quit"""))
    assert "commit complete" in out
    ok(run_all(cs.create_cli(), """
        enable
        configure terminal
        interface gi0/0
        ip address 10.0.23.2 255.255.255.252
        no shutdown
        router ospf 1
        network 10.0.23.0 0.0.0.3 area 0
        end"""))
    converge(t)
    nb = run(s, "show ospf neighbor")
    assert "10.0.23.2" in nb and "ge-0/0/0.0" in nb and "Full" in nb
    assert "lo0.0" in run(s, "show ospf interface")
    assert "OSPF database, Area 0.0.0.0" in run(s, "show ospf database")
    cs_rt = run(cs.create_cli(), "show ip route")
    assert "3.3.3.3/32 [110/1] via 10.0.23.1" in cs_rt          # lo0 costs 0 on Junos
    assert "O E2" in cs_rt and "172.16.0.0/16 [110/20] via 10.0.23.1" in cs_rt
    run_all(cs.create_cli(), "enable\nconf t\ninterface loopback1\nip address 2.2.2.2 255.255.255.255\n"
                             "router ospf 1\nnetwork 2.2.2.2 0.0.0.0 area 0\nend")
    converge(t, 3)
    rt = run(s, "show route")
    assert "2.2.2.2/32" in rt and "[OSPF/10]" in rt and "metric 2" in rt
    cfg = run(s, "show configuration")
    assert "router-id 3.3.3.3;" in cfg and "interface lo0.0 {" in cfg and "export STATIC;" in cfg
    assert "from protocol static;" in cfg
    assert "Policy XYZ referenced but not defined" in run_all(
        s, "configure\nset protocols ospf export XYZ\ncommit")


def test_mikrotik_ospf_v7_with_huawei():
    t = Topology()
    mt, hw = t.add_device("mikrotik-rb4011"), t.add_device("huawei-ar2220")
    t.connect(mt.get_port("ether1"), hw.get_port("GE0/0/0"))
    a = pc(t, "192.168.4.10", "192.168.4.1")
    t.connect(a.nic, mt.get_port("ether2"))
    s = mt.create_cli()
    ok(run_all(s, """
        /system identity set name=MT1
        /interface bridge add name=loopback
        /ip address add address=4.4.4.4/32 interface=loopback
        /ip address add address=10.0.34.1/30 interface=ether1
        /ip address add address=192.168.4.1/24 interface=ether2
        /routing ospf instance add name=default-v2 router-id=4.4.4.4
        /routing ospf area add name=backbone-v2 area-id=0.0.0.0 instance=default-v2
        /routing ospf interface-template add networks=10.0.34.0/30 area=backbone-v2
        /routing ospf interface-template add networks=192.168.4.0/24,4.4.4.4/32 area=backbone-v2 passive
        """))
    ok(run_all(hw.create_cli(), """
        system-view
        interface GigabitEthernet0/0/0
        ip address 10.0.34.2 30
        quit
        interface LoopBack0
        ip address 5.5.5.5 32
        quit
        ospf 1 router-id 5.5.5.5
        area 0.0.0.0
        network 10.0.34.0 0.0.0.3
        network 5.5.5.5 0.0.0.0
        return"""))
    converge(t)
    nb = run(s, "/routing ospf neighbor print")
    assert "router-id=5.5.5.5" in nb and 'state="Full"' in nb and "address=10.0.34.2" in nb
    rt = run(s, "/ip route print")
    assert "DAo" in rt and "5.5.5.5/32" in rt and "10.0.34.2%ether1" in rt
    assert "backbone-v2" in run(s, "/routing ospf interface print")
    assert "router" in run(s, "/routing ospf lsa print")
    hw_rt = run(hw.create_cli(), "display ip routing-table")
    assert "192.168.4.0/24 OSPF    10   2" in hw_rt and "4.4.4.4/32 OSPF    10   2" in hw_rt
    exp = run(s, "/export")
    assert "/routing ospf instance" in exp and "add area=backbone-v2 networks=10.0.34.0/30" in exp
    assert "add name=loopback" in exp
    run(s, "/routing ospf instance set default-v2 originate-default=always")
    converge(t, 3)
    assert "0.0.0.0/0 O_ASE   150  1" in run(hw.create_cli(), "display ip routing-table")
    assert "input does not match any value of area" in run(
        s, "/routing ospf interface-template add networks=1.0.0.0/8 area=nope")


def test_example_ospf_lab_converges_after_loading():
    from pathlib import Path
    from netemu.core import persistence
    path = Path(__file__).resolve().parents[1] / "examples" / "ospf-lab.netemu"
    t = persistence.load(path, Topology())
    converge(t, 20)
    pcs = sorted((d for d in t.devices.values() if d.category == "pc"), key=lambda d: str(d.nic.ip))
    for target in ("192.168.2.10", "192.168.3.10", "192.168.4.10"):
        ping(t, pcs[0], target)
        assert ping(t, pcs[0], target).received == 4, target
    r1 = t.find("R1")
    assert len([n for n in r1.ospf.neighbors.values() if n.two_way]) == 2
    assert {"MT1", "SRX1", "AR1", "R1"} <= {d.name for d in t.devices.values()}


def test_cisco_ecmp_route_is_one_subnet_in_show_ip_route():
    from test_ospf_core import ring, converge as conv
    t, r, h = ring()
    conv(t)
    tbl = r[0].routing_table()
    unique = {x.network for x in tbl if x.network.subnet_of(__import__("ipaddress").IPv4Network("10.0.0.0/8"))}
    out = run(r[0].create_cli(), "show ip route")
    header = next(l for l in out.splitlines() if l.strip().startswith("10.0.0.0/8 is"))
    assert f"{len(unique)} subnets" in header                  # 10.3.0.0/24 has two paths, counted once
    assert len([x for x in tbl if str(x.network) == "10.3.0.0/24"]) == 2
