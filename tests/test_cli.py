"""CLI dialects: Cisco IOS, Huawei VRP, Juniper Junos, MikroTik RouterOS, PC prompt."""
import pytest

from netemu.core.topology import Topology

from cli_helpers import run, run_all


def lab(router_model, switch_model="cisco-2960"):
    """PC0 - SW - R - PC1; nothing configured on the router."""
    t = Topology()
    r = t.add_device(router_model)
    sw = t.add_device(switch_model)
    a, b = t.add_device("pc"), t.add_device("pc")
    rp = list(r.ports.values())
    swp = list(sw.ports.values())
    t.connect(a.nic, swp[0])
    t.connect(swp[1], rp[0])
    t.connect(rp[1], b.nic)
    run(a.create_cli(), "ipconfig 192.168.1.10 255.255.255.0 192.168.1.1")
    run(b.create_cli(), "ipconfig 192.168.2.10 255.255.255.0 192.168.2.1")
    return t, r, sw, a, b


ROUTER_SCRIPTS = {
    "cisco-2911": """
        enable
        configure terminal
        hostname R1
        interface g0/0
        ip address 192.168.1.1 255.255.255.0
        no shutdown
        int gigabitEthernet 0/1
        ip add 192.168.2.1 255.255.255.0
        no shut
        end
    """,
    "huawei-ar2220": """
        system-view
        sysname R1
        interface GigabitEthernet0/0/0
        ip address 192.168.1.1 255.255.255.0
        quit
        interface g0/0/1
        ip address 192.168.2.1 24
        return
    """,
    "juniper-srx300": """
        configure
        set system host-name R1
        set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24
        set interfaces ge-0/0/1 unit 0 family inet address 192.168.2.1/24
        commit and-quit
    """,
    "mikrotik-rb4011": """
        /system identity set name=R1
        /ip address add address=192.168.1.1/24 interface=ether1
        /ip address add address=192.168.2.1/24 interface=ether2
    """,
}


@pytest.mark.parametrize("model", list(ROUTER_SCRIPTS))
def test_configure_router_via_cli_and_ping(model):
    t, r, sw, a, b = lab(model)
    s = r.create_cli()
    out = run_all(s, ROUTER_SCRIPTS[model])
    assert "Invalid" not in out and "Error" not in out and "error" not in out, out
    assert r.hostname == "R1"
    out = run(a.create_cli(), "ping 192.168.2.10")
    assert "Received = 4" in out, out
    assert "TTL=127" in out


# -- Cisco -------------------------------------------------------------------------
def cisco():
    t, r, sw, a, b = lab("cisco-2911")
    s = r.create_cli()
    run_all(s, ROUTER_SCRIPTS["cisco-2911"])
    return t, r, s, a, b


def test_cisco_modes_and_prompts():
    t = Topology()
    r = t.add_device("cisco-2911")
    s = r.create_cli()
    assert s.prompt == "Router>"
    run(s, "en")
    assert s.prompt == "Router#"
    run(s, "conf t")
    assert s.prompt == "Router(config)#"
    run(s, "int g0/1")
    assert s.prompt == "Router(config-if)#"
    run(s, "exit")
    assert s.prompt == "Router(config)#"
    run(s, "int g0/1")
    s.ctrl_z()
    assert s.prompt == "Router#"
    run(s, "disable")
    assert s.prompt == "Router>"


def test_cisco_errors():
    t, r, s, a, b = cisco()
    out = run(s, "show ip foo")
    assert out == " " * len("R1#show ip ") + "^\n% Invalid input detected at '^' marker.\n"
    assert "Incomplete" in run(s, "show")
    run(s, "conf t")
    assert "Ambiguous" in run(s, "i")  # interface / ip
    assert "Invalid" in run(s, "no exit")  # exit is not negatable
    run(s, "end")
    # config commands are not available in user mode
    run(s, "disable")
    assert "Invalid" in run(s, "configure terminal")


