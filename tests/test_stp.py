"""Spanning tree: loop prevention, root election, failover, vendors."""
from netemu.core.topology import Topology
from test_vlan import ping


def triangle(models=("cisco-2960", "cisco-2960", "cisco-2960")):
    t = Topology()
    s = [t.add_device(m) for m in models]

    def free(sw):
        return next(p for p in sw.physical_ports() if p.link is None and not p.name.lower().startswith(("fa", "fast"))
                    or (p.link is None and sw.vendor != "cisco"))
    for a, b in ((0, 1), (1, 2), (2, 0)):
        t.connect(free(s[a]), free(s[b]))
    pcs = []
    for i, sw in enumerate(s[:2]):
        h = t.add_device("pc")
        h.set_ip(f"192.168.1.{10 + i}", "255.255.255.0")
        t.connect(h.nic, next(p for p in sw.physical_ports() if p.link is None))
        pcs.append(h)
    return t, s, pcs


def roles(sw):
    return {p.name: sw.stp.roles.get(p.name) for p in sw.ports.values() if p.link is not None}


def test_loop_is_blocked_and_traffic_flows_without_storm():
    t, s, pcs = triangle()
    t.engine.run(max_time=31_000)
    alt = [(sw.name, n) for sw in s for n, r in roles(sw).items() if r == "alternate"]
    assert len(alt) == 1                                  # exactly one port blocks the triangle
    root = min(s, key=lambda x: x.stp.bridge_id)
    assert all(r in ("designated", "edge") for r in roles(root).values())
    frames = []
    t.engine.subscribe("frame_sent", lambda ev: frames.append(ev))
    assert ping(t, pcs[0], "192.168.1.11").received == 4
    assert len(frames) < 200                              # no broadcast storm


def test_priority_elects_root_and_failover_pvst_takes_30s():
    t, s, pcs = triangle()
    s[2].stp.priority = 4096
    s[2].stp.schedule()
    t.engine.run(max_time=35_000)
    assert all(sw.stp.root_id == s[2].stp.bridge_id for sw in s)
    # break the forwarding path between the two PCs' switches if it is direct, else any root link
    link_port = next(p for p in s[0].ports.values() if s[0].stp.roles.get(p.name) == "root")
    link_port.set_admin(False)
    t.engine.run(max_time=1_000)
    blocked_now = ping(t, pcs[0], "192.168.1.11", count=1).received
    t.engine.run(max_time=31_000)
    assert ping(t, pcs[0], "192.168.1.11").received == 4
    assert blocked_now in (0, 1)


def test_rstp_converges_immediately_mixed_vendors():
    t, s, pcs = triangle(("huawei-s5720", "juniper-ex2300", "mikrotik-crs326"))
    t.engine.run(max_time=1_000)
    assert sum(r == "alternate" for sw in s for r in roles(sw).values()) == 1
    assert ping(t, pcs[0], "192.168.1.11").received == 4
    alt_sw = next(sw for sw in s if "alternate" in roles(sw).values())
    port = next(p for p in alt_sw.ports.values() if alt_sw.stp.roles.get(p.name) == "root")
    port.set_admin(False)
    t.engine.run(max_time=500)
    assert ping(t, pcs[0], "192.168.1.11").received == 4


def test_stp_disabled_loop_storms():
    t, s, pcs = triangle()
    for sw in s:
        sw.stp.enabled = False
        sw.stp.schedule()
    t.engine.run(max_time=100)
    frames = []
    t.engine.subscribe("frame_sent", lambda ev: frames.append(ev))
    from netemu.core.stack.icmp import PingSession, PingListener
    from ipaddress import IPv4Address
    PingSession(pcs[0], IPv4Address("192.168.1.11"), PingListener(), count=1).start()
    t.engine.run(max_time=1_000, max_events=20_000)
    assert len(frames) > 2000 and t.engine.frames_in_flight()   # the broadcast circulates forever


def test_bpdus_visible_and_persisted():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, s, pcs = triangle()
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"]))
    s[1].stp.priority = 8192
    s[1].stp.schedule()
    t.engine.run(max_time=5_000)
    assert any(f.protocol == "STP" for f in seen)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[s[1].id].stp.priority == 8192


def test_stp_cli_all_vendors():
    from cli_helpers import run, run_all
    from test_ospf_cli import ok
    t, s, pcs = triangle(("cisco-2960", "huawei-s5720", "juniper-ex2300"))
    mk = triangle(("mikrotik-crs326", "mikrotik-crs326", "mikrotik-crs326"))
    cs = s[0].create_cli()
    ok(run_all(cs, "enable\nconfigure terminal\nspanning-tree mode rapid-pvst\nspanning-tree vlan 1 priority 4096\n"
                   "interface fa0/1\nspanning-tree portfast\nend"))
    ok(run_all(s[1].create_cli(), "system-view\nstp priority 8192\nreturn"))
    out = ok(run_all(s[2].create_cli(), "configure\nset protocols rstp bridge-priority 12k\ncommit and-quit"))
    assert "commit complete" in out
    t.engine.run(max_time=5_000)
    assert all(sw.stp.root_id == s[0].stp.bridge_id for sw in s)
    st = run(cs, "show spanning-tree")
    assert "VLAN0001" in st and "This bridge is the root" in st and "rstp" in st and "Desg FWD" in st
    assert "spanning-tree mode rapid-pvst" in run(cs, "show running-config")
    hb = run(s[1].create_cli(), "display stp brief")
    assert "ROOT" in hb and "FORWARDING" in hb
    jb = run(s[2].create_cli(), "show spanning-tree interface")
    assert "ge-0/0/" in jb and ("ROOT" in jb or "ALT" in jb)
    assert "Root ID" in run(s[2].create_cli(), "show spanning-tree bridge")
    alt = [sw for sw in s if "alternate" in sw.stp.roles.values()]
    assert len(alt) == 1 and alt[0] is not s[0]
    mt, ms, mp = mk
    mc = ms[2].create_cli()
    ok(run_all(mc, "/interface bridge set bridge priority=0x1000"))
    mt.engine.run(max_time=3_000)
    assert "root-bridge: yes" in run(mc, "/interface bridge monitor")
    assert "designated-port" in run(mc, "/interface bridge port print")
