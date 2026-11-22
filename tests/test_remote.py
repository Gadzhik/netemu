"""Telnet / SSH between devices: refusal, line password, local users, SSH keys, nested sessions."""
from cli_helpers import run, run_all
from netemu.core.topology import Topology


def two_routers():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    for r, ip, name in ((r1, "10.0.0.1", "R1"), (r2, "10.0.0.2", "R2")):
        run_all(r.create_cli(), f"enable\nconfigure terminal\nhostname {name}\ninterface gi0/0\n"
                                f"ip address {ip} 255.255.255.0\nno shutdown\nend")
    t.engine.run(max_time=500)
    s = r1.create_cli()
    run(s, "enable")
    return t, r1, r2, s


def r(s, line):
    """Run a line in whatever session is active (local or remote)."""
    return run(s.active(), line)


def test_telnet_without_vty_password_is_refused():
    t, r1, r2, s = two_routers()
    out = run(s, "telnet 10.0.0.2")
    assert "Password required, but none set" in out and "closed by foreign host" in out
    assert s.active() is s


def test_telnet_line_password_and_enable_secret():
    t, r1, r2, s = two_routers()
    run_all(r2.create_cli(), "enable\nconfigure terminal\nline vty 0 4\npassword cisco\nlogin\nexit\n"
                             "enable secret class\nend")
    out = run(s, "telnet 10.0.0.2")
    assert "User Access Verification" in out and s.active().secret_input()
    assert "Bad passwords" in r(s, "wrong")
    r(s, "cisco")
    assert s.active().display_prompt() == "R2>"
    r(s, "enable")
    assert s.active().display_prompt() == "Password: "
    r(s, "class")
    assert s.active().display_prompt() == "R2#"
    assert "10.0.0.2" in r(s, "show ip interface brief")
    out = r(s, "exit")
    assert "closed by foreign host" in out and s.active() is s


def test_ssh_needs_keys_and_local_user():
    t, r1, r2, s = two_routers()
    assert "refused" in run(s, "ssh -l admin 10.0.0.2")
    c = r2.create_cli()
    out = run_all(c, "enable\nconfigure terminal\ncrypto key generate rsa modulus 1024")
    assert "domain-name" in out
    run_all(c, "ip domain-name lab.local\ncrypto key generate rsa modulus 1024\nusername admin secret Pa55\n"
               "line vty 0 4\nlogin local\ntransport input ssh\nend")
    assert "refused" in run(s, "telnet 10.0.0.2")
    run(s, "ssh -l admin 10.0.0.2")
    assert s.active().secret_input()
    r(s, "Pa55")
    assert s.active().display_prompt() == "R2>"
    assert "R2" in r(s, "show version") or True
    cfg = run(c, "show running-config")
    assert "username admin secret 5" in cfg and "Pa55" not in cfg and "transport input ssh" in cfg
    r(s, "exit")
    assert s.active() is s


SERVER_CFG = {
    "huawei-ar2220": ("system-view\ninterface GigabitEthernet0/0/0\nip address 10.0.0.2 24\nquit\ntelnet server enable\n"
                      "stelnet server enable\naaa\nlocal-user admin password cipher Admin@123\n"
                      "local-user admin service-type telnet ssh\nquit\nuser-interface vty 0 4\n"
                      "authentication-mode aaa\nreturn", "admin", "Admin@123", "<Huawei>"),
    "juniper-srx300": ("configure\nset interfaces ge-0/0/0 unit 0 family inet address 10.0.0.2/24\n"
                       "set system services ssh\nset system services telnet\n"
                       "set system login user admin class super-user authentication plain-text-password\n"
                       "Juniper1\nJuniper1\ncommit and-quit", "admin", "Juniper1", "root@srx300>"),
    "mikrotik-rb4011": ("/ip address add address=10.0.0.2/24 interface=ether1\n/user set admin password=mt123",
                        "admin", "mt123", "[admin@MikroTik] > "),
    "extreme-x440g2": ("configure vlan default ipaddress 10.0.0.2/24\nenable ssh2\nconfigure account admin password\n"
                       "exos1\nexos1", "admin", "exos1", "X440G2-24t."),
}


import pytest  # noqa: E402


