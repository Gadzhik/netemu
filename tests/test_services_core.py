"""DHCP, DNS and HTTP at the engine level."""
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from netemu.core.services.dhcp import DhcpPool
from netemu.core.services.dns import Resolve
from netemu.core.services.http import HttpGet
from netemu.core.topology import Topology
from test_vlan import ping


def run(t, s=10):
    t.engine.run(max_time=s * 1000)


def segment():
    """Server 192.168.1.2 and two DHCP PCs on one switch."""
    t = Topology()
    sw = t.add_device("cisco-2960")
    srv = t.add_device("server")
    srv.set_ip("192.168.1.2", "255.255.255.0")
    srv.set_gateway("192.168.1.1")
    t.connect(srv.nic, sw.get_port("fa0/24"))
    pcs = []
    for i in (1, 2):
        h = t.add_device("pc")
        t.connect(h.nic, sw.get_port(f"fa0/{i}"))
        pcs.append(h)
    return t, sw, srv, pcs


def test_dhcp_from_server_pc():
    t, sw, srv, pcs = segment()
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools.append(DhcpPool("serverPool", "192.168.1.0/24", "192.168.1.1", "192.168.1.2",
                                              start="192.168.1.100"))
    for h in pcs:
        h.start_dhcp()
    run(t)
    assert [str(h.nic.ip) for h in pcs] == ["192.168.1.100/24", "192.168.1.101/24"]
    assert str(pcs[0].gateway) == "192.168.1.1" and str(pcs[0].dns) == "192.168.1.2"
    assert len(srv.dhcp_server.bindings) == 2
    assert ping(t, pcs[0], "192.168.1.101").received == 4
    pcs[0].release_dhcp()
    run(t, 1)
    assert pcs[0].nic.ip is None and len(srv.dhcp_server.bindings) == 1


def test_dhcp_without_server_falls_back_to_apipa():
    t, sw, srv, pcs = segment()
    pcs[0].start_dhcp()
    run(t, 15)
    assert str(pcs[0].nic.ip).startswith("169.254.") and pcs[0].dhcp_info.get("apipa")


def test_router_dhcp_server_and_relay():
    """PC1 on R1 gi0/0 (DHCP from R1), PC2 on R1 gi0/1 relayed to the server PC behind gi0/2."""
    t = Topology()
    r1 = t.add_device("cisco-2911")
    srv = t.add_device("server")
    pc1, pc2 = t.add_device("pc"), t.add_device("pc")
    t.connect(pc1.nic, r1.get_port("gi0/0"))
    t.connect(pc2.nic, r1.get_port("gi0/1"))
    t.connect(srv.nic, r1.get_port("gi0/2"))
    for port, cidr in (("gi0/0", "192.168.10.1/24"), ("gi0/1", "192.168.20.1/24"), ("gi0/2", "10.0.0.1/24")):
        p = r1.get_port(port)
        p.ip = IPv4Interface(cidr)
        p.set_admin(True)
    srv.set_ip("10.0.0.5", "255.255.255.0")
    srv.set_gateway("10.0.0.1")
    d = r1.dhcp()
    d.cfg.pools.append(DhcpPool("LAN10", "192.168.10.0/24", "192.168.10.1", "10.0.0.5"))
    d.cfg.excluded.append(("192.168.10.1", "192.168.10.9"))
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools.append(DhcpPool("LAN20", "192.168.20.0/24", "192.168.20.1", "10.0.0.5",
                                              start="192.168.20.50"))
    r1.get_port("gi0/1").helpers.append("10.0.0.5")
    pc1.start_dhcp()
    pc2.start_dhcp()
    run(t)
    assert str(pc1.nic.ip) == "192.168.10.10/24"            # first address after the exclusion
    assert str(pc2.nic.ip) == "192.168.20.50/24" and str(pc2.gateway) == "192.168.20.1"
    assert ping(t, pc2, "192.168.10.10").received == 4


