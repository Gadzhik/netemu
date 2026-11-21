"""RIP through all four vendor CLIs in one chain: Cisco - Huawei - Juniper - MikroTik."""
from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_ospf_cli import ok
from test_vlan import ping


def test_rip_four_vendors():
    t = Topology()
    cs, hw, jn, mt = (t.add_device(m) for m in ("cisco-2911", "huawei-ar2220", "juniper-srx300", "mikrotik-rb4011"))
    t.connect(cs.get_port("gi0/0"), hw.get_port("GE0/0/0"))
    t.connect(hw.get_port("GE0/0/1"), jn.get_port("ge-0/0/0"))
    t.connect(jn.get_port("ge-0/0/1"), mt.get_port("ether1"))
    pcs = []
    for dev, port, n in ((cs, "gi0/1", 1), (mt, "ether2", 4)):
        h = t.add_device("pc")
        h.set_ip(f"192.168.{n}.10", "255.255.255.0")
        h.set_gateway(f"192.168.{n}.1")
        t.connect(h.nic, dev.get_port(port))
        pcs.append(h)
    ok(run_all(cs.create_cli(), """
        enable
        configure terminal
        interface gi0/0
        ip address 10.0.12.1 255.255.255.0
        no shutdown
        interface gi0/1
        ip address 192.168.1.1 255.255.255.0
        no shutdown
        router rip
        version 2
        network 10.0.0.0
        network 192.168.1.0
        passive-interface gi0/1
        no auto-summary
        end"""))
    ok(run_all(hw.create_cli(), """
        system-view
        interface GigabitEthernet0/0/0
        ip address 10.0.12.2 24
        quit
        interface GigabitEthernet0/0/1
        ip address 10.0.23.1 24
        quit
        rip 1
        version 2
        undo summary
        network 10.0.0.0
        return"""))
    ok(run_all(jn.create_cli(), """
        configure
        set interfaces ge-0/0/0 unit 0 family inet address 10.0.23.2/24
        set interfaces ge-0/0/1 unit 0 family inet address 10.0.34.1/24
        set policy-options policy-statement RIP-EXPORT term 1 from protocol direct
        set policy-options policy-statement RIP-EXPORT term 1 from protocol rip
        set policy-options policy-statement RIP-EXPORT term 1 then accept
        set protocols rip group RIP neighbor ge-0/0/0.0
        set protocols rip group RIP neighbor ge-0/0/1.0
        set protocols rip group RIP export RIP-EXPORT
        commit and-quit"""))
    ok(run_all(mt.create_cli(), """
        /ip address add address=10.0.34.2/24 interface=ether1
        /ip address add address=192.168.4.1/24 interface=ether2
        /routing rip instance add name=rip1 redistribute=connected
        /routing rip interface-template add interfaces=ether1 instance=rip1
        """))
    t.engine.run(max_time=15_000)
    cs_rt = run(cs.create_cli(), "show ip route")
    assert "R    192.168.4.0/24 [120/" in cs_rt
    assert ping(t, pcs[0], "192.168.4.10").received == 4
    assert "RIP" in run(hw.create_cli(), "display ip routing-table") and "Peer 10.0.12.1" in run(hw.create_cli(), "display rip 1 route")
    rt = run(jn.create_cli(), "show route protocol rip")
    assert "[RIP/100]" in rt and "192.168.1.0/24" in rt
    assert "ge-0/0/0.0" in run(jn.create_cli(), "show rip neighbor")
    assert "DAr" in run(mt.create_cli(), "/ip route print")
    cs_s = cs.create_cli()
    run(cs_s, "enable")
    assert "router rip" in run(cs_s, "show running-config") and "192.168.4.0/24" in run(cs_s, "show ip rip database")
    assert "rip {" in run(jn.create_cli(), "show configuration") and "rip 1" in run(hw.create_cli(), "display current-configuration")
    assert "/routing rip instance" in run(mt.create_cli(), "/export")
