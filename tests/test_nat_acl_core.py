"""NAT (PAT, static, pool, port forwarding) and ACLs at the engine level."""
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from netemu.core.security.acl import Acl, AclRule
from netemu.core.security.nat import NatConfig, NatRule
from netemu.core.topology import Topology
from test_vlan import ping


def lab():
    """PC1/PC2 (192.168.1.0/24) - R1 [inside gi0/0 | outside gi0/1 203.0.113.1/29] - ISP - SRV 8.8.8.8."""
    t = Topology()
    r1, isp = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    sw = t.add_device("cisco-2960")
    pcs = []
    for i in (10, 11):
        h = t.add_device("pc")
        h.set_ip(f"192.168.1.{i}", "255.255.255.0")
        h.set_gateway("192.168.1.1")
        t.connect(h.nic, next(p for p in sw.physical_ports() if p.link is None))
        pcs.append(h)
    t.connect(sw.get_port("gi0/1"), r1.get_port("gi0/0"))
    t.connect(r1.get_port("gi0/1"), isp.get_port("gi0/0"))
    srv = t.add_device("server")
    srv.set_ip("8.8.8.8", "255.255.255.0")
    srv.set_gateway("8.8.8.1")
    t.connect(srv.nic, isp.get_port("gi0/1"))
    for dev, port, cidr in ((r1, "gi0/0", "192.168.1.1/24"), (r1, "gi0/1", "203.0.113.1/29"),
                            (isp, "gi0/0", "203.0.113.6/29"), (isp, "gi0/1", "8.8.8.1/24")):
        p = dev.get_port(port)
        p.ip = IPv4Interface(cidr)
        p.set_admin(True)
    r1.add_static_route(IPv4Network("0.0.0.0/0"), IPv4Address("203.0.113.6"))
    return t, r1, isp, pcs, srv


def cisco_nat(r1, *rules):
    nat = r1.nat_engine()
    nat.cfg = NatConfig(inside=["GigabitEthernet0/0"], outside=["GigabitEthernet0/1"], rules=list(rules))
    r1.acls["1"] = Acl("1", "standard", [AclRule("permit", src=IPv4Network("192.168.1.0/24"))])
    return nat


def test_without_nat_private_addresses_are_unreachable():
    t, r1, isp, pcs, srv = lab()
    assert ping(t, pcs[0], "8.8.8.8").received == 0      # ISP has no route back to 192.168.1.0/24


def test_pat_overload_on_interface():
    t, r1, isp, pcs, srv = lab()
    nat = cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    assert ping(t, pcs[1], "8.8.8.8").received == 4
    globals_ = {tr.in_global for tr in nat.table}
    assert {g[0] for g in globals_} == {IPv4Address("203.0.113.1")}
    assert len({g[1] for g in globals_}) == len(globals_) >= 2       # unique ICMP ids per inside host
    # the server only ever sees the router's outside address
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"].payload)
                       if ev.info["dst"].device is srv else None)
    ping(t, pcs[0], "8.8.8.8", count=1)
    assert {str(p.src) for p in seen if hasattr(p, "src")} == {"203.0.113.1"}
    # pinging the router's own outside address from outside still works (no translation)
    assert ping(t, srv, "203.0.113.1").received == 4


def test_traceroute_through_pat():
    from netemu.core.stack.icmp import TraceListener, TracerouteSession

    class L(TraceListener):
        hops = []

        def on_hop(self, hop):
            L.hops.append(hop)
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    s = TracerouteSession(pcs[0], IPv4Address("8.8.8.8"), L()).start()
    t.engine.run_until(lambda: s.done)
    addrs = [str(h.address) for h in L.hops if h.address is not None]
    assert addrs[:2] == ["192.168.1.1", "203.0.113.6"] and addrs[-1] == "8.8.8.8"


def test_static_nat_reachable_from_outside():
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("static", local="192.168.1.10", glob="203.0.113.3"))
    assert ping(t, srv, "203.0.113.3").received >= 3         # ARP answered by R1 for the global address
    assert ping(t, srv, "203.0.113.3").received == 4
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    assert ping(t, pcs[1], "8.8.8.8").received == 0          # not translated


def test_dynamic_pool_without_overload_runs_out():
    t, r1, isp, pcs, srv = lab()
    nat = cisco_nat(r1, NatRule("dynamic", acl="1", pool=("203.0.113.2", "203.0.113.2")))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    assert ping(t, pcs[1], "8.8.8.8").received == 0          # one address, already in use
    assert nat.misses >= 1


def test_port_forwarding_rule_and_save_load():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    r2 = t2.devices[r1.id]
    assert r2.nat.cfg.rules[0].overload and r2.acls["1"].rules[0].src == IPv4Network("192.168.1.0/24")
    pc2 = t2.devices[pcs[0].id]
    assert ping(t2, pc2, "8.8.8.8").received == 4


def test_extended_acl_blocks_icmp_with_unreachable():
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    r1.acls["100"] = Acl("100", "extended", [
        AclRule("deny", "icmp", IPv4Network("192.168.1.11/32"), IPv4Network("8.8.8.8/32"), icmp_type=8),
        AclRule("permit", "ip")])
    r1.get_port("gi0/0").acl_in = "100"
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    r = ping(t, pcs[1], "8.8.8.8")
    assert r.received == 0 and any(e[0] == "unreachable" for e in r.events)
    assert r1.acls["100"].rules[0].hits == 4


def test_implicit_deny_and_outbound_acl():
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    r1.acls["2"] = Acl("2", "standard", [AclRule("permit", src=IPv4Network("203.0.113.1/32"))])
    r1.get_port("gi0/1").acl_out = "2"          # outbound ACL sees the translated source
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    r1.acls["2"].rules[0].src = IPv4Network("192.168.1.0/24")
    assert ping(t, pcs[0], "8.8.8.8").received == 0           # implicit deny


def test_huawei_style_default_permit_and_routeros_forward_chain():
    t, r1, isp, pcs, srv = lab()
    cisco_nat(r1, NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True))
    r1.acls["3000"] = Acl("3000", "advanced", [AclRule("deny", "icmp", IPv4Network("192.168.1.11/32"))],
                          default="permit")
    r1.get_port("gi0/0").acl_in = "3000"
    assert ping(t, pcs[0], "8.8.8.8").received == 4             # unmatched traffic is permitted
    assert ping(t, pcs[1], "8.8.8.8").received == 0
    r1.get_port("gi0/0").acl_in = None
    r1.firewall = [AclRule("deny", "icmp", IPv4Network("192.168.1.10/32"),
                           extra={"chain": "forward", "out": "GigabitEthernet0/1"})]
    assert ping(t, pcs[0], "8.8.8.8").received == 0
    assert ping(t, pcs[0], "192.168.1.1").received == 4          # input chain not affected
