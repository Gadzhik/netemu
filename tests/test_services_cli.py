"""PC command prompt with DHCP/DNS/HTTP, and router DHCP servers in every vendor CLI."""
from cli_helpers import run, run_all
from test_ospf_cli import ok
from test_services_core import dns_web, segment
from netemu.core.services.dhcp import DhcpPool


def test_pc_ipconfig_renew_release_and_all():
    t, sw, srv, pcs = segment()
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools.append(DhcpPool("serverPool", "192.168.1.0/24", "192.168.1.1", "192.168.1.2",
                                              start="192.168.1.100"))
    s = pcs[0].create_cli()
    out = run(s, "ipconfig /renew")
    assert "IP Address......................: 192.168.1.100" in out and "Default Gateway.................: 192.168.1.1" in out
    allout = run(s, "ipconfig /all")
    assert "DHCP Enabled....................: Yes" in allout and "DHCP Server.....................: 192.168.1.2" in allout
    out = run(s, "ipconfig /release")
    assert "0.0.0.0" in out
    t.engine.run(max_time=500)
    assert pcs[0].nic.ip is None
    # static configuration switches DHCP off
    run(s, "ipconfig 192.168.1.50 255.255.255.0 192.168.1.1")
    assert not pcs[0].dhcp_enabled
    srv.dhcp_server.cfg.enabled = False
    out = run(pcs[1].create_cli(), "ipconfig /renew")
    assert "DHCP request failed." in out and "169.254." in out


def test_pc_nslookup_ping_by_name_and_curl():
    t, srv, pcs = dns_web()
    s = pcs[0].create_cli()
    out = run(s, "nslookup www.lab.local")
    assert "Name:\twww.lab.local" in out and "Address:  192.168.1.2" in out
    assert "Non-existent domain" in run(s, "nslookup nope.lab.local")
    out = run(s, "ping -n 2 www.lab.local")
    assert "Pinging www.lab.local [192.168.1.2] with 32 bytes of data" in out and "Received = 2" in out
    assert "could not find host nope.lab.local" in run(s, "ping nope.lab.local")
    assert "<h1>Hello lab</h1>" in run(s, "curl http://www.lab.local")
    assert "404" in run(s, "curl http://www.lab.local/x.html")
    srv.http_server.enabled = False
    assert "curl: (28) Failed to connect to 192.168.1.2 port 80" in run(s, "curl http://192.168.1.2/")


def router_lan(model, lan_port, relay_port, srv_port):
    """PC1 on lan_port (router DHCP), PC2 on relay_port (relayed to a server PC on srv_port)."""
    from netemu.core.topology import Topology
    t = Topology()
    r = t.add_device(model)
    srv = t.add_device("server")
    pc1, pc2 = t.add_device("pc"), t.add_device("pc")
    t.connect(pc1.nic, r.get_port(lan_port))
    t.connect(pc2.nic, r.get_port(relay_port))
    t.connect(srv.nic, r.get_port(srv_port))
    srv.set_ip("10.0.0.5", "255.255.255.0")
    srv.set_gateway("10.0.0.1")
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools.append(DhcpPool("LAN20", "192.168.20.0/24", "192.168.20.1", "10.0.0.5",
                                              start="192.168.20.50"))
    return t, r, pc1, pc2


def test_cisco_dhcp_pool_and_helper():
    t, r, pc1, pc2 = router_lan("cisco-2911", "gi0/0", "gi0/1", "gi0/2")
    s = r.create_cli()
    ok(run_all(s, """
        enable
        configure terminal
        interface gi0/0
        ip address 192.168.10.1 255.255.255.0
        no shutdown
        interface gi0/1
        ip address 192.168.20.1 255.255.255.0
        ip helper-address 10.0.0.5
        no shutdown
        interface gi0/2
        ip address 10.0.0.1 255.255.255.0
        no shutdown
        exit
        ip dhcp excluded-address 192.168.10.1 192.168.10.9
        ip dhcp pool LAN10
        network 192.168.10.0 255.255.255.0
        default-router 192.168.10.1
        dns-server 10.0.0.5
        domain-name lab.local
        end"""))
    assert "192.168.10.10" in run(pc1.create_cli(), "ipconfig /renew")
    assert "192.168.20.50" in run(pc2.create_cli(), "ipconfig /renew")
    b = run(s, "show ip dhcp binding")
    assert "192.168.10.10" in b and pc1.nic.mac.cisco().upper() in b and "Automatic" in b
    assert "Pool LAN10 :" in run(s, "show ip dhcp pool") and "Leased addresses               : 1" in run(s, "show ip dhcp pool")
    cfg = run(s, "show running-config")
    assert "ip dhcp pool LAN10" in cfg and " default-router 192.168.10.1" in cfg
    assert "ip dhcp excluded-address 192.168.10.1 192.168.10.9" in cfg and " ip helper-address 10.0.0.5" in cfg
    run(s, "clear ip dhcp binding *")
    assert "192.168.10.10" not in run(s, "show ip dhcp binding")


