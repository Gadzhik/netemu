"""Hints for failed checks name the planted faults of the troubleshooting labs."""
from pathlib import Path

from netemu.core import persistence
from netemu.core.diagnose import hints_for
from netemu.core.lab import evaluate

LABS = Path(__file__).resolve().parents[1] / "netemu" / "resources" / "labs"


def failed_hints(name):
    t = persistence.load(LABS / name)
    t.engine.run(max_time=5000)
    out = []
    for c, ok, _ in evaluate(t, t.lab):
        if not ok:
            out += hints_for(t, c)
    return " | ".join(out)


def test_shutdown_interface_is_named():
    h = failed_hints("12-trouble-ospf.netemu")
    assert "shutdown" in h and "GigabitEthernet0/0" in h, h


def test_wrong_gateway_is_named():
    h = failed_hints("11-trouble-intervlan.netemu")
    assert "192.168.20.254" in h, h


def test_missing_route_and_no_ip():
    from netemu.core.topology import Topology
    from netemu.core.lab import Check
    t = Topology()
    r = t.add_device("cisco-2911", "R1")
    pc = t.add_device("pc", "PC1")
    t.connect(pc.nic, r.get_port("gi0/0"))
    assert "shutdown" in hints_for(t, Check("ping", ["PC1", "10.9.9.9"], ""))[0]
    r.get_port("gi0/0").set_admin(True)
    assert "IP" in hints_for(t, Check("ping", ["PC1", "10.9.9.9"], ""))[0]
    pc.set_ip("192.168.1.10", "255.255.255.0")
    assert "шлюз" in hints_for(t, Check("ping", ["PC1", "10.9.9.9"], ""))[0] or \
        "gateway" in hints_for(t, Check("ping", ["PC1", "10.9.9.9"], ""))[0]
