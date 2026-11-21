"""L3 switching core: VLAN interfaces route between VLANs; autostate; persistence."""
from netemu.core.addresses import IPv4Interface
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received == 4


def build(model="cisco-2960"):
    t = Topology()
    sw = t.add_device(model)
    ports = sw.physical_ports()
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, ports[0])
    t.connect(b.nic, ports[1])
    sw.add_vlan(10)
    sw.add_vlan(20)
    for p, v in ((ports[0], 10), (ports[1], 20)):
        p.mode, p.pvid, p.access_vlan = "access", v, v
    for v in (10, 20):
        sw.add_svi(v).ip = IPv4Interface(f"10.0.{v}.1/24")
    a.set_ip("10.0.10.5", "255.255.255.0")
    a.set_gateway("10.0.10.1")
    b.set_ip("10.0.20.5", "255.255.255.0")
    b.set_gateway("10.0.20.1")
    t.engine.run(max_time=31_000)
    return t, sw, a, b


def test_svi_answers_and_routes_only_with_ip_routing():
    t, sw, a, b = build()
    assert ping(a, "10.0.10.1")                  # own SVI
    assert not ping(a, "10.0.20.5")              # Cisco: ip routing is off by default
    sw.ip_routing = True
    assert ping(a, "10.0.20.5") and ping(b, "10.0.10.5")
    assert {str(r.network) for r in sw.routing_table() if r.code == "C"} == {"10.0.10.0/24", "10.0.20.0/24"}


def test_huawei_routes_by_default_and_autostate():
    t, sw, a, b = build("huawei-s5720")
    assert ping(a, "10.0.20.5")
    svi = sw.svi_for_vlan(20)
    assert svi.is_up
    sw.physical_ports()[1].set_admin(False)
    assert not svi.is_up and not any(str(r.network) == "10.0.20.0/24" for r in sw.routing_table())


def test_svi_persists():
    t, sw, a, b = build()
    sw.ip_routing = True
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    sw2 = t2.devices[sw.id]
    assert [p.name for p in sw2.svis()] == ["Vlan10", "Vlan20"] and sw2.ip_routing
    t2.engine.run(max_time=31_000)
    assert ping(t2.devices[a.id], "10.0.20.5")
