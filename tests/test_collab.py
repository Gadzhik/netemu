"""LAN collaboration core: two topologies kept in step by snapshot diffs."""
from cli_helpers import run_all
from netemu.core.collab import SyncPeer, decode, encode
from netemu.core.persistence import topology_to_dict
from netemu.core.topology import Topology


def _wire(ops):
    return decode(encode(ops, "x"))[1]           # through the JSON line format


def _same(a, b):
    da, db = topology_to_dict(a), topology_to_dict(b)
    strip = lambda d: sorted((x["id"], x["name"], x["model"], x["x"], x["y"]) for x in d["devices"])
    return strip(da) == strip(db) and len(da["links"]) == len(db["links"])


def test_two_peers_converge_and_stay_quiet():
    ta, tb = Topology(), Topology()
    r = ta.add_device("cisco-2911", "R1", 100, 100)
    pa, pb = SyncPeer(ta), SyncPeer(tb)
    pb.receive(_wire(pa.start_from_local()))
    assert _same(ta, tb)

    # A adds a PC with DHCP, cables it and configures the router through the CLI
    pc = ta.add_device("pc", "PC1", 300, 100)
    ta.connect(pc.nic, r.get_port("gi0/0"))
    run_all(r.create_cli(), "enable\nconfigure terminal\nhostname EDGE\ninterface gi0/0\n"
                            "ip address 192.168.1.1 255.255.255.0\nno shutdown\nexit\nip dhcp pool LAN\n"
                            "network 192.168.1.0 255.255.255.0\ndefault-router 192.168.1.1\nend")
    pc.start_dhcp()
    ops = pa.local_ops()
    kinds = {o["op"] for o in ops}
    assert {"add", "link", "config"} <= kinds
    pb.receive(_wire(ops))
    assert _same(ta, tb)
    rb = tb.devices[r.id]
    assert rb.hostname == "EDGE" and str(rb.get_port("gi0/0").ip.ip) == "192.168.1.1"

    # both simulations run on their own: no changes bounce back and forth
    for t in (ta, tb):
        t.engine.run(max_time=t.engine.now + 10000)
    assert tb.devices[pc.id].nic.ip is not None              # B's own DHCP worked
    assert pa.local_ops() == [] and pb.local_ops() == []

    # B moves and renames, A sees it; B deletes the PC
    tb.devices[pc.id].x = 500
    tb.engine.emit("device_moved", tb.devices[pc.id])
    tb.rename(tb.devices[pc.id], "Office-PC")
    pa.receive(_wire(pb.local_ops()))
    assert ta.devices[pc.id].x == 500 and ta.devices[pc.id].name == "Office-PC"
    tb.remove_device(tb.devices[pc.id])
    pa.receive(_wire(pb.local_ops()))
    assert pc.id not in ta.devices and not ta.links


def test_local_edits_survive_a_remote_update():
    ta, tb = Topology(), Topology()
    pa, pb = SyncPeer(ta), SyncPeer(tb)
    pb.receive(pa.start_from_local())
    tb.add_device("pc", "B-PC")                               # B edits...
    ta.add_device("pc", "A-PC")
    back = pb.receive(pa.local_ops())                         # ...while A's update arrives
    assert any(o["op"] == "add" and o["dev"]["name"] == "B-PC" for o in back)
    pa.receive(back)
    assert {d.name for d in ta.devices.values()} == {d.name for d in tb.devices.values()} == {"A-PC", "B-PC"}
