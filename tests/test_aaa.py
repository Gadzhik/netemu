"""AAA: device login through TACACS+ / RADIUS with local fallback, and 802.1X port authentication."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def r(s, line):
    return run(s.active(), line)


def lab():
    """R1 (client) - SW1 - R2 (protected router) and an AAA server on the same LAN."""
    t = Topology()
    r1, r2 = t.add_device("cisco-2911", "R1"), t.add_device("cisco-2911", "R2")
    sw = t.add_device("cisco-2960", "SW1")
    srv = t.add_device("server", "AAA")
    for dev, p in ((r1, "gi0/0"), (r2, "gi0/0")):
        t.connect(dev.get_port(p), sw.get_port(f"Fa0/{1 if dev is r1 else 2}"))
    t.connect(srv.nic, sw.get_port("Fa0/10"))
    srv.set_ip("10.0.0.100", "255.255.255.0")
    srv.aaa_server.tacacs = srv.aaa_server.radius = True
    srv.aaa_server.secret = "T4c4cs"
    srv.aaa_server.users = {"alice": "Wonder1", "pcuser": "Pc-pass1"}
    run_all(r1.create_cli(), "enable\nconfigure terminal\nhostname R1\ninterface gi0/0\nip address 10.0.0.1 255.255.255.0\n"
                             "no shutdown\nend")
    out = run_all(r2.create_cli(), """enable
configure terminal
hostname R2
interface gi0/0
ip address 10.0.0.2 255.255.255.0
no shutdown
exit
username backup secret Local-1
aaa new-model
tacacs server TAC1
address ipv4 10.0.0.100
key T4c4cs
exit
aaa authentication login default group tacacs+ local
line vty 0 4
transport input telnet ssh
end""")
    assert "Invalid" not in out and "%" not in out.replace("%SYS", "").replace("%LINK", "").replace("%LINEPROTO", ""), out
    t.engine.run(max_time=500)
    s = r1.create_cli()
    run(s, "enable")
    return t, r1, r2, sw, srv, s


def test_tacacs_login_and_local_fallback():
    t, r1, r2, sw, srv, s = lab()
    out = run(s, "telnet 10.0.0.2")
    assert "User Access Verification" in out and "Username" in s.active().display_prompt()
    r(s, "alice")
    r(s, "wrong")                                   # rejected by the server
    assert s.active() is not s and "Username" in s.active().display_prompt()
    r(s, "alice")
    r(s, "Wonder1")
    assert s.active().display_prompt() == "R2>"
    r(s, "exit")
    assert any("alice" in x and "accepted" in x for x in srv.aaa_server.log)
    srv.aaa_server.tacacs = False                   # server unreachable: the "local" method is used
    run(s, "telnet 10.0.0.2")
    r(s, "backup")
    r(s, "Local-1")
    assert s.active().display_prompt() == "R2>"
    r(s, "exit")
    rc = run(r2.create_cli(), "enable") + run(_en(r2), "show running-config")
    assert "aaa authentication login default group tacacs+ local" in rc and "tacacs server TAC1" in rc


def test_dot1x_port_opens_only_for_valid_credentials():
    t, r1, r2, sw, srv, s = lab()
    pc = t.add_device("pc", "PC1")
    out = run_all(sw.create_cli(), """enable
configure terminal
interface vlan 1
ip address 10.0.0.250 255.255.255.0
no shutdown
exit
aaa new-model
radius server RAD1
address ipv4 10.0.0.100 auth-port 1812 acct-port 1813
key T4c4cs
exit
aaa authentication dot1x default group radius
dot1x system-auth-control
interface fa0/5
switchport mode access
authentication port-control auto
dot1x pae authenticator
end""")
    assert "Invalid" not in out, out
    pc.set_ip("10.0.0.50", "255.255.255.0")
    pc.dot1x = {"user": "pcuser", "password": "bad"}
    t.connect(pc.nic, sw.get_port("Fa0/5"))
    t.engine.run(max_time=t.engine.now + 3000)
    assert pc.dot1x_state == "failed" and _ping(None, pc, "10.0.0.1").received == 0
    pc.dot1x = {"user": "pcuser", "password": "Pc-pass1"}
    sw.get_port("Fa0/5").set_admin(False)
    sw.get_port("Fa0/5").set_admin(True)
    t.engine.run(max_time=t.engine.now + 3000)
    assert pc.dot1x_state == "authenticated" and _ping(None, pc, "10.0.0.1").received == 4
    ss = _en(sw)
    assert "AUTHORIZED" in run(ss, "show dot1x all") and "pcuser" in run(ss, "show dot1x all")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[sw.id].get_port("Fa0/5").dot1x and t2.devices[pc.id].dot1x["user"] == "pcuser"
    assert t2.devices[srv.id].aaa_server.users["alice"] == "Wonder1"


def _en(dev):
    c = dev.create_cli()
    run(c, "enable")
    return c
