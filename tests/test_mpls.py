"""MPLS/LDP on Cisco: LDP sessions, label bindings, LFIB, labelled forwarding with PHP."""
from cli_helpers import run, run_all
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def build():
    """PE1 - P1 - P2 - PE2 in a line, OSPF everywhere, LDP on the core links; LANs behind the PEs."""
    t = Topology()
    rs = [t.add_device("cisco-2911", n) for n in ("PE1", "P1", "P2", "PE2")]
    a, b = t.add_device("pc"), t.add_device("pc")
    for i in range(3):
        t.connect(rs[i].get_port("gi0/1"), rs[i + 1].get_port("gi0/2"))
    t.connect(a.nic, rs[0].get_port("gi0/0"))
    t.connect(b.nic, rs[3].get_port("gi0/0"))
    for i, r in enumerate(rs, 1):
        lines = ["enable", "configure terminal", f"hostname {r.name}", "mpls label protocol ldp",
                 "interface loopback0", f"ip address {i}.{i}.{i}.{i} 255.255.255.255"]
        if i < 4:
            lines += ["interface gi0/1", f"ip address 10.0.{i}{i + 1}.1 255.255.255.252", "mpls ip", "no shutdown"]
        if i > 1:
            lines += ["interface gi0/2", f"ip address 10.0.{i - 1}{i}.2 255.255.255.252", "mpls ip", "no shutdown"]
        if i in (1, 4):
            lines += ["interface gi0/0", f"ip address 192.168.{i}.1 255.255.255.0", "no shutdown"]
        lines += ["exit", "mpls ldp router-id loopback0 force", "router ospf 1",
                  "network 0.0.0.0 255.255.255.255 area 0", "end"]
        out = run_all(r.create_cli(), "\n".join(lines))
        assert "Invalid" not in out, out
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.4.10", "255.255.255.0")
    b.set_gateway("192.168.4.1")
    t.engine.run(max_time=60000)
    return t, rs, a, b


def test_ldp_labels_and_label_switching():
    t, (pe1, p1, p2, pe2), a, b = build()
    s = p1.create_cli()
    run(s, "enable")
    nb = run(s, "show mpls ldp neighbor")
    assert "Peer LDP Ident: 1.1.1.1:0" in nb and "Peer LDP Ident: 3.3.3.3:0" in nb
    lfib = run(s, "show mpls forwarding-table")
    assert "192.168.4.0/24" in lfib and "4.4.4.4/32" in lfib
    # the router before PE2 pops (PHP) for PE2's prefixes
    assert "Pop Label" in run(p2.create_cli(), "enable") + run(_en(p2), "show mpls forwarding-table")
    assert "remote binding" in run(s, "show mpls ldp bindings")
    before = p1.mpls.switched + p2.mpls.switched
    assert _ping(None, a, "192.168.4.10").received == 4
    assert p1.mpls.switched + p2.mpls.switched > before                # really label-switched in the core
    rc = run(s, "show running-config")
    assert "mpls label protocol ldp" in rc and " mpls ip" in rc
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    t2.engine.run(max_time=60000)
    a2 = t2.devices[a.id]
    assert _ping(None, a2, "192.168.4.10").received == 4


def _en(r):
    s = r.create_cli()
    run(s, "enable")
    return s
