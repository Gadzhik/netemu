"""ACL and NAT through the vendor CLIs."""
from cli_helpers import run, run_all
from test_nat_acl_core import lab
from test_ospf_cli import ok
from test_vlan import ping

CISCO_PAT = """
    enable
    configure terminal
    access-list 1 permit 192.168.1.0 0.0.0.255
    interface gi0/0
    ip nat inside
    interface gi0/1
    ip nat outside
    exit
    ip nat inside source list 1 interface gi0/1 overload
    end
"""


def test_cisco_pat_and_show_translations():
    t, r1, isp, pcs, srv = lab()
    s = r1.create_cli()
    ok(run_all(s, CISCO_PAT))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    tr = run(s, "show ip nat translations")
    assert "icmp 203.0.113.1:" in tr and "192.168.1.10:" in tr and "8.8.8.8:" in tr
    st = run(s, "show ip nat statistics")
    assert "Outside interfaces:" in st and "GigabitEthernet0/1" in st and "access-list 1 interface" in st
    cfg = run(s, "show running-config")
    assert " ip nat inside" in cfg and " ip nat outside" in cfg
    assert "ip nat inside source list 1 interface GigabitEthernet0/1 overload" in cfg
    assert "access-list 1 permit 192.168.1.0 0.0.0.255" in cfg
    run(s, "clear ip nat translation *")
    assert "icmp" not in run(s, "show ip nat translations")


def test_cisco_static_nat_and_pool():
    t, r1, isp, pcs, srv = lab()
    s = r1.create_cli()
    ok(run_all(s, """
        enable
        configure terminal
        interface gi0/0
        ip nat inside
        interface gi0/1
        ip nat outside
        exit
        ip nat inside source static 192.168.1.10 203.0.113.3
        access-list 2 permit host 192.168.1.11
        ip nat pool PUB 203.0.113.4 203.0.113.5 netmask 255.255.255.248
        ip nat inside source list 2 pool PUB
        end"""))
    assert ping(t, srv, "203.0.113.3").received >= 3
    assert ping(t, pcs[1], "8.8.8.8").received == 4
    tr = run(s, "show ip nat translations")
    assert "203.0.113.4" in tr and "192.168.1.11" in tr and "203.0.113.3" in tr
    cfg = run(s, "show running-config")
    assert "ip nat pool PUB 203.0.113.4 203.0.113.5 netmask 255.255.255.248" in cfg
    assert "ip nat inside source list 2 pool PUB" in cfg and "ip nat inside source static 192.168.1.10 203.0.113.3" in cfg
    assert "%Pool NOPE not found" in run_all(s, "conf t\nip nat inside source list 2 pool NOPE")


def test_cisco_extended_and_named_acls():
    t, r1, isp, pcs, srv = lab()
    s = r1.create_cli()
    ok(run_all(s, CISCO_PAT))
    ok(run_all(s, """
        configure terminal
        access-list 100 deny icmp host 192.168.1.11 host 8.8.8.8 echo
        access-list 100 permit ip any any
        interface gi0/0
        ip access-group 100 in
        end"""))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    r = ping(t, pcs[1], "8.8.8.8")
    assert r.received == 0 and any(e[0] == "unreachable" for e in r.events)
    acl = run(s, "show access-lists 100")
    assert "Extended IP access list 100" in acl
    assert "10 deny icmp host 192.168.1.11 host 8.8.8.8 echo (4 matches)" in acl
    assert "20 permit ip any any" in acl
    # named extended ACL with explicit sequence numbers
    ok(run_all(s, """
        configure terminal
        ip access-list extended BLOCK-WEB
        10 deny tcp any any eq www
        20 permit ip any any
        exit
        interface gi0/1
        ip access-group BLOCK-WEB out
        end"""))
    named = run(s, "show ip access-lists BLOCK-WEB")
    assert "10 deny tcp any any eq www" in named and "20 permit ip any any" in named
    cfg = run(s, "show running-config")
    assert "ip access-list extended BLOCK-WEB" in cfg and " ip access-group BLOCK-WEB out" in cfg
    assert "access-list 100 deny icmp host 192.168.1.11 host 8.8.8.8 echo" in cfg
    bad = run_all(s, "configure terminal\naccess-list 100 permit tcp any any eq notaport")
    assert "Invalid input" in bad
    run_all(s, "no access-list 100\nend")
    assert "100" not in r1.acls


