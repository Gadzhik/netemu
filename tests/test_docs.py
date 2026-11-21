"""Documentation stays true to the program: generated command lists are current,
every example in the vendor pages runs without a CLI error, and every GUI string
has an English translation."""
import re
from pathlib import Path

import pytest

from cli_helpers import run
from netemu.core.cli.reference import DOC_VENDORS, refresh_doc
from netemu.core.topology import Topology
from netemu.i18n import EN

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LANGS = ("ru", "en")
MODEL = {"cisco": "cisco-2911", "mikrotik": "mikrotik-rb4011", "juniper": "juniper-srx300",
         "huawei": "huawei-ar2220", "extreme": "extreme-x440g2", "host": "pc"}
SWITCH = {"cisco": "cisco-2960", "mikrotik": "mikrotik-crs326", "juniper": "juniper-ex2300",
          "huawei": "huawei-s5720"}
ERRORS = ("% Invalid", "% Incomplete", "% Ambiguous", "% Unknown", "syntax error", "bad command",
          "Error:", "error:", "expected end of command", "Unrecognized", "is not recognized",
          "does not match", "failure:", "Bad ")


def doc_pages():
    return [(lang, name, vendor) for lang in LANGS for name, vendor in DOC_VENDORS.items()]


@pytest.mark.parametrize("lang,name,vendor", doc_pages())
def test_generated_command_lists_are_current(lang, name, vendor):
    text = (DOCS / lang / name).read_text(encoding="utf-8")
    assert refresh_doc(text, vendor, lang) == text, "run: python tools/gen_docs.py"


def _examples(text):
    """Fenced example blocks of the hand-written part (before the generated lists)."""
    return _blocks(text, "")


def _switch_examples(text):
    """Blocks marked ```switch run on the vendor's switch model."""
    return _blocks(text, "switch")


def _blocks(text, tag):
    hand = text.split("<!-- BEGIN GENERATED")[0]
    return [b for t, b in re.findall(r"```(\w*)\n(.*?)```", hand, re.S) if t == tag]


def _check_commands(text):
    """Inline `commands` listed on the Check:/Проверка: line."""
    for line in text.splitlines():
        if line.startswith(("Check:", "Проверка:")):
            return [c for c in re.findall(r"`([^`]+)`", line) if len(c) > 1]  # skip flag letters
    return []


@pytest.mark.parametrize("lang,name,vendor", doc_pages())
def test_doc_examples_run_without_errors(lang, name, vendor):
    text = (DOCS / lang / name).read_text(encoding="utf-8")
    dev = Topology().add_device(MODEL[vendor])
    s = dev.create_cli()
    ran = 0
    for block in _examples(text):
        lines = [l.strip() for l in block.strip().splitlines()]
        if lines and lines[0].startswith("[admin@"):          # a transcript, not input
            lines = [l.split(">", 1)[1].strip() for l in lines]
        if vendor == "juniper" and lines[0].startswith("set "):
            lines = ["configure"] + lines + ["commit and-quit"]
        for line in lines:
            line = re.sub(r"\s+\(.*\)$", "", line)             # "(runs until Ctrl+C)"
            if not line or " -t " in line:
                continue
            out = run(s, line)
            ran += 1
            assert not any(e in out for e in ERRORS), f"{name}: {line!r}\n{out}"
    if vendor in SWITCH:
        sw = Topology().add_device(SWITCH[vendor])
        ss = sw.create_cli()
        for block in _switch_examples(text):
            for line in block.strip().splitlines():
                out = run(ss, line.strip())
                ran += 1
                assert not any(e in out for e in ERRORS), f"{name} (switch): {line!r}\n{out}"
    if vendor == "host":
        lx = Topology().add_device("pc-linux").create_cli()
        for block in _blocks(text, "linux"):
            for line in block.strip().splitlines():
                out = run(lx, line.strip())
                ran += 1
                assert not any(e in out for e in ERRORS + ("command not found", "not permitted", "Permission denied")), \
                    f"{name} (linux): {line!r}\n{out}"
    if vendor == "cisco":
        run(s, "enable")
    for cmd in _check_commands(text):
        out = run(s, cmd)
        ran += 1
        assert not any(e in out for e in ERRORS), f"{name}: {cmd!r}\n{out}"
    assert ran >= 5


def test_doc_examples_configure_what_they_say():
    t = Topology()
    r = t.add_device("juniper-srx300")
    s = r.create_cli()
    for block in _examples((DOCS / "en" / "juniper.md").read_text(encoding="utf-8"))[:1]:
        for line in block.strip().splitlines():
            run(s, line)
    assert r.hostname == "SRX1" and len(r.static_routes) == 2


def test_every_gui_string_has_english_translation():
    used = set()
    for path in [*(ROOT / "netemu" / "gui").glob("*.py"), ROOT / "netemu" / "core" / "diagnose.py"]:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'(?:\btr|_act)\(((?:\s*"(?:[^"\\]|\\.)*"\s*)+)', src):
            parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
            used.add("".join(parts).encode().decode("unicode_escape").encode("latin-1").decode("utf-8"))
    assert len(used) > 100                               # the scan really finds the GUI strings
    missing = sorted(u for u in used if u not in EN and re.search("[А-Яа-яЁё]", u))
    assert not missing, missing


def test_index_links_exist():
    for lang in LANGS:
        for name in DOC_VENDORS:
            assert (DOCS / lang / name).exists()
        assert (DOCS / lang / "guide.md").exists()
