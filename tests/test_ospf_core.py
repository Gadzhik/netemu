"""OSPF engine without any CLI: adjacencies, SPF, ECMP, areas, externals, failover."""
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from netemu.core.routing.ospf import OspfConfig
from netemu.core.topology import Topology
from test_vlan import ping


def addr(dev, port, cidr):
    p = dev.get_port(port)
    p.ip = IPv4Interface(cidr)
    p.set_admin(True)
    return p


def ospf(dev, *nets, area="0.0.0.0", rid=None, **kw):
    cfg = OspfConfig(router_id=rid, networks=[{"prefix": n, "area": area} for n in nets], **kw)
    return dev.enable_ospf(cfg)


def converge(t, seconds=15):
    t.engine.run(max_time=seconds * 1000)


def ring():
    """R1 - R2 - R3 - R4 - R1 (all Cisco), each with a LAN 10.N.0.0/24."""
    t = Topology()
    r = [t.add_device("cisco-2911") for _ in range(4)]
    links = [(0, 1), (1, 2), (2, 3), (3, 0)]
    for k, (a, b) in enumerate(links):
        pa = next(p for p in r[a].physical_ports() if p.link is None)
        pb = next(p for p in r[b].physical_ports() if p.link is None)
        t.connect(pa, pb)
        addr(r[a], pa.name, f"172.16.{k}.1/30")
        addr(r[b], pb.name, f"172.16.{k}.2/30")
    hosts = []
    for i, dev in enumerate(r):
        lan = next(p for p in dev.physical_ports() if p.link is None)
        h = t.add_device("pc")
        t.connect(h.nic, lan)
        addr(dev, lan.name, f"10.{i + 1}.0.1/24")
        h.set_ip(f"10.{i + 1}.0.10", "255.255.255.0")
        h.set_gateway(f"10.{i + 1}.0.1")
        hosts.append(h)
    for i, dev in enumerate(r):
        ospf(dev, "172.16.0.0/16", f"10.{i + 1}.0.0/24", rid=f"{i + 1}.{i + 1}.{i + 1}.{i + 1}")
    return t, r, hosts


def test_adjacencies_and_full_routing():
    t, r, h = ring()
    converge(t)
    assert len(r[0].ospf.neighbors) == 2
    assert all(n.two_way for n in r[0].ospf.neighbors.values())
    codes = {(str(x.network), x.code) for x in r[0].routing_table()}
    assert ("10.3.0.0/24", "O") in codes and ("172.16.1.0/30", "O") in codes
    # opposite LAN at equal cost both ways round the ring: ECMP
    via = [x for x in r[0].routing_table() if str(x.network) == "10.3.0.0/24"]
    assert len(via) == 2 and {x.metric for x in via} == {3}
    assert ping(t, h[0], "10.3.0.10").received == 4


def test_failover_and_recovery():
    t, r, h = ring()
    converge(t)
    link = r[0].get_port("gi0/0").link      # R1-R2
    link.a.set_admin(False)
    converge(t, 2)
    via = [x for x in r[0].routing_table() if str(x.network) == "10.2.0.0/24"]
    assert len(via) == 1 and via[0].metric == 4   # now the long way round: R1-R4-R3-R2
    assert ping(t, h[0], "10.2.0.10").received == 4
    link.a.set_admin(True)
    converge(t, 15)
    via = [x for x in r[0].routing_table() if str(x.network) == "10.2.0.0/24"]
    assert via[0].metric == 2


def test_dead_timer_when_neighbor_silently_disappears():
    t, r, h = ring()
    converge(t)
    r[1].disable_ospf()                          # R2 stops talking, links stay up
    converge(t, 30)
    assert any(n.rid == IPv4Address("2.2.2.2") for n in r[0].ospf.neighbors.values())
    converge(t, 15)                              # > 40 s dead interval in total
    assert not any(n.rid == IPv4Address("2.2.2.2") for n in r[0].ospf.neighbors.values())


