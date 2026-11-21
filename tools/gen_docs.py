"""Refresh the generated command lists in docs/<lang>/*.md.

Run after changing any CLI dialect:  python tools/gen_docs.py
(tests/test_docs.py fails while the docs are out of date)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from netemu.core.cli.reference import DOC_VENDORS, refresh_doc  # noqa: E402


def main() -> int:
    changed = 0
    for lang_dir in sorted((ROOT / "docs").iterdir()):
        if not lang_dir.is_dir():
            continue
        for name, vendor in DOC_VENDORS.items():
            path = lang_dir / name
            old = path.read_text(encoding="utf-8")
            new = refresh_doc(old, vendor, lang_dir.name)
            if new != old:
                path.write_text(new, encoding="utf-8", newline="\n")
                changed += 1
                print("updated", path.relative_to(ROOT))
    print(f"{changed} file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