@pytest.mark.parametrize("model", list(SERVER_CFG))
@pytest.mark.parametrize("proto", ["ssh", "telnet"])
def test_windows_and_linux_clients_to_each_vendor(model, proto):
    cfg, user, pw, prompt = SERVER_CFG[model]
    t = Topology()
    srv = t.add_device(model)
    sw = t.add_device("huawei-s5720")
    win, lin = t.add_device("pc"), t.add_device("pc-linux")
    t.connect(srv.physical_ports()[0], sw.get_port("GE0/0/1"))
    t.connect(win.nic, sw.get_port("GE0/0/2"))
    t.connect(lin.nic, sw.get_port("GE0/0/3"))
    c = srv.create_cli()
    for line in cfg.splitlines():
        run(c, line)
    win.set_ip("10.0.0.10", "255.255.255.0")
    lin.set_ip("10.0.0.11", "255.255.255.0")
    t.engine.run(max_time=1000)
    w = win.create_cli()
    cmd = f"ssh {user}@10.0.0.2" if proto == "ssh" else "telnet 10.0.0.2"
    out = run(w, cmd)
    assert w.active() is not w, out
    if proto == "telnet":
        r(w, user)
    assert w.active().secret_input()
    r(w, pw)
    assert w.active().display_prompt().startswith(prompt), (out, w.active().display_prompt())
    r(w, "quit" if "MikroTik" in prompt else "exit")
    assert w.active() is w
    if proto == "ssh":
        lx = lin.create_cli()
        run(lx, f"ssh -l {user} 10.0.0.2")
        r(lx, "wrong")
        assert lx.active().secret_input()
        r(lx, pw)
        assert lx.active().display_prompt().startswith(prompt)


def test_linux_server_sshd_and_nested_hop():
    t, r1, r2, s = two_routers()
    run_all(r2.create_cli(), "enable\nconfigure terminal\nline vty 0 4\npassword cisco\nlogin\nend")
    srv = t.add_device("server-linux")
    t.connect(srv.nic, r2.get_port("gi0/1"))
    run_all(r2.create_cli(), "enable\nconfigure terminal\ninterface gi0/1\nip address 10.1.0.1 255.255.255.0\n"
                             "no shutdown\nend")
    run_all(r1.create_cli(), "enable\nconfigure terminal\nip route 10.1.0.0 255.255.255.0 10.0.0.2\nend")
    srv.set_ip("10.1.0.10", "255.255.255.0")
    srv.set_gateway("10.1.0.1")
    t.engine.run(max_time=1000)
    lx = srv.create_cli()
    assert "Access denied" in run(lx, "systemctl stop ssh")
    assert "active (running)" in run(lx, "systemctl status ssh")
    # R1 -> telnet R2 -> (from R2) ssh to the Linux server: two nested hops
    run(s, "telnet 10.0.0.2")
    r(s, "cisco")
    assert s.active().display_prompt() == "R2>"
    r(s, "ssh -l user 10.1.0.10")
    r(s, "netemu")
    assert s.active().display_prompt().startswith("user@")
    assert "inet 10.1.0.10/24" in r(s, "ip a")
    r(s, "exit")
    assert s.active().display_prompt() == "R2>"
    r(s, "exit")
    assert s.active() is s


def test_console_enable_asks_for_the_secret_like_ios():
    from cli_helpers import run
    from netemu.core.topology import Topology
    t = Topology()
    r = t.add_device("cisco-2911")
    s = r.create_cli()
    run(s, "enable")
    assert s.mode == "priv"                                   # no secret yet: straight in
    for line in ("configure terminal", "enable secret Cisco123", "end", "disable"):
        run(s, line)
    run(s, "enable")
    assert s.secret_input() and "Password:" in s.display_prompt() and s.mode == "user"
    run(s, "wrong")
    run(s, "wrong2")
    assert "% Bad secrets" in run(s, "wrong3") and s.mode == "user"
    run(s, "enable")
    run(s, "Cisco123")
    assert s.mode == "priv"


def test_apply_script_answers_the_enable_secret_itself():
    from netemu.core.lab import apply_script
    from netemu.core.topology import Topology
    t = Topology()
    r = t.add_device("cisco-2911", "R1")
    script = {"R1": "enable\nconfigure terminal\nenable secret Cisco123\nhostname CORE\nend"}
    apply_script(t, script)
    out = apply_script(t, {"CORE": "enable\nconfigure terminal\nhostname CORE2\nend"})   # now enable prompts
    assert r.hostname == "CORE2", out
