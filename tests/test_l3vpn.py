"""MPLS L3VPN (RFC 4364) on Cisco: VRFs with RD/RT, MP-BGP VPNv4 between PEs, two-label forwarding.

Two customers (A and B) use the *same* addresses at both sites; each must reach only its own other site."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def pe_script(i):
    other = 4 if i == 1 else 1
    site = 1 if i == 1 else 2
    up = "gi0/1" if i == 1 else "gi0/2"
    bport = "gi0/2" if i == 1 else "gi0/1"
    core = "10.0.12.1 255.255.255.252" if i == 1 else "10.0.34.2 255.255.255.252"
    return f"""enable
configure terminal
hostname PE{site}
ip vrf A
rd 65000:1
route-target both 65000:1
ip vrf B
rd 65000:2
route-target export 65000:2
route-target import 65000:2
exit
interface loopback0
ip address {i}.{i}.{i}.{i} 255.255.255.255
interface {up}
ip address {core}
mpls ip
no shutdown
interface gi0/0
ip vrf forwarding A
ip address 192.168.{site}.1 255.255.255.0
no shutdown
interface {bport}
ip vrf forwarding B
ip address 192.168.{site}.1 255.255.255.0
no shutdown
exit
mpls ldp router-id loopback0 force
router ospf 1
network {i}.{i}.{i}.{i} 0.0.0.0 area 0
network 10.0.0.0 0.255.255.255 area 0
router bgp 65000
neighbor {other}.{other}.{other}.{other} remote-as 65000
neighbor {other}.{other}.{other}.{other} update-source loopback0
address-family vpnv4
neighbor {other}.{other}.{other}.{other} activate
neighbor {other}.{other}.{other}.{other} send-community extended
exit-address-family
address-family ipv4 vrf A
redistribute connected
exit-address-family
address-family ipv4 vrf B
redistribute connected
exit-address-family
end"""


def p_script(i):
    return f"""enable
configure terminal
interface loopback0
ip address {i}.{i}.{i}.{i} 255.255.255.255
interface gi0/2
ip address 10.0.{i - 1}{i}.2 255.255.255.252
mpls ip
no shutdown
interface gi0/1
ip address 10.0.{i}{i + 1}.1 255.255.255.252
mpls ip
no shutdown
exit
mpls ldp router-id loopback0 force
router ospf 1
network 0.0.0.0 255.255.255.255 area 0
end"""


def build():
    t = Topology()
    pe1, p2, p3, pe4 = (t.add_device("cisco-2911", n) for n in ("PE1", "P2", "P3", "PE4"))
    t.connect(pe1.get_port("gi0/1"), p2.get_port("gi0/2"))
    t.connect(p2.get_port("gi0/1"), p3.get_port("gi0/2"))
    t.connect(p3.get_port("gi0/1"), pe4.get_port("gi0/2"))
    hosts = {}
    for pe, site in ((pe1, 1), (pe4, 2)):
        for cust, port in (("A", "gi0/0"), ("B", "gi0/2" if site == 1 else "gi0/1")):
            h = t.add_device("pc", f"{cust}{site}")
            t.connect(h.nic, pe.get_port(port))
            h.set_ip(f"192.168.{site}.10", "255.255.255.0")
            h.set_gateway(f"192.168.{site}.1")
            hosts[cust + str(site)] = h
    for r, script in ((pe1, pe_script(1)), (p2, p_script(2)), (p3, p_script(3)), (pe4, pe_script(4))):
        out = run_all(r.create_cli(), script)
        assert "Invalid" not in out and "% " not in out, out
    t.engine.run(max_time=90000)
    return t, (pe1, p2, p3, pe4), hosts


def test_l3vpn_two_customers_with_overlapping_addresses():
    t, (pe1, p2, p3, pe4), h = build()
    s = pe1.create_cli()
    run(s, "enable")
    vpn = run(s, "show bgp vpnv4 unicast all")
    assert "Route Distinguisher: 65000:1 (default for vrf A)" in vpn and "Route Distinguisher: 65000:2" in vpn
    assert "*>i" in vpn and "4.4.4.4" in vpn, vpn
    rt = run(s, "show ip route vrf A")
    assert "B        192.168.2.0/24 [200/0] via 4.4.4.4" in rt or ("192.168.2.0/24" in rt and "4.4.4.4" in rt), rt
    assert "192.168.2.0" not in run(s, "show ip route")                # not leaked into the global table
    before = p2.mpls.switched + p3.mpls.switched
    assert _ping(None, h["A1"], "192.168.2.10").received == 4          # A site 1 -> A site 2
    assert p2.mpls.switched + p3.mpls.switched > before                 # label-switched through the core
    assert _ping(None, h["B1"], "192.168.2.10").received == 4          # B uses the same addresses
    # each customer reached its own host: the replies came from the right VRF
    assert h["A2"].arp.lookup(__import__("ipaddress").IPv4Address("192.168.2.1"), t.engine.now) is not None
    cfg = run(s, "show running-config")
    for line in ("route-target both 65000:1", "route-target both 65000:2", " address-family vpnv4",
                 "  neighbor 4.4.4.4 activate", " address-family ipv4 vrf A", "  redistribute connected"):
        assert line in cfg, line
    # B stops importing: B site 1 loses site 2, A keeps working
    run_all(s, "configure terminal\nip vrf B\nno route-target import 65000:2\nend")
    t.engine.run(max_time=t.engine.now + 5000)
    assert "192.168.2.0" not in run(s, "show ip route vrf B")
    assert _ping(None, h["B1"], "192.168.2.10").received == 0
    assert _ping(None, h["A1"], "192.168.2.10").received == 4
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=90000)
    a1 = next(d for d in t2.devices.values() if d.name == "A1")
    assert _ping(None, a1, "192.168.2.10").received == 4


def test_vpn_packet_has_two_labels():
    from netemu.core.routing.mpls import MplsPacket
    t, (pe1, p2, p3, pe4), h = build()
    seen = []
    t.engine.subscribe("frame_delivered", lambda ev: seen.append(ev.info["frame"].payload))
    _ping(None, h["A1"], "192.168.2.10")
    stacks = {len(p.labels) for p in seen if isinstance(p, MplsPacket)}
    assert stacks == {1, 2}            # outer LDP + VPN label in the core, VPN label alone after PHP
