"""Simulation core: switching, ARP, routing, ICMP, persistence."""
import pytest

from netemu.core import persistence
from netemu.core.addresses import (IPv4Address, IPv4Network, MacAddress, is_valid_mask,
                                   mask_to_prefix, prefix_to_mask)
from netemu.core.devices import MODELS
from netemu.core.engine import SimEngine
from netemu.core.stack.icmp import TraceListener, TracerouteSession
from netemu.core.topology import Topology

from test_ping_core import ping


def two_subnets(router_model="cisco-2911"):
    """PC0 - SW0 - R0 - SW1 - PC1 with 192.168.1.0/24 and 192.168.2.0/24."""
    t = Topology()
    r = t.add_device(router_model)
    s1, s2 = t.add_device("cisco-2960"), t.add_device("cisco-2960")
    a, b = t.add_device("pc"), t.add_device("pc")
    r1, r2 = list(r.ports.values())[:2]
    t.connect(a.nic, s1.get_port("fa0/1"))
    t.connect(s1.get_port("gi0/1"), r1)
    t.connect(b.nic, s2.get_port("fa0/1"))
    t.connect(s2.get_port("gi0/1"), r2)
    from netemu.core.addresses import make_interface
    r1.ip = make_interface("192.168.1.1", "255.255.255.0")
    r2.ip = make_interface("192.168.2.1", "255.255.255.0")
    r1.set_admin(True)
    r2.set_admin(True)
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")
    return t, r, a, b


# -- addresses -----------------------------------------------------------------
def test_mac_formatting():
    m = MacAddress.parse("00:01:42:AB:cd:01")
    assert m.cisco() == "0001.42ab.cd01"
    assert m.windows() == "00-01-42-AB-CD-01"
    assert MacAddress.parse("0001.42ab.cd01") == m


def test_masks():
    assert is_valid_mask("255.255.255.0")
    assert not is_valid_mask("255.0.255.0")
    assert mask_to_prefix("255.255.255.252") == 30
    assert prefix_to_mask(20) == "255.255.240.0"


def test_engine_ordering_and_cancel():
    e = SimEngine()
    log = []
    e.schedule(5, log.append, "b")
    e.schedule(1, log.append, "a")
    ev = e.schedule(3, log.append, "x")
    ev.cancel()
    e.run()
    assert log == ["a", "b"]
    assert e.now == 5


def test_engine_step_groups_same_time():
    e = SimEngine()
    log = []
    e.schedule(1, log.append, 1)
    e.schedule(1, log.append, 2)
    e.schedule(2, log.append, 3)
    assert len(e.step()) == 2
    assert log == [1, 2]
    e.step()
    assert log == [1, 2, 3]


# -- L2 ------------------------------------------------------------------------
def test_hub_floods_and_switch_learns():
    t = Topology()
    hub = t.add_device("hub")
    a, b, c = (t.add_device("pc") for _ in range(3))
    for i, pc in enumerate((a, b, c)):
        t.connect(pc.nic, hub.ports[f"Port{i}"])
        pc.set_ip(f"10.0.0.{i + 1}", "255.255.255.0")
    s, _ = ping(a, "10.0.0.2")
    assert s.stats.received == 4
    # c saw the flooded frames
    assert c.nic.rx_frames > 0


def test_switch_does_not_flood_known_unicast():
    t = Topology()
    sw = t.add_device("cisco-2960")
    a, b, c = (t.add_device("pc") for _ in range(3))
    for i, pc in enumerate((a, b, c)):
        t.connect(pc.nic, sw.get_port(f"fa0/{i + 1}"))
        pc.set_ip(f"10.0.0.{i + 1}", "255.255.255.0")
    ping(a, "10.0.0.2")
    rx_before = c.nic.rx_frames
    ping(a, "10.0.0.2")
    assert c.nic.rx_frames == rx_before  # ARP cached, unicast only