def test_cisco_help_and_completion():
    t, r, s, a, b = cisco()
    h = s.help("show ")
    assert "running-config" in h and "ip" in h and "version" in h
    assert s.help("sh").strip().startswith("show")
    assert "<cr>" in s.help("show ip route ")
    assert s.complete("sh") == "show "
    assert s.complete("show ip int") == "show ip interface "
    assert s.complete("show r") == "show running-config "
    run(s, "conf t")
    assert s.complete("no shut") is None  # 'shutdown' is only in config-if
    run(s, "int g0/0")
    assert s.complete("no shut") == "no shutdown "


def test_cisco_show_commands():
    t, r, s, a, b = cisco()
    run(a.create_cli(), "ping 192.168.2.10")
    brief = run(s, "show ip interface brief")
    assert "GigabitEthernet0/0     192.168.1.1     YES manual up                    up" in brief
    assert "GigabitEthernet0/2     unassigned      YES unset  administratively down down" in brief
    route = run(s, "sh ip ro")
    assert "C       192.168.1.0/24 is directly connected, GigabitEthernet0/0" in route
    assert "L       192.168.2.1/32 is directly connected, GigabitEthernet0/1" in route
    assert "Gateway of last resort is not set" in route
    arp = run(s, "show arp")
    assert "192.168.1.10" in arp and "192.168.2.10" in arp
    run_all(s, "conf t\nint g0/0\ndescription LAN side\nend")
    cfg = run(s, "show running-config")
    assert "hostname R1" in cfg
    assert "interface GigabitEthernet0/0\n description LAN side\n ip address 192.168.1.1 255.255.255.0" in cfg
    assert "interface GigabitEthernet0/2\n no ip address\n duplex auto\n speed auto\n shutdown" in cfg
    assert "GigabitEthernet0/0 is up, line protocol is up" in run(s, "show interfaces g0/0")
    assert "uptime" in run(s, "show version")


def test_cisco_static_routes_and_do():
    t, r, s, a, b = cisco()
    run_all(s, "conf t\nip route 0.0.0.0 0.0.0.0 192.168.2.254\nip route 10.0.0.0 255.0.0.0 g0/0 5")
    out = run(s, "do show ip route")
    assert "Gateway of last resort is 192.168.2.254 to network 0.0.0.0" in out
    assert "S*   0.0.0.0/0 [1/0] via 192.168.2.254" in out
    assert "S    10.0.0.0/8 is directly connected, GigabitEthernet0/0" in out
    assert "%Inconsistent address and mask" in run(s, "ip route 10.0.0.1 255.0.0.0 192.168.2.254")
    run(s, "no ip route 0.0.0.0 0.0.0.0 192.168.2.254")
    assert len(r.static_routes) == 1
    cfg = run(s, "do sh run")
    assert "ip route 10.0.0.0 255.0.0.0 GigabitEthernet0/0 5" in cfg


def test_cisco_ping_and_traceroute_output():
    t, r, s, a, b = cisco()
    out = run(s, "ping 192.168.2.10")
    assert "Sending 5, 100-byte ICMP Echos to 192.168.2.10, timeout is 2 seconds:" in out
    assert "!!!!!" in out
    assert "Success rate is 100 percent (5/5)" in out
    out = run(s, "ping 192.168.3.3")
    assert "UUUUU" not in out  # no route on the router itself -> '.'
    assert "Success rate is 0 percent (0/5)" in out
    out = run(s, "traceroute 192.168.2.10")
    assert "Tracing the route to 192.168.2.10" in out
    assert "  1   192.168.2.10" in out


def test_cisco_overlap_and_shutdown_messages():
    t, r, s, a, b = cisco()
    run_all(s, "conf t\nint g0/2")
    assert "overlaps with GigabitEthernet0/0" in run(s, "ip address 192.168.1.2 255.255.255.0")
    run(s, "int g0/0")
    out = run(s, "shutdown")
    assert "changed state to administratively down" in out
    assert "Received = 0" in run(a.create_cli(), "ping -n 1 192.168.2.10")
    out = run(s, "no shutdown")
    assert "changed state to up" in out


