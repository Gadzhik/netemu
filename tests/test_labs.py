"""Ready-made labs, teacher mode (encrypted solution), planted faults and the HTML report."""
import json
import random
from pathlib import Path

import pytest

from netemu.core import persistence
from netemu.core.lab import Lab, apply_script, evaluate, parse_checks, plant_fault, record_attempt, report_html
from netemu.core.topology import Topology

LABS = sorted((Path(__file__).resolve().parents[1] / "netemu" / "resources" / "labs").glob("*.netemu"))


def test_twelve_labs_shipped():
    assert len(LABS) >= 12


@pytest.mark.parametrize("path", LABS, ids=[p.stem for p in LABS])
def test_lab_is_solvable(path):
    t = persistence.load(path)
    t.engine.run(max_time=5000)
    lab = t.lab
    assert lab.title and lab.text and lab.checks and lab.script
    assert not all(ok for _, ok, _ in evaluate(t, lab)), "the task must not be solved already"
    apply_script(t, lab.script)
    failed = [(c.text, d) for c, ok, d in evaluate(t, lab) if not ok]
    assert not failed


def test_teacher_password_hides_solution(tmp_path):
    t = persistence.load(LABS[0])
    lab = t.lab
    lab.set_password("s3cret")
    lab.faults = ["none"]
    f = tmp_path / "lab.netemu"
    persistence.save(t, f)
    raw = f.read_text(encoding="utf-8")
    assert "switchport access vlan" not in raw and "s3cret" not in raw and '"secret"' in raw
    t2 = persistence.load(f)
    lab2 = t2.lab
    assert lab2.locked and lab2.script == {} and not lab2.unlock("wrong")
    assert lab2.unlock("s3cret") and "SW1" in lab2.script and lab2.faults == ["none"]
    lab2.lock()
    assert lab2.locked and lab2.script == {}
    persistence.save(t2, f)                                  # saving while locked keeps the blob
    assert persistence.load(f).lab.unlock("s3cret")


def test_planted_fault_breaks_a_working_lab():
    t = persistence.load(next(p for p in LABS if "inter-vlan" in p.stem and "trouble" not in p.stem))
    t.engine.run(max_time=3000)
    apply_script(t, t.lab.script)
    assert all(ok for _, ok, _ in evaluate(t, t.lab))
    broken = 0
    for seed in range(6):
        t2 = persistence.topology_from_dict(persistence.topology_to_dict(t))
        t2.engine.run(max_time=3000)
        desc = plant_fault(t2, random.Random(seed))
        assert desc
        t2.engine.run(max_time=t2.engine.now + 3000)
        broken += not all(ok for _, ok, _ in evaluate(t2, t2.lab))
    assert broken >= 4


def test_attempt_report():
    lab = Lab("Test", "", parse_checks("hostname R1 R1 | named")[0])
    t = Topology()
    t.add_device("cisco-2911", "R1")
    a = record_attempt(lab, "Иванов", evaluate(t, lab))
    html = report_html(lab, a)
    assert "Иванов" in html and "0 из 1" in html and lab.attempts == [a]
    assert json.loads(json.dumps(lab.to_dict()))["attempts"][0]["student"] == "Иванов"


def test_planted_fault_always_breaks_a_check_when_the_lab_is_given():
    """A fault that no check notices (a server port nobody tests) makes an unsolvable troubleshooting task."""
    for stem in ("02-inter-vlan", "04-ospf", "19-exam-office"):
        t = persistence.load(next(p for p in LABS if p.stem == stem))
        t.engine.run(max_time=3000)
        apply_script(t, t.lab.script)
        assert all(ok for _, ok, _ in evaluate(t, t.lab))
        for seed in range(5):
            t2 = persistence.topology_from_dict(persistence.topology_to_dict(t))
            t2.engine.run(max_time=3000)
            desc = plant_fault(t2, random.Random(seed), lab=t2.lab)
            assert desc
            failed = [c.text for c, ok, _ in evaluate(t2, t2.lab) if not ok]
            assert failed, f"{stem} seed {seed}: '{desc}' broke no check"
