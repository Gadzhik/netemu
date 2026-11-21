"""TFTP/FTP config backup and restore, e-mail between PCs, DHCP option 82 from snooping switches."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.services.files import fetch_mail, send_mail
from netemu.core.topology import Topology


def lan():
    t = Topology()
    r = t.add_device("cisco-2911", "R1")
    sw = t.add_device("cisco-2960", "SW1")
    srv = t.add_device("server", "SRV")
    a, b = t.add_device("pc", "PCA"), t.add_device("pc", "PCB")
    t.connect(r.get_port("gi0/0"), sw.get_port("Fa0/24"))
    t.connect(srv.nic, sw.get_port("Fa0/10"))
    t.connect(a.nic, sw.get_port("Fa0/1"))
    t.connect(b.nic, sw.get_port("Fa0/2"))
    run_all(r.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 10.0.0.1 255.255.255.0\nno shutdown\nend")
    for dev, ip in ((srv, "10.0.0.100"), (a, "10.0.0.11"), (b, "10.0.0.12")):
        dev.set_ip(ip, "255.255.255.0")
    t.engine.run(max_time=500)
    s = r.create_cli()
    run(s, "enable")
    return t, r, sw, srv, a, b, s


def test_tftp_backup_and_restore_interactive():
    t, r, sw, srv, a, b, s = lan()
    run_all(s, "configure terminal\nhostname EDGE\ninterface loopback 7\nip address 7.7.7.7 255.255.255.255\nend")
    out = run(s, "copy running-config tftp:")
    assert "Address or name of remote host" in s.display_prompt()
    run(s, "10.0.0.100")
    assert "Destination filename [edge-confg]" in s.display_prompt()
    out = run(s, "")
    assert "bytes copied" in out, out
    assert "interface Loopback7" in srv.file_server.files["edge-confg"]
    run_all(s, "configure terminal\nno interface loopback 7\nhostname OTHER\nend")
    out = run(s, "copy tftp://10.0.0.100/edge-confg running-config")
    assert "[OK -" in out and r.hostname == "EDGE" and r.get_port("Loopback7") is not None
    assert "No such file" in run(s, "copy tftp://10.0.0.100/missing running-config") or \
        "File not found" in run(s, "copy tftp://10.0.0.100/missing running-config")


def test_ftp_needs_credentials():
    t, r, sw, srv, a, b, s = lan()
    srv.file_server.ftp_users = {"backup": "S3cret"}
    assert "Login incorrect" in run(s, "copy running-config ftp://backup:wrong@10.0.0.100/r1.cfg")
    assert "bytes copied" in run(s, "copy running-config ftp://backup:S3cret@10.0.0.100/r1.cfg")
    assert "r1.cfg" in srv.file_server.files


def test_mail_between_pcs():
    t, r, sw, srv, a, b, s = lan()
    fs = srv.file_server
    fs.mail = True
    fs.mail_users = {"alice": "pa", "bob": "pb"}
    a.mail = {"address": "alice@netemu.lab", "server": "10.0.0.100", "user": "alice", "password": "pa"}
    b.mail = {"address": "bob@netemu.lab", "server": "10.0.0.100", "user": "bob", "password": "pb"}
    res = []
    send_mail(a, "bob@netemu.lab", "Hi", "Lab at 10:00", lambda ok, d: res.append(ok))
    send_mail(a, "nobody@netemu.lab", "x", "y", lambda ok, d: res.append(d))
    t.engine.run(max_time=t.engine.now + 1000)
    assert res[0] is True and "No such user" in res[1]
    fetch_mail(b, lambda ok, d: res.append((ok, len(d or []))))
    t.engine.run(max_time=t.engine.now + 1000)
    assert res[-1] == (True, 1) and b.inbox[0]["subject"] == "Hi" and b.inbox[0]["from"] == "alice@netemu.lab"
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[b.id].inbox[0]["body"] == "Lab at 10:00"


def test_option82_from_snooping_needs_trust_on_ios_dhcp_server():
    t, r, sw, srv, a, b, s = lan()
    run_all(s, "configure terminal\nip dhcp excluded-address 10.0.0.1 10.0.0.150\nip dhcp pool LAN\n"
               "network 10.0.0.0 255.255.255.0\ndefault-router 10.0.0.1\nend")
    run_all(sw.create_cli(), "enable\nconfigure terminal\nip dhcp snooping\nip dhcp snooping vlan 1\n"
                             "interface fa0/24\nip dhcp snooping trust\nend")
    a.start_dhcp()
    t.engine.run(max_time=t.engine.now + 8000)
    assert not a.dhcp_info.get("server")                        # dropped: option 82 with giaddr 0.0.0.0
    assert r.dhcp_server.dropped82 > 0
    run_all(s, "configure terminal\nip dhcp relay information trust-all\nend")
    a.start_dhcp()
    t.engine.run(max_time=t.engine.now + 8000)
    assert a.dhcp_info.get("server") and str(a.nic.ip.ip).startswith("10.0.0.15")
    assert any("Fa0/1" in v[0] for v in r.dhcp_server.option82.values())
