"""Cisco ASA: nameif / security levels, stateful inspection, inspect icmp, object NAT, ACL to the DMZ."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

CFG = """enable
configure terminal
hostname FW1
interface GigabitEthernet1/1
nameif outside
ip address 203.0.113.1 255.255.255.0
no shutdown
interface GigabitEthernet1/2
nameif inside
ip address 192.168.1.1 255.255.255.0
no shutdown
interface GigabitEthernet1/3
nameif dmz
security-level 50
ip address 172.16.0.1 255.255.255.0
no shutdown
exit
object network INSIDE-NET
subnet 192.168.1.0 255.255.255.0
nat (inside,outside) dynamic interface
exit
access-list OUTSIDE-IN extended permit tcp any host 172.16.0.10 eq 80
access-group OUTSIDE-IN in interface outside
end"""


def build():
    t = Topology()
    fw = t.add_device("cisco-asa5506")
    pc, web, ext = t.add_device("pc"), t.add_device("server"), t.add_device("server")
    t.connect(ext.nic, fw.get_port("Gi1/1"))
    t.connect(pc.nic, fw.get_port("Gi1/2"))
    t.connect(web.nic, fw.get_port("Gi1/3"))
    out = run_all(fw.create_cli(), CFG)
    assert "ERROR" not in out and "Invalid" not in out, out
    assert 'Security level for "inside" set to 100' in out
    for h, ip, gw in ((pc, "192.168.1.10", "192.168.1.1"), (web, "172.16.0.10", "172.16.0.1"),
                      (ext, "203.0.113.10", "203.0.113.1")):
        h.set_ip(ip, "255.255.255.0")
        h.set_gateway(gw)
    web.http_server.pages["index.html"] = "<h1>DMZ web</h1>"
    ext.http_server.pages["index.html"] = "<h1>Internet</h1>"
    t.engine.run(max_time=500)
    return t, fw, pc, web, ext


def test_asa_security_levels_nat_and_acl():
    t, fw, pc, web, ext = build()
    s = fw.create_cli()
    run(s, "enable")
    assert "inside" in run(s, "show nameif") and "100" in run(s, "show nameif")
    # TCP inside -> outside is allowed and NATed; replies come back (stateful)
    assert "Internet" in run(pc.create_cli(), "curl http://203.0.113.10/")
    assert "TCP" in run(s, "show conn")
    # ICMP is not inspected by default: the echo replies are dropped
    assert _ping(None, pc, "203.0.113.10").received == 0
    run_all(s, "configure terminal\npolicy-map global_policy\nclass inspection_default\ninspect icmp\nend")
    assert _ping(None, pc, "203.0.113.10").received == 4
    # outside -> DMZ only what the ACL permits, outside -> inside never
    assert "DMZ web" in run(ext.create_cli(), "curl http://172.16.0.10/")
    assert _ping(None, ext, "172.16.0.10").received == 0
    assert _ping(None, ext, "192.168.1.10").received == 0
    # DMZ (50) -> inside (100) is denied, inside -> DMZ allowed
    assert _ping(None, web, "192.168.1.10").received == 0
    assert _ping(None, pc, "172.16.0.10").received == 4
    rc = run(s, "show running-config")
    assert " nameif dmz" in rc and " security-level 50" in rc and "nat (inside,outside) dynamic interface" in rc
    assert "access-group OUTSIDE-IN in interface outside" in rc and "inspect icmp" in rc
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=500)
    assert _ping(None, t2.devices[pc.id], "203.0.113.10").received == 4
    assert _ping(None, t2.devices[ext.id], "192.168.1.10").received == 0
