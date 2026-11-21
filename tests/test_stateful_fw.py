"""Stateful firewalls: inside may start connections, replies come back, outside cannot start any."""
import pytest

from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CFG = {
    "cisco-2911": ("gi0/0", "gi0/1", """enable
configure terminal
interface gi0/0
ip address 192.168.1.1 255.255.255.0
no shutdown
interface gi0/1
ip address 203.0.113.1 255.255.255.0
no shutdown
exit
zone security INSIDE
exit
zone security OUTSIDE
exit
class-map type inspect match-any IN-TRAFFIC
match protocol icmp
match protocol tcp
match protocol udp
exit
policy-map type inspect IN-TO-OUT
class type inspect IN-TRAFFIC
inspect
exit
class class-default
drop
exit
exit
zone-pair security IN-OUT source INSIDE destination OUTSIDE
service-policy type inspect IN-TO-OUT
exit
interface gi0/0
zone-member security INSIDE
interface gi0/1
zone-member security OUTSIDE
end""", "show policy-map type inspect zone-pair sessions", "Session"),
    "juniper-srx300": ("ge-0/0/0", "ge-0/0/1", """configure
set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24
set interfaces ge-0/0/1 unit 0 family inet address 203.0.113.1/24
set security zones security-zone trust interfaces ge-0/0/0.0
set security zones security-zone untrust interfaces ge-0/0/1.0
set security policies from-zone trust to-zone untrust policy ALLOW-OUT match source-address any
set security policies from-zone trust to-zone untrust policy ALLOW-OUT match destination-address any
set security policies from-zone trust to-zone untrust policy ALLOW-OUT match application any
set security policies from-zone trust to-zone untrust policy ALLOW-OUT then permit
commit and-quit""", "show security flow session", "Session ID"),
    "mikrotik-rb4011": ("ether1", "ether2", """/ip address add address=192.168.1.1/24 interface=ether1
/ip address add address=203.0.113.1/24 interface=ether2
/ip firewall filter add chain=forward connection-state=established,related action=accept
/ip firewall filter add chain=forward in-interface=ether1 action=accept
/ip firewall filter add chain=forward action=drop""", "/ip firewall connection print", "icmp"),
}


@pytest.mark.parametrize("model", list(CFG))
def test_inside_out_only(model):
    pin, pout, cfg, show, want = CFG[model]
    t = Topology()
    r = t.add_device(model)
    inside, outside = t.add_device("pc"), t.add_device("server")
    t.connect(inside.nic, r.get_port(pin))
    t.connect(outside.nic, r.get_port(pout))
    out = run_all(r.create_cli(), cfg)
    assert not any(e in out for e in ("Invalid", "error:", "failure", "% Zone")), out
    inside.set_ip("192.168.1.10", "255.255.255.0")
    inside.set_gateway("192.168.1.1")
    outside.set_ip("203.0.113.10", "255.255.255.0")
    outside.set_gateway("203.0.113.1")
    t.engine.run(max_time=500)
    assert _ping(None, inside, "203.0.113.10").received == 4          # replies are let back in
    assert _ping(None, outside, "192.168.1.10").received == 0         # nothing starts from outside
    page = run(inside.create_cli(), "curl http://203.0.113.10/")    # TCP state works too
    assert "200" in page or "<" in page, page
    s = r.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert want in run(s, show)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=500)
    i2, o2 = t2.devices[inside.id], t2.devices[outside.id]
    assert _ping(None, i2, "203.0.113.10").received == 4 and _ping(None, o2, "192.168.1.10").received == 0
