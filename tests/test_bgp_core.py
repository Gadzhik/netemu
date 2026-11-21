"""BGP engine without CLI."""
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from netemu.core.routing.bgp import BgpConfig
from netemu.core.routing.ospf import OspfConfig
from netemu.core.topology import Topology
from test_vlan import ping


def ip(dev, port, cidr):
    p = dev.get_port(port)
    p.ip = IPv4Interface(cidr)
    p.set_admin(True)
    return p


def lan(t, dev, port, net3):
    """PC behind *dev* in 192.168.<net3>.0/24 (router .1, PC .10)."""
    ip(dev, port, f"192.168.{net3}.1/24")
    h = t.add_device("pc")
    h.set_ip(f"192.168.{net3}.10", "255.255.255.0")
    h.set_gateway(f"192.168.{net3}.1")
    t.connect(h.nic, dev.get_port(port))
    return h


def bgp(dev, asn, *neighbors, networks=(), **kw):
    nb = {}
    for n in neighbors:
        if isinstance(n, tuple):
            nb[n[0]] = dict(n[1])
        else:
            raise TypeError
    return dev.enable_bgp(BgpConfig(asn=asn, neighbors=nb, networks=list(networks), **kw))


def run(t, s=20):
    t.engine.run(max_time=s * 1000)


def ebgp_pair():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    ip(r1, "gi0/0", "10.0.12.1/30")
    ip(r2, "gi0/0", "10.0.12.2/30")
    a, b = lan(t, r1, "gi0/1", 1), lan(t, r2, "gi0/1", 2)
    return t, r1, r2, a, b


def test_ebgp_session_and_routes():
    t, r1, r2, a, b = ebgp_pair()
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65002}), networks=["192.168.1.0/24"])
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}), networks=["192.168.2.0/24"])
    run(t)
    p = r1.bgp.peers[IPv4Address("10.0.12.2")]
    assert p.state == "Established" and len(p.adj_in) == 1
    rt = {(str(r.network), r.code, r.distance) for r in r1.routing_table()}
    assert ("192.168.2.0/24", "B", 20) in rt
    best = r1.bgp.paths[IPv4Network("192.168.2.0/24")][0]
    assert best.attrs.as_path == (65002,) and best.attrs.next_hop == IPv4Address("10.0.12.2")
    assert ping(t, a, "192.168.2.10").received == 4
    # network statement needs an exact RIB match: a /16 is not advertised
    r1.bgp.cfg.networks.append("192.168.0.0/16")
    run(t, 3)
    assert IPv4Network("192.168.0.0/16") not in r2.bgp.paths


def test_wrong_remote_as_gives_notification():
    t, r1, r2, a, b = ebgp_pair()
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65099}))
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}))
    run(t)
    p = r1.bgp.peers[IPv4Address("10.0.12.2")]
    assert p.state != "Established" and "wrong AS" in p.last_error


def test_link_down_withdraws_routes_and_session_recovers():
    t, r1, r2, a, b = ebgp_pair()
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65002}), networks=["192.168.1.0/24"])
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}), networks=["192.168.2.0/24"])
    run(t)
    r1.get_port("gi0/0").set_admin(False)
    run(t, 2)
    assert r1.bgp.peers[IPv4Address("10.0.12.2")].state != "Established"
    assert not any(r.code == "B" for r in r1.routing_table())
    r1.get_port("gi0/0").set_admin(True)
    run(t, 10)
    assert r1.bgp.peers[IPv4Address("10.0.12.2")].state == "Established"
    assert ping(t, a, "192.168.2.10").received == 4


def ibgp_chain(update_source=True, nhs=False):
    """R1 (AS 65001) --eBGP-- R2 (AS 65002) --OSPF+iBGP via loopbacks-- R3 (AS 65002)."""
    t = Topology()
    r1, r2, r3 = (t.add_device("cisco-2911") for _ in range(3))
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    t.connect(r2.get_port("gi0/1"), r3.get_port("gi0/0"))
    ip(r1, "gi0/0", "10.0.12.1/30"); ip(r2, "gi0/0", "10.0.12.2/30")
    ip(r2, "gi0/1", "10.0.23.1/30"); ip(r3, "gi0/0", "10.0.23.2/30")
    for r, n in ((r2, 2), (r3, 3)):
        lo = r.add_loopback("Loopback0")
        lo.ip = IPv4Interface(f"{n}.{n}.{n}.{n}/32")
        r.enable_ospf(OspfConfig(networks=[{"prefix": "10.0.23.0/30", "area": "0.0.0.0"},
                                           {"prefix": f"{n}.{n}.{n}.{n}/32", "area": "0.0.0.0"}]))
    a, c = lan(t, r1, "gi0/1", 1), lan(t, r3, "gi0/1", 3)
    src = {"update_source": "Loopback0"} if update_source else {}
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65002}), networks=["192.168.1.0/24"])
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}),
        ("3.3.3.3", {"remote_as": 65002, "next_hop_self": nhs, **src}))
    bgp(r3, 65002, ("2.2.2.2", {"remote_as": 65002, **src}), networks=["192.168.3.0/24"])
    return t, r1, r2, r3, a, c


def test_ibgp_over_loopbacks_needs_update_source():
    t, r1, r2, r3, a, c = ibgp_chain(update_source=False)
    run(t, 30)
    assert r3.bgp.peers[IPv4Address("2.2.2.2")].state != "Established"


