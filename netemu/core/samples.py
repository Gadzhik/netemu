"""Ready-made demo topology (also used by tests)."""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

from .addresses import make_interface
from .topology import Topology


def build_demo(topo: Topology) -> Topology:
    """Multi-vendor lab:

    PC0 ─ Switch0 (Cisco 2960) ─ Router0 (Cisco 2911) ─ MikroTik0 ─ SRX0 (Juniper) ─ AR0 (Huawei) ─ LSW0 (Huawei S5720) ─ PC1

    LAN1 192.168.1.0/24, LAN2 192.168.2.0/24, transit links 10.0.x.0/30, static routing.
    """
    t = topo
    pc0 = t.add_device("pc", x=-560, y=0)
    sw0 = t.add_device("cisco-2960", x=-400, y=0)
    r0 = t.add_device("cisco-2911", x=-240, y=0)
    mt = t.add_device("mikrotik-rb4011", x=-80, y=-110)
    srx = t.add_device("juniper-srx300", x=80, y=-110)
    ar = t.add_device("huawei-ar2220", x=240, y=0)
    lsw = t.add_device("huawei-s5720", x=400, y=0)
    pc1 = t.add_device("pc", x=560, y=0)
    srv = t.add_device("server", x=400, y=130)

    t.connect(pc0.nic, sw0.get_port("Fa0/1"))
    t.connect(sw0.get_port("Gi0/1"), r0.get_port("Gi0/0"))
    t.connect(r0.get_port("Gi0/1"), mt.get_port("ether1"))
    t.connect(mt.get_port("ether2"), srx.get_port("ge-0/0/0"))
    t.connect(srx.get_port("ge-0/0/1"), ar.get_port("GE0/0/0"))
    t.connect(ar.get_port("GE0/0/1"), lsw.get_port("GE0/0/1"))
    t.connect(lsw.get_port("GE0/0/2"), pc1.nic)
    t.connect(lsw.get_port("GE0/0/3"), srv.nic)

    def ip(dev, port, addr, mask):
        p = dev.get_port(port)
        p.ip = make_interface(addr, mask)
        p.set_admin(True)

    r0.hostname = "R1"
    ip(r0, "Gi0/0", "192.168.1.1", "255.255.255.0")
    ip(r0, "Gi0/1", "10.0.1.1", "255.255.255.252")
    r0.add_static_route(IPv4Network("0.0.0.0/0"), IPv4Address("10.0.1.2"))

    mt.hostname = "MT1"
    ip(mt, "ether1", "10.0.1.2", "255.255.255.252")
    ip(mt, "ether2", "10.0.2.1", "255.255.255.252")
    mt.add_static_route(IPv4Network("192.168.1.0/24"), IPv4Address("10.0.1.1"))
    mt.add_static_route(IPv4Network("0.0.0.0/0"), IPv4Address("10.0.2.2"))

    srx.hostname = "SRX1"
    ip(srx, "ge-0/0/0", "10.0.2.2", "255.255.255.252")
    ip(srx, "ge-0/0/1", "10.0.3.1", "255.255.255.252")
    srx.add_static_route(IPv4Network("192.168.1.0/24"), IPv4Address("10.0.2.1"), distance=5)
    srx.add_static_route(IPv4Network("10.0.1.0/30"), IPv4Address("10.0.2.1"), distance=5)
    srx.add_static_route(IPv4Network("192.168.2.0/24"), IPv4Address("10.0.3.2"), distance=5)

    ar.hostname = "AR1"
    ip(ar, "GE0/0/0", "10.0.3.2", "255.255.255.252")
    ip(ar, "GE0/0/1", "192.168.2.1", "255.255.255.0")
    ar.add_static_route(IPv4Network("0.0.0.0/0"), IPv4Address("10.0.3.1"), distance=60)

    sw0.hostname = "SW1"
    lsw.hostname = "SW2"
    pc0.set_ip("192.168.1.10", "255.255.255.0")
    pc0.set_gateway("192.168.1.1")
    pc1.set_ip("192.168.2.10", "255.255.255.0")
    pc1.set_gateway("192.168.2.1")
    srv.set_ip("192.168.2.100", "255.255.255.0")
    srv.set_gateway("192.168.2.1")
    for d in t.devices.values():
        if d.category in ("router", "switch"):
            d.save_startup()
    t.sync_names()
    return t