def huawei_lab():
    """Same lab with a Huawei AR2220 as the NAT router (GE0/0/0 inside, GE0/0/1 outside)."""
    from ipaddress import IPv4Address, IPv4Interface, IPv4Network
    from netemu.core.topology import Topology
    t = Topology()
    r1, isp = t.add_device("huawei-ar2220"), t.add_device("cisco-2911")
    pcs = []
    sw = t.add_device("huawei-s5720")
    for i in (10, 11):
        h = t.add_device("pc")
        h.set_ip(f"192.168.1.{i}", "255.255.255.0")
        h.set_gateway("192.168.1.1")
        t.connect(h.nic, next(p for p in sw.physical_ports() if p.link is None))
        pcs.append(h)
    t.connect(sw.get_port("GE0/0/24"), r1.get_port("GE0/0/0"))
    t.connect(r1.get_port("GE0/0/1"), isp.get_port("gi0/0"))
    srv = t.add_device("server")
    srv.set_ip("8.8.8.8", "255.255.255.0")
    srv.set_gateway("8.8.8.1")
    t.connect(srv.nic, isp.get_port("gi0/1"))
    for dev, port, cidr in ((r1, "GE0/0/0", "192.168.1.1/24"), (r1, "GE0/0/1", "203.0.113.1/29"),
                            (isp, "gi0/0", "203.0.113.6/29"), (isp, "gi0/1", "8.8.8.1/24")):
        p = dev.get_port(port)
        p.ip = IPv4Interface(cidr)
        p.set_admin(True)
    r1.add_static_route(IPv4Network("0.0.0.0/0"), IPv4Address("203.0.113.6"))
    return t, r1, isp, pcs, srv


def test_huawei_easy_ip_static_and_traffic_filter():
    t, r1, isp, pcs, srv = huawei_lab()
    s = r1.create_cli()
    ok(run_all(s, """
        system-view
        acl number 2000
        rule 5 permit source 192.168.1.0 0.0.0.255
        quit
        acl 3000
        rule 5 deny icmp source 192.168.1.11 0 destination 8.8.8.8 0 icmp-type echo
        quit
        interface GigabitEthernet0/0/1
        nat outbound 2000
        nat static global 203.0.113.3 inside 192.168.1.10
        quit
        interface GigabitEthernet0/0/0
        traffic-filter inbound acl 3000
        return"""))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    assert ping(t, pcs[1], "8.8.8.8").received == 0          # denied by ACL 3000
    assert ping(t, srv, "203.0.113.3").received >= 3          # static NAT from outside
    acl = run(s, "display acl 3000")
    assert "Advanced ACL 3000, 1 rule" in acl and "rule 5 deny icmp source 192.168.1.11 0 destination 8.8.8.8 0 icmp-type echo" in acl
    assert "matches" in acl
    out = run(s, "display nat outbound")
    assert "GigabitEthernet0/0/1" in out and "2000" in out and "easyip" in out
    assert "203.0.113.3" in run(s, "display nat static")
    sess = run(s, "display nat session all")
    assert "New SrcAddr     : 203.0.113.3" in sess          # static NAT wins over Easy IP
    cur = run(s, "display current-configuration")
    assert "acl number 2000\n rule 5 permit source 192.168.1.0 0.0.0.255" in cur
    assert " nat outbound 2000" in cur and " traffic-filter inbound acl 3000" in cur
    assert " nat static global 203.0.113.3 inside 192.168.1.10" in cur


