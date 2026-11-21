"""Syslog, SNMP and NTP on every vendor: a Linux server collects logs, serves time and polls SNMP."""
import pytest

from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

SRV = "192.168.1.100"

# model: (port to the server, config, enable cmd, status command, expected sync text)
CFG = {
    "cisco-2911": ("gi0/0", "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                   f"no shutdown\nexit\nlogging host {SRV}\nsnmp-server community public RO\n"
                   "snmp-server community private RW\nsnmp-server location Lab-1\n"
                   f"ntp server {SRV}\nend", "enable", "show ntp status", "Clock is synchronized, stratum 2"),
    "huawei-ar2220": ("GE0/0/0", "system-view\ninterface GigabitEthernet0/0/0\nip address 192.168.1.1 24\nquit\n"
                      f"info-center loghost {SRV}\nsnmp-agent\nsnmp-agent community read public\n"
                      "snmp-agent community write private\nsnmp-agent sys-info location Lab-1\n"
                      f"ntp-service unicast-server {SRV}\nreturn", None, "display ntp-service status", "synchronized"),
    "mikrotik-rb4011": ("ether1", "/ip address add address=192.168.1.1/24 interface=ether1\n"
                        f"/system logging action add name=remote1 target=remote remote={SRV}\n"
                        "/snmp set enabled=yes location=Lab-1\n/snmp community add name=private write-access=yes\n"
                        f"/system ntp client set enabled=yes servers={SRV}", None, "/system ntp client print",
                        "status: synchronized"),
    "juniper-srx300": ("ge-0/0/0", "configure\nset interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24\n"
                       f"set system syslog host {SRV} any any\nset snmp community public authorization read-only\n"
                       "set snmp community private authorization read-write\nset snmp location Lab-1\n"
                       f"set system ntp server {SRV}\ncommit and-quit", None, "show ntp status", "synchronized"),
    "extreme-x440g2": ("1", "configure vlan Default ipaddress 192.168.1.1 255.255.255.0\n"
                     f"configure syslog add {SRV} local0\nenable syslog\nconfigure snmp add community readonly public\n"
                     "configure snmp add community readwrite private\nconfigure snmp sysLocation Lab-1\n"
                     f"configure ntp server add {SRV}\nenable ntp", None, "show ntp", "synchronized"),
}


@pytest.mark.parametrize("model", list(CFG))
def test_syslog_snmp_ntp(model):
    port, cfg, en, status, want = CFG[model]
    t = Topology()
    dev = t.add_device(model)
    srv = t.add_device("server-linux")
    t.connect(srv.nic, dev.get_port(port))
    srv.set_ip(SRV, "255.255.255.0")
    out = run_all(dev.create_cli(), cfg)
    assert not any(e in out for e in ("Invalid", "Error", "error:", "failure", "syntax error")), out
    t.engine.run(max_time=3000)
    s = dev.create_cli()
    if en:
        run(s, en)
    assert want in run(s, status)

    # link flap -> the "link up" message reaches the syslog server once the link is back
    before = len(srv.syslog_server.messages)
    srv.nic.set_admin(False)
    t.engine.run(max_time=500)
    srv.nic.set_admin(True)
    t.engine.run(max_time=3000)
    sh = srv.create_cli()
    log = run(sh, "cat /var/log/syslog")
    name = dev.get_port(port).name
    assert len(srv.syslog_server.messages) > before and "192.168.1.1" in log and name in log, log

    # SNMP: get, walk, set (rw community), wrong community times out
    agent = "192.168.1.1"
    assert '"Lab-1"' in run(sh, f"snmpget -v2c -c public {agent} sysLocation.0")
    walk = run(sh, f"snmpwalk -v2c -c public {agent} system")
    assert "SNMPv2-MIB::sysName.0" in walk and "sysUpTimeInstance" in walk
    assert "IF-MIB::ifDescr.1" in run(sh, f"snmpwalk -v2c -c public {agent} ifDescr")
    run(sh, f"snmpset -v2c -c private {agent} sysName.0 s CORE-1")
    assert dev.hostname == "CORE-1"
    assert "noAccess" in run(sh, f"snmpset -v2c -c public {agent} sysName.0 s X")
    assert "Timeout" in run(sh, f"snmpget -v2c -c wrong {agent} sysName.0")

    t2 = topology_from_dict(topology_to_dict(t), Topology())
    m2 = t2.devices[dev.id].mgmt
    assert m2.syslog_hosts == [SRV] and m2.ntp_servers == [SRV] and m2.communities["private"] == "rw"


def test_cisco_ntp_master_and_show_logging():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911"), t.add_device("cisco-2911")
    t.connect(r1.get_port("gi0/0"), r2.get_port("gi0/0"))
    run_all(r1.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 10.0.0.1 255.255.255.252\n"
                             "no shutdown\nexit\nntp master 3\nend")
    s2 = r2.create_cli()
    run_all(s2, "enable\nconfigure terminal\ninterface gi0/0\nip address 10.0.0.2 255.255.255.252\nno shutdown\n"
                "exit\nntp server 10.0.0.1\nend")
    t.engine.run(max_time=3000)
    assert "stratum 4, reference is 10.0.0.1" in run(s2, "show ntp status")
    assert run(s2, "show clock")[0] != "*"
    assert "*~10.0.0.1" in run(s2, "show ntp associations")
    log = run(s2, "show logging")
    assert "%LINK-3-UPDOWN: Interface GigabitEthernet0/0, changed state to up" in log
    assert "%SYS-5-CONFIG_I" in log and "%NTP-5-PEERSYNC" in log