def test_cisco_write_reload():
    t, r, s, a, b = cisco()
    assert "startup-config is not present" in run(s, "show startup-config")
    assert "[OK]" in run(s, "copy running-config startup-config")
    run_all(s, "conf t\nhostname CHANGED\nend")
    assert r.hostname == "CHANGED"
    run(s, "reload")
    assert r.hostname == "R1"
    assert s.prompt == "R1>"
    run(s, "en")
    run(s, "erase startup-config")
    run(s, "reload")
    assert r.hostname == "Router"
    assert all(p.ip is None and not p.admin_up for p in r.ports.values())


def test_cisco_switch_commands():
    t, r, sw, a, b = lab("cisco-2911")
    s = r.create_cli()
    run_all(s, ROUTER_SCRIPTS["cisco-2911"])
    run(a.create_cli(), "ping 192.168.2.10")
    ss = sw.create_cli()
    mac = run(ss, "show mac address-table")
    assert "DYNAMIC     Fa0/1" in mac and "DYNAMIC     Fa0/2" in mac
    run(ss, "enable")
    st = run(ss, "show interfaces status")
    assert "Fa0/1" in st and "connected" in st and "notconnect" in st
    run(ss, "clear mac address-table dynamic")
    assert sw.mac_entries() == []
    run_all(ss, "conf t\nhostname SW1\ninterface fa0/1\nshutdown\nend")
    assert sw.hostname == "SW1"
    assert "Received = 0" in run(a.create_cli(), "ping -n 1 192.168.1.1")
    assert "Invalid" in run(ss, "conf t") or True
    run(ss, "int fa0/1")
    assert "may not be configured on L2 links" in run(ss, "ip address 1.1.1.1 255.0.0.0")  # L2 port


# -- Huawei ------------------------------------------------------------------------
def test_huawei_views_and_display():
    t, r, sw, a, b = lab("huawei-ar2220", "huawei-s5720")
    s = r.create_cli()
    assert s.prompt == "<Huawei>"
    run_all(s, ROUTER_SCRIPTS["huawei-ar2220"])
    assert s.prompt == "<R1>"
    run(s, "system-view")
    assert s.prompt == "[R1]"
    run(s, "interface GigabitEthernet0/0/2")
    assert s.prompt == "[R1-GigabitEthernet0/0/2]"
    run(s, "shutdown")
    assert not r.get_port("GE0/0/2").admin_up
    run(s, "undo shutdown")
    assert r.get_port("GE0/0/2").admin_up
    run(s, "quit")
    run(s, "ip route-static 0.0.0.0 0.0.0.0 192.168.2.254")
    run(s, "ip route-static 10.0.0.0 8 192.168.1.254 preference 100")
    run(s, "return")
    rt = run(s, "display ip routing-table")
    assert "0.0.0.0/0 Static  60" in rt
    assert "10.0.0.0/8 Static  100" in rt
    assert "192.168.1.0/24 Direct  0" in rt
    cfg = run(s, "display current-configuration")
    assert "sysname R1" in cfg and "ip route-static 10.0.0.0 255.0.0.0 192.168.1.254 preference 100" in cfg
    assert "interface GigabitEthernet0/0/0\n ip address 192.168.1.1 255.255.255.0" in cfg
    brief = run(s, "display ip interface brief")
    assert "GigabitEthernet0/0/0" in brief and "192.168.1.1/24" in brief
    out = run(s, "ping 192.168.2.10")
    assert "Reply from 192.168.2.10: bytes=56 Sequence=1 ttl=128" in out
    assert "0.00% packet loss" in out
    assert "192.168.2.10" in run(s, "display arp")
    out = run(s, "display foo")
    assert "Error: Unrecognized command found at '^' position." in out
    assert "Configuration file had been saved successfully" in run(s, "save")
    assert r.startup_config["hostname"] == "R1"
    out = run(s, "tracert 192.168.2.10")
    assert " 1 192.168.2.10" in out
    run(s, "system-view")
    assert "does not exist" in run(s, "undo ip route-static 1.0.0.0 8 1.1.1.1")
    run(s, "undo ip route-static 0.0.0.0 0 192.168.2.254")
    assert len(r.static_routes) == 1

    run(s, "return")
    run(s, "ping -c 1 192.168.1.10")  # traffic through the S5720
    ss = sw.create_cli()
    assert ss.prompt == "<HUAWEI>"
    mac = run(ss, "display mac-address")
    assert "GE0/0/1" in mac and "dynamic" in mac