def test_hello_mismatch_prevents_adjacency():
    t, r, h = ring()
    r[0].ospf.cfg.hello["GigabitEthernet0/0"] = 5.0
    converge(t)
    assert not any(n.port.name == "GigabitEthernet0/0" and n.two_way for n in r[0].ospf.neighbors.values())
    assert len([n for n in r[0].ospf.neighbors.values() if n.two_way]) == 1


def test_static_beats_ospf_and_passive_interfaces():
    t, r, h = ring()
    r[0].ospf.cfg.passive.append("GigabitEthernet0/2")   # the LAN
    converge(t)
    assert not any(n.port.name == "GigabitEthernet0/2" for n in r[0].ospf.neighbors.values())
    r[0].add_static_route(IPv4Network("10.3.0.0/24"), IPv4Address("172.16.0.2"))
    best = [x for x in r[0].routing_table() if str(x.network) == "10.3.0.0/24"]
    assert [x.code for x in best] == ["S"]


def test_default_information_originate_and_redistribute_static():
    t, r, h = ring()
    r[2].add_static_route(IPv4Network("0.0.0.0/0"), None, "GigabitEthernet0/2")
    r[2].add_static_route(IPv4Network("192.168.99.0/24"), IPv4Address("10.3.0.10"))
    r[2].ospf.cfg.default_originate = "yes"
    r[2].ospf.cfg.redistribute = ["static"]
    converge(t)
    tbl = {(str(x.network), x.code, x.metric) for x in r[0].routing_table()}
    assert ("0.0.0.0/0", "O E2", 1) in tbl and ("192.168.99.0/24", "O E2", 20) in tbl
    assert r[0].gateway_of_last_resort() is not None


def test_multi_area_inter_area_routes():
    """R1(area 1) - R2(ABR) - R3(area 0)."""
    t = Topology()
    r1, r2, r3 = (t.add_device("cisco-2911") for _ in range(3))
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    t.connect(r2.get_port("gi0/1"), r3.get_port("gi0/0"))
    addr(r1, "gi0/0", "10.0.12.1/30"); addr(r2, "gi0/0", "10.0.12.2/30")
    addr(r2, "gi0/1", "10.0.23.1/30"); addr(r3, "gi0/0", "10.0.23.2/30")
    lo1 = r1.add_loopback("Loopback0"); lo1.ip = IPv4Interface("1.1.1.1/32")
    lo3 = r3.add_loopback("Loopback0"); lo3.ip = IPv4Interface("3.3.3.3/32")
    ospf(r1, "10.0.12.0/30", "1.1.1.1/32", area="0.0.0.1")
    cfg = OspfConfig(networks=[{"prefix": "10.0.12.0/30", "area": "0.0.0.1"},
                               {"prefix": "10.0.23.0/30", "area": "0.0.0.0"}])
    r2.enable_ospf(cfg)
    ospf(r3, "10.0.23.0/30", "3.3.3.3/32")
    converge(t)
    assert r1.ospf.router_id == IPv4Address("1.1.1.1")       # loopback wins
    assert r2.ospf.is_abr
    t3 = {(str(x.network), x.code) for x in r3.routing_table()}
    assert ("1.1.1.1/32", "O IA") in t3 and ("10.0.12.0/30", "O IA") in t3
    t1 = {(str(x.network), x.code) for x in r1.routing_table()}
    assert ("3.3.3.3/32", "O IA") in t1
    assert r1._resolve(IPv4Address("3.3.3.3"))[1] == IPv4Address("10.0.12.2")


def test_ospf_config_survives_save_load():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, r, h = ring()
    converge(t)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    converge(t2)
    r0 = t2.devices[r[0].id]
    assert r0.ospf is not None and len(r0.ospf.neighbors) == 2
    assert any(x.code == "O" for x in r0.routing_table())


def test_ospf_frames_visible_in_simulation():
    t, r, h = ring()
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"]))
    converge(t)
    protos = {f.protocol for f in seen}
    assert "OSPF" in protos
    hello = next(f for f in seen if f.protocol == "OSPF")
    assert hello.summary().startswith("OSPF Hello") and str(hello.dst) == "0100.5E00.0005"
    assert any(title == "OSPF" for title, _ in hello.layers())
