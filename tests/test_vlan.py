"""802.1Q VLANs: access/trunk switchports, router-on-a-stick, all four vendor CLIs."""
from ipaddress import IPv4Interface

import pytest

from cli_helpers import run, run_all
from test_ping_core import ping as _ping
from netemu.core.topology import Topology


class _Result:
    def __init__(self, events):
        self.events = events
        self.received = sum(1 for e in events if e[0] == "reply")


def ping(t, dev, dst, count=4):
    """Ping and return an object with .received (replies counted)."""
    return _Result(_ping(dev, dst, count)[1].events)


def access(port, vlan):
    port.mode, port.pvid, port.tagged = "access", vlan, None


def trunk(port, allowed=None, native=1):
    port.mode, port.pvid = "trunk", native
    port.tagged = None if allowed is None else frozenset(allowed)


def pc(t, ip, gw):
    h = t.add_device("pc")
    h.set_ip(ip, "255.255.255.0")
    h.set_gateway(gw)
    return h


def two_switch_lab(switch="cisco-2960"):
    """A(v10) C(v20) on SW1 --trunk-- SW2 with B(v10) D(v20)."""
    t = Topology()
    s1, s2 = t.add_device(switch), t.add_device(switch)
    for s in (s1, s2):
        s.add_vlan(10, "SALES")
        s.add_vlan(20, "HR")
    a = pc(t, "192.168.10.1", None)
    b = pc(t, "192.168.10.2", None)
    c = pc(t, "192.168.10.3", None)   # same subnet as A/B but VLAN 20
    d = pc(t, "192.168.10.4", None)
    p1, p2 = list(s1.ports.values()), list(s2.ports.values())
    t.connect(a.nic, p1[0]); access(p1[0], 10)
    t.connect(c.nic, p1[1]); access(p1[1], 20)
    t.connect(b.nic, p2[0]); access(p2[0], 10)
    t.connect(d.nic, p2[1]); access(p2[1], 20)
    t.connect(p1[-1], p2[-1]); trunk(p1[-1]); trunk(p2[-1])
    t.engine.run(max_time=31_000)          # PVST: the inter-switch port listens + learns for 30 s
    return t, s1, s2, a, b, c, d


def test_vlans_isolate_and_trunk_carries_them():
    t, s1, s2, a, b, c, d = two_switch_lab()
    assert ping(t, a, "192.168.10.2").received == 4      # A -> B across the trunk, VLAN 10
    assert ping(t, c, "192.168.10.4").received == 4      # C -> D, VLAN 20
    assert ping(t, a, "192.168.10.3").received == 0      # A -> C: different VLANs
    assert ping(t, a, "192.168.10.4").received == 0
    vlans = {e.vlan for e in s1.mac_entries()}
    assert vlans == {10, 20}


def test_trunk_allowed_list_and_native_vlan():
    t, s1, s2, a, b, c, d = two_switch_lab()
    tr1, tr2 = list(s1.ports.values())[-1], list(s2.ports.values())[-1]
    trunk(tr1, allowed={10}); trunk(tr2, allowed={10})
    assert ping(t, a, "192.168.10.2").received == 4
    assert ping(t, c, "192.168.10.4").received == 0      # VLAN 20 pruned from the trunk
    # VLAN 20 as native on both ends: carried untagged
    trunk(tr1, allowed={10, 20}, native=20); trunk(tr2, allowed={10, 20}, native=20)
    seen = []
    t.engine.subscribe("frame_sent", lambda ev: seen.append(ev))
    assert ping(t, c, "192.168.10.4").received == 4


def test_tags_on_the_wire():
    t, s1, s2, a, b, c, d = two_switch_lab()
    tagged = []
    t.engine.subscribe("frame_delivered",
                       lambda ev: tagged.append((ev.info["dst"].device, ev.info["frame"].vlan)))
    ping(t, a, "192.168.10.2", count=1)
    on_trunk = {v for dev, v in tagged if dev is s2 and dev is not None}
    to_hosts = {v for dev, v in tagged if dev is b}
    assert 10 in on_trunk and to_hosts == {None}


