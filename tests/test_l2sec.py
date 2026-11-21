"""Port security and DHCP snooping (Cisco and Huawei CLIs)."""
import pytest

from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received


PSEC = {"cisco-2960": ("enable\nconfigure terminal\ninterface fa0/1\nswitchport mode access\nswitchport port-security\n"
                       "switchport port-security maximum 1\nswitchport port-security violation shutdown\nend",
                       "fa0/1", "fa0/2", "show port-security interface fa0/1", "Secure-shutdown"),
        "huawei-s5720": ("system-view\ninterface GigabitEthernet0/0/1\nport link-type access\nport-security enable\n"
                         "port-security max-mac-num 1\nport-security protect-action error-down\nreturn",
                         "GE0/0/1", "GE0/0/2", "display port-security", "error-down")}


@pytest.mark.parametrize("model", list(PSEC))
def test_port_security_violation_shuts_port(model):
    cfg, p1, p2, show, want = PSEC[model]
    t = Topology()
    sw = t.add_device(model)
    hub = t.add_device("hub")
    a, b, c = t.add_device("pc"), t.add_device("pc"), t.add_device("pc")
    t.connect(hub.ports["Port0"], sw.get_port(p1))
    t.connect(a.nic, hub.ports["Port1"])
    t.connect(b.nic, hub.ports["Port2"])
    t.connect(c.nic, sw.get_port(p2))
    out = run_all(sw.create_cli(), cfg)
    assert "Invalid" not in out and "Error" not in out, out
    for h, ip in ((a, "10.0.0.1"), (b, "10.0.0.2"), (c, "10.0.0.3")):
        h.set_ip(ip, "255.255.255.0")
    t.engine.run(max_time=3000)
    assert ping(a, "10.0.0.3") == 4                 # first MAC is learned as secure
    ping(b, "10.0.0.3")                              # second MAC on the same port: violation
    port = sw.get_port(p1)
    assert port.err_disabled and not port.admin_up
    s = sw.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert want in run(s, show)


def test_dhcp_snooping_blocks_rogue_server():
    t = Topology()
    sw = t.add_device("cisco-2960")
    good, rogue, pc = t.add_device("server"), t.add_device("server"), t.add_device("pc")
    t.connect(good.nic, sw.get_port("fa0/1"))
    t.connect(rogue.nic, sw.get_port("fa0/2"))
    t.connect(pc.nic, sw.get_port("fa0/3"))
    from netemu.core.services.dhcp import DhcpPool
    for srv, ip, start in ((good, "10.0.0.2", "10.0.0.100"), (rogue, "10.0.0.66", "10.0.0.200")):
        srv.set_ip(ip, "255.255.255.0")
        srv.dhcp_server.cfg.enabled = True
        srv.dhcp_server.cfg.pools = [DhcpPool("P", "10.0.0.0/24", "10.0.0.1", None, "", start,
                                              start.rsplit(".", 1)[0] + "." + str(int(start.rsplit(".", 1)[1]) + 20))]
    s = sw.create_cli()
    run_all(s, "enable\nconfigure terminal\nip dhcp snooping\nip dhcp snooping vlan 1\ninterface fa0/1\n"
               "ip dhcp snooping trust\nend")
    t.engine.run(max_time=3000)
    pc.start_dhcp()
    t.engine.run(max_time=5000)
    assert str(pc.nic.ip.ip).startswith("10.0.0.1")              # 10.0.0.1xx from the trusted server
    b = run(s, "show ip dhcp snooping binding")
    assert str(pc.nic.ip.ip) in b and "FastEthernet0/3" in b
    assert "ip dhcp snooping trust" in run(s, "show running-config")


def test_juniper_ex_mac_limit_and_dhcp_security():
    t = Topology()
    sw = t.add_device("juniper-ex2300")
    hub = t.add_device("hub")
    a, b, c = t.add_device("pc"), t.add_device("pc"), t.add_device("pc")
    t.connect(hub.ports["Port0"], sw.get_port("ge-0/0/0"))
    t.connect(a.nic, hub.ports["Port1"])
    t.connect(b.nic, hub.ports["Port2"])
    t.connect(c.nic, sw.get_port("ge-0/0/1"))
    out = run_all(sw.create_cli(), "configure\nset vlans V1 vlan-id 10\n"
                                   "set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members V1\n"
                                   "set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members V1\n"
                                   "set switch-options interface ge-0/0/0 interface-mac-limit 1 packet-action shutdown\n"
                                   "set vlans V1 forwarding-options dhcp-security group G overrides trusted interface ge-0/0/1\n"
                                   "commit and-quit")
    assert "error" not in out and "syntax" not in out, out
    for h, ip in ((a, "10.0.0.1"), (b, "10.0.0.2"), (c, "10.0.0.3")):
        h.set_ip(ip, "255.255.255.0")
    t.engine.run(max_time=3000)
    assert ping(a, "10.0.0.3") == 4
    ping(b, "10.0.0.3")
    assert sw.get_port("ge-0/0/0").err_disabled
    assert sw.dhcp_snooping["enabled"] and 10 in sw.dhcp_snooping["vlans"] and sw.get_port("ge-0/0/1").dhcp_trust
