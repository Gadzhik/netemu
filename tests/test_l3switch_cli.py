"""Inter-VLAN routing on a switch through each vendor CLI (SVI / Vlanif / irb / bridge VLAN interface)."""
import pytest

from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_ospf_cli import ok

CFG = {
    "cisco-2960": ("enable\nconfigure terminal\nvlan 10\nvlan 20\nexit\n"
                   "interface fa0/1\nswitchport access vlan 10\ninterface fa0/2\nswitchport access vlan 20\n"
                   "interface vlan 10\nip address 10.0.10.1 255.255.255.0\nno shutdown\n"
                   "interface vlan 20\nip address 10.0.20.1 255.255.255.0\nno shutdown\nexit\nip routing\nend"),
    "huawei-s5720": ("system-view\nvlan batch 10 20\ninterface GigabitEthernet0/0/1\nport link-type access\n"
                     "port default vlan 10\ninterface GigabitEthernet0/0/2\nport link-type access\nport default vlan 20\n"
                     "interface Vlanif10\nip address 10.0.10.1 24\ninterface Vlanif20\nip address 10.0.20.1 24\nreturn"),
    "juniper-ex2300": ("configure\nset vlans V10 vlan-id 10\nset vlans V20 vlan-id 20\n"
                       "set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members V10\n"
                       "set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members V20\n"
                       "set interfaces irb unit 10 family inet address 10.0.10.1/24\n"
                       "set interfaces irb unit 20 family inet address 10.0.20.1/24\n"
                       "set vlans V10 l3-interface irb.10\nset vlans V20 l3-interface irb.20\ncommit and-quit"),
    "mikrotik-crs326": ("/interface bridge set bridge vlan-filtering=yes\n"
                        "/interface bridge port set ether1 pvid=10\n/interface bridge port set ether2 pvid=20\n"
                        "/interface bridge vlan add bridge=bridge vlan-ids=10 tagged=bridge untagged=ether1\n"
                        "/interface bridge vlan add bridge=bridge vlan-ids=20 tagged=bridge untagged=ether2\n"
                        "/interface vlan add interface=bridge vlan-id=10 name=vlan10\n"
                        "/interface vlan add interface=bridge vlan-id=20 name=vlan20\n"
                        "/ip address add address=10.0.10.1/24 interface=vlan10\n"
                        "/ip address add address=10.0.20.1/24 interface=vlan20"),
}
SHOW = {"cisco-2960": "show ip route", "huawei-s5720": "display ip routing-table",
        "juniper-ex2300": "show route", "mikrotik-crs326": "/ip route print"}
CONF = {"cisco-2960": ("show running-config", "interface Vlan10"),
        "huawei-s5720": ("display current-configuration", "interface Vlanif10"),
        "juniper-ex2300": ("show configuration", "l3-interface irb.10;"),
        "mikrotik-crs326": ("/export", "add interface=bridge name=vlan10 vlan-id=10")}


@pytest.mark.parametrize("model", list(CFG))
def test_inter_vlan_routing_on_switch(model):
    t = Topology()
    sw = t.add_device(model)
    ports = sw.physical_ports()
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, ports[0])
    t.connect(b.nic, ports[1])
    ok(run_all(sw.create_cli(), CFG[model]))
    for h, ip in ((a, "10.0.10.5"), (b, "10.0.20.5")):
        h.set_ip(ip, "255.255.255.0")
        h.set_gateway(ip.rsplit(".", 1)[0] + ".1")
    t.engine.run(max_time=1000)
    out = run(a.create_cli(), "ping 10.0.20.5")
    assert "Received = 4" in out, out
    rt = run(sw.create_cli() if model != "cisco-2960" else _en(sw), SHOW[model])
    assert "10.0.20.0" in rt, rt
    cmd, want = CONF[model]
    assert want in run(sw.create_cli() if model != "cisco-2960" else _en(sw), cmd)


def _en(sw):
    s = sw.create_cli()
    run(s, "enable")
    return s


def test_cisco_switch_l2_port_refuses_ip_and_default_gateway():
    t = Topology()
    sw = t.add_device("cisco-2960")
    s = _en(sw)
    out = run_all(s, "configure terminal\ninterface fa0/1\nip address 1.1.1.1 255.0.0.0")
    assert "may not be configured on L2 links" in out
    ok(run_all(s, "end\nconfigure terminal\ninterface vlan 1\nip address 192.168.1.2 255.255.255.0\nno shutdown\n"
                  "exit\nip default-gateway 192.168.1.1\nend"))
    cfg = run(s, "show running-config")
    assert "ip default-gateway 192.168.1.1" in cfg and "interface Vlan1\n ip address 192.168.1.2 255.255.255.0" in cfg
    assert cfg.count("interface Vlan1") == 1
