"""OSPFv3 and RIPng: neighbours from hellos, routes over the chain, failure handling, persistence."""
from ipaddress import IPv6Interface

import pytest

from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.routing.v6igp import enable_iface
from netemu.core.topology import Topology
from test_ipv6_core import ping6


def chain(kind):
    """PC1 - R1 - R2 - R3 - PC2, IPv6 only."""
    t = Topology()
    rs = [t.add_device("cisco-2911") for _ in range(3)]
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, rs[0].get_port("gi0/0"))
    t.connect(rs[0].get_port("gi0/1"), rs[1].get_port("gi0/0"))
    t.connect(rs[1].get_port("gi0/1"), rs[2].get_port("gi0/0"))
    t.connect(rs[2].get_port("gi0/1"), b.nic)
    nets = {(0, "gi0/0"): "2001:db8:1::1/64", (0, "gi0/1"): "2001:db8:12::1/64", (1, "gi0/0"): "2001:db8:12::2/64",
            (1, "gi0/1"): "2001:db8:23::2/64", (2, "gi0/0"): "2001:db8:23::3/64", (2, "gi0/1"): "2001:db8:2::1/64"}
    for (i, pn), addr in nets.items():
        r = rs[i]
        r.ipv6_routing = True
        p = r.get_port(pn)
        p.ipv6 = [IPv6Interface(addr)]
        p.set_admin(True)
        enable_iface(r, kind, p)
    a.set_ipv6("2001:db8:1::10/64")
    a.set_gateway6("2001:db8:1::1")
    b.set_ipv6("2001:db8:2::10/64")
    b.set_gateway6("2001:db8:2::1")
    t.engine.run(max_time=5000)
    return t, rs, a, b


@pytest.mark.parametrize("kind,code", [("ospf6", "O"), ("ripng", "R")])
def test_routes_across_chain(kind, code):
    t, rs, a, b = chain(kind)
    r1 = rs[0]
    learned = {str(r.network) for r in r1.routing_table6() if r.code == code}
    assert {"2001:db8:23::/64", "2001:db8:2::/64"} <= learned, learned
    nh = next(r.next_hop for r in r1.routing_table6() if str(r.network) == "2001:db8:2::/64")
    assert nh.is_link_local
    assert ping6(t, a, "2001:db8:2::10").ok == 4
    rs[1].get_port("gi0/1").set_admin(False)
    t.engine.run(max_time=2000)
    assert "2001:db8:2::/64" not in {str(r.network) for r in r1.routing_table6()}


def test_area_mismatch_blocks_adjacency_and_persistence():
    t, rs, a, b = chain("ospf6")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=15000)
    assert ping6(t2, t2.devices[a.id], "2001:db8:2::10").ok == 4
    r2 = rs[1]
    r2.v6igp["ospf6"].cfg.interfaces["GigabitEthernet0/0"]["area"] = "0.0.0.1"
    rs[0].v6igp["ospf6"].neighbors.clear()
    r2.v6igp["ospf6"].neighbors.clear()
    t.engine.run(max_time=45000)
    assert not any(str(r.network) == "2001:db8:2::/64" for r in rs[0].routing_table6())


from cli_helpers import run, run_all  # noqa: E402

