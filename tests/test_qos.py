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
