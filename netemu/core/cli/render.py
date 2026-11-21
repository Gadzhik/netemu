"""Small formatting helpers shared by the dialects."""
from __future__ import annotations

from datetime import datetime, timedelta

_BOOT = datetime.now().replace(microsecond=0)


def sim_clock(now_ms: float) -> datetime:
    return _BOOT + timedelta(milliseconds=now_ms)


def uptime_text(now_ms: float) -> str:
    secs = int(now_ms // 1000)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    mins, secs = divmod(secs, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    if not days and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return ", ".join(parts)


def hms(now_ms: float) -> str:
    secs = int(now_ms // 1000)
    h, secs = divmod(secs, 3600)
    m, s = divmod(secs, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def table(headers: list[str], rows: list[list[str]], gap: int = 2) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    fmt = lambda r: (" " * gap).join(f"{str(c):<{widths[i]}}" for i, c in enumerate(r)).rstrip()
    return "\n".join([fmt(headers)] + [fmt(r) for r in rows])
