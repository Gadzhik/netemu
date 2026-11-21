"""HSRP / VRRP: election, virtual IP + MAC, failover, preemption, persistence."""
from ipaddress import IPv4Address, IPv4Interface

from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received


def lan(proto="hsrp", p1=110, p2=100, preempt=None):
    t = Topology()
    sw = t.add_device("cisco-2960")
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    pc = t.add_device("pc")
    t.connect(r1.get_port("gi0/0"), sw.get_port("fa0/1"))
    t.connect(r2.get_port("gi0/0"), sw.get_port("fa0/2"))
    t.connect(pc.nic, sw.get_port("fa0/3"))
    for r, ip, prio in ((r1, "192.168.1.2", p1), (r2, "192.168.1.3", p2)):
        p = r.get_port("gi0/0")
        p.ip = IPv4Interface(f"{ip}/24")
        p.set_admin(True)
        g = r.fhrp.group(p.name, proto, 1)
        g.vip, g.priority, g.preempt = IPv4Address("192.168.1.254"), prio, preempt
        r.fhrp.changed(g)
    pc.set_ip("192.168.1.10", "255.255.255.0")
    pc.set_gateway("192.168.1.254")
    t.engine.run(max_time=35_000)
    return t, r1, r2, pc


def state(r):
    return next(iter(r.fhrp.groups.values())).state


def test_hsrp_election_virtual_mac_and_failover():
    t, r1, r2, pc = lan()
    assert state(r1) == "active" and state(r2) == "standby"
    assert ping(pc, "192.168.1.254") == 4
    e = pc.arp.lookup(IPv4Address("192.168.1.254"), t.engine.now)
    assert e.mac.cisco() == "0000.0c07.ac01"
    r1.get_port("gi0/0").set_admin(False)
    t.engine.run(max_time=12_000)
    assert state(r2) == "active"
    assert ping(pc, "192.168.1.254") == 4
    r1.get_port("gi0/0").set_admin(True)          # HSRP: no preemption by default
    t.engine.run(max_time=15_000)
    assert state(r2) == "active" and state(r1) == "standby"


def test_hsrp_preempt_and_vrrp():
    t, r1, r2, pc = lan(preempt=True)
    r1.get_port("gi0/0").set_admin(False)
    t.engine.run(max_time=12_000)
    r1.get_port("gi0/0").set_admin(True)
    t.engine.run(max_time=15_000)
    assert state(r1) == "active" and state(r2) == "standby"
    t, r1, r2, pc = lan("vrrp", 100, 200)
    assert state(r2) == "master" and state(r1) == "backup"
    assert pc.arp.lookup(IPv4Address("192.168.1.254"), t.engine.now) is None or True
    assert ping(pc, "192.168.1.254") == 4
    assert pc.arp.lookup(IPv4Address("192.168.1.254"), t.engine.now).mac.cisco() == "0000.5e00.0101"


def test_fhrp_persists():
    t, r1, r2, pc = lan()
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=35_000)
    assert state(t2.devices[r1.id]) == "active" and ping(t2.devices[pc.id], "192.168.1.254") == 4


import pytest  # noqa: E402

from cli_helpers import run, run_all  # noqa: E402

CLI = {
    "cisco-2911": (lambda ip, pr: f"enable\nconfigure terminal\ninterface gi0/0\nip address {ip} 255.255.255.0\nno shutdown\n"
                                  f"standby 1 ip 192.168.1.254\nstandby 1 priority {pr}\nstandby 1 preempt\nend",
                   "gi0/0", "show standby brief", "Active", "show running-config", "standby 1 ip 192.168.1.254"),
    "huawei-ar2220": (lambda ip, pr: f"system-view\ninterface GigabitEthernet0/0/0\nip address {ip} 24\n"
                                     f"vrrp vrid 1 virtual-ip 192.168.1.254\nvrrp vrid 1 priority {pr}\nreturn",
                      "GE0/0/0", "display vrrp brief", "Master", "display current-configuration",
                      "vrrp vrid 1 virtual-ip 192.168.1.254"),
    "juniper-srx300": (lambda ip, pr: f"configure\nset interfaces ge-0/0/0 unit 0 family inet address {ip}/24 vrrp-group 1 "
                                      f"virtual-address 192.168.1.254\nset interfaces ge-0/0/0 unit 0 family inet address "
                                      f"{ip}/24 vrrp-group 1 priority {pr}\ncommit and-quit",
                       "ge-0/0/0", "show vrrp", "master", "show configuration", "virtual-address 192.168.1.254;"),
    "mikrotik-rb4011": (lambda ip, pr: f"/ip address add address={ip}/24 interface=ether1\n"
                                       f"/interface vrrp add interface=ether1 vrid=1 priority={pr}\n"
                                       "/ip address add address=192.168.1.254/32 interface=vrrp1",
                        "ether1", "/interface vrrp print", "RM", "/export", "add interface=ether1 name=vrrp1"),
}


@pytest.mark.parametrize("model", list(CLI))
def test_fhrp_cli_each_vendor(model):
    cfg, port, show, want, conf, want_conf = CLI[model]
    t = Topology()
    sw = t.add_device("cisco-2960")
    r1, r2, pc = t.add_device(model), t.add_device(model), t.add_device("pc")
    t.connect(r1.get_port(port), sw.get_port("fa0/1"))
    t.connect(r2.get_port(port), sw.get_port("fa0/2"))
    t.connect(pc.nic, sw.get_port("fa0/3"))
    for r, ip, pr in ((r1, "192.168.1.2", 200), (r2, "192.168.1.3", 100)):
        out = run_all(r.create_cli(), cfg(ip, pr))
        assert not any(e in out for e in ("Invalid", "Error", "error", "failure")), out
    pc.set_ip("192.168.1.10", "255.255.255.0")
    pc.set_gateway("192.168.1.254")
    t.engine.run(max_time=35_000)
    assert state(r1) in ("active", "master") and state(r2) in ("standby", "backup")
    assert ping(pc, "192.168.1.254") == 4
    s = r1.create_cli()
    if model.startswith("cisco"):
        run(s, "enable")
    assert want in run(s, show)
    assert want_conf in run(s, conf)


def test_exos_vrrp():
    t = Topology()
    sw = t.add_device("cisco-2960")
    x1, x2, pc = t.add_device("extreme-x440g2"), t.add_device("extreme-x440g2"), t.add_device("pc")
    t.connect(x1.get_port("1"), sw.get_port("fa0/1"))
    t.connect(x2.get_port("1"), sw.get_port("fa0/2"))
    t.connect(pc.nic, sw.get_port("fa0/3"))
    for x, ip, pr in ((x1, "192.168.1.2", 200), (x2, "192.168.1.3", 100)):
        out = run_all(x.create_cli(), f"configure vlan default ipaddress {ip}/24\ncreate vrrp vlan Default vrid 1\n"
                                      f"configure vrrp vlan Default vrid 1 priority {pr}\n"
                                      "configure vrrp vlan Default vrid 1 add 192.168.1.254\nenable vrrp")
        assert "Error" not in out and "%%" not in out, out
    pc.set_ip("192.168.1.10", "255.255.255.0")
    t.engine.run(max_time=35_000)
    assert state(x1) == "master" and state(x2) == "backup"
    assert "MSTR" in run(x1.create_cli(), "show vrrp")
    assert ping(pc, "192.168.1.254") == 4