def test_mac_aging():
    t = Topology()
    sw = t.add_device("cisco-2960")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("fa0/1"))
    t.connect(b.nic, sw.get_port("fa0/2"))
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    ping(a, "10.0.0.2", 1)
    assert len(sw.mac_entries()) == 2
    t.engine.run(max_time=301_000)
    assert sw.mac_entries() == []


def test_unplugged_or_shutdown_port():
    t = Topology()
    sw = t.add_device("cisco-2960")
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, sw.get_port("fa0/1"))
    link = t.connect(b.nic, sw.get_port("fa0/2"))
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    sw.get_port("fa0/2").set_admin(False)
    s, c = ping(a, "10.0.0.2", 2)
    assert s.stats.received == 0
    sw.get_port("fa0/2").set_admin(True)
    s, _ = ping(a, "10.0.0.2", 2)
    assert s.stats.received == 2
    t.disconnect(link)
    assert not b.nic.is_up
    s, _ = ping(a, "10.0.0.2", 1)
    assert s.stats.received == 0


# -- L3 ------------------------------------------------------------------------
@pytest.mark.parametrize("model", [k for k, m in MODELS.items() if m.category == "router" and not m.dialect])
def test_routing_between_two_subnets(model):
    t, r, a, b = two_subnets(model)
    s, c = ping(a, "192.168.2.10")
    assert s.stats.received == 4, c.events
    assert c.events[0][3] == 128 - 1  # one routed hop decrements the PC's TTL
    s, _ = ping(b, "192.168.1.1")    # router's far interface
    assert s.stats.received == 4


def test_cisco_router_ports_start_shutdown():
    t = Topology()
    r = t.add_device("cisco-2911")
    assert all(not p.admin_up for p in r.ports.values())
    m = t.add_device("mikrotik-rb4011")
    assert all(p.admin_up for p in m.ports.values())


def test_no_gateway_fails():
    t, r, a, b = two_subnets()
    a.set_gateway(None)
    s, c = ping(a, "192.168.2.10", 2)
    assert s.stats.received == 0
    assert c.events[0][0] == "fail"


def test_destination_unreachable_from_router():
    t, r, a, b = two_subnets()
    s, c = ping(a, "8.8.8.8", 2)
    assert s.stats.received == 0
    assert c.events[0] == ("unreachable", 0, "192.168.1.1")


def chain():
    """PC0 - R0 - R1 - R2 - PC1 with static routes."""
    from netemu.core.addresses import make_interface as mi
    t = Topology()
    r0, r1, r2 = t.add_device("cisco-2911"), t.add_device("huawei-ar2220"), t.add_device(
        "mikrotik-rb4011")
    a, b = t.add_device("pc"), t.add_device("pc")
    p = lambda r, i: list(r.ports.values())[i]
    t.connect(a.nic, p(r0, 0))
    t.connect(p(r0, 1), p(r1, 0))
    t.connect(p(r1, 1), p(r2, 0))
    t.connect(p(r2, 1), b.nic)
    p(r0, 0).ip = mi("10.1.0.1", "255.255.255.0")
    p(r0, 1).ip = mi("10.12.0.1", "255.255.255.252")
    p(r1, 0).ip = mi("10.12.0.2", "255.255.255.252")
    p(r1, 1).ip = mi("10.23.0.1", "255.255.255.252")
    p(r2, 0).ip = mi("10.23.0.2", "255.255.255.252")
    p(r2, 1).ip = mi("10.3.0.1", "255.255.255.0")
    for r in (r0, r1, r2):
        for port in r.ports.values():
            port.set_admin(True)
    a.set_ip("10.1.0.10", "255.255.255.0")
    a.set_gateway("10.1.0.1")
    b.set_ip("10.3.0.10", "255.255.255.0")
    b.set_gateway("10.3.0.1")
    N = IPv4Network
    r0.add_static_route(N("0.0.0.0/0"), IPv4Address("10.12.0.2"))
    r1.add_static_route(N("10.1.0.0/24"), IPv4Address("10.12.0.1"))
    r1.add_static_route(N("10.3.0.0/24"), IPv4Address("10.23.0.2"))
    r2.add_static_route(N("0.0.0.0/0"), IPv4Address("10.23.0.1"))
    return t, (r0, r1, r2), a, b


