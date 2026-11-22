"""DDoS training: flows, saturation, server resources, reflection and every mitigation."""
from ipaddress import IPv4Address

from cli_helpers import run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.security.ddos import Attack, guard, monitor
from netemu.core.topology import Topology


def availability(t, client, url, n=10) -> float:
    from netemu.core.services.http import HttpGet
    ok = 0
    for _ in range(n):
        res = {}
        job = HttpGet(client, url, lambda s, b: res.__setitem__("s", s)).start()
        t.engine.run_until(lambda: job.done)
        ok += res.get("s") == 200
    return ok / n


def site():
    """Botnet and a customer behind ISP; the site (EDGE + switch + web server on FastEthernet = 100 Mbit/s)."""
    t = Topology()
    isp, edge = t.add_device("cisco-2911", "ISP"), t.add_device("cisco-2911", "EDGE")
    sw = t.add_device("cisco-2960", "SW")
    srv, client, bot = t.add_device("server", "WEB"), t.add_device("pc", "CLIENT"), t.add_device("botnet", "BOTS")
    t.connect(bot.nic, isp.get_port("gi0/0"))
    t.connect(client.nic, isp.get_port("gi0/1"))
    t.connect(isp.get_port("gi0/2"), edge.get_port("gi0/0"))
    t.connect(edge.get_port("gi0/1"), sw.get_port("Gi0/1"))
    t.connect(srv.nic, sw.get_port("Fa0/1"))
    run_all(isp.create_cli(), """enable
configure terminal
interface gi0/0
ip address 198.51.100.1 255.255.255.0
no shutdown
interface gi0/1
ip address 192.0.2.1 255.255.255.0
no shutdown
interface gi0/2
ip address 203.0.113.1 255.255.255.252
no shutdown
exit
ip route 203.0.113.128 255.255.255.128 203.0.113.2
end""")
    run_all(edge.create_cli(), """enable
configure terminal
interface gi0/0
ip address 203.0.113.2 255.255.255.252
no shutdown
interface gi0/1
ip address 203.0.113.129 255.255.255.128
no shutdown
exit
ip route 0.0.0.0 0.0.0.0 203.0.113.1
end""")
    srv.set_ip("203.0.113.130", "255.255.255.128")
    srv.set_gateway("203.0.113.129")
    client.set_ip("192.0.2.10", "255.255.255.0")
    client.set_gateway("192.0.2.1")
    bot.set_ip("198.51.100.10", "255.255.255.0")
    bot.set_gateway("198.51.100.1")
    t.engine.run(max_time=5000)
    return t, isp, edge, sw, srv, client, bot


def test_volumetric_udp_flood_saturates_the_last_mile_and_an_edge_acl_saves_it():
    t, isp, edge, sw, srv, client, bot = site()
    assert availability(t, client, "http://203.0.113.130") == 1.0
    bot.attacks = [Attack("udp", "203.0.113.130", 80, bots=200, rate=100, size=1250)]   # 200 Mbit/s
    bot.start()
    t.engine.run(max_time=3000)
    m = monitor(t)
    link = srv.nic.link
    assert 1.9 < m.utilization(link, link.other(srv.nic)) < 2.1             # 200 Mbit/s on a 100 Mbit/s port
    assert m.attack_bps_at(srv) <= 100e6 * 1.01                             # the cable cuts it to 100 Mbit/s
    assert availability(t, client, "http://203.0.113.130") < 0.6
    run_all(edge.create_cli(), """enable
configure terminal
access-list 100 deny udp any host 203.0.113.130
access-list 100 permit ip any any
interface gi0/0
ip access-group 100 in
end""")
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(srv) == 0
    assert availability(t, client, "http://203.0.113.130") == 1.0
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    b2 = next(d for d in t2.devices.values() if d.name == "BOTS")
    assert b2.attacks[0].running and b2.attacks[0].bots == 200