def test_vlan_must_exist_on_switch():
    t, s1, s2, a, b, c, d = two_switch_lab()
    s2.remove_vlan(10)
    assert ping(t, a, "192.168.10.2").received == 0


def router_on_a_stick(router_model="cisco-2911", switch="cisco-2960"):
    t = Topology()
    sw = t.add_device(switch)
    r = t.add_device(router_model)
    a = pc(t, "192.168.10.10", "192.168.10.1")
    b = pc(t, "192.168.20.10", "192.168.20.1")
    sw.add_vlan(10); sw.add_vlan(20)
    ps = list(sw.ports.values())
    t.connect(a.nic, ps[0]); access(ps[0], 10)
    t.connect(b.nic, ps[1]); access(ps[1], 20)
    rp = list(r.ports.values())[0]
    t.connect(ps[-1], rp); trunk(ps[-1])
    return t, sw, r, rp, a, b


def test_router_on_a_stick_core():
    t, sw, r, rp, a, b = router_on_a_stick()
    rp.set_admin(True)
    s10 = r.add_subinterface(rp, 10, vlan=10)
    s20 = r.add_subinterface(rp, 20, vlan=20)
    s10.ip = IPv4Interface("192.168.10.1/24")
    s20.ip = IPv4Interface("192.168.20.1/24")
    assert ping(t, a, "192.168.20.10").received >= 4 - 1
    assert ping(t, a, "192.168.20.10").received == 4
    names = list(r.ports)
    assert names.index(s10.name) == names.index(rp.name) + 1
    assert s10.mac == rp.mac and s10.peer is rp.peer and s10 not in r.free_ports()
    # persistence of subinterfaces and switchport settings
    from netemu.core.persistence import topology_from_dict, topology_to_dict
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    r2 = t2.devices[r.id]
    assert r2.ports[s20.name].vlan == 20 and str(r2.ports[s20.name].ip) == "192.168.20.1/24"
    sw2 = t2.devices[sw.id]
    assert list(sw2.ports.values())[-1].mode == "trunk" and 10 in sw2.vlans
    a2 = t2.devices[a.id]
    assert ping(t2, a2, "192.168.20.10").received >= 3


# -- vendor CLIs ---------------------------------------------------------------------
SWITCH_SCRIPTS = {
    "cisco-2960": """
        enable
        configure terminal
        vlan 10
        name SALES
        exit
        vlan 20
        name HR
        exit
        interface FastEthernet0/1
        switchport mode access
        switchport access vlan 10
        interface fa0/2
        switchport access vlan 20
        interface GigabitEthernet0/2
        switchport mode trunk
        switchport trunk allowed vlan 10,20
        end
    """,
    "huawei-s5720": """
        system-view
        vlan batch 10 20
        interface GigabitEthernet0/0/1
        port link-type access
        port default vlan 10
        quit
        interface GigabitEthernet0/0/2
        port link-type access
        port default vlan 20
        quit
        interface GigabitEthernet0/0/24
        port link-type trunk
        port trunk allow-pass vlan 10 20
        return
    """,
    "juniper-ex2300": """
        configure
        set vlans SALES vlan-id 10
        set vlans HR vlan-id 20
        set interfaces ge-0/0/0 unit 0 family ethernet-switching interface-mode access
        set interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members SALES
        set interfaces ge-0/0/1 unit 0 family ethernet-switching vlan members 20
        set interfaces ge-0/0/23 unit 0 family ethernet-switching interface-mode trunk
        set interfaces ge-0/0/23 unit 0 family ethernet-switching vlan members [ SALES HR ]
        commit and-quit
    """,
    "mikrotik-crs326": """
        /interface bridge port set ether1 pvid=10
        /interface bridge port set ether2 pvid=20
        /interface bridge vlan add bridge=bridge vlan-ids=10 tagged=ether24 untagged=ether1
        /interface bridge vlan add bridge=bridge vlan-ids=20 tagged=ether24 untagged=ether2
        /interface bridge set bridge vlan-filtering=yes
    """,
}