def test_huawei_ip_pool_select_global_and_relay():
    t, r, pc1, pc2 = router_lan("huawei-ar2220", "GE0/0/0", "GE0/0/1", "GE0/0/2")
    s = r.create_cli()
    ok(run_all(s, """
        system-view
        interface GigabitEthernet0/0/0
        ip address 192.168.10.1 24
        quit
        interface GigabitEthernet0/0/1
        ip address 192.168.20.1 24
        quit
        interface GigabitEthernet0/0/2
        ip address 10.0.0.1 24
        quit
        ip pool LAN10
        gateway-list 192.168.10.1
        network 192.168.10.0 mask 255.255.255.0
        dns-list 10.0.0.5
        excluded-ip-address 192.168.10.2 192.168.10.9
        quit
        return"""))
    assert "169.254." in run(pc1.create_cli(), "ipconfig /renew")       # dhcp enable / select global missing
    assert "Please enable DHCP" in run_all(s, "system-view\ninterface GigabitEthernet0/0/0\ndhcp select global")
    ok(run_all(s, """
        quit
        dhcp enable
        interface GigabitEthernet0/0/0
        dhcp select global
        quit
        interface GigabitEthernet0/0/1
        dhcp select relay
        dhcp relay server-ip 10.0.0.5
        return"""))
    assert "192.168.10.10" in run(pc1.create_cli(), "ipconfig /renew")
    assert "192.168.20.50" in run(pc2.create_cli(), "ipconfig /renew")
    used = run(s, "display ip pool name LAN10 used")
    assert "Pool-name      : LAN10" in used and "192.168.10.10" in used
    cur = run(s, "display current-configuration")
    assert "dhcp enable" in cur and "ip pool LAN10" in cur and " dhcp select global" in cur
    assert " dhcp relay server-ip 10.0.0.5" in cur and " excluded-ip-address 192.168.10.2 192.168.10.9" in cur


def test_junos_dhcp_local_server_and_relay():
    t, r, pc1, pc2 = router_lan("juniper-srx300", "ge-0/0/0", "ge-0/0/1", "ge-0/0/2")
    s = r.create_cli()
    out = ok(run_all(s, """
        configure
        set interfaces ge-0/0/0 unit 0 family inet address 192.168.10.1/24
        set interfaces ge-0/0/1 unit 0 family inet address 192.168.20.1/24
        set interfaces ge-0/0/2 unit 0 family inet address 10.0.0.1/24
        set access address-assignment pool LAN10 family inet network 192.168.10.0/24
        set access address-assignment pool LAN10 family inet range R1 low 192.168.10.100 high 192.168.10.199
        set access address-assignment pool LAN10 family inet dhcp-attributes router 192.168.10.1
        set access address-assignment pool LAN10 family inet dhcp-attributes name-server 10.0.0.5
        set system services dhcp-local-server group LAN interface ge-0/0/0.0
        set forwarding-options dhcp-relay server-group SRV 10.0.0.5
        set forwarding-options dhcp-relay active-server-group SRV
        set forwarding-options dhcp-relay group RELAY interface ge-0/0/1.0
        commit and-quit"""))
    assert "commit complete" in out
    assert "192.168.10.100" in run(pc1.create_cli(), "ipconfig /renew")
    assert "192.168.20.50" in run(pc2.create_cli(), "ipconfig /renew")
    b = run(s, "show dhcp server binding")
    assert "192.168.10.100" in b and "BOUND" in b and "ge-0/0/0.0" in b
    cfg = run(s, "show configuration")
    assert "pool LAN10 {" in cfg and "dhcp-local-server {" in cfg and "active-server-group SRV;" in cfg


def test_mikrotik_dhcp_server_and_relay():
    t, r, pc1, pc2 = router_lan("mikrotik-rb4011", "ether1", "ether2", "ether3")
    s = r.create_cli()
    ok(run_all(s, """
        /ip address add address=192.168.10.1/24 interface=ether1
        /ip address add address=192.168.20.1/24 interface=ether2
        /ip address add address=10.0.0.1/24 interface=ether3
        /ip pool add name=dhcp_pool ranges=192.168.10.100-192.168.10.200
        /ip dhcp-server add name=dhcp1 interface=ether1 address-pool=dhcp_pool
        /ip dhcp-server network add address=192.168.10.0/24 gateway=192.168.10.1 dns-server=10.0.0.5
        /ip dhcp-relay add name=relay1 interface=ether2 dhcp-server=10.0.0.5 local-address=192.168.20.1
        """))
    assert "192.168.10.100" in run(pc1.create_cli(), "ipconfig /renew")
    assert "192.168.20.50" in run(pc2.create_cli(), "ipconfig /renew")
    lease = run(s, "/ip dhcp-server lease print")
    assert "192.168.10.100" in lease and "bound" in lease
    exp = run(s, "/export")
    assert "/ip pool" in exp and "/ip dhcp-server network" in exp and "/ip dhcp-relay" in exp
