"""EIGRP: adjacencies, D routes, feasible successor failover, bandwidth/delay metrics, redistribution."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def cfg(r, lines):
    out = run_all(r.create_cli(), "enable\nconfigure terminal\n" + lines + "\nend")
    assert "Invalid" not in out and "%" not in out.replace("%SYS-5", "").replace("%LINK", "").replace("%LINEPROTO", ""), out


def triangle():
    """R1 - R2 - R3 and a direct R1 - R3 link; LANs behind R1 and R3."""
    t = Topology()
    r1, r2, r3 = (t.add_device("cisco-2911", f"R{i}") for i in (1, 2, 3))
    pc1, pc3 = t.add_device("pc"), t.add_device("pc")
    t.connect(r1.get_port("gi0/1"), r2.get_port("gi0/1"))
    t.connect(r2.get_port("gi0/2"), r3.get_port("gi0/1"))
    t.connect(r1.get_port("gi0/2"), r3.get_port("gi0/2"))
    t.connect(pc1.nic, r1.get_port("gi0/0"))
    t.connect(pc3.nic, r3.get_port("gi0/0"))
    ips = {r1: ("192.168.1.1", "10.0.12.1", "10.0.13.1"), r2: (None, "10.0.12.2", "10.0.23.1"),
           r3: ("192.168.3.1", "10.0.23.2", "10.0.13.2")}
    for r, (lan, a, b) in ips.items():
        lines = []
        if lan:
            lines += ["interface gi0/0", f"ip address {lan} 255.255.255.0", "no shutdown"]
        lines += ["interface gi0/1", f"ip address {a} 255.255.255.252", "no shutdown",
                  "interface gi0/2", f"ip address {b} 255.255.255.252", "no shutdown", "exit",
                  "router eigrp 100", "network 10.0.0.0 0.255.255.255", "network 192.168.0.0 0.0.255.255",
                  "no auto-summary"]
        cfg(r, "\n".join(lines))
    pc1.set_ip("192.168.1.10", "255.255.255.0")
    pc1.set_gateway("192.168.1.1")
    pc3.set_ip("192.168.3.10", "255.255.255.0")
    pc3.set_gateway("192.168.3.1")
    t.engine.run(max_time=20000)
    return t, r1, r2, r3, pc1, pc3


def via(r, net):
    return [str(x.next_hop) for x in r.routing_table() if str(x.network) == net and x.code == "D"]


def test_eigrp_converges_and_uses_feasible_successor():
    t, r1, r2, r3, pc1, pc3 = triangle()
    s = r1.create_cli()
    run(s, "enable")
    assert run(s, "show ip eigrp neighbors").count("10.0.1") == 2
    assert via(r1, "192.168.3.0/24") == ["10.0.13.2"]                  # direct link is best
    topo = run(s, "show ip eigrp topology")
    assert "192.168.3.0/24, 1 successors" in topo and "via 10.0.12.2" in topo   # feasible successor listed
    assert "D        192.168.3.0" in run(s, "show ip route") or "D    192.168.3.0" in run(s, "show ip route")
    assert _ping(None, pc1, "192.168.3.10").received == 4
    # slow down the direct link: traffic moves to R2
    cfg(r1, "interface gi0/2\ndelay 100000")
    cfg(r3, "interface gi0/2\ndelay 100000")
    t.engine.run(max_time=t.engine.now + 6000)
    assert via(r1, "192.168.3.0/24") == ["10.0.12.2"]
    cfg(r1, "interface gi0/2\nno delay")
    cfg(r3, "interface gi0/2\nno delay")
    t.engine.run(max_time=t.engine.now + 6000)
    assert via(r1, "192.168.3.0/24") == ["10.0.13.2"]
    # the direct link fails: the feasible successor takes over at once
    r1.get_port("gi0/2").set_admin(False)
    t.engine.run(max_time=t.engine.now + 2000)
    assert via(r1, "192.168.3.0/24") == ["10.0.12.2"]
    assert _ping(None, pc1, "192.168.3.10").received == 4
    assert "%DUAL-5-NBRCHANGE" in run(s, "show logging")


def test_eigrp_redistribute_static_and_persistence():
    t, r1, r2, r3, pc1, pc3 = triangle()
    cfg(r3, "ip route 172.16.0.0 255.255.0.0 null0\nrouter eigrp 100\nredistribute static" if False else
        "ip route 172.16.0.0 255.255.0.0 192.168.3.10\nrouter eigrp 100\nredistribute static")
    t.engine.run(max_time=t.engine.now + 6000)
    ex = [x for x in r1.routing_table() if str(x.network) == "172.16.0.0/16"]
    assert ex and ex[0].code == "D EX" and ex[0].distance == 170
    s = r3.create_cli()
    run(s, "enable")
    rc = run(s, "show running-config")
    assert "router eigrp 100" in rc and " network 10.0.0.0 0.255.255.255" in rc and " redistribute static" in rc
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=20000)
    assert via(t2.devices[r1.id], "192.168.3.0/24")