@pytest.mark.parametrize("model", list(SWITCH_SCRIPTS))
def test_switch_vlan_cli(model):
    t, sw, r, rp, a, b = router_on_a_stick(switch=model)
    ps = list(sw.ports.values())
    for p in ps:                         # undo the core-level setup: the CLI must do it
        access(p, 1)
    sw.vlans = {1: "default"}
    out = run_all(sw.create_cli(), SWITCH_SCRIPTS[model])
    for bad in ("Invalid", "Error", "error", "bad command", "syntax"):
        assert bad not in out, out
    assert (ps[0].mode, ps[0].pvid) == ("access", 10)
    assert (ps[1].mode, ps[1].pvid) == ("access", 20)
    assert ps[-1].mode == "trunk" and ps[-1].vlan_allowed(10) and ps[-1].vlan_allowed(20)
    assert {10, 20} <= set(sw.vlans)
    rp.set_admin(True)
    for vid, ip in ((10, "192.168.10.1/24"), (20, "192.168.20.1/24")):
        s = r.add_subinterface(rp, vid, vlan=vid)
        s.ip = IPv4Interface(ip)
    ping(t, a, "192.168.20.10")
    assert ping(t, a, "192.168.20.10").received == 4


ROUTER_SCRIPTS = {
    "cisco-2911": """
        enable
        configure terminal
        interface GigabitEthernet0/0
        no shutdown
        interface GigabitEthernet0/0.10
        encapsulation dot1Q 10
        ip address 192.168.10.1 255.255.255.0
        interface g0/0.20
        encapsulation dot1q 20
        ip address 192.168.20.1 255.255.255.0
        end
    """,
    "huawei-ar2220": """
        system-view
        interface GigabitEthernet0/0/0.10
        dot1q termination vid 10
        ip address 192.168.10.1 24
        arp broadcast enable
        quit
        interface GigabitEthernet0/0/0.20
        dot1q termination vid 20
        ip address 192.168.20.1 255.255.255.0
        arp broadcast enable
        return
    """,
    "juniper-srx300": """
        configure
        set interfaces ge-0/0/0 vlan-tagging
        set interfaces ge-0/0/0 unit 10 vlan-id 10 family inet address 192.168.10.1/24
        set interfaces ge-0/0/0 unit 20 vlan-id 20
        set interfaces ge-0/0/0 unit 20 family inet address 192.168.20.1/24
        commit and-quit
    """,
    "mikrotik-rb4011": """
        /interface vlan add name=vlan10 vlan-id=10 interface=ether1
        /interface vlan add name=vlan20 vlan-id=20 interface=ether1
        /ip address add address=192.168.10.1/24 interface=vlan10
        /ip address add address=192.168.20.1/24 interface=vlan20
    """,
}


@pytest.mark.parametrize("model", list(ROUTER_SCRIPTS))
def test_router_subinterface_cli(model):
    t, sw, r, rp, a, b = router_on_a_stick(router_model=model)
    out = run_all(r.create_cli(), ROUTER_SCRIPTS[model])
    for bad in ("Invalid", "Error", "error", "bad command", "syntax", "Incomplete"):
        assert bad not in out, out
    subs = rp.subinterfaces
    assert sorted(s.vlan for s in subs) == [10, 20]
    ping(t, a, "192.168.20.10")
    assert ping(t, a, "192.168.20.10").received == 4