def test_policer_caps_the_flood():
    t, isp, edge, sw, srv, client, bot = site()
    bot.attacks = [Attack("icmp", "203.0.113.130", bots=500, rate=100, size=1000)]      # 400 Mbit/s
    bot.start()
    run_all(edge.create_cli(), """enable
configure terminal
access-list 101 permit icmp any any
class-map match-all ICMP
match access-group 101
exit
policy-map EDGE-IN
class ICMP
police 8000000
exit
exit
interface gi0/0
service-policy input EDGE-IN
end""")
    t.engine.run(max_time=3000)
    assert monitor(t).attack_bps_at(srv) <= 8e6 * 1.01
    assert availability(t, client, "http://203.0.113.130") == 1.0


def test_syn_flood_needs_syn_cookies_or_tcp_intercept():
    t, isp, edge, sw, srv, client, bot = site()
    bot.attacks = [Attack("syn", "203.0.113.130", bots=50, rate=200, spoof=True)]        # 10 kpps, 4.8 Mbit/s
    bot.start()
    t.engine.run(max_time=3000)
    assert availability(t, client, "http://203.0.113.130") < 0.3
    guard(srv)["syncookies"] = True
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") == 1.0
    guard(srv)["syncookies"] = False
    run_all(edge.create_cli(), """enable
configure terminal
access-list 120 permit tcp any host 203.0.113.130 eq 80
ip tcp intercept list 120
end""")
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") == 1.0


def test_http_flood_per_ip_limit_and_challenge():
    t, isp, edge, sw, srv, client, bot = site()
    bot.attacks = [Attack("http", "203.0.113.130", bots=50, rate=20)]                   # 1000 rps
    bot.start()
    t.engine.run(max_time=3000)
    assert availability(t, client, "http://203.0.113.130") < 0.5
    guard(srv)["http_limit"] = 5                                                        # 50 x 5 = 250 rps
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") == 1.0
    bot.attacks[0].bots, bot.attacks[0].rate = 400, 4                                   # many slow bots
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") < 0.8                        # 1600 rps: limit is useless
    guard(srv)["challenge"] = True
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") == 1.0


def test_slowloris_and_connection_limit():
    t, isp, edge, sw, srv, client, bot = site()
    bot.attacks = [Attack("slowloris", "203.0.113.130", bots=10, rate=50)]              # 500 open connections
    bot.start()
    t.engine.run(max_time=3000)
    assert monitor(t).attack_bps_at(srv) < 1e6                                          # low bandwidth ...
    assert availability(t, client, "http://203.0.113.130") == 0.0                       # ... and the site is down
    guard(srv)["conn_limit"] = 10
    t.engine.run(max_time=2000)
    assert availability(t, client, "http://203.0.113.130") == 1.0


def test_dns_amplification_through_an_open_resolver_and_rtbh():
    t, isp, edge, sw, srv, client, bot = site()
    resolver = t.add_device("server", "RESOLVER")
    t.connect(resolver.nic, isp.get_port("gi0/1") if False else sw.get_port("Fa0/2"))
    resolver.set_ip("203.0.113.131", "255.255.255.128")
    resolver.set_gateway("203.0.113.129")
    resolver.dns_server.enabled = True
    guard(resolver)["open_resolver"] = True
    # the victim is the customer network's neighbour; the botnet spoofs the client's address
    bot.attacks = [Attack("dns-amp", "192.0.2.10", bots=100, rate=100, reflectors=["203.0.113.131"])]
    bot.start()
    t.engine.run(max_time=3000)
    m = monitor(t)
    got = m.attack_bps_at(client)
    sent = bot.attacks[0].total_bps
    assert got > 15 * sent             # ~47x on paper; the resolver's own 100 Mbit/s port caps it here
    guard(resolver)["open_resolver"] = False                   # close the resolver
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(client) == 0
    # RTBH: blackhole the victim at the ISP (the victim is offline, the rest of the network survives)
    guard(resolver)["open_resolver"] = True
    run_all(isp.create_cli(), "enable\nconfigure terminal\nip route 192.0.2.10 255.255.255.255 Null0\nend")
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(client) == 0