def test_ibgp_next_hop_unreachable_without_next_hop_self():
    t, r1, r2, r3, a, c = ibgp_chain(nhs=False)
    run(t, 30)
    assert r3.bgp.peers[IPv4Address("2.2.2.2")].state == "Established"
    assert IPv4Network("192.168.1.0/24") in r3.bgp.peers[IPv4Address("2.2.2.2")].adj_in
    assert not any(str(r.network) == "192.168.1.0/24" for r in r3.routing_table())   # NH 10.0.12.1 unknown
    r2.bgp.cfg.neighbors["3.3.3.3"]["next_hop_self"] = True
    r2.bgp.reset_peer("3.3.3.3")
    run(t, 10)
    rt = {(str(r.network), r.code, r.distance) for r in r3.routing_table()}
    assert ("192.168.1.0/24", "B", 200) in rt
    assert ping(t, c, "192.168.1.10").received == 4
    path = r1.bgp.paths[IPv4Network("192.168.3.0/24")][0]
    assert path.attrs.as_path == (65002,)        # iBGP-learned, re-advertised over eBGP


def test_ebgp_multihop_between_loopbacks():
    t, r1, r2, a, b = ebgp_pair()
    for r, n, other in ((r1, 1, 2), (r2, 2, 1)):
        lo = r.add_loopback("Loopback0")
        lo.ip = IPv4Interface(f"{n}.{n}.{n}.{n}/32")
        r.add_static_route(IPv4Network(f"{other}.{other}.{other}.{other}/32"), IPv4Address(f"10.0.12.{other}"))
    bgp(r1, 65001, ("2.2.2.2", {"remote_as": 65002, "update_source": "Loopback0"}), networks=["192.168.1.0/24"])
    bgp(r2, 65002, ("1.1.1.1", {"remote_as": 65001, "update_source": "Loopback0"}), networks=["192.168.2.0/24"])
    run(t)
    assert r1.bgp.peers[IPv4Address("2.2.2.2")].state != "Established"   # TTL 1 dies at the peer's interface
    r1.bgp.cfg.neighbors["2.2.2.2"]["multihop"] = 2
    r2.bgp.cfg.neighbors["1.1.1.1"]["multihop"] = 2
    run(t)
    assert r1.bgp.peers[IPv4Address("2.2.2.2")].state == "Established"
    assert ping(t, a, "192.168.2.10").received == 4


def test_shortest_as_path_and_failover_triangle():
    """AS1 R1 connected to AS2 R2 and AS3 R3, which are also connected; R1 prefers the direct path."""
    t = Topology()
    r1, r2, r3 = (t.add_device("cisco-2911") for _ in range(3))
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    t.connect(r1.get_port("gi0/1"), r3.get_port("gi0/0"))
    t.connect(r2.get_port("gi0/1"), r3.get_port("gi0/1"))
    ip(r1, "gi0/0", "10.0.12.1/30"); ip(r2, "gi0/0", "10.0.12.2/30")
    ip(r1, "gi0/1", "10.0.13.1/30"); ip(r3, "gi0/0", "10.0.13.2/30")
    ip(r2, "gi0/1", "10.0.23.1/30"); ip(r3, "gi0/1", "10.0.23.2/30")
    lan(t, r2, "gi0/2", 2)
    a = lan(t, r1, "gi0/2", 1)
    bgp(r1, 1, ("10.0.12.2", {"remote_as": 2}), ("10.0.13.2", {"remote_as": 3}), networks=["192.168.1.0/24"])
    bgp(r2, 2, ("10.0.12.1", {"remote_as": 1}), ("10.0.23.2", {"remote_as": 3}), networks=["192.168.2.0/24"])
    bgp(r3, 3, ("10.0.13.1", {"remote_as": 1}), ("10.0.23.1", {"remote_as": 2}))
    run(t)
    lst = r1.bgp.paths[IPv4Network("192.168.2.0/24")]
    assert len(lst) == 2 and lst[0].best and lst[0].attrs.as_path == (2,)
    assert lst[1].attrs.as_path == (3, 2)
    # R2 never accepts its own prefix back (AS_PATH loop)
    assert all(bp.peer is None for bp in r2.bgp.paths[IPv4Network("192.168.2.0/24")])
    r1.get_port("gi0/0").set_admin(False)
    run(t, 3)
    best = r1.bgp.paths[IPv4Network("192.168.2.0/24")][0]
    assert best.attrs.as_path == (3, 2)
    assert ping(t, a, "192.168.2.10").received == 4


def test_acl_blocking_tcp_179_prevents_session():
    from netemu.core.security.acl import Acl, AclRule
    t, r1, r2, a, b = ebgp_pair()
    r2.acls["101"] = Acl("101", "extended", [AclRule("deny", "tcp", dport=("eq", 179)), AclRule("permit")])
    r2.get_port("gi0/0").acl_in = "101"
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65002}))
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}))
    run(t)
    assert r1.bgp.peers[IPv4Address("10.0.12.2")].state != "Established"
    assert r2.acls["101"].rules[0].hits > 0


def test_bgp_frames_and_persistence():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, r1, r2, a, b = ebgp_pair()
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"]))
    bgp(r1, 65001, ("10.0.12.2", {"remote_as": 65002}), networks=["192.168.1.0/24"])
    bgp(r2, 65002, ("10.0.12.1", {"remote_as": 65001}), networks=["192.168.2.0/24"])
    run(t)
    f = next(f for f in seen if f.protocol == "BGP")
    assert "BGP" in f.summary() and any(title == "BGP" for title, _ in f.layers())
    assert any("UPDATE" in x.summary() for x in seen if x.protocol == "BGP")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    run(t2)
    a2 = t2.devices[a.id]
    assert ping(t2, a2, "192.168.2.10").received == 4
