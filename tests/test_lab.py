"""Lab assignments: parsing, automatic checks, persistence, GUI panel."""
from cli_helpers import run_all
from netemu.core.lab import Lab, evaluate, parse_checks
from netemu.core.persistence import topology_from_dict, topology_to_dict
from netemu.core.topology import Topology

CHECKS = """# comment
ping PC0 192.168.2.10 | PC0 reaches PC1
route R1 192.168.2.0/24 | R1 knows the far LAN
hostname R1 R1 | the router is named R1
ospf R1 1 | R1 has an OSPF neighbour"""


def lab_topology():
    t = Topology()
    r1, r2 = t.add_device("cisco-2911", "R1"), t.add_device("cisco-2911", "R2")
    a, b = t.add_device("pc", "PC0"), t.add_device("pc", "PC1")
    t.connect(a.nic, r1.get_port("gi0/0"))
    t.connect(r1.get_port("gi0/1"), r2.get_port("gi0/1"))
    t.connect(b.nic, r2.get_port("gi0/0"))
    for r, lan, wan in ((r1, "192.168.1.1", "10.0.0.1"), (r2, "192.168.2.1", "10.0.0.2")):
        run_all(r.create_cli(), f"enable\nconfigure terminal\nhostname {r.name}\ninterface gi0/0\n"
                                f"ip address {lan} 255.255.255.0\nno shutdown\ninterface gi0/1\n"
                                f"ip address {wan} 255.255.255.252\nno shutdown\nend")
    a.set_ip("192.168.1.10", "255.255.255.0")
    a.set_gateway("192.168.1.1")
    b.set_ip("192.168.2.10", "255.255.255.0")
    b.set_gateway("192.168.2.1")
    checks, errors = parse_checks(CHECKS)
    assert not errors and len(checks) == 4
    t.lab = Lab("OSPF lab", "Configure OSPF so that PC0 reaches PC1.", checks)
    return t, r1, r2


def test_checks_fail_then_pass_after_solution():
    t, r1, r2 = lab_topology()
    res = evaluate(t, t.lab)
    assert [ok for _, ok, _ in res] == [False, False, True, False]
    for r in (r1, r2):
        run_all(r.create_cli(), "enable\nconfigure terminal\nrouter ospf 1\nnetwork 0.0.0.0 255.255.255.255 area 0\nend")
    t.engine.run(max_time=45000)
    assert all(ok for _, ok, _ in evaluate(t, t.lab))


def test_lab_parsing_errors_and_persistence():
    assert parse_checks("pong A B | x\nping PC0 | y")[1] == ["1: unknown check 'pong'", "2: 'ping' needs 2 argument(s)"]
    t, r1, r2 = lab_topology()
    t2 = topology_from_dict(topology_to_dict(t), Topology())
    assert t2.lab.title == "OSPF lab" and len(t2.lab.checks) == 4