def test_huawei_address_group_and_nat_server():
    t, r1, isp, pcs, srv = huawei_lab()
    s = r1.create_cli()
    ok(run_all(s, """
        system-view
        acl 2000
        rule permit source 192.168.1.0 0.0.0.255
        quit
        nat address-group 1 203.0.113.4 203.0.113.5
        interface GigabitEthernet0/0/1
        nat outbound 2000 address-group 1
        nat server protocol tcp global 203.0.113.1 8080 inside 192.168.1.10 80
        return"""))
    assert ping(t, pcs[1], "8.8.8.8").received == 4
    assert "203.0.113.4" in run(s, "display nat session all")
    assert "8080" in run(s, "display nat server") and "203.0.113.4" in run(s, "display nat address-group")
    assert "address-group 1" in run(s, "display current-configuration")


def juniper_lab():
    """PCs - EX2300 - SRX300 (addresses set through Junos) - ISP - SRV 8.8.8.8."""
    from ipaddress import IPv4Interface
    from netemu.core.topology import Topology
    t = Topology()
    srx, isp = t.add_device("juniper-srx300"), t.add_device("cisco-2911")
    sw = t.add_device("juniper-ex2300")
    pcs = []
    for i in (10, 11):
        h = t.add_device("pc")
        h.set_ip(f"192.168.1.{i}", "255.255.255.0")
        h.set_gateway("192.168.1.1")
        t.connect(h.nic, next(p for p in sw.physical_ports() if p.link is None))
        pcs.append(h)
    t.connect(sw.get_port("ge-0/0/23"), srx.get_port("ge-0/0/0"))
    t.connect(srx.get_port("ge-0/0/1"), isp.get_port("gi0/0"))
    srv = t.add_device("server")
    srv.set_ip("8.8.8.8", "255.255.255.0")
    srv.set_gateway("8.8.8.1")
    t.connect(srv.nic, isp.get_port("gi0/1"))
    for dev, port, cidr in ((isp, "gi0/0", "203.0.113.6/29"), (isp, "gi0/1", "8.8.8.1/24")):
        p = dev.get_port(port)
        p.ip = IPv4Interface(cidr)
        p.set_admin(True)
    return t, srx, isp, pcs, srv


def test_junos_source_nat_static_nat_and_filter():
    t, srx, isp, pcs, srv = juniper_lab()
    s = srx.create_cli()
    out = ok(run_all(s, """
        configure
        set interfaces ge-0/0/0 unit 0 family inet address 192.168.1.1/24
        set interfaces ge-0/0/1 unit 0 family inet address 203.0.113.1/29
        set routing-options static route 0.0.0.0/0 next-hop 203.0.113.6
        set security nat source rule-set TRUST-TO-UNTRUST from interface ge-0/0/0.0
        set security nat source rule-set TRUST-TO-UNTRUST to interface ge-0/0/1.0
        set security nat source rule-set TRUST-TO-UNTRUST rule SNAT match source-address 192.168.1.0/24
        set security nat source rule-set TRUST-TO-UNTRUST rule SNAT then source-nat interface
        set security nat static rule-set FROM-OUT from interface ge-0/0/1.0
        set security nat static rule-set FROM-OUT rule WEB match destination-address 203.0.113.3/32
        set security nat static rule-set FROM-OUT rule WEB then static-nat prefix 192.168.1.10/32
        set firewall family inet filter PROTECT term NO-PING from source-address 192.168.1.11/32
        set firewall family inet filter PROTECT term NO-PING from protocol icmp
        set firewall family inet filter PROTECT term NO-PING then discard
        set firewall family inet filter PROTECT term REST then accept
        set interfaces ge-0/0/0 unit 0 family inet filter input PROTECT
        commit and-quit"""))
    assert "commit complete" in out
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    r = ping(t, pcs[1], "8.8.8.8")
    assert r.received == 0 and not any(e[0] == "unreachable" for e in r.events)   # discard is silent
    assert ping(t, srv, "203.0.113.3").received >= 3
    sess = run(s, "show security flow session")
    assert "192.168.1.10" in sess and "Total sessions:" in sess
    assert "TRUST-TO-UNTRUST" in run(s, "show security nat source summary")
    assert "203.0.113.3" in run(s, "show security nat static rule all")
    fw = run(s, "show firewall")
    assert "Filter: PROTECT" in fw and "NO-PING" in fw
    cfg = run(s, "show configuration")
    assert "filter PROTECT {" in cfg and "then discard;" in cfg and "input PROTECT;" in cfg
    assert "rule-set TRUST-TO-UNTRUST {" in cfg and "source-nat { interface; }" in cfg
    err = run_all(s, "configure\nset interfaces ge-0/0/1 unit 0 family inet filter output NOPE\ncommit")
    assert "Referenced filter 'NOPE' is not defined" in err


