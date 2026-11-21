"""GRE and IPsec site-to-site tunnels across the Internet cloud."""
import pytest

from cli_helpers import run, run_all
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def ping(dev, dst):
    return _ping(None, dev, dst).received


# per vendor: (wan port, lan port, config(i, peer_wan, isp_gw, key))
CFG = {
    "cisco-2911": ("gi0/1", "gi0/0", lambda i, me, peer, gw, key: (
        "enable\nconfigure terminal\ninterface gi0/0\n"
        f"ip address 192.168.{i}.1 255.255.255.0\nno shutdown\ninterface gi0/1\nip address {me} 255.255.255.252\n"
        f"no shutdown\nexit\nip route 0.0.0.0 0.0.0.0 {gw}\n"
        + (f"crypto isakmp policy 10\nencryption aes 256\nauthentication pre-share\ngroup 14\nexit\n"
           f"crypto isakmp key {key} address {peer}\ncrypto ipsec transform-set TS esp-aes 256 esp-sha-hmac\nexit\n"
           "crypto ipsec profile VPN\nset transform-set TS\nexit\n" if key else "")
        + f"interface tunnel 0\nip address 10.255.0.{i} 255.255.255.252\ntunnel source gi0/1\ntunnel destination {peer}\n"
        + ("tunnel protection ipsec profile VPN\n" if key else "")
        + f"exit\nip route 192.168.{3 - i}.0 255.255.255.0 Tunnel0\nend")),
    "mikrotik-rb4011": ("ether2", "ether1", lambda i, me, peer, gw, key: (
        f"/ip address add address=192.168.{i}.1/24 interface=ether1\n/ip address add address={me}/30 interface=ether2\n"
        f"/ip route add dst-address=0.0.0.0/0 gateway={gw}\n"
        f"/interface gre add name=gre1 local-address={me} remote-address={peer}" + (f" ipsec-secret={key}" if key else "")
        + f"\n/ip address add address=10.255.0.{i}/30 interface=gre1\n"
        f"/ip route add dst-address=192.168.{3 - i}.0/24 gateway=10.255.0.{3 - i}")),
    "huawei-ar2220": ("GE0/0/1", "GE0/0/0", lambda i, me, peer, gw, key: (
        f"system-view\ninterface GigabitEthernet0/0/0\nip address 192.168.{i}.1 24\ninterface GigabitEthernet0/0/1\n"
        f"ip address {me} 30\nquit\nip route-static 0.0.0.0 0 {gw}\ninterface Tunnel0/0/0\ntunnel-protocol gre\n"
        f"source {me}\ndestination {peer}\nip address 10.255.0.{i} 30\nquit\n"
        f"ip route-static 192.168.{3 - i}.0 24 Tunnel0/0/0\nreturn")),
    "juniper-srx300": ("ge-0/0/1", "ge-0/0/0", lambda i, me, peer, gw, key: (
        f"configure\nset interfaces ge-0/0/0 unit 0 family inet address 192.168.{i}.1/24\n"
        f"set interfaces ge-0/0/1 unit 0 family inet address {me}/30\n"
        f"set routing-options static route 0.0.0.0/0 next-hop {gw}\n"
        f"set interfaces gr-0/0/0 unit 0 tunnel source {me} destination {peer}\n"
        f"set interfaces gr-0/0/0 unit 0 family inet address 10.255.0.{i}/30\n"
        f"set routing-options static route 192.168.{3 - i}.0/24 next-hop 10.255.0.{3 - i}\ncommit and-quit")),
}


def build(model, key=None, key2=None):
    wan, lan, cfg = CFG[model]
    t = Topology()
    inet = t.add_device("internet")
    r1, r2 = t.add_device(model), t.add_device(model)
    a, b = t.add_device("pc"), t.add_device("pc")
    t.connect(r1.get_port(wan), inet.get_port("ISP0"))
    t.connect(r2.get_port(wan), inet.get_port("ISP1"))
    t.connect(a.nic, r1.get_port(lan))
    t.connect(b.nic, r2.get_port(lan))
    for i, r, me, peer, gw, k in ((1, r1, "203.0.113.2", "203.0.113.6", "203.0.113.1", key),
                                  (2, r2, "203.0.113.6", "203.0.113.2", "203.0.113.5", key2 or key)):
        out = run_all(r.create_cli(), cfg(i, me, peer, gw, k))
        assert not any(e in out for e in ("Invalid", "Error", "error", "failure", "% No")), out
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")
    t.engine.run(max_time=2000)
    return t, r1, r2, a, b


@pytest.mark.parametrize("model", list(CFG))
def test_gre_between_private_lans(model):
    t, r1, r2, a, b = build(model)
    assert ping(a, "192.168.2.10") == 4
    tun = next(p for p in r1.ports.values() if p.is_tunnel and p.destination is not None)
    assert tun.counters["encaps"] >= 4 and tun.counters["decaps"] >= 4


@pytest.mark.parametrize("model", ["cisco-2911", "mikrotik-rb4011"])
def test_ipsec_protects_and_needs_matching_keys(model):
    t, r1, r2, a, b = build(model, key="S3cret!")
    assert ping(a, "192.168.2.10") == 4
    if model == "cisco-2911":
        s = r1.create_cli()
        run(s, "enable")
        assert "#pkts encaps: " in run(s, "show crypto ipsec sa") and "QM_IDLE" in run(s, "show crypto isakmp sa")
    t, r1, r2, a, b = build(model, key="S3cret!", key2="different")
    assert ping(a, "192.168.2.10") == 0


def test_ospf_over_gre():
    t, r1, r2, a, b = build("cisco-2911")
    for r in (r1, r2):
        run_all(r.create_cli(), f"enable\nconfigure terminal\nno ip route 192.168.{2 if r is r1 else 1}.0 255.255.255.0 Tunnel0\n"
                                "router ospf 1\nnetwork 192.168.0.0 0.0.255.255 area 0\nnetwork 10.255.0.0 0.0.0.3 area 0\nend")
    t.engine.run(max_time=45000)
    assert any(r.code.startswith("O") and str(r.network) == "192.168.2.0/24" for r in r1.routing_table())
    assert ping(a, "192.168.2.10") == 4
