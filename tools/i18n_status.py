"""Report translation coverage: strings of netemu/i18n.py EN missing from i18n_<code>.py (they fall back to English).

    python tools/i18n_status.py          # summary
    python tools/i18n_status.py uk       # list the missing Russian keys for Ukrainian
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netemu import i18n  # noqa: E402


def main(argv):
    for code in i18n.LANGUAGES:
        if code in ("ru", "en"):
            continue
        t = i18n.table(code)
        missing = [k for k in i18n.EN if k not in t]
        print(f"{code}: {len(i18n.EN) - len(missing)}/{len(i18n.EN)} translated")
        if code in argv:
            for k in missing:
                print("   ", repr(k))


if __name__ == "__main__":
    main(sys.argv[1:])
