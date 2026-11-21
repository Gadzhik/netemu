"""IPv6 through each vendor CLI: PC1 - router - PC2, static and SLAAC."""
import pytest

from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_ospf_cli import ok

CFG = {
    "cisco-2911": ("enable\nconfigure terminal\nipv6 unicast-routing\n"
                   "interface gi0/0\nipv6 address 2001:db8:1::1/64\nno shutdown\n"
                   "interface gi0/1\nipv6 address 2001:db8:2::1/64\nno shutdown\nend"),
    "huawei-ar2220": ("system-view\nipv6\ninterface GigabitEthernet0/0/0\nipv6 enable\n"
                      "ipv6 address 2001:db8:1::1 64\nundo ipv6 nd ra halt\nundo shutdown\n"
                      "interface GigabitEthernet0/0/1\nipv6 enable\nipv6 address 2001:db8:2::1/64\n"
                      "undo ipv6 nd ra halt\nundo shutdown\nreturn"),
    "juniper-srx300": ("configure\nset interfaces ge-0/0/0 unit 0 family inet6 address 2001:db8:1::1/64\n"
                       "set interfaces ge-0/0/1 unit 0 family inet6 address 2001:db8:2::1/64\ncommit and-quit"),
    "mikrotik-rb4011": ("/ipv6 address add address=2001:db8:1::1/64 interface=ether1\n"
                        "/ipv6 address add address=2001:db8:2::1/64 interface=ether2"),
}
SHOW = {"cisco-2911": "show ipv6 route", "huawei-ar2220": "display ipv6 routing-table",
        "juniper-srx300": "show route table inet6.0", "mikrotik-rb4011": "/ipv6 route print"}
CONF = {"cisco-2911": "show running-config", "huawei-ar2220": "display current-configuration",
        "juniper-srx300": "show configuration", "mikrotik-rb4011": "/export"}


@pytest.mark.parametrize("model", list(CFG))
def test_ipv6_routing_and_slaac(model):
    t = Topology()
    r = t.add_device(model)
    pcs = [t.add_device("pc"), t.add_device("pc")]
    ports = [p for p in r.physical_ports() if not p.is_loopback][:2]
    for h, p in zip(pcs, ports):
        t.connect(h.nic, p)
    cli = r.create_cli()
    ok(run_all(cli, CFG[model]))
    h1, h2 = (h.create_cli() for h in pcs)
    ok(run(h1, "ipconfig /ipv6 2001:db8:1::10/64 2001:db8:1::1"))
    ok(run(h2, "ipconfig /ipv6 2001:db8:2::10/64 2001:db8:2::1"))
    out = run(h1, "ping 2001:db8:2::10")
    assert "Reply from 2001:db8:2::10" in out and "Lost = 0" in out, out
    rt = run(cli, SHOW[model]).lower()
    assert "2001:db8:1::" in rt and "2001:db8:2::" in rt, rt
    assert "2001:db8:1::1" in run(cli, CONF[model]).lower()
    if model in ("cisco-2911", "huawei-ar2220", "mikrotik-rb4011"):
        ok(run(h2, "ipconfig /ipv6 auto"))
        t.engine.run(max_time=2_000)
        addr = pcs[1].nic.ipv6[0].ip
        assert str(addr).startswith("2001:db8:2:")
        assert "Lost = 0" in run(h1, f"ping {addr}")
        assert "IPv6 Address" in run(h2, "ipconfig")
    # router pings over IPv6 too
    assert "2001:db8:1::10" in run(cli, {"cisco-2911": "ping 2001:db8:1::10", "huawei-ar2220": "ping ipv6 2001:db8:1::10",
                                         "juniper-srx300": "ping 2001:db8:1::10 count 2",
                                         "mikrotik-rb4011": "/ping 2001:db8:1::10 count=2"}[model])


def test_ipv6_persists_through_save():
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t = Topology()
    r = t.add_device("cisco-2911")
    ok(run_all(r.create_cli(), CFG["cisco-2911"] + "\nconfigure terminal\nipv6 route 2001:db8:9::/48 2001:db8:2::10\nend"))
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    out = run_all(t2.devices[r.id].create_cli(), "enable\nshow running-config")
    assert "ipv6 unicast-routing" in out and "ipv6 route 2001:DB8:9::/48 2001:DB8:2::10" in out


def test_ipv6_traceroute_all_prompts():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    win, lin = t.add_device("pc"), t.add_device("pc-linux")
    t.connect(win.nic, r1.get_port("gi0/0"))
    t.connect(r1.get_port("gi0/1"), r2.get_port("gi0/1"))
    t.connect(lin.nic, r2.get_port("gi0/0"))
    run_all(r1.create_cli(), "enable\nconfigure terminal\nipv6 unicast-routing\ninterface gi0/0\n"
                             "ipv6 address 2001:db8:1::1/64\nno shutdown\ninterface gi0/1\nipv6 address 2001:db8:12::1/64\n"
                             "no shutdown\nexit\nipv6 route 2001:db8:2::/64 2001:db8:12::2\nend")
    run_all(r2.create_cli(), "enable\nconfigure terminal\nipv6 unicast-routing\ninterface gi0/0\n"
                             "ipv6 address 2001:db8:2::1/64\nno shutdown\ninterface gi0/1\nipv6 address 2001:db8:12::2/64\n"
                             "no shutdown\nexit\nipv6 route 2001:db8:1::/64 2001:db8:12::1\nend")
    w = win.create_cli()
    run(w, "ipconfig /ipv6 2001:db8:1::10/64 2001:db8:1::1")
    t.engine.run(max_time=2000)
    lx = next(a for a in lin.nic.ipv6 if str(a.ip).startswith("2001:db8:2:"))
    out = run(w, f"tracert -6 {lx.ip}")
    assert "2001:db8:1::1" in out and str(lx.ip) in out and "Trace complete" in out, out
    out = run(lin.create_cli(), "traceroute6 -n 2001:db8:1::10")
    assert " 1  2001:db8:2::1" in out and " 3  2001:db8:1::10" in out, out
    c = r1.create_cli()
    run(c, "enable")
    assert str(lx.ip).upper() in run(c, f"traceroute ipv6 {lx.ip}").upper()