V6CLI = {
    ("cisco-2911", "ospf6"): (lambda i: f"enable\nconfigure terminal\nipv6 unicast-routing\nipv6 router ospf 1\n"
                                        f"router-id {i}.{i}.{i}.{i}\nexit\ninterface gi0/0\nipv6 address 2001:db8:{i}::1/64\n"
                                        "ipv6 ospf 1 area 0\nno shutdown\ninterface gi0/1\n"
                                        f"ipv6 address 2001:db8:12::{i}/64\nipv6 ospf 1 area 0\nno shutdown\nend",
                             ("gi0/0", "gi0/1"), "show ipv6 ospf neighbor", "FULL"),
    ("cisco-2911", "ripng"): (lambda i: f"enable\nconfigure terminal\nipv6 unicast-routing\nipv6 router rip LAB\nexit\n"
                                        f"interface gi0/0\nipv6 address 2001:db8:{i}::1/64\nipv6 rip LAB enable\nno shutdown\n"
                                        f"interface gi0/1\nipv6 address 2001:db8:12::{i}/64\nipv6 rip LAB enable\n"
                                        "no shutdown\nend",
                              ("gi0/0", "gi0/1"), "show ipv6 rip", "FE80::"),
    ("huawei-ar2220", "ospf6"): (lambda i: f"system-view\nipv6\nospfv3 1\nrouter-id {i}.{i}.{i}.{i}\nquit\n"
                                           f"interface GigabitEthernet0/0/0\nipv6 enable\nipv6 address 2001:db8:{i}::1/64\n"
                                           "undo ipv6 nd ra halt\nospfv3 1 area 0.0.0.0\ninterface GigabitEthernet0/0/1\n"
                                           f"ipv6 enable\nipv6 address 2001:db8:12::{i}/64\nospfv3 1 area 0.0.0.0\nreturn",
                                 ("GE0/0/0", "GE0/0/1"), "display ospfv3 peer", "Full"),
    ("juniper-srx300", "ospf6"): (lambda i: f"configure\nset interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:{i}::1/64\n"
                                            f"set interfaces ge-0/0/1 unit 0 family inet6 address 2001:db8:12::{i}/64\n"
                                            "set protocols ospf3 area 0.0.0.0 interface ge-0/0/0.0\n"
                                            "set protocols ospf3 area 0.0.0.0 interface ge-0/0/1.0\ncommit and-quit",
                                  ("ge-0/0/0", "ge-0/0/1"), "show ospf3 neighbor", "Full"),
}


@pytest.mark.parametrize("model,kind", list(V6CLI))
def test_v6igp_cli(model, kind):
    cfg, (lan, wan), show, want = V6CLI[(model, kind)]
    t = Topology()
    r1, r2 = t.add_device(model), t.add_device(model)
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(r1.get_port(wan), r2.get_port(wan))
    t.connect(a.nic, r1.get_port(lan))
    t.connect(b.nic, r2.get_port(lan))
    for i, r in ((1, r1), (2, r2)):
        out = run_all(r.create_cli(), cfg(i))
        assert not any(e in out for e in ("Invalid", "Error", "error", "% ")), out
    a.set_ipv6("2001:db8:1::10/64")
    a.set_gateway6("2001:db8:1::1")
    b.set_ipv6("2001:db8:2::10/64")
    b.set_gateway6("2001:db8:2::1")
    t.engine.run(max_time=12000)
    assert ping6(t, a, "2001:db8:2::10").ok == 4
    s = r1.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert want in run(s, show)


@pytest.mark.parametrize("model,cfg", [
    ("cisco-2911", "enable\nconfigure terminal\nipv6 unicast-routing\nipv6 dhcp pool LAN6\naddress prefix 2001:db8:5::/64\n"
                   "dns-server 2001:db8:5::53\nexit\ninterface gi0/0\nipv6 address 2001:db8:5::1/64\nipv6 dhcp server LAN6\n"
                   "no shutdown\nend"),
    ("huawei-ar2220", "system-view\nipv6\ndhcpv6 pool LAN6\naddress prefix 2001:db8:5::/64\nquit\n"
                      "interface GigabitEthernet0/0/0\nipv6 enable\nipv6 address 2001:db8:5::1/64\ndhcpv6 server LAN6\nreturn"),
])
def test_dhcpv6(model, cfg):
    t = Topology()
    r = t.add_device(model)
    sw = t.add_device("cisco-2960")
    win, lin = t.add_device("pc"), t.add_device("pc-linux")
    t.connect(r.physical_ports()[0], sw.get_port("fa0/1"))
    t.connect(win.nic, sw.get_port("fa0/2"))
    t.connect(lin.nic, sw.get_port("fa0/3"))
    out = run_all(r.create_cli(), cfg)
    assert "Invalid" not in out and "Error" not in out, out
    t.engine.run(max_time=1000)
    out = run(win.create_cli(), "ipconfig /renew6")
    assert "IPv6 Address" in out and "2001:db8:5::" in out, out
    out = run(lin.create_cli(), "sudo dhclient -6 -v eth0")
    assert "Reply message" in out, out
    assert ping6(t, win, "2001:db8:5::1").ok == 4
    addrs = {str(a.ip) for a in win.nic.ipv6} | {str(a.ip) for a in lin.nic.ipv6}
    assert "2001:db8:5::10" in addrs and "2001:db8:5::11" in addrs