def test_static_routing_chain():
    t, rs, a, b = chain()
    s, c = ping(a, "10.3.0.10")
    assert s.stats.received == 4, c.events
    assert c.events[0][3] == 125


def test_static_route_missing_then_added():
    t, (r0, r1, r2), a, b = chain()
    r1.remove_static_route(IPv4Network("10.3.0.0/24"))
    s, c = ping(a, "10.3.0.10", 1)
    assert c.events[0] == ("unreachable", 0, "10.12.0.2")
    r1.add_static_route(IPv4Network("10.3.0.0/24"), IPv4Address("10.23.0.2"))
    s, c = ping(a, "10.3.0.10", 1)
    assert s.stats.received == 1


def test_static_route_via_exit_interface_uses_proxy_arp():
    t, (r0, r1, r2), a, b = chain()
    r0.static_routes.clear()
    r0.add_static_route(IPv4Network("10.3.0.0/24"), None, list(r0.ports)[1])
    r2.static_routes.clear()
    r2.add_static_route(IPv4Network("10.1.0.0/24"), None, list(r2.ports)[0])
    s, c = ping(a, "10.3.0.10", 2)
    assert s.stats.received == 2, c.events


def test_routing_loop_ttl_exceeded():
    t, (r0, r1, r2), a, b = chain()
    r1.remove_static_route(IPv4Network("10.3.0.0/24"))
    r1.add_static_route(IPv4Network("10.3.0.0/24"), IPv4Address("10.12.0.1"))  # back to r0
    s, c = ping(a, "10.3.0.10", 1)
    assert c.events[0][0] == "ttl"


def test_longest_prefix_match():
    t, (r0, r1, r2), a, b = chain()
    # resolvable recursively through the default route (like Cisco)
    r0.add_static_route(IPv4Network("10.3.0.0/24"), IPv4Address("10.99.0.1"))
    assert r0.route_lookup(IPv4Address("10.3.0.10"))[1] == IPv4Address("10.12.0.2")
    r0.add_static_route(IPv4Network("10.3.0.10/32"), IPv4Address("10.1.0.10"))
    assert r0.route_lookup(IPv4Address("10.3.0.10"))[1] == IPv4Address("10.1.0.10")
    table = r0.routing_table()
    codes = {(str(r.network), r.code) for r in table}
    assert ("10.1.0.0/24", "C") in codes and ("10.1.0.1/32", "L") in codes
    assert ("0.0.0.0/0", "S") in codes and ("10.3.0.0/24", "S") in codes
    # without the default route the next hop is unresolvable -> not installed
    r0.remove_static_route(IPv4Network("0.0.0.0/0"))
    codes = {(str(r.network), r.code) for r in r0.routing_table()}
    assert ("10.3.0.0/24", "S") not in codes and ("10.3.0.10/32", "S") in codes


class TraceCollect(TraceListener):
    def __init__(self):
        self.hops = None
        self.reached = None

    def on_finish(self, hops, reached):
        self.hops, self.reached = hops, reached


def test_traceroute():
    t, rs, a, b = chain()
    c = TraceCollect()
    s = TracerouteSession(a, IPv4Address("10.3.0.10"), c).start()
    t.engine.run_until(lambda: s.done)
    assert c.reached
    assert [str(h.address) for h in c.hops] == ["10.1.0.1", "10.12.0.2", "10.23.0.2", "10.3.0.10"]
    assert all(len(h.probes) == 3 and None not in h.probes for h in c.hops)