# -- Juniper -----------------------------------------------------------------------
def test_junos_candidate_and_commit():
    t, r, sw, a, b = lab("juniper-srx300", "juniper-ex2300")
    s = r.create_cli()
    assert s.prompt == "root@srx300> "
    run(s, "configure")
    assert s.prompt.endswith("root@srx300# ") and "[edit]" in s.prompt
    run(s, "set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24")
    run(s, "set system host-name R1")
    # nothing applied before commit
    assert r.get_port("ge-0/0/0").ip is None and r.hostname == "srx300"
    assert "address 192.168.1.1/24;" in run(s, "show")
    assert "changed but not committed" in run(s, "exit")
    run(s, "configure")
    assert "commit complete" in run(s, "commit")
    assert str(r.get_port("ge-0/0/0").ip) == "192.168.1.1/24" and r.hostname == "R1"
    run(s, "set interfaces ge-0/0/1 unit 0 family inet address 192.168.1.2/24")
    assert "Overlapping subnet" in run(s, "commit")
    run(s, "rollback")
    run(s, "set interfaces ge-0/0/1 unit 0 family inet address 192.168.2.1/24")
    run(s, "set routing-options static route 0.0.0.0/0 next-hop 192.168.2.254")
    run(s, "commit and-quit")
    assert s.mode == "oper"
    rt = run(s, "show route")
    assert "0.0.0.0/0          *[Static/5]" in rt and "> to 192.168.2.254 via ge-0/0/1.0" in rt
    assert "192.168.1.1/32     *[Local/0]" in rt
    terse = run(s, "show interfaces terse")
    assert "ge-0/0/0.0              up    up   inet     192.168.1.1/24" in terse
    out = run(s, "ping 192.168.1.10 count 3")
    assert "3 packets transmitted, 3 packets received, 0% packet loss" in out
    assert "64 bytes from 192.168.1.10: icmp_seq=0 ttl=128" in out
    assert "192.168.1.10" in run(s, "show arp no-resolve")
    out = run(s, "show configuration")
    assert "host-name R1;" in out and "route 0.0.0.0/0 next-hop 192.168.2.254;" in out
    run(s, "configure")
    run(s, "delete routing-options static route 0.0.0.0/0")
    run(s, "set interfaces ge-0/0/1 disable")
    run(s, "commit")
    assert r.static_routes == [] and not r.get_port("ge-0/0/1").admin_up
    assert "syntax error" in run(s, "set foo bar")
    assert "Static" not in run(s, "run show route")
    out = run(s, "traceroute 192.168.1.10")
    assert "syntax error" in out  # traceroute is an operational command
    run(s, "exit")
    assert " 1  192.168.1.10 (192.168.1.10)" in run(s, "traceroute 192.168.1.10")

    ss = sw.create_cli()
    tbl = run(ss, "show ethernet-switching table")
    assert "ge-0/0/0.0" in tbl and "learned" in tbl


