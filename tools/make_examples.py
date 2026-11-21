"""Build the example projects in examples/ through the vendor CLIs (run: python tools/make_examples.py).

Each lab is configured exactly as a student would do it, then verified with pings
before it is saved, so the examples are known to work."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from netemu.core import persistence  # noqa: E402
from netemu.core.addresses import IPv4Address  # noqa: E402
from netemu.core.stack.icmp import PingListener, PingSession  # noqa: E402
from netemu.core.topology import Topology  # noqa: E402


def cli(dev, script: str) -> str:
    s = dev.create_cli()
    out = []
    for line in script.strip().splitlines():
        job = s.execute(line.strip(), out.append)
        if job is not None:
            dev.engine.run_until(lambda: job.done)
    text = "".join(out)
    for bad in ("Invalid", "Error", "error:", "bad command", "syntax error"):
        if bad in text:
            raise SystemExit(f"{dev.name}: {bad}\n{text}")
    return text


def ping_ok(src, dst: str) -> bool:
    class L(PingListener):
        ok = 0

        def on_reply(self, *a):
            L.ok += 1
    for _ in range(2):                       # the first round fills ARP caches
        L.ok = 0
        s = PingSession(src, IPv4Address(dst), L(), count=3).start()
        src.engine.run_until(lambda: s.done)
    return L.ok == 3


def vlan_lab() -> Topology:
    """PC-A1/PC-B1 (VLAN 10/20) on SW1 --trunk-- SW2 with PC-A2/PC-B2; R1 routes between VLANs."""
    t = Topology()
    r1 = t.add_device("cisco-2911", x=0, y=-180)
    sw1 = t.add_device("cisco-2960", x=-200, y=0)
    sw2 = t.add_device("cisco-2960", x=200, y=0)
    pcs = [t.add_device("pc", x=x, y=170) for x in (-320, -110, 110, 320)]
    t.connect(r1.get_port("gi0/0"), sw1.get_port("gi0/1"))
    t.connect(sw1.get_port("gi0/2"), sw2.get_port("gi0/2"))
    t.connect(pcs[0].nic, sw1.get_port("fa0/1"))
    t.connect(pcs[1].nic, sw1.get_port("fa0/2"))
    t.connect(pcs[2].nic, sw2.get_port("fa0/1"))
    t.connect(pcs[3].nic, sw2.get_port("fa0/2"))
    for sw, name, trunks in ((sw1, "SW1", ("gi0/1", "gi0/2")), (sw2, "SW2", ("gi0/2",))):
        cli(sw, f"""
            enable
            configure terminal
            hostname {name}
            vlan 10
            name USERS
            vlan 20
            name SERVERS
            exit
            interface fa0/1
            switchport mode access
            switchport access vlan 10
            interface fa0/2
            switchport mode access
            switchport access vlan 20
            """ + "".join(f"""
            interface {tr}
            switchport mode trunk
            switchport trunk allowed vlan 10,20
            """ for tr in trunks) + """
            end
            write memory""")
    cli(r1, """
        enable
        configure terminal
        hostname R1
        interface gi0/0
        no shutdown
        interface gi0/0.10
        description USERS
        encapsulation dot1Q 10
        ip address 192.168.10.1 255.255.255.0
        interface gi0/0.20
        description SERVERS
        encapsulation dot1Q 20
        ip address 192.168.20.1 255.255.255.0
        end
        write memory""")
    for pc, ip, gw in ((pcs[0], "192.168.10.11", "192.168.10.1"), (pcs[1], "192.168.20.11", "192.168.20.1"),
                       (pcs[2], "192.168.10.12", "192.168.10.1"), (pcs[3], "192.168.20.12", "192.168.20.1")):
        cli(pc, f"ipconfig {ip} 255.255.255.0 {gw}")
    t.engine.run(max_time=31_000)               # PVST: the SW1-SW2 trunk listens + learns for 30 s
    assert ping_ok(pcs[0], "192.168.10.12"), "VLAN 10 across the trunk"
    assert ping_ok(pcs[0], "192.168.20.12"), "inter-VLAN routing"
    return t


def ospf_lab() -> Topology:
    """Ring R1 (Cisco) - MT1 (MikroTik) - SRX1 (Juniper) - AR1 (Huawei) - R1, area 0, loopbacks, LANs."""
    t = Topology()
    r1 = t.add_device("cisco-2911", x=-220, y=-120)
    mt = t.add_device("mikrotik-rb4011", x=220, y=-120)
    srx = t.add_device("juniper-srx300", x=220, y=120)
    ar = t.add_device("huawei-ar2220", x=-220, y=120)
    pcs = [t.add_device("pc", x=x, y=y) for x, y in ((-420, -120), (420, -120), (420, 120), (-420, 120))]
    t.connect(r1.get_port("gi0/0"), mt.get_port("ether1"))      # 10.0.12.0/30
    t.connect(mt.get_port("ether2"), srx.get_port("ge-0/0/0"))  # 10.0.23.0/30
    t.connect(srx.get_port("ge-0/0/1"), ar.get_port("GE0/0/0"))  # 10.0.34.0/30
    t.connect(ar.get_port("GE0/0/1"), r1.get_port("gi0/1"))     # 10.0.41.0/30
    t.connect(pcs[0].nic, r1.get_port("gi0/2"))
    t.connect(pcs[1].nic, mt.get_port("ether3"))
    t.connect(pcs[2].nic, srx.get_port("ge-0/0/2"))
    t.connect(pcs[3].nic, ar.get_port("GE0/0/2"))
    cli(r1, """
        enable
        configure terminal
        hostname R1
        interface loopback0
        ip address 1.1.1.1 255.255.255.255
        interface gi0/0
        ip address 10.0.12.1 255.255.255.252
        no shutdown
        interface gi0/1
        ip address 10.0.41.2 255.255.255.252
        no shutdown
        interface gi0/2
        ip address 192.168.1.1 255.255.255.0
        no shutdown
        router ospf 1
        router-id 1.1.1.1
        network 10.0.0.0 0.0.255.255 area 0
        network 192.168.1.0 0.0.0.255 area 0
        network 1.1.1.1 0.0.0.0 area 0
        passive-interface gi0/2
        end
        write memory""")
    cli(mt, """
        /system identity set name=MT1
        /interface bridge add name=loopback
        /ip address add address=2.2.2.2/32 interface=loopback
        /ip address add address=10.0.12.2/30 interface=ether1
        /ip address add address=10.0.23.1/30 interface=ether2
        /ip address add address=192.168.2.1/24 interface=ether3
        /routing ospf instance add name=default-v2 router-id=2.2.2.2
        /routing ospf area add name=backbone-v2 area-id=0.0.0.0 instance=default-v2
        /routing ospf interface-template add networks=10.0.12.0/30,10.0.23.0/30 area=backbone-v2
        /routing ospf interface-template add networks=192.168.2.0/24,2.2.2.2/32 area=backbone-v2 passive""")
    cli(srx, """
        configure
        set system host-name SRX1
        set interfaces lo0 unit 0 family inet address 3.3.3.3/32
        set interfaces ge-0/0/0 unit 0 family inet address 10.0.23.2/30
        set interfaces ge-0/0/1 unit 0 family inet address 10.0.34.1/30
        set interfaces ge-0/0/2 unit 0 family inet address 192.168.3.1/24
        set routing-options router-id 3.3.3.3
        set protocols ospf area 0.0.0.0 interface ge-0/0/0.0
        set protocols ospf area 0.0.0.0 interface ge-0/0/1.0
        set protocols ospf area 0.0.0.0 interface ge-0/0/2.0 passive
        set protocols ospf area 0.0.0.0 interface lo0.0 passive
        commit and-quit""")
    cli(ar, """
        system-view
        sysname AR1
        interface LoopBack0
        ip address 4.4.4.4 32
        quit
        interface GigabitEthernet0/0/0
        ip address 10.0.34.2 30
        quit
        interface GigabitEthernet0/0/1
        ip address 10.0.41.1 30
        quit
        interface GigabitEthernet0/0/2
        ip address 192.168.4.1 24
        quit
        ospf 1 router-id 4.4.4.4
        silent-interface GigabitEthernet0/0/2
        area 0
        network 10.0.0.0 0.0.255.255
        network 192.168.4.0 0.0.0.255
        network 4.4.4.4 0.0.0.0
        return
        save""")
    for i, pc in enumerate(pcs, 1):
        cli(pc, f"ipconfig 192.168.{i}.10 255.255.255.0 192.168.{i}.1")
    t.engine.run(max_time=20_000)
    assert ping_ok(pcs[0], "192.168.3.10"), "OSPF across the ring"
    link = r1.get_port("gi0/0")
    link.set_admin(False)                       # break R1-MT1: traffic goes round the other way
    t.engine.run(max_time=3_000)
    assert ping_ok(pcs[0], "192.168.2.10"), "OSPF failover"
    link.set_admin(True)
    t.engine.run(max_time=20_000)
    assert ping_ok(pcs[0], "192.168.2.10")
    return t


def nat_lab() -> Topology:
    """Office LAN behind R1 (PAT + static NAT for a web server + ACL) - ISP - public server 8.8.8.8."""
    t = Topology()
    r1 = t.add_device("cisco-2911", x=-60, y=0)
    isp = t.add_device("cisco-2911", x=200, y=0)
    sw = t.add_device("cisco-2960", x=-260, y=0)
    pcs = [t.add_device("pc", x=-440, y=y) for y in (-110, 0)]
    web = t.add_device("server", x=-440, y=110)
    srv = t.add_device("server", x=400, y=0)
    for i, h in enumerate(pcs + [web], 1):
        t.connect(h.nic, sw.get_port(f"fa0/{i}"))
    t.connect(sw.get_port("gi0/1"), r1.get_port("gi0/0"))
    t.connect(r1.get_port("gi0/1"), isp.get_port("gi0/0"))
    t.connect(isp.get_port("gi0/1"), srv.nic)
    cli(sw, "enable\nconf t\nhostname SW1\nend")
    cli(r1, """
        enable
        configure terminal
        hostname R1
        interface gi0/0
        ip address 192.168.1.1 255.255.255.0
        ip nat inside
        ip access-group 100 in
        no shutdown
        interface gi0/1
        ip address 203.0.113.1 255.255.255.248
        ip nat outside
        no shutdown
        exit
        ip route 0.0.0.0 0.0.0.0 203.0.113.6
        access-list 1 permit 192.168.1.0 0.0.0.255
        access-list 100 deny icmp host 192.168.1.11 any echo
        access-list 100 permit ip any any
        ip nat inside source list 1 interface gi0/1 overload
        ip nat inside source static 192.168.1.20 203.0.113.3
        end
        write memory""")
    cli(isp, """
        enable
        configure terminal
        hostname ISP
        interface gi0/0
        ip address 203.0.113.6 255.255.255.248
        no shutdown
        interface gi0/1
        ip address 8.8.8.1 255.255.255.0
        no shutdown
        end
        write memory""")
    for h, ip in zip(pcs + [web], ("192.168.1.10", "192.168.1.11", "192.168.1.20")):
        cli(h, f"ipconfig {ip} 255.255.255.0 192.168.1.1")
    cli(srv, "ipconfig 8.8.8.8 255.255.255.0 8.8.8.1")
    assert ping_ok(pcs[0], "8.8.8.8"), "PAT"
    assert not ping_ok(pcs[1], "8.8.8.8"), "ACL 100 blocks PC .11"
    assert ping_ok(srv, "203.0.113.3"), "static NAT to the web server"
    return t


def bgp_lab() -> Topology:
    """AS 65001 (R1 Cisco + SRX1 Juniper, OSPF + iBGP over loopbacks) peering with AS 65002 (AR1 Huawei)
    and AS 65003 (MT1 MikroTik); AR1 and MT1 also peer with each other."""
    t = Topology()
    r1 = t.add_device("cisco-2911", x=0, y=0)
    srx = t.add_device("juniper-srx300", x=-260, y=0)
    ar = t.add_device("huawei-ar2220", x=240, y=-140)
    mt = t.add_device("mikrotik-rb4011", x=240, y=140)
    pcs = [t.add_device("pc", x=x, y=y) for x, y in ((-460, 0), (460, -140), (460, 140))]
    t.connect(srx.get_port("ge-0/0/0"), r1.get_port("gi0/0"))      # 10.0.1.0/30 (AS 65001 inside)
    t.connect(r1.get_port("gi0/1"), ar.get_port("GE0/0/0"))        # 10.12.0.0/30
    t.connect(r1.get_port("gi0/2"), mt.get_port("ether1"))         # 10.13.0.0/30
    t.connect(ar.get_port("GE0/0/1"), mt.get_port("ether2"))       # 10.23.0.0/30
    t.connect(pcs[0].nic, srx.get_port("ge-0/0/1"))
    t.connect(pcs[1].nic, ar.get_port("GE0/0/2"))
    t.connect(pcs[2].nic, mt.get_port("ether3"))
    cli(r1, """
        enable
        configure terminal
        hostname R1
        interface loopback0
        ip address 1.1.1.1 255.255.255.255
        interface gi0/0
        ip address 10.0.1.1 255.255.255.252
        no shutdown
        interface gi0/1
        ip address 10.12.0.1 255.255.255.252
        no shutdown
        interface gi0/2
        ip address 10.13.0.1 255.255.255.252
        no shutdown
        router ospf 1
        network 10.0.1.0 0.0.0.3 area 0
        network 1.1.1.1 0.0.0.0 area 0
        router bgp 65001
        bgp router-id 1.1.1.1
        neighbor 10.12.0.2 remote-as 65002
        neighbor 10.13.0.2 remote-as 65003
        neighbor 6.6.6.6 remote-as 65001
        neighbor 6.6.6.6 update-source Loopback0
        neighbor 6.6.6.6 next-hop-self
        end
        write memory""")
    cli(srx, """
        configure
        set system host-name SRX1
        set interfaces lo0 unit 0 family inet address 6.6.6.6/32
        set interfaces ge-0/0/0 unit 0 family inet address 10.0.1.2/30
        set interfaces ge-0/0/1 unit 0 family inet address 192.168.1.1/24
        set routing-options router-id 6.6.6.6
        set routing-options autonomous-system 65001
        set protocols ospf area 0.0.0.0 interface ge-0/0/0.0
        set protocols ospf area 0.0.0.0 interface lo0.0 passive
        set policy-options policy-statement LAN term 1 from protocol direct
        set policy-options policy-statement LAN term 1 from route-filter 192.168.1.0/24 exact
        set policy-options policy-statement LAN term 1 then accept
        set protocols bgp group IBGP type internal
        set protocols bgp group IBGP local-address 6.6.6.6
        set protocols bgp group IBGP neighbor 1.1.1.1
        set protocols bgp group IBGP export LAN
        commit and-quit""")
    cli(ar, """
        system-view
        sysname AR1
        interface GigabitEthernet0/0/0
        ip address 10.12.0.2 30
        quit
        interface GigabitEthernet0/0/1
        ip address 10.23.0.1 30
        quit
        interface GigabitEthernet0/0/2
        ip address 192.168.2.1 24
        quit
        bgp 65002
        router-id 2.2.2.2
        peer 10.12.0.1 as-number 65001
        peer 10.23.0.2 as-number 65003
        ipv4-family unicast
        network 192.168.2.0 24
        return
        save""")
    cli(mt, """
        /system identity set name=MT1
        /ip address add address=10.13.0.2/30 interface=ether1
        /ip address add address=10.23.0.2/30 interface=ether2
        /ip address add address=192.168.3.1/24 interface=ether3
        /ip firewall address-list add list=bgp-networks address=192.168.3.0/24
        /routing bgp connection add name=to-r1 remote.address=10.13.0.1 remote.as=65001 as=65003 local.role=ebgp router-id=3.3.3.3 output.network=bgp-networks
        /routing bgp connection add name=to-ar1 remote.address=10.23.0.1 remote.as=65002 as=65003 local.role=ebgp router-id=3.3.3.3 output.network=bgp-networks""")
    for i, pc in enumerate(pcs, 1):
        cli(pc, f"ipconfig 192.168.{i}.10 255.255.255.0 192.168.{i}.1")
    t.engine.run(max_time=40_000)
    assert ping_ok(pcs[0], "192.168.2.10"), "AS65001 (iBGP) -> AS65002"
    assert ping_ok(pcs[0], "192.168.3.10"), "AS65001 (iBGP) -> AS65003"
    r1.get_port("gi0/1").set_admin(False)       # R1-AR1 down: AS65002 is reached through AS65003
    t.engine.run(max_time=5_000)
    assert ping_ok(pcs[0], "192.168.2.10"), "BGP failover via AS65003"
    r1.get_port("gi0/1").set_admin(True)
    t.engine.run(max_time=20_000)
    assert ping_ok(pcs[1], "192.168.3.10")
    return t


def services_lab() -> Topology:
    """R1 with two LANs; Server0 (LAN1) gives DHCP to both (relay on R1), DNS and a web site; PCs use DHCP."""
    from netemu.core.services.dhcp import DhcpPool
    t = Topology()
    r1 = t.add_device("cisco-2911", x=0, y=0)
    sw1 = t.add_device("cisco-2960", x=-220, y=0)
    sw2 = t.add_device("cisco-2960", x=220, y=0)
    srv = t.add_device("server", x=-220, y=150)
    pcs = [t.add_device("pc", x=x, y=-150) for x in (-320, -120, 120, 320)]
    t.connect(sw1.get_port("gi0/1"), r1.get_port("gi0/0"))
    t.connect(sw2.get_port("gi0/1"), r1.get_port("gi0/1"))
    t.connect(srv.nic, sw1.get_port("fa0/24"))
    for i, pc in enumerate(pcs):
        t.connect(pc.nic, (sw1 if i < 2 else sw2).get_port(f"fa0/{i % 2 + 1}"))
    cli(r1, """
        enable
        configure terminal
        hostname R1
        interface gi0/0
        ip address 192.168.1.1 255.255.255.0
        no shutdown
        interface gi0/1
        ip address 192.168.2.1 255.255.255.0
        ip helper-address 192.168.1.2
        no shutdown
        end
        write memory""")
    cli(srv, "ipconfig 192.168.1.2 255.255.255.0 192.168.1.1")
    cli(srv, "ipconfig /dns 192.168.1.2")
    srv.hostname = "SRV"
    t.rename(srv, "SRV")
    srv.dhcp_server.cfg.enabled = True
    srv.dhcp_server.cfg.pools = [DhcpPool("LAN1", "192.168.1.0/24", "192.168.1.1", "192.168.1.2", start="192.168.1.100"),
                                 DhcpPool("LAN2", "192.168.2.0/24", "192.168.2.1", "192.168.1.2", start="192.168.2.100")]
    srv.dns_server.enabled = True
    srv.dns_server.records = {"www.lab.local": "192.168.1.2", "srv.lab.local": "192.168.1.2"}
    srv.http_server.pages["index.html"] = ("<html><body><h1>NetEmu lab web server</h1>"
                                           "<p>DHCP, DNS and HTTP work.</p></body></html>")
    for pc in pcs:
        cli(pc, "ipconfig /renew")
    assert str(pcs[0].nic.ip).startswith("192.168.1.10") and str(pcs[3].nic.ip).startswith("192.168.2.10")
    assert ping_ok(pcs[3], "192.168.1.2")
    out = cli(pcs[3], "curl http://www.lab.local/")
    assert "NetEmu lab web server" in out, out
    return t


LABS = {"services-lab.netemu": services_lab, "vlan-lab.netemu": vlan_lab, "ospf-lab.netemu": ospf_lab, "nat-lab.netemu": nat_lab,
        "bgp-lab.netemu": bgp_lab}

if __name__ == "__main__":
    for name, build in LABS.items():
        topo = build()
        persistence.save(topo, ROOT / "examples" / name)
        print("saved examples/" + name)
