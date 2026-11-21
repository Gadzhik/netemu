"""MPLS traffic engineering (RSVP-TE) on Cisco: explicit and dynamic paths, bandwidth, failover, autoroute."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.routing.te import te_of
from netemu.core.topology import Topology
from test_vlan import ping as _ping

# R1 -gi0/1- R2 -gi0/2- R4 ;  R1 -gi0/2- R3 -gi0/2- R4  (R3's side to R4 is gi0/2 on R4 as well)
LINKS = {("R1", "gi0/1"): ("R2", "gi0/1", "10.0.12"), ("R1", "gi0/2"): ("R3", "gi0/1", "10.0.13"),
         ("R2", "gi0/2"): ("R4", "gi0/1", "10.0.24"), ("R3", "gi0/2"): ("R4", "gi0/2", "10.0.34")}


def base(name, i):
    lines = ["enable", "configure terminal", f"hostname {name}", "mpls traffic-eng tunnels",
             "interface loopback0", f"ip address {i}.{i}.{i}.{i} 255.255.255.255"]
    for (a, ap), (b, bp, net) in LINKS.items():
        for me, port, host in ((a, ap, 1), (b, bp, 2)):
            if me == name:
                lines += [f"interface {port}", f"ip address {net}.{host} 255.255.255.0", "mpls traffic-eng tunnels",
                          "ip rsvp bandwidth 10000", "no shutdown"]
    if name in ("R1", "R4"):
        lines += ["interface gi0/0", f"ip address 192.168.{i}.1 255.255.255.0", "no shutdown"]
    lines += ["exit", "router ospf 1", "network 0.0.0.0 255.255.255.255 area 0", "mpls traffic-eng router-id loopback0",
              "mpls traffic-eng area 0", "end"]
    return "\n".join(lines)


HEAD = """configure terminal
ip explicit-path name VIA-R3 enable
next-address 10.0.13.2
next-address 10.0.34.2
exit
interface tunnel0
ip unnumbered loopback0
tunnel destination 4.4.4.4
tunnel mode mpls traffic-eng
tunnel mpls traffic-eng bandwidth 4000
tunnel mpls traffic-eng path-option 1 explicit name VIA-R3
tunnel mpls traffic-eng path-option 2 dynamic
exit
ip route 192.168.4.0 255.255.255.0 tunnel0
end"""


def build():
    t = Topology()
    rs = {n: t.add_device("cisco-2911", n) for n in ("R1", "R2", "R3", "R4")}
    for (a, ap), (b, bp, _) in LINKS.items():
        t.connect(rs[a].get_port(ap), rs[b].get_port(bp))
    a, b = t.add_device("pc", "A"), t.add_device("pc", "B")
    t.connect(a.nic, rs["R1"].get_port("gi0/0"))
    t.connect(b.nic, rs["R4"].get_port("gi0/0"))
    for i, (n, r) in enumerate(rs.items(), 1):
        out = run_all(r.create_cli(), base(n, i))
        assert "Invalid" not in out and "% " not in out, out
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.4.10", "255.255.255.0")
    b.set_gateway("192.168.4.1")
    t.engine.run(max_time=45000)
    s = rs["R1"].create_cli()
    run(s, "enable")
    out = run_all(s, HEAD)
    assert "Invalid" not in out, out
    t.engine.run(max_time=t.engine.now + 5000)
    return t, rs, a, b, s


def test_te_explicit_path_bandwidth_and_failover():
    t, rs, a, b, s = build()
    st = te_of(rs["R1"])
    lsp = st.lsps["Tunnel0"]
    assert lsp.option == 1 and [str(h[2]) for h in lsp.hops] == ["10.0.13.2", "10.0.34.2"]
    te = run(s, "show mpls traffic-eng tunnels")
    assert "Oper: up" in te and "path option 1, type explicit VIA-R3 (Basis for Setup" in te, te
    assert "Explicit Route: 10.0.13.2 10.0.34.2" in te
    assert "4000K" in run(s, "show ip rsvp interface")                  # bandwidth reserved on gi0/2
    assert te_of(rs["R3"]).reserved.get("GigabitEthernet0/2") == 4000   # ... and on R3 towards R4
    before = rs["R3"].mpls.switched
    assert _ping(None, a, "192.168.4.10").received == 4
    assert rs["R3"].mpls.switched > before                              # label-switched through R3
    # the explicit path breaks: the head end falls back to path option 2 (CSPF, via R2)
    rs["R3"].get_port("gi0/2").set_admin(False)
    t.engine.run(max_time=t.engine.now + 6000)
    lsp = st.lsps["Tunnel0"]
    assert lsp.option == 2 and str(lsp.hops[0][2]) == "10.0.12.2"
    assert te_of(rs["R3"]).reserved.get("GigabitEthernet0/2", 0) == 0    # old reservation released
    assert _ping(None, a, "192.168.4.10").received == 4
    # asking for more than any link can reserve: no LSP, tunnel down
    run_all(s, "configure terminal\ninterface tunnel0\ntunnel mpls traffic-eng bandwidth 20000\nend")
    t.engine.run(max_time=t.engine.now + 3000)
    assert "Tunnel0" not in st.lsps and not rs["R1"].get_port("Tunnel0").is_up
    assert "Oper: down" in run(s, "show mpls traffic-eng tunnels")
    cfg = run(s, "show running-config")
    for line in ("mpls traffic-eng tunnels", "ip explicit-path name VIA-R3 enable", " next-address 10.0.13.2",
                 " tunnel mode mpls traffic-eng", " tunnel mpls traffic-eng path-option 1 explicit name VIA-R3",
                 " tunnel mpls traffic-eng path-option 2 dynamic", " ip rsvp bandwidth 10000"):
        assert line in cfg, line


def test_te_autoroute_and_persistence():
    t, rs, a, b, s = build()
    run_all(s, "configure terminal\nno ip route 192.168.4.0 255.255.255.0 tunnel0\ninterface tunnel0\n"
               "tunnel mpls traffic-eng autoroute announce\nend")
    t.engine.run(max_time=t.engine.now + 3000)
    via = [r for r in rs["R1"].routing_table() if str(r.network) == "192.168.4.0/24"]
    assert via and via[0].port.name == "Tunnel0", via                  # OSPF route moved onto the tunnel
    before = rs["R3"].mpls.switched
    assert _ping(None, a, "192.168.4.10").received == 4
    assert rs["R3"].mpls.switched > before
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=50000)
    r1 = next(d for d in t2.devices.values() if d.name == "R1")
    assert "Tunnel0" in te_of(r1).lsps
    a2 = next(d for d in t2.devices.values() if d.name == "A")
    assert _ping(None, a2, "192.168.4.10").received == 4