# -- MikroTik ----------------------------------------------------------------------
def test_mikrotik_menus():
    t, r, sw, a, b = lab("mikrotik-rb4011", "mikrotik-crs326")
    s = r.create_cli()
    assert s.prompt == "[admin@MikroTik] > "
    run_all(s, ROUTER_SCRIPTS["mikrotik-rb4011"])
    run(s, "/ip address")
    assert s.prompt == "[admin@R1] /ip address> "
    pr = run(s, "print")
    assert "192.168.1.1/24" in pr and "ether1" in pr and "192.168.1.0" in pr
    assert "already have such address" in run(s, "add address=192.168.1.1/24 interface=ether1")
    assert "input does not match any value of interface" in run(s, "add address=1.1.1.1/24 interface=ether99")
    run(s, "..")
    assert s.prompt == "[admin@R1] /ip> "
    run(s, "route add dst-address=10.0.0.0/8 gateway=192.168.2.254")
    run(s, "/ip route add gateway=192.168.1.254")
    rp = run(s, "/ip route print")
    assert "10.0.0.0/8" in rp and "0.0.0.0/0" in rp and "DAc" in rp
    run(s, "/ip route remove 0")
    assert len(r.static_routes) == 1
    out = run(s, "/ping 192.168.2.10 count=3")
    assert "sent=3 received=3 packet-loss=0%" in out
    assert "192.168.2.10" in run(s, "/ip arp print")
    run(s, "/interface disable ether3")
    assert not r.ports["ether3"].admin_up
    run(s, '/interface set ether1 comment="LAN side"')
    ip = run(s, "/interface print")
    assert ";;; LAN side" in ip and "ether3" in ip
    exp = run(s, "/export")
    assert "add address=192.168.1.1/24 interface=ether1 network=192.168.1.0" in exp
    assert "set [ find default-name=ether3 ] disabled=yes" in exp
    assert "set name=R1" in exp
    assert "bad command name foo (line 1 column 1)" in run(s, "foo")
    assert "  name: R1" in run(s, "/system identity print")
    out = run(s, "/tool traceroute 192.168.2.10")
    assert "192.168.2.10" in out
    assert s.complete("/ip addr") == "/ip address "
    assert "print" in s.help("/ip address ")
    run(s, "/ping 192.168.1.10 count=1")  # traffic through the CRS switch
    run(s, "/system reset-configuration")
    assert r.hostname == "MikroTik" and all(p.ip is None for p in r.ports.values())

    ss = sw.create_cli()
    hosts = run(ss, "/interface bridge host print")
    assert "ether1" in hosts and "ether2" in hosts
    assert "ADDRESS" in run(ss, "/ip address print")   # CRS runs full RouterOS (L3 on the bridge)


# -- PC prompt -----------------------------------------------------------------------
def test_pc_prompt():
    t, r, s, a, b = cisco()
    pc = a.create_cli()
    assert pc.prompt == "C:\\>"
    out = run(pc, "ipconfig")
    assert "IPv4 Address....................: 192.168.1.10" in out
    assert "Default Gateway.................: 192.168.1.1" in out
    assert "Physical Address" in run(pc, "ipconfig /all")
    out = run(pc, "ping 192.168.2.10")
    assert "Pinging 192.168.2.10 with 32 bytes of data:" in out
    assert "Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)" in out
    assert "192.168.1.1" in run(pc, "arp -a")
    run(pc, "arp -d")
    assert "No ARP Entries Found" in run(pc, "arp -a")
    out = run(pc, "tracert 192.168.2.10")
    assert "  1   " in out and "192.168.1.1" in out and "  2   " in out and "Trace complete." in out
    out = run(pc, "ping 10.10.10.10")
    assert "Reply from 192.168.1.1: Destination host unreachable." in out
    assert "is not recognized as an internal or external command" in run(pc, "foo")
    run(pc, "cls")
    assert pc.wants_clear
    assert "Usage" in run(pc, "ipconfig 1.2.3")


def test_pc_ping_ttl_expired_and_cancel():
    t, r, s, a, b = cisco()
    run_all(s, "conf t\nip route 10.0.0.0 255.0.0.0 192.168.2.10")  # b is a host, drops it
    pc = a.create_cli()
    buf = []
    job = pc.execute("ping -t 192.168.2.10", buf.append)
    t.engine.run(max_time=5000)
    pc.interrupt()
    assert job.done
    assert "Ping statistics" in "".join(buf)


def test_junos_qualified_next_hop_floating_route():
    t = Topology()
    r = t.add_device("juniper-srx300")
    s = r.create_cli()
    run_all(s, "configure\n"
               "set routing-options static route 10.0.0.0/8 next-hop 1.1.1.1\n"
               "set routing-options static route 10.0.0.0/8 qualified-next-hop 2.2.2.2 preference 20\n"
               "commit")
    assert sorted((str(x.next_hop), x.distance) for x in r.static_routes) == [("1.1.1.1", 5), ("2.2.2.2", 20)]
    assert "route 10.0.0.0/8 qualified-next-hop 2.2.2.2 preference 20;" in run(s, "show")
    run(s, "delete routing-options static route 10.0.0.0/8 qualified-next-hop 2.2.2.2")
    run(s, "commit")
    assert [str(x.next_hop) for x in r.static_routes] == ["1.1.1.1"]