def dns_web():
    t, sw, srv, pcs = segment()
    srv.dns_server.enabled = True
    srv.dns_server.records["www.lab.local"] = "192.168.1.2"
    srv.http_server.pages["index.html"] = "<h1>Hello lab</h1>"
    for i, h in enumerate(pcs, 10):
        h.set_ip(f"192.168.1.{i}", "255.255.255.0")
        h.dns = IPv4Address("192.168.1.2")
    return t, srv, pcs


def test_dns_resolution_and_nxdomain():
    t, srv, pcs = dns_web()
    out = []
    Resolve(pcs[0], "www.lab.local", lambda ip, err: out.append((ip, err))).start()
    Resolve(pcs[0], "nope.lab.local", lambda ip, err: out.append((ip, err))).start()
    run(t, 3)
    assert out[0] == (IPv4Address("192.168.1.2"), "") and out[1][0] is None and "Non-existent" in out[1][1]
    assert srv.dns_server.queries == 2


def test_http_get_with_handshake_frames():
    t, srv, pcs = dns_web()
    seen, res = [], []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"]))
    HttpGet(pcs[0], "http://www.lab.local/", lambda st, body: res.append((st, body))).start()
    run(t, 5)
    assert res == [(200, "<h1>Hello lab</h1>")]
    protos = [f.protocol for f in seen if f.protocol in ("DNS", "TCP", "HTTP")]
    assert protos.count("DNS") >= 2 and "HTTP" in protos and "TCP" in protos
    flags = []
    for f in seen:
        if f.protocol in ("TCP", "HTTP") and (not flags or flags[-1] != f.payload.payload.flags):
            flags.append(f.payload.payload.flags)
    assert flags[:3] == ["SYN", "SYN,ACK", "PSH,ACK"]
    res.clear()
    HttpGet(pcs[0], "www.lab.local/missing.html", lambda st, body: res.append(st)).start()
    run(t, 5)
    assert res == [404]


def test_http_through_pat_and_acl_on_port_80():
    from netemu.core.security.acl import Acl, AclRule
    from netemu.core.security.nat import NatConfig, NatRule
    from test_nat_acl_core import lab
    t, r1, isp, pcs, srv = lab()
    srv.http_server.pages["index.html"] = "public site"
    nat = r1.nat_engine()
    nat.cfg = NatConfig(inside=["GigabitEthernet0/0"], outside=["GigabitEthernet0/1"],
                        rules=[NatRule("dynamic", acl="1", interface="GigabitEthernet0/1", overload=True)])
    r1.acls["1"] = Acl("1", "standard", [AclRule("permit", src=IPv4Network("192.168.1.0/24"))])
    res = []
    HttpGet(pcs[0], "http://8.8.8.8/", lambda st, body: res.append((st, body))).start()
    run(t, 5)
    assert res == [(200, "public site")]
    assert any(tr.proto == 6 and tr.in_global[1] is not None for tr in nat.table)   # TCP port translated
    r1.acls["120"] = Acl("120", "extended", [AclRule("deny", "tcp", dport=("eq", 80)), AclRule("permit")])
    r1.get_port("gi0/0").acl_in = "120"
    res.clear()
    HttpGet(pcs[0], "http://8.8.8.8/", lambda st, body: res.append(st)).start()
    run(t, 6)
    assert res == [None]
    assert ping(t, pcs[0], "8.8.8.8").received == 4          # ICMP still allowed


def test_services_persist():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t, sw, srv, pcs = segment()
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools.append(DhcpPool("p", "192.168.1.0/24", "192.168.1.1", "192.168.1.2"))
    srv.dns_server.enabled = True
    srv.dns_server.records["a.lab"] = "192.168.1.2"
    pcs[0].start_dhcp()
    run(t)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    run(t2)
    s2, p2 = t2.devices[srv.id], t2.devices[pcs[0].id]
    assert s2.dns_server.records == {"a.lab": "192.168.1.2"} and s2.dhcp_server.cfg.pools[0].name == "p"
    assert p2.dhcp_enabled and p2.nic.ip is not None and not str(p2.nic.ip).startswith("169.254")