def test_cisco_vlan_show_commands_and_errors():
    t, sw, r, rp, a, b = router_on_a_stick()
    rp.set_admin(True)
    s = sw.create_cli()
    run_all(s, SWITCH_SCRIPTS["cisco-2960"])
    out = run(s, "show vlan brief")
    assert "10   SALES" in out and "Fa0/1" in out.split("10   SALES")[1].splitlines()[0]
    assert "1002 fddi-default" in out
    tr = run(s, "show interfaces trunk")
    assert "Gi0/2" in tr and "802.1q" in tr and "10,20" in tr
    st = run(s, "show interfaces status")
    assert st.splitlines()[1].split()[2] == "10" or " 10 " in st.splitlines()[1]
    run_all(s, "conf t\ninterface fa0/3\nswitchport access vlan 30\nend")
    assert 30 in sw.vlans                                  # auto-created like IOS
    run_all(s, "conf t\nno vlan 30\nend")
    assert 30 not in sw.vlans
    cfg = run(s, "show running-config")
    assert "switchport access vlan 10" in cfg and "switchport mode trunk" in cfg
    assert "vlan 10\n name SALES" in cfg
    rs = r.create_cli()
    run_all(rs, "enable\nconf t\ninterface g0/0.30")
    assert "Configuring IP routing on a LAN subinterface is only allowed" in \
        run(rs, "ip address 10.30.0.1 255.255.255.0")
    run_all(rs, "encapsulation dot1Q 30\nip address 10.30.0.1 255.255.255.0\nend")
    assert "interface GigabitEthernet0/0.30\n encapsulation dot1Q 30\n ip address 10.30.0.1" \
        in run(rs, "show running-config")
    assert "GigabitEthernet0/0.30" in run(rs, "show ip interface brief")
    run_all(rs, "conf t\nno interface g0/0.30\nend")
    assert r.get_port("GigabitEthernet0/0.30") is None


def test_mikrotik_bridge_is_vlan_transparent_until_filtering_enabled():
    t, sw, r, rp, a, b = router_on_a_stick(switch="mikrotik-crs326")
    for p in sw.ports.values():
        access(p, 1)
    sw.vlans = {1: "default"}
    s = sw.create_cli()
    run_all(s, "\n".join(SWITCH_SCRIPTS["mikrotik-crs326"].strip().splitlines()[:-1]))  # no vlan-filtering
    assert not sw.vlan_filtering
    rp.set_admin(True)
    for vid, ip in ((10, "192.168.10.1/24"), (20, "192.168.20.1/24")):
        r.add_subinterface(rp, vid, vlan=vid).ip = IPv4Interface(ip)
    assert ping(t, a, "192.168.20.10").received == 0      # untagged frames never reach a subinterface
    run(s, "/interface bridge set bridge vlan-filtering=yes")
    ping(t, a, "192.168.20.10")
    assert ping(t, a, "192.168.20.10").received == 4
    out = run(s, "/interface bridge vlan print")
    assert "ether24" in out and "10" in out
    assert "pvid=10" in run(s, "/export") and "vlan-filtering=yes" in run(s, "/export")
    assert "PVID" in run(s, "/interface bridge port print")
    run(s, "/interface bridge port set [find interface=ether2] pvid=10")
    assert list(sw.ports.values())[1].pvid == 10


def test_mikrotik_vlan_interface_print_and_export():
    t, sw, r, rp, a, b = router_on_a_stick(router_model="mikrotik-rb4011")
    s = r.create_cli()
    run_all(s, ROUTER_SCRIPTS["mikrotik-rb4011"])
    out = run(s, "/interface vlan print")
    assert "vlan10" in out and "ether1" in out
    exp = run(s, "/export")
    assert "add interface=ether1 name=vlan10 vlan-id=10" in exp and "interface=vlan20" in exp
    assert "already exists" in run(s, "/interface vlan add name=x vlan-id=10 interface=ether1")
    run(s, "/interface vlan remove 1")
    assert [x.name for x in rp.subinterfaces] == ["vlan10"]


