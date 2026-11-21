"""NAT64: an IPv6-only PC pings an IPv4 server through 64:ff9b::/96 (Cisco and Huawei CLIs)."""
import pytest

from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

CFG = {
    "cisco-2911": ("enable\nconfigure terminal\nipv6 unicast-routing\ninterface gi0/0\n"
                   "ipv6 address 2001:db8:6::1/64\nnat64 enable\nno shutdown\ninterface gi0/1\n"
                   "ip address 198.51.100.1 255.255.255.0\nno shutdown\nexit\nnat64 prefix stateful 64:ff9b::/96\nend",
                   ("gi0/0", "gi0/1"), "show nat64 translations", "icmp"),
    "huawei-ar2220": ("system-view\nipv6\nnat64 enable\nnat64 prefix 64:ff9b:: prefix-length 96\n"
                      "interface GigabitEthernet0/0/0\nipv6 enable\nipv6 address 2001:db8:6::1/64\nundo ipv6 nd ra halt\n"
                      "nat64 enable\ninterface GigabitEthernet0/0/1\nip address 198.51.100.1 24\nreturn",
                      ("GE0/0/0", "GE0/0/1"), "display nat64 session all", "ICMP"),
}


@pytest.mark.parametrize("model", list(CFG))
def test_nat64_ping(model):
    cfg, (p6, p4), show, want = CFG[model]
    t = Topology()
    r = t.add_device(model)
    pc6, srv4 = t.add_device("pc"), t.add_device("server")
    t.connect(pc6.nic, r.get_port(p6))
    t.connect(srv4.nic, r.get_port(p4))
    out = run_all(r.create_cli(), cfg)
    assert "Invalid" not in out and "Error" not in out, out
    pc6.set_ipv6("2001:db8:6::10/64")
    pc6.set_gateway6("2001:db8:6::1")
    srv4.set_ip("198.51.100.20", "255.255.255.0")
    srv4.set_gateway("198.51.100.1")
    t.engine.run(max_time=1000)
    out = run(pc6.create_cli(), "ping 64:ff9b::198.51.100.20")
    assert "Reply from 64:ff9b::c633:6414" in out and "Received = 4" in out, out
    s = r.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert want in run(s, show)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[r.id].nat64 is not None and t2.devices[r.id].nat64.interfaces


@pytest.mark.parametrize("model", list(CFG))
def test_nat64_tcp_http(model):
    cfg, (p6, p4), show, want = CFG[model]
    t = Topology()
    r = t.add_device(model)
    pc6, srv4 = t.add_device("pc"), t.add_device("server")
    t.connect(pc6.nic, r.get_port(p6))
    t.connect(srv4.nic, r.get_port(p4))
    run_all(r.create_cli(), cfg)
    pc6.set_ipv6("2001:db8:6::10/64")
    pc6.set_gateway6("2001:db8:6::1")
    srv4.set_ip("198.51.100.20", "255.255.255.0")
    srv4.set_gateway("198.51.100.1")
    srv4.http_server.pages["index.html"] = "<h1>v4 only</h1>"
    t.engine.run(max_time=1000)
    out = run(pc6.create_cli(), "curl http://[64:ff9b::198.51.100.20]/")
    assert "v4 only" in out, out
    assert any(s.proto == "tcp" and s.dport == 80 for s in r.nat64.sessions)
    s = r.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert ("tcp" if model.startswith("cisco") else "TCP") in run(s, show)
