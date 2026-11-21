"""IPv6 core: NDP, ICMPv6 echo, routing, SLAAC."""
from ipaddress import IPv6Address, IPv6Interface, IPv6Network

from netemu.core.stack.icmp import PingListener
from netemu.core.stack.ipv6 import Ping6Session, StaticRoute6, eui64_link_local
from netemu.core.topology import Topology


class L(PingListener):
    def __init__(self):
        self.ok = 0
        self.errors = []

    def on_reply(self, *a):
        self.ok += 1

    def on_error(self, seq, kind, src):
        self.errors.append(kind)


def ping6(t, dev, dst, count=4):
    lis = L()
    s = Ping6Session(dev, IPv6Address(dst), lis, count=count, size=32, timeout=2000.0, interval=0).start()
    t.engine.run_until(lambda: s.done)
    return lis


def lan_pair():
    t = Topology()
    sw = t.add_device("cisco-2960")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("fa0/1"))
    t.connect(b.nic, sw.get_port("fa0/2"))
    a.set_ipv6("2001:db8:1::10/64")
    b.set_ipv6("2001:db8:1::11/64")
    return t, a, b


def test_link_local_is_eui64_and_ping_on_link():
    t, a, b = lan_pair()
    ll = eui64_link_local(a.nic.mac)
    assert ll.ip.is_link_local and str(ll.network) == "fe80::/64"
    assert ping6(t, a, "2001:db8:1::11").ok == 4
    assert any(k[1] == IPv6Address("2001:db8:1::11") for k in a.nd_cache)


def routed():
    """PC1 2001:db8:1::/64 - R1 (Cisco) - PC2 2001:db8:2::/64."""
    t = Topology()
    r = t.add_device("cisco-2911")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, r.get_port("gi0/0"))
    t.connect(b.nic, r.get_port("gi0/1"))
    for port, net in (("gi0/0", 1), ("gi0/1", 2)):
        p = r.get_port(port)
        p.ipv6 = [IPv6Interface(f"2001:db8:{net}::1/64")]
        p.set_admin(True)
    a.set_ipv6("2001:db8:1::10/64")
    a.set_gateway6("2001:db8:1::1")
    b.set_ipv6("2001:db8:2::10/64")
    b.set_gateway6("2001:db8:2::1")
    return t, r, a, b


def test_cisco_needs_ipv6_unicast_routing():
    t, r, a, b = routed()
    assert ping6(t, a, "2001:db8:1::1").ok == 4          # router answers itself
    assert ping6(t, a, "2001:db8:2::10").ok == 0          # but does not forward
    r.ipv6_routing = True
    assert ping6(t, a, "2001:db8:2::10").ok == 4


def test_static_route6_and_unreachable():
    t, r, a, b = routed()
    r.ipv6_routing = True
    lis = ping6(t, a, "2001:db8:99::1", count=1)
    assert lis.ok == 0 and lis.errors == ["unreachable"]
    p = r.get_port("gi0/2")
    p.ipv6 = [IPv6Interface("2001:db8:99::1/64")]
    p.set_admin(True)
    assert ping6(t, a, "2001:db8:99::1").ok == 4          # router's own address on a down port? no: own


def test_slaac_from_router_advertisement():
    t, r, a, b = routed()
    r.ipv6_routing = True
    for h in (a, b):
        h.set_ipv6(None)
        h.set_gateway6(None)
        h.start_slaac()
    r.ipv6_changed()
    t.engine.run(max_time=1_000)
    addr = a.nic.ipv6[0]
    assert addr in IPv6Interface("2001:db8:1::/64").network and int(addr.ip) & 0xFFFF != 0
    assert a.routing_table6()[0].network == IPv6Network("::/0") or any(
        r6.code == "ND" for r6 in a.routing_table6())
    assert ping6(t, a, str(b.nic.ipv6[0].ip)).ok == 4


def test_ipv6_persists():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, r, a, b = routed()
    r.ipv6_routing = True
    r.static_routes6.append(StaticRoute6(IPv6Network("2001:db8:50::/48"), IPv6Address("2001:db8:2::10")))
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    r2 = t2.devices[r.id]
    assert r2.ipv6_routing and r2.static_routes6[0].network == IPv6Network("2001:db8:50::/48")
    assert str(r2.get_port("gi0/0").ipv6[0]) == "2001:db8:1::1/64"
    a2 = t2.devices[a.id]
    assert str(a2.gateway6) == "2001:db8:1::1"
    assert ping6(t2, a2, "2001:db8:2::10").ok == 4