def test_huawei_trunk_defaults_and_arp_broadcast():
    t, sw, r, rp, a, b = router_on_a_stick(router_model="huawei-ar2220", switch="huawei-s5720")
    for p in sw.ports.values():
        access(p, 1)
    sw.vlans = {1: "default"}
    ss = sw.create_cli()
    assert "does not exist" in run_all(ss, "system-view\ninterface GigabitEthernet0/0/1\nport default vlan 10")
    run_all(ss, "quit\nreturn")
    run_all(ss, SWITCH_SCRIPTS["huawei-s5720"])
    trunk_port = list(sw.ports.values())[-1]
    assert trunk_port.tagged == frozenset({1, 10, 20})   # VLAN 1 is allowed by default on VRP trunks
    disp = run(ss, "display vlan")
    assert "10   common  UT:GE0/0/1(U)" in disp and "TG:GE0/0/24(U)" in disp
    assert "trunk" in run(ss, "display port vlan")
    cur = run(ss, "display current-configuration")
    assert "vlan batch 10 20" in cur and " port trunk allow-pass vlan 10 20" in cur
    rs = r.create_cli()
    script = [l for l in ROUTER_SCRIPTS["huawei-ar2220"].strip().splitlines() if "arp broadcast" not in l]
    run_all(rs, "\n".join(script))
    assert ping(t, a, "192.168.20.10").received == 0      # forgot "arp broadcast enable"
    run_all(rs, "system-view\ninterface GigabitEthernet0/0/0.10\narp broadcast enable\nquit\n"
                "interface GigabitEthernet0/0/0.20\narp broadcast enable\nreturn")
    ping(t, a, "192.168.20.10")
    assert ping(t, a, "192.168.20.10").received == 4
    cur = run(rs, "display current-configuration")
    assert "interface GigabitEthernet0/0/0.10\n dot1q termination vid 10\n ip address 192.168.10.1" in cur
    run_all(rs, "system-view\nundo interface GigabitEthernet0/0/0.20\nreturn")
    assert len(rp.subinterfaces) == 1


def test_junos_vlan_names_resolved_at_commit():
    t, sw, r, rp, a, b = router_on_a_stick(router_model="juniper-srx300", switch="juniper-ex2300")
    for p in sw.ports.values():
        access(p, 1)
    sw.vlans = {1: "default"}
    s = sw.create_cli()
    out = run_all(s, "configure\nset interfaces ge-0/0/0 unit 0 family ethernet-switching vlan members NOPE\ncommit")
    assert "Vlan NOPE is not configured" in out and list(sw.ports.values())[0].pvid == 1
    run_all(s, "rollback\n" + SWITCH_SCRIPTS["juniper-ex2300"].strip().split("\n", 1)[1])
    assert list(sw.ports.values())[0].pvid == 10
    cfg = run(s, "show configuration")
    assert "members SALES;" in cfg and "members [ SALES HR ];" in cfg and "vlan-id 20;" in cfg
    vl = run(s, "show vlans")
    assert "SALES" in vl and "ge-0/0/0.0" in vl
    assert "tagged" in run(s, "show ethernet-switching interface")
    rs = r.create_cli()
    run_all(rs, ROUTER_SCRIPTS["juniper-srx300"])
    assert "ge-0/0/0.10" in run(rs, "show interfaces terse")
    rc = run(rs, "show configuration")
    assert "vlan-tagging;" in rc and "unit 10 {" in rc and "vlan-id 10;" in rc
    ping(t, a, "192.168.20.10")
    assert "via ge-0/0/0.20" in run(rs, "show route")


def test_cisco_native_vlan_and_allowed_add_remove():
    t, sw, r, rp, a, b = router_on_a_stick()
    s = sw.create_cli()
    run_all(s, "enable\nconf t\ninterface gi0/2\nswitchport mode trunk\n"
               "switchport trunk allowed vlan 10\nswitchport trunk allowed vlan add 20,30-31\n"
               "switchport trunk allowed vlan remove 30\nswitchport trunk native vlan 99\nend")
    p = sw.get_port("gi0/2")
    assert p.tagged == frozenset({10, 20, 31}) and p.pvid == 99
    run_all(s, "conf t\ninterface gi0/2\nswitchport mode access\nend")
    assert (p.mode, p.pvid) == ("access", 1)
    run_all(s, "conf t\ninterface gi0/2\nswitchport mode trunk\nend")
    assert p.pvid == 99                                     # native VLAN remembered
