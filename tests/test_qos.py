"""QoS: classification, DSCP marking and policing (Cisco MQC, Huawei traffic policy)."""
from ipaddress import IPv4Address

import pytest

from cli_helpers import run, run_all
from netemu.core.packets import IcmpPacket, IPv4Packet
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.security.qos import QosClass, QosPolicy, apply
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CFG = {
    "cisco-2911": ("gi0/0", "gi0/1", "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                   "no shutdown\ninterface gi0/1\nip address 192.168.2.1 255.255.255.0\nno shutdown\nexit\n"
                   "class-map match-any ICMP\nmatch protocol icmp\nexit\npolicy-map MARK\nclass ICMP\nset dscp ef\nexit\n"
                   "class class-default\nset dscp default\nexit\nexit\ninterface gi0/0\nservice-policy input MARK\nend",
                   "show policy-map interface gi0/0", "dscp ef"),
    "huawei-ar2220": ("GE0/0/0", "GE0/0/1", "system-view\ninterface GigabitEthernet0/0/0\nip address 192.168.1.1 24\n"
                      "interface GigabitEthernet0/0/1\nip address 192.168.2.1 24\nquit\n"
                      "traffic classifier ICMP operator or\nif-match protocol icmp\nquit\n"
                      "traffic behavior EF\nremark dscp ef\nquit\ntraffic policy MARK\nclassifier ICMP behavior EF\nquit\n"
                      "interface GigabitEthernet0/0/0\ntraffic-policy MARK inbound\nreturn",
                      "display traffic policy statistics", "dscp ef"),
}


@pytest.mark.parametrize("model", list(CFG))
def test_marking_visible_on_the_wire(model):
    pin, pout, cfg, show, want = CFG[model]
    t = Topology()
    r = t.add_device(model)
    a, b = t.add_device("pc"), t.add_device("server")
    t.connect(a.nic, r.get_port(pin))
    t.connect(b.nic, r.get_port(pout))
    out = run_all(r.create_cli(), cfg)
    assert "Invalid" not in out and "Error" not in out and "not configured" not in out, out
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")


    t.engine.run(max_time=500)
    assert _ping(None, a, "192.168.2.10").received == 4
    s = r.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    o = run(s, show)
    assert want in o and "4 packets" in o, o
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    r2 = t2.devices[r.id]
    assert r2.qos["MARK"].classes[0].set_dscp == 46 and r2.get_port(pin).qos_in == "MARK"


def test_policer_drops_above_rate():
    t = Topology()
    r = t.add_device("cisco-2911")
    pol = QosPolicy("P", [QosClass("class-default", {"any": True}, police_bps=8000)])
    port = r.get_port("gi0/0")
    pkt = IPv4Packet(IPv4Address("10.0.0.1"), IPv4Address("10.0.0.2"), 64, IcmpPacket(8, 0, 1, 1, 1000))
    passed = sum(apply(r, pol, pkt, port, "in") is not None for _ in range(10))   # 10 KB in the same instant
    assert passed == 1 and pol.classes[0].dropped == 9
    t.engine.run(max_time=3000)                                                   # bucket refills (1 KB/s)
    assert apply(r, pol, pkt, port, "in") is not None


def _lan(t, r, pin, pout):
    a, b = t.add_device("pc"), t.add_device("server")
    t.connect(a.nic, r.get_port(pin))
    t.connect(b.nic, r.get_port(pout))
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")
    return a, b


def test_mikrotik_queue_and_mangle():
    t = Topology()
    r = t.add_device("mikrotik-rb4011")
    a, b = _lan(t, r, "ether1", "ether2")
    out = run_all(r.create_cli(), "/ip address add address=192.168.1.1/24 interface=ether1\n"
                                  "/ip address add address=192.168.2.1/24 interface=ether2\n"
                                  "/ip firewall mangle add chain=forward protocol=icmp action=change-dscp new-dscp=46\n"
                                  "/queue simple add name=pc1 target=192.168.1.10 max-limit=8k/8k")
    assert "failure" not in out and "invalid" not in out, out
    t.engine.run(max_time=300)
    assert _ping(None, a, "192.168.2.10").received >= 2
    s = r.create_cli()
    assert "packets=" in run(s, "/ip firewall mangle print") and r.qos["mt-mangle"].classes[0].matched > 0
    assert "pc1" in run(s, "/queue simple print")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[r.id].qos_global == ["mt-mangle", "mt-q-pc1"]


def test_juniper_policer_and_dscp():
    t = Topology()
    r = t.add_device("juniper-srx300")
    a, b = _lan(t, r, "ge-0/0/0", "ge-0/0/1")
    out = run_all(r.create_cli(), "configure\nset interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24\n"
                                  "set interfaces ge-0/0/1 unit 0 family inet address 192.168.2.1/24\n"
                                  "set firewall policer LIMIT if-exceeding bandwidth-limit 1m burst-size-limit 15k\n"
                                  "set firewall policer LIMIT then discard\n"
                                  "set firewall family inet filter QOS term PING from protocol icmp\n"
                                  "set firewall family inet filter QOS term PING then dscp ef\n"
                                  "set firewall family inet filter QOS term PING then policer LIMIT\n"
                                  "set firewall family inet filter QOS term PING then accept\n"
                                  "set firewall family inet filter QOS term ALL then accept\n"
                                  "set interfaces ge-0/0/0 unit 0 family inet filter input QOS\ncommit and-quit")
    assert "error" not in out, out
    t.engine.run(max_time=300)
    assert _ping(None, a, "192.168.2.10").received == 4
    c = r.qos["filter-QOS"].classes[0]
    assert c.set_dscp == 46 and c.police_bps == 1000000 and c.matched >= 4
    assert r.get_port("ge-0/0/0").qos_in == "filter-QOS"


def test_extreme_egress_rate_limit_drops():
    t = Topology()
    sw = t.add_device("extreme-x440g2")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("1"))
    t.connect(b.nic, sw.get_port("2"))
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    out = run_all(sw.create_cli(), "configure ports 2 rate-limit egress 64 Kbps")
    assert "Error" not in out, out
    t.engine.run(max_time=500)
    assert _ping(None, a, "10.0.0.2").received >= 1
    pol = sw.qos["rl-" + sw.get_port("2").name]
    assert pol.classes[0].matched > 0 and pol.classes[0].police_bps == 64000
