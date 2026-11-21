"""Undo / redo of canvas edits."""
from netemu.core.topology import Topology


def test_undo_redo_devices_links_moves():
    t = Topology()
    u = t.undo
    r = t.add_device("cisco-2911", "R1")
    u.commit()
    pc = t.add_device("pc", "PC0")
    u.commit()
    t.connect(pc.nic, r.get_port("gi0/0"))
    u.commit()
    r.hostname = "EDGE"
    pc.x, pc.y = 300, 40
    u.commit()
    assert len(u.undo_steps) == 4
    assert u.undo() and (pc.x, pc.y) == (0, 0)                  # move undone
    assert u.undo() and not t.links                              # cable undone
    assert u.redo() and len(t.links) == 1
    # deleting a device (with its cable) is one step and comes back configured
    t.remove_device(r)
    u.commit()
    assert "R1" not in {d.name for d in t.devices.values()}
    assert u.undo()
    r2 = t.devices[r.id]
    assert r2.hostname == "EDGE" and len(t.links) == 1 and r2.id == r.id
    assert u.redo() and len(t.devices) == 1 and not t.links