def test_urpf_drops_spoofed_sources_at_the_customer_edge():
    t, isp, edge, sw, srv, client, bot = site()
    bot.attacks = [Attack("udp", "203.0.113.130", bots=100, rate=100, spoof=True)]
    bot.start()
    t.engine.run(max_time=3000)
    assert monitor(t).attack_bps_at(srv) > 0
    run_all(isp.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip verify unicast source reachable-via rx\nend")
    t.engine.run(max_time=3000)
    assert monitor(t).attack_bps_at(srv) == 0
    bot.attacks[0].spoof = False                               # real addresses pass uRPF (then an ACL is needed)
    t.engine.run(max_time=3000)
    assert monitor(t).attack_bps_at(srv) > 0


def test_urpf_and_null_routes_on_every_vendor():
    from cli_helpers import run
    t = Topology()
    hw, jn, mt = t.add_device("huawei-ar2220"), t.add_device("juniper-srx300"), t.add_device("mikrotik-rb4011")
    out = run_all(hw.create_cli(), """system-view
interface GigabitEthernet0/0/1
urpf strict
quit
ip route-static 203.0.113.99 32 NULL0
return""")
    assert "Error" not in out, out
    assert hw.get_port("GE0/0/1").urpf == "strict" and hw.routing_table()[0].port.name == "Null0"
    cfg = run_all(hw.create_cli(), "display current-configuration")
    assert " urpf strict" in cfg and "ip route-static 203.0.113.99 255.255.255.255 NULL0" in cfg
    out = run_all(jn.create_cli(), """configure
set interfaces ge-0/0/1 unit 0 family inet address 10.0.0.1/24
set interfaces ge-0/0/1 unit 0 family inet rpf-check
set routing-options static route 203.0.113.99/32 discard
commit and-quit""")
    assert "error" not in out.lower(), out
    assert jn.get_port("ge-0/0/1").urpf == "strict"
    assert any(r.port.name == "Null0" for r in jn.routing_table())
    show = run(jn.create_cli(), "show configuration")
    assert "rpf-check;" in show and "route 203.0.113.99/32 discard;" in show
    s = mt.create_cli()
    for line in ("/ip settings set rp-filter=loose", "/ip route add dst-address=203.0.113.99/32 blackhole"):
        assert "failure" not in run(s, line)
    assert mt.get_port("ether1").urpf == "loose"
    assert "add blackhole dst-address=203.0.113.99/32" in run(s, "/export")
    assert "rp-filter: loose" in run(s, "/ip settings print")
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[hw.id].get_port("GE0/0/1").urpf == "strict"


def test_provider_scrubbing_rtbh_and_flowspec():
    t = Topology()
    inet = t.add_device("internet", "ISP")
    edge, client, bot = t.add_device("cisco-2911", "EDGE"), t.add_device("pc", "C"), t.add_device("botnet", "B")
    t.connect(edge.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(client.nic, inet.get_port("ISP2"))
    t.connect(bot.nic, inet.get_port("ISP3"))
    run_all(edge.create_cli(), "enable\nconfigure terminal\ninterface gi0/1\nip address 203.0.113.2 255.255.255.252\n"
                               "no shutdown\nexit\nip route 0.0.0.0 0.0.0.0 203.0.113.1\nend")
    client.set_ip("203.0.113.10", "255.255.255.252")
    client.set_gateway("203.0.113.9")
    bot.set_ip("203.0.113.14", "255.255.255.252")
    bot.set_gateway("203.0.113.13")
    bot.attacks = [Attack("udp", "203.0.113.2", 80, bots=1000, rate=200, spoof=True),
                   Attack("ntp-amp", "203.0.113.2", 123, bots=100, rate=100, reflectors=["198.51.100.123"])]
    bot.start()
    t.engine.run(max_time=3000)
    m = monitor(t)
    rx = m.received(edge)
    assert "udp" in rx and "ntp-amp" in rx
    inet.protect = {"ISP0": {"flowspec": ["udp src 123"]}}
    t.engine.run(max_time=3000)
    assert "ntp-amp" not in m.received(edge) and "udp" in m.received(edge)
    inet.protect = {"ISP0": {"scrub": True}}
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(edge) == 0
    from test_vlan import ping as _ping
    assert _ping(None, client, "203.0.113.2").received == 4                     # legitimate traffic passes
    inet.protect = {"ISP0": {"rtbh": ["203.0.113.2"]}}
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(edge) == 0 and _ping(None, client, "203.0.113.2").received == 0   # victim sacrificed
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[inet.id].protect == {"ISP0": {"rtbh": ["203.0.113.2"]}}


def test_server_guard_persists():
    t = Topology()
    srv = t.add_device("server", "S")
    guard(srv).update(syncookies=True, http_limit=5, challenge=True)
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    g = guard(t2.devices[srv.id])
    assert g["syncookies"] and g["http_limit"] == 5 and g["challenge"] and not g["open_resolver"]



def test_dns_switch_to_protection_service():
    """The domain moves to the provider's reverse proxy: attacks by name follow DNS and are absorbed,
    clients move after the TTL, attacks on the origin's IP still hit it."""
    t = Topology()
    inet = t.add_device("internet", "ISP")
    edge, sw, web = t.add_device("cisco-2911", "EDGE"), t.add_device("cisco-2960", "SW"), t.add_device("server", "WEB")
    client, bot = t.add_device("pc", "C"), t.add_device("botnet", "B")
    t.connect(edge.get_port("gi0/1"), inet.get_port("ISP0"))
    t.connect(edge.get_port("gi0/0"), sw.get_port("Gi0/1"))
    t.connect(web.nic, sw.get_port("Gi0/2"))
    t.connect(client.nic, inet.get_port("ISP2"))
    t.connect(bot.nic, inet.get_port("ISP3"))
    run_all(edge.create_cli(), "enable\nconfigure terminal\ninterface gi0/0\nip address 192.168.1.1 255.255.255.0\n"
                               "ip nat inside\nno shutdown\ninterface gi0/1\nip address 203.0.113.2 255.255.255.252\n"
                               "ip nat outside\nno shutdown\nexit\nip route 0.0.0.0 0.0.0.0 203.0.113.1\n"
                               "ip nat inside source static tcp 192.168.1.10 80 203.0.113.2 80\nend")
    web.set_ip("192.168.1.10", "255.255.255.0")
    web.set_gateway("192.168.1.1")
    for h, ip, gw in ((client, "203.0.113.10", "203.0.113.9"), (bot, "203.0.113.14", "203.0.113.13")):
        h.set_ip(ip, "255.255.255.252")
        h.set_gateway(gw)
        h.dns = IPv4Address("8.8.8.8")
    inet.set_zone("shop.example", "203.0.113.2", ttl=30)
    assert availability(t, client, "http://shop.example") == 1.0
    bot.attacks = [Attack("udp", "shop.example", 80, bots=30000, rate=10, size=1250, spoof=True)]
    bot.start()
    t.engine.run(max_time=3000)
    m = monitor(t)
    assert m.attack_bps_at(edge) > 0.9e9
    assert availability(t, client, "http://shop.example") < 0.7
    inet.set_zone("shop.example", shield=True)                       # switch DNS to the protection service
    t.engine.run(max_time=5000)
    assert m.attack_bps_at(edge) > 0.9e9                             # old record still cached (TTL 30 s)
    t.engine.run(max_time=31000)
    assert m.attack_bps_at(edge) == 0                                # the bots followed DNS to the proxy
    assert availability(t, client, "http://shop.example") == 1.0      # proxied to the origin
    assert inet.shield_stats["proxied"] >= 10
    from netemu.core.services.dns import cached
    assert str(cached(client, "shop.example")) == "198.51.100.200"
    bot.attacks.append(Attack("udp", "203.0.113.2", 80, bots=30000, rate=10, size=1250, spoof=True, running=True))
    t.engine.run(max_time=3000)
    assert m.attack_bps_at(edge) > 0.9e9                             # the origin's IP leaked: still attacked
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.devices[inet.id].zones["shop.example"] == {"a": "203.0.113.2", "ttl": 30, "shield": True}
    assert t2.devices[bot.id].attacks[0].target == "shop.example"
