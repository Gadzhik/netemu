"""RIPv2 engine."""
from ipaddress import IPv4Interface

from netemu.core.routing.rip import RipConfig
from netemu.core.topology import Topology
from test_vlan import ping


def chain(n=3):
    """R0 - R1 - ... chain, each Rk with LAN 192.168.k.0/24 + PC."""
    t = Topology()
    rs = [t.add_device("cisco-2911") for _ in range(n)]
    for k in range(n - 1):
        t.connect(rs[k].get_port("gi0/1"), rs[k + 1].get_port("gi0/0"))
        for dev, port, ip in ((rs[k], "gi0/1", f"10.0.{k}.1/30"), (rs[k + 1], "gi0/0", f"10.0.{k}.2/30")):
            p = dev.get_port(port)
            p.ip = IPv4Interface(ip)
            p.set_admin(True)
    pcs = []
    for k, r in enumerate(rs):
        p = r.get_port("gi0/2")
        p.ip = IPv4Interface(f"192.168.{k}.1/24")
        p.set_admin(True)
        h = t.add_device("pc")
        h.set_ip(f"192.168.{k}.10", "255.255.255.0")
        h.set_gateway(f"192.168.{k}.1")
        t.connect(h.nic, p)
        pcs.append(h)
        r.enable_rip(RipConfig(networks=["10.0.0.0/8", f"192.168.{k}.0/24"]))
    return t, rs, pcs


def test_rip_converges_with_hop_metrics():
    t, rs, pcs = chain(4)
    t.engine.run(max_time=10_000)
    tbl = {str(r.network): (r.code, r.distance, r.metric) for r in rs[0].routing_table()}
    assert tbl["192.168.1.0/24"] == ("R", 120, 1) and tbl["192.168.3.0/24"] == ("R", 120, 3)
    assert ping(t, pcs[0], "192.168.3.10").received == 4


def test_rip_link_failure_and_timeout():
    t, rs, pcs = chain(3)
    t.engine.run(max_time=10_000)
    rs[2].get_port("gi0/2").set_admin(False)          # LAN of R2 goes away: triggered update
    t.engine.run(max_time=3_000)
    assert not any(str(r.network) == "192.168.2.0/24" for r in rs[0].routing_table())
    rs[2].get_port("gi0/2").set_admin(True)
    t.engine.run(max_time=3_000)
    assert any(str(r.network) == "192.168.2.0/24" for r in rs[0].routing_table())
    rs[1].disable_rip()                               # neighbour silently stops: 180 s timeout
    t.engine.run(max_time=170_000)
    assert any(str(r.network) == "192.168.2.0/24" for r in rs[0].routing_table())
    t.engine.run(max_time=15_000)
    assert not any(r.code == "R" for r in rs[0].routing_table())


def test_rip_passive_and_frames():
    t, rs, pcs = chain(2)
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"]))
    rs[0].rip.cfg.passive.append("GigabitEthernet0/2")
    t.engine.run(max_time=5_000)
    rip = [f for f in seen if f.protocol == "RIP"]
    assert rip and all(f.payload.dst.compressed == "224.0.0.9" for f in rip)
