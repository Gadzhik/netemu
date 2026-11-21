"""FortiGate: config/edit/set CLI, stateful policies with NAT, default deny, static routes."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CFG = """config system global
set hostname FGT-HQ
end
config system interface
edit "port1"
set ip 203.0.113.2 255.255.255.0
set allowaccess ping
next
edit "port2"
set ip 192.168.1.99 255.255.255.0
set allowaccess ping https ssh
next
end
config firewall address
edit "LAN"
set subnet 192.168.1.0 255.255.255.0
next
end
config router static
edit 1
set dst 0.0.0.0 0.0.0.0
set gateway 203.0.113.1
set device "port1"
next
end
config firewall policy
edit 1
set name "LAN-to-WAN"
set srcintf "port2"
set dstintf "port1"
set srcaddr "LAN"
set dstaddr "all"
set action accept
set schedule "always"
set service "ALL"
set nat enable
next
end"""


def test_fortigate_policy_nat_and_default_deny():
    t = Topology()
    fgt = t.add_device("fortigate-60f")
    pc, isp = t.add_device("pc"), t.add_device("server")
    t.connect(isp.nic, fgt.get_port("port1"))
    t.connect(pc.nic, fgt.get_port("port2"))
    s = fgt.create_cli()
    out = run_all(s, CFG)
    assert "fail" not in out, out
    assert fgt.hostname == "FGT-HQ" and s.prompt == "FGT-HQ # "
    pc.set_ip("192.168.1.10", "255.255.255.0")
    pc.set_gateway("192.168.1.99")
    isp.set_ip("203.0.113.1", "255.255.255.0")                # no route back to 192.168.1.0: NAT must hide it
    t.engine.run(max_time=500)
    assert _ping(None, pc, "203.0.113.1").received == 4
    assert "total session" in run(s, "diagnose sys session list")
    assert _ping(None, isp, "192.168.1.10").received == 0                 # no policy port1 -> port2
    assert "S*" in run(s, "get router info routing-table all")
    assert 'set srcintf "port2"' in run(s, "show firewall policy")
    assert "Command fail" in run_all(s, "config firewall policy\nedit 2\nset action maybe")
    run(s, "abort")
    run(s, "end")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=500)
    f2 = t2.devices[fgt.id]
    run(f2.create_cli(), "show")                 # session over the restored device (rebuild from config)
    assert _ping(None, t2.devices[pc.id], "203.0.113.1").received == 4
