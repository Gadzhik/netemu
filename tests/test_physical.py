"""Physical layer: placement-based cable lengths, copper limit, fiber, persistence and the panel."""
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology
from test_vlan import ping as _ping


def test_copper_between_buildings_needs_fiber():
    t = Topology()
    a, b = t.add_device("pc"), t.add_device("pc")
    link = t.connect(a.nic, b.nic)
    a.set_ip("10.0.0.1", "255.255.255.0")
    b.set_ip("10.0.0.2", "255.255.255.0")
    a.location, b.location = "HQ / Floor 1 / Rack 1", "HQ / Floor 1 / Rack 1"
    assert link.length == 2 and a.nic.is_up
    b.location = "Warehouse / Floor 1"
    link.set_physical()
    assert link.length == 300 and link.too_long and not a.nic.is_up
    assert _ping(None, a, "10.0.0.2").received == 0
    link.set_physical("fiber")
    assert a.nic.is_up and _ping(None, a, "10.0.0.2").received == 4
    link.set_physical("copper", 90)                       # measured length overrides the estimate
    assert a.nic.is_up
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    l2 = t2.links[0]
    assert l2.length_m == 90 and l2.media == "copper" and t2.devices[b.id].location == "Warehouse / Floor 1"


def test_physical_panel(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from netemu.gui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    w = MainWindow()
    qtbot.addWidget(w)
    a, b = w.topology.add_device("pc", "A"), w.topology.add_device("pc", "B")
    w.topology.connect(a.nic, b.nic)
    from netemu.gui.physical_panel import set_location
    set_location(w.topology, a, "HQ / F1 / R1")
    set_location(w.topology, b, "Plant / F1")
    p = w.show_physical()
    p.refresh()
    assert p.tree.topLevelItemCount() == 2 and p.cables.rowCount() == 1
    assert "100" in p.cables.cellWidget(0, 1).currentText() and p.cables.item(0, 3).foreground().color().name() == "#ff0000"
    w.shutdown()
