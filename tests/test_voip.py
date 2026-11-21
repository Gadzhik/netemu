"""VoIP: IP phones in a voice VLAN, PCs behind them in the data VLAN, Cisco CME, a call with RTP."""
import pytest

from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping

ROUTER = """enable
configure terminal
interface gi0/0
no shutdown
interface gi0/0.10
encapsulation dot1Q 10
ip address 192.168.10.1 255.255.255.0
interface gi0/0.20
encapsulation dot1Q 20
ip address 192.168.20.1 255.255.255.0
exit
ip dhcp pool DATA
network 192.168.10.0 255.255.255.0
default-router 192.168.10.1
exit
ip dhcp pool VOICE
network 192.168.20.0 255.255.255.0
default-router 192.168.20.1
option 150 ip 192.168.20.1
exit
telephony-service
max-ephones 5
max-dn 5
ip source-address 192.168.20.1 port 2000
auto assign 1 to 5
exit
ephone-dn 1
number 1001
exit
ephone-dn 2
number 1002
end"""

SWITCH = {
    "cisco-2960": ("Fa0/24", ["Fa0/1", "Fa0/2"], """enable
configure terminal
vlan 10
name DATA
vlan 20
name VOICE
exit
interface range fa0/1 - 2
switchport mode access
switchport access vlan 10
switchport voice vlan 20
exit
interface fa0/24
switchport mode trunk
end"""),
    "huawei-s5720": ("GE0/0/24", ["GE0/0/1", "GE0/0/2"], """system-view
vlan batch 10 20
interface GigabitEthernet0/0/1
port link-type access
port default vlan 10
voice-vlan 20 enable
interface GigabitEthernet0/0/2
port link-type access
port default vlan 10
voice-vlan 20 enable
interface GigabitEthernet0/0/24
port link-type trunk
port trunk allow-pass vlan 10 20
return"""),
    "juniper-ex2300": ("ge-0/0/23", ["ge-0/0/0", "ge-0/0/1"], """configure
set vlans DATA vlan-id 10
set vlans VOICE vlan-id 20
set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members DATA
set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members DATA
set switch-options voip interface ge-0/0/0 vlan VOICE
set switch-options voip interface ge-0/0/1 vlan VOICE
set interfaces ge-0/0/23 unit 0 family ethernet-switching interface-mode trunk
set interfaces ge-0/0/23 unit 0 family ethernet-switching vlan members all
commit and-quit"""),
    "mikrotik-crs326": ("ether24", ["ether1", "ether2"], """/interface bridge port set ether1 pvid=10
/interface bridge port set ether2 pvid=10
/interface bridge vlan add bridge=bridge vlan-ids=10 tagged=ether24 untagged=ether1,ether2
/interface bridge vlan add bridge=bridge vlan-ids=20 tagged=ether24,ether1,ether2
/interface bridge set bridge vlan-filtering=yes
/ip neighbor discovery-settings set lldp-med-net-policy-vlan=20"""),
    "extreme-x440g2": ("24", ["1", "2"], """create vlan DATA
configure vlan DATA tag 10
create vlan VOICE
configure vlan VOICE tag 20
configure vlan Default delete ports 1-2,24
configure vlan DATA add ports 1-2 untagged
configure vlan DATA add ports 24 tagged
configure vlan VOICE add ports 1-2,24 tagged
configure lldp ports 1-2 advertise vendor-specific med policy application voice vlan VOICE dscp 46"""),
}


@pytest.mark.parametrize("sw_model", list(SWITCH))
def test_phones_register_and_call(sw_model):
    up, access, swcfg = SWITCH[sw_model]
    t = Topology()
    r = t.add_device("cisco-2911")
    sw = t.add_device(sw_model)
    p1, p2 = t.add_device("ip-phone"), t.add_device("ip-phone")
    pc = t.add_device("pc")
    t.connect(r.get_port("gi0/0"), sw.get_port(up))
    t.connect(p1.nic, sw.get_port(access[0]))
    t.connect(p2.nic, sw.get_port(access[1]))
    t.connect(pc.nic, p1.pc_port)                      # PC daisy-chained behind phone 1
    for dev, cfg in ((r, ROUTER), (sw, swcfg)):
        out = run_all(dev.create_cli(), cfg)
        assert not any(e in out for e in ("Invalid", "Error", "error:", "syntax error")), out
    pc.start_dhcp()
    t.engine.run(max_time=15000)

    assert p1.voice_vlan == 20 and str(p1.nic.ip.ip).startswith("192.168.20.")
    assert str(pc.nic.ip.ip).startswith("192.168.10."), "PC behind the phone must land in the data VLAN"
    assert {p1.number, p2.number} == {"1001", "1002"} and p1.state == p2.state == "idle"
    assert _ping(None, pc, "192.168.10.1").received == 4

    s = r.create_cli()
    run(s, "enable")
    assert run(s, "show ephone registered").count("REGISTERED") == 2

    assert p1.dial(p2.number) == ""
    t.engine.run(max_time=500)
    assert p2.state == "ringing" and p1.state == "ringback"
    assert p2.answer()
    t.engine.run(max_time=5000)
    assert p1.state == p2.state == "connected"
    assert p1.rtp_rx >= 8 and p2.rtp_rx >= 8
    assert "CONNECTED" in run(s, "show ephone-dn summary")
    p2.hangup()
    t.engine.run(max_time=500)
    assert p1.state == "idle" and p1.call is None

    assert p1.dial("9999") == ""
    t.engine.run(max_time=500)
    assert p1.state == "idle" and p1.history[-1].endswith("unknown number")

    t2 = topology_from_dict(topology_to_dict(t), Topology())
    sw2 = t2.devices[sw.id]
    assert (sw2.get_port(access[0]).voice_vlan or sw2.extra.get("lldp_med_vlan")) == 20
    assert t2.devices[r.id].cme.dns == {1: "1001", 2: "1002"}


def test_cme_running_config_and_max_ephones():
    t = Topology()
    r = t.add_device("cisco-2911")
    run_all(r.create_cli(), ROUTER)
    s = r.create_cli()
    run(s, "enable")
    rc = run(s, "show running-config")
    for line in ("telephony-service", " ip source-address 192.168.20.1 port 2000", " auto assign 1 to 5",
                 "ephone-dn  1", " number 1001", " option 150 ip 192.168.20.1"):
        assert line in rc