def mikrotik_lab():
    from ipaddress import IPv4Interface
    from netemu.core.topology import Topology
    t = Topology()
    mt, isp = t.add_device("mikrotik-rb4011"), t.add_device("cisco-2911")
    sw = t.add_device("mikrotik-crs326")
    pcs = []
    for i in (10, 11):
        h = t.add_device("pc")
        h.set_ip(f"192.168.1.{i}", "255.255.255.0")
        h.set_gateway("192.168.1.1")
        t.connect(h.nic, next(p for p in sw.physical_ports() if p.link is None))
        pcs.append(h)
    t.connect(sw.get_port("ether24"), mt.get_port("ether2"))
    t.connect(mt.get_port("ether1"), isp.get_port("gi0/0"))
    srv = t.add_device("server")
    srv.set_ip("8.8.8.8", "255.255.255.0")
    srv.set_gateway("8.8.8.1")
    t.connect(srv.nic, isp.get_port("gi0/1"))
    for dev, port, cidr in ((isp, "gi0/0", "203.0.113.6/29"), (isp, "gi0/1", "8.8.8.1/24")):
        p = dev.get_port(port)
        p.ip = IPv4Interface(cidr)
        p.set_admin(True)
    return t, mt, isp, pcs, srv


def test_mikrotik_masquerade_dstnat_and_filter():
    t, mt, isp, pcs, srv = mikrotik_lab()
    s = mt.create_cli()
    ok(run_all(s, """
        /ip address add address=192.168.1.1/24 interface=ether2
        /ip address add address=203.0.113.1/29 interface=ether1
        /ip route add gateway=203.0.113.6
        /ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
        /ip firewall nat add chain=dstnat dst-address=203.0.113.1 protocol=tcp dst-port=8080 action=dst-nat to-addresses=192.168.1.10 to-ports=80
        /ip firewall filter add chain=forward src-address=192.168.1.11 protocol=icmp action=drop
        """))
    assert ping(t, pcs[0], "8.8.8.8").received == 4
    assert ping(t, pcs[1], "8.8.8.8").received == 0
    conn = run(s, "/ip firewall connection print")
    assert "icmp" in conn and "192.168.1.10" in conn and "203.0.113.1" in conn
    st = run(s, "/ip firewall filter print stats")
    assert "action=drop packets=4" in st
    assert "action=masquerade out-interface=ether1" in run(s, "/ip firewall nat print")
    exp = run(s, "/export")
    assert "/ip firewall nat" in exp and "add chain=srcnat out-interface=ether1 action=masquerade" in exp
    assert "input does not match any value of chain" in run(s, "/ip firewall filter add chain=bogus action=drop")
    # the router itself stays reachable (input chain untouched)
    assert ping(t, pcs[1], "192.168.1.1").received == 4
    run(s, "/ip firewall filter remove 0")
    assert ping(t, pcs[1], "8.8.8.8").received == 4