def test_traceroute_unreachable_stops():
    t, (r0, r1, r2), a, b = chain()
    r1.remove_static_route(IPv4Network("10.3.0.0/24"))
    c = TraceCollect()
    s = TracerouteSession(a, IPv4Address("10.3.0.10"), c).start()
    t.engine.run_until(lambda: s.done)
    assert not c.reached
    assert len(c.hops) == 3 and c.hops[-1].unreachable
    assert str(c.hops[-1].address) == "10.12.0.2"


def test_ping_cancel():
    t, rs, a, b = chain()
    from test_ping_core import Collect
    from netemu.core.stack.icmp import PingSession
    s = PingSession(a, IPv4Address("10.9.9.9"), Collect(), count=100).start()
    t.engine.run(max_time=3000)
    s.cancel()
    assert s.done
    t.engine.run()
    assert not t.engine.has_pending()


def test_frame_events_emitted():
    t, r, a, b = two_subnets()
    sent, delivered = [], []
    t.engine.subscribe("frame_sent", sent.append)
    t.engine.subscribe("frame_delivered", delivered.append)
    ping(a, "192.168.2.10", 1)
    protos = {ev.info["frame"].protocol for ev in delivered}
    assert protos == {"ARP", "ICMP"}
    assert len(sent) == len(delivered)


# -- persistence ---------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    t, rs, a, b = chain()
    rs[0].hostname = "EDGE"
    rs[0].save_startup()
    a.x, a.y = 120, -40
    path = tmp_path / "lab.netemu"
    persistence.save(t, path)
    t2 = persistence.load(path)
    assert len(t2.devices) == len(t.devices)
    assert len(t2.links) == 4
    r0 = t2.find("EDGE")  # label follows hostname after loading
    assert r0.hostname == "EDGE" and r0.startup_config["hostname"] == "EDGE"
    assert len(r0.static_routes) == 1
    pc = t2.find(a.name)
    assert (pc.x, pc.y) == (120, -40)
    s, c = ping(pc, "10.3.0.10")
    assert s.stats.received == 4


def test_load_rejects_foreign_file(tmp_path):
    p = tmp_path / "x.netemu"
    p.write_text('{"format": "other"}')
    with pytest.raises(ValueError):
        persistence.load(p)


def test_remove_device_removes_links():
    t, r, a, b = two_subnets()
    t.remove_device(r)
    assert len(t.links) == 2
    assert all(l.a.device is not r and l.b.device is not r for l in t.links)


def test_cannot_double_connect():
    t = Topology()
    a, b, c = t.add_device("pc"), t.add_device("pc"), t.add_device("pc")
    t.connect(a.nic, b.nic)
    with pytest.raises(ValueError):
        t.connect(a.nic, c.nic)


def test_label_follows_hostname_from_every_cli():
    from cli_helpers import run_all
    t = Topology()
    r = t.add_device("cisco-2911")
    mt = t.add_device("mikrotik-rb4011")
    srx = t.add_device("juniper-srx300")
    ar = t.add_device("huawei-ar2220")
    run_all(r.create_cli(), "enable\nconf t\nhostname EDGE\nend")
    run_all(mt.create_cli(), "/system identity set name=CORE")
    s = srx.create_cli()
    run_all(s, "configure\nset system host-name FW")
    assert srx.name == "SRX0"                       # Junos: nothing changes before commit
    run_all(s, "commit")
    run_all(ar.create_cli(), "system-view\nsysname EDGE")   # duplicate -> suffix
    assert (r.name, mt.name, srx.name, ar.name) == ("EDGE", "CORE", "FW", "EDGE-2")
    run_all(r.create_cli(), "enable\nconf t\nno hostname\nend")
    assert r.hostname == "Router" and r.name == "Router0"    # back to a default label
    t.rename(mt, "MyLabel")                          # manual label survives hostname reset
    run_all(mt.create_cli(), "/system identity set name=MikroTik")
    assert mt.name == "MyLabel"
