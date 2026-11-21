"""EtherChannel / LACP: bundling rules, STP sees one port, failover, persistence."""
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received == 4


def pair(mode_a="active", mode_b="passive", model="cisco-2960"):
    t = Topology()
    a, b = t.add_device(model), t.add_device(model)
    pa, pb = a.physical_ports(), b.physical_ports()
    for i in (0, 1):
        t.connect(pa[i], pb[i])
    for sw, mode in ((a, mode_a), (b, mode_b)):
        sw.add_channel(1, "Port-channel1", "Po1")
        for p in sw.physical_ports()[:2]:
            sw.set_channel_member(p, 1, mode)
    h1, h2 = t.add_device("pc"), t.add_device("pc")
    t.connect(h1.nic, pa[5])
    t.connect(h2.nic, pb[5])
    h1.set_ip("10.0.0.1", "255.255.255.0")
    h2.set_ip("10.0.0.2", "255.255.255.0")
    for sw in (a, b):
        sw.stp.mode = "rapid-pvst"
        sw.stp.schedule()
    t.engine.run(max_time=3000)
    return t, a, b, h1, h2


def test_lacp_bundle_forms_and_stp_sees_one_port():
    t, a, b, h1, h2 = pair()
    po = a.channel_for_group(1)
    assert po.is_up and len(po.active_members) == 2
    assert "alternate" not in a.stp.roles.values() and "alternate" not in b.stp.roles.values()
    assert po.name in a.stp.roles
    assert ping(h1, "10.0.0.2")
    a.physical_ports()[0].set_admin(False)          # one member fails: traffic keeps flowing
    t.engine.run(max_time=1000)
    assert po.is_up and len(po.active_members) == 1 and ping(h1, "10.0.0.2")


def test_incompatible_modes_do_not_bundle():
    for ma, mb in (("passive", "passive"), ("on", "active"), ("auto", "auto")):
        t, a, b, h1, h2 = pair(ma, mb)
        assert not a.channel_for_group(1).is_up, (ma, mb)
    t, a, b, h1, h2 = pair("desirable", "auto")
    assert a.channel_for_group(1).is_up


def test_channel_persists():
    t, a, b, h1, h2 = pair("on", "on")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    a2 = t2.devices[a.id]
    po = a2.channel_for_group(1)
    assert po is not None and [m.channel_mode for m in po.members] == ["on", "on"]
    t2.engine.run(max_time=3000)
    assert po.is_up and ping(t2.devices[h1.id], "10.0.0.2")


import pytest  # noqa: E402

from cli_helpers import run, run_all  # noqa: E402

CLI = {
    "cisco-2960": ("enable\nconfigure terminal\ninterface fa0/1\nchannel-group 1 mode active\n"
                   "interface fa0/2\nchannel-group 1 mode active\nend",
                   "show etherchannel summary", "Po1(SU)", "show running-config", "channel-group 1 mode active"),
    "huawei-s5720": ("system-view\ninterface Eth-Trunk 1\nmode lacp\nquit\ninterface GigabitEthernet0/0/1\neth-trunk 1\n"
                     "interface GigabitEthernet0/0/2\neth-trunk 1\nreturn",
                     "display eth-trunk 1", "Operate status: up", "display current-configuration", " eth-trunk 1"),
    "juniper-ex2300": ("configure\nset chassis aggregated-devices ethernet device-count 1\n"
                       "set interfaces ge-0/0/0 ether-options 802.3ad ae0\nset interfaces ge-0/0/1 ether-options 802.3ad ae0\n"
                       "set interfaces ae0 aggregated-ether-options lacp active\ncommit and-quit",
                       "show lacp interfaces", "Collecting distributing", "show configuration", "802.3ad ae0;"),
    "mikrotik-crs326": ("/interface bonding add name=bond1 slaves=ether1,ether2 mode=802.3ad",
                        "/interface bonding print", "R bond1", "/export", "add mode=802.3ad name=bond1"),
    "extreme-x440g2": ("enable sharing 1 grouping 1-2 algorithm address-based L2 lacp",
                       "show sharing", "LACP", "show configuration", "enable sharing 1 grouping 1-2"),
}


@pytest.mark.parametrize("model", list(CLI))
def test_lag_cli_each_vendor(model):
    cfg, show, want, conf, want_conf = CLI[model]
    t = Topology()
    a, b = t.add_device(model), t.add_device(model)
    pa, pb = a.physical_ports(), b.physical_ports()
    for i in (0, 1):
        t.connect(pa[i], pb[i])
    for sw in (a, b):
        out = run_all(sw.create_cli(), cfg)
        assert "Invalid" not in out and "Error" not in out and "error" not in out and "failure" not in out, out
    t.engine.run(max_time=1000)
    po = a.channels()[0]
    assert po.is_up and len(po.active_members) == 2
    s = a.create_cli()
    if model == "cisco-2960":
        run(s, "enable")
    assert want in run(s, show)
    assert want_conf in run(s, conf)
