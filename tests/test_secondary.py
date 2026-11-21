"""Secondary IPv4 addresses: two subnets on one router interface (Cisco, Huawei, Junos, RouterOS)."""
import pytest

from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CASES = {
    "cisco-2911": ("gi0/0", """enable
configure terminal
interface gi0/0
ip address 192.168.1.1 255.255.255.0
ip address 192.168.2.1 255.255.255.0 secondary
no shutdown
end""", "show running-config", " ip address 192.168.2.1 255.255.255.0 secondary"),
    "huawei-ar2220": ("GE0/0/0", """system-view
interface GigabitEthernet0/0/0
ip address 192.168.1.1 24
ip address 192.168.2.1 24 sub
return""", "display current-configuration", " ip address 192.168.2.1 255.255.255.0 sub"),
    "juniper-srx300": ("ge-0/0/0", """configure
set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24
set interfaces ge-0/0/0 unit 0 family inet address 192.168.2.1/24
commit and-quit""", "show configuration", "address 192.168.2.1/24;"),
    "mikrotik-rb4011": ("ether1", """/ip address add address=192.168.1.1/24 interface=ether1
/ip address add address=192.168.2.1/24 interface=ether1""", "/export", "add address=192.168.2.1/24 interface=ether1"),
}


@pytest.mark.parametrize("model", list(CASES))
def test_two_subnets_on_one_interface(model):
    port, script, show, line = CASES[model]
    t = Topology()
    r = t.add_device(model, "R1")
    sw = t.add_device("cisco-2960", "SW")
    a, b = t.add_device("pc", "A"), t.add_device("pc", "B")
    t.connect(r.get_port(port), sw.get_port("fa0/1"))
    t.connect(a.nic, sw.get_port("fa0/2"))
    t.connect(b.nic, sw.get_port("fa0/3"))
    out = run_all(r.create_cli(), script)
    assert not any(e in out for e in ("Invalid", "Error", "error:", "failure", "% ")), out
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")
    t.engine.run(max_time=35000)                       # STP on the switch
    assert {str(x.network) for x in r.routing_table() if x.code == "C"} >= {"192.168.1.0/24", "192.168.2.0/24"}
    assert _ping(None, b, "192.168.2.1").received == 4         # the router answers on its secondary address
    assert _ping(None, a, "192.168.2.10").received == 4        # routed between the two subnets
    s = r.create_cli()
    if model == "cisco-2911":
        run(s, "enable")
    assert line in run(s, show)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=35000)
    a2 = next(d for d in t2.devices.values() if d.name == "A")
    assert _ping(None, a2, "192.168.2.10").received == 4