@pytest.mark.parametrize("kind", ["ospf6", "ripng"])
def test_mikrotik_v6igp_and_dhcpv6(kind):
    t = Topology()
    r1, r2 = t.add_device("mikrotik-rb4011"), t.add_device("mikrotik-rb4011")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(r1.get_port("ether2"), r2.get_port("ether2"))
    t.connect(a.nic, r1.get_port("ether1"))
    t.connect(b.nic, r2.get_port("ether1"))
    for i, r in ((1, r1), (2, r2)):
        proto = ("/routing ospf instance add name=v3 version=3 router-id={i}.{i}.{i}.{i}\n"
                 "/routing ospf area add name=bb6 area-id=0.0.0.0 instance=v3\n"
                 "/routing ospf interface-template add area=bb6 interfaces=ether1,ether2") if kind == "ospf6" else \
                ("/routing rip instance add name=rng afi=ipv6\n"
                 "/routing rip interface-template add instance=rng interfaces=ether1,ether2")
        out = run_all(r.create_cli(), f"/ipv6 address add address=2001:db8:{i}::1/64 interface=ether1\n"
                                      f"/ipv6 address add address=2001:db8:12::{i}/64 interface=ether2\n"
                                      + proto.format(i=i))
        assert "failure" not in out and "bad command" not in out and "invalid" not in out, out
    run_all(r1.create_cli(), "/ipv6 pool add name=p6 prefix=2001:db8:1::/64 prefix-length=64\n"
                             "/ipv6 dhcp-server add name=d6 interface=ether1 address-pool=p6")
    b.set_ipv6("2001:db8:2::10/64")
    b.set_gateway6("2001:db8:2::1")
    t.engine.run(max_time=12000)
    assert "IPv6 Address" in run(a.create_cli(), "ipconfig /renew6")
    a.set_gateway6("2001:db8:1::1")
    assert ping6(t, a, "2001:db8:2::10").ok == 4
    exp = run(r1.create_cli(), "/export")
    assert ("version=3" in exp) if kind == "ospf6" else ("afi=ipv6" in exp)


def test_juniper_dhcpv6_server():
    t = Topology()
    r = t.add_device("juniper-srx300")
    pc = t.add_device("pc")
    t.connect(pc.nic, r.get_port("ge-0/0/0"))
    out = run_all(r.create_cli(), "configure\nset interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:7::1/64\n"
                                  "set access address-assignment pool P6 family inet6 prefix 2001:db8:7::/64\n"
                                  "set system services dhcp-local-server dhcpv6 group G interface ge-0/0/0.0\n"
                                  "commit and-quit")
    assert "error" not in out, out
    t.engine.run(max_time=500)
    assert "2001:db8:7::10" in run(pc.create_cli(), "ipconfig /renew6")


def test_ospfv3_lsdb_and_inter_area():
    """PC1 - R1 (area 1) - R2 (ABR) - R3 (area 0) - PC2: R3 learns PC1's LAN as OI through LSAs."""
    t, rs, a, b = chain("ospf6")
    r1, r2, r3 = rs
    for r, ports in ((r1, ("GigabitEthernet0/0", "GigabitEthernet0/1")), (r2, ("GigabitEthernet0/0",))):
        for pn in ports:
            r.v6igp["ospf6"].cfg.interfaces[pn]["area"] = "0.0.0.1"
    for r in rs:
        r.v6igp["ospf6"].neighbors.clear()
        r.v6igp["ospf6"].lsdb.clear()
        r.v6igp["ospf6"]._mine.clear()
        r.v6igp["ospf6"].kick()
    t.engine.run(max_time=30000)
    assert r2.v6igp["ospf6"].is_abr()
    codes = {str(r.network): r.code for r in r3.routing_table6()}
    assert codes.get("2001:db8:1::/64") == "OI" and codes.get("2001:db8:12::/64") == "OI"
    assert {str(r.network): r.code for r in r1.routing_table6()}.get("2001:db8:2::/64") == "OI"
    db = r3.v6igp["ospf6"].database()
    assert any(k[1] == "inter" for k, _ in db) and any(k[1] == "router" for k, _ in db)
    assert ping6(t, a, "2001:db8:2::10").ok == 4
    from cli_helpers import run as crun
    c3 = r3.create_cli()
    crun(c3, "enable")
    out = crun(c3, "show ipv6 ospf database")
    assert "Inter Area Prefix Link States (Area 0.0.0.0)" in out and "2001:db8:1::/64" in out
    r1.get_port("gi0/0").set_admin(False)                 # PC1 LAN goes away: withdrawn everywhere
    t.engine.run(max_time=3000)
    assert "2001:db8:1::/64" not in {str(r.network) for r in r3.routing_table6()}
