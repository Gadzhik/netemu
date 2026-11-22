"""Classroom mode: a teacher hands one lab to every student over the LAN and watches progress.

The teacher's copy of NetEmu holds the roster; every student works on their own copy of the
lab (the assignment is the full project file).  Each time a student runs the checks, the results
go to the teacher.  The transport (Qt sockets) lives in the GUI; this module is the data model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape


@dataclass
class Student:
    name: str
    connected: bool = True
    passed: int = 0
    total: int = 0
    results: list = field(default_factory=list)       # [[check text, ok, detail]]
    updated: str = ""
    alerts: int = 0                                   # live SIEM alerts (attack-and-defend mode)
    critical: int = 0

    @property
    def percent(self) -> int:
        return round(100 * self.passed / self.total) if self.total else 0

    @property
    def status(self) -> str:
        """Attack-and-defend status: 'under attack' (critical/high alert) / 'alert' / 'defended'."""
        if self.critical:
            return "critical"
        if self.alerts:
            return "alert"
        return "clear"


@dataclass
class Roster:
    lab_title: str = ""
    students: dict = field(default_factory=dict)      # name -> Student

    def join(self, name: str) -> Student:
        base, n, nm = name.strip() or "Student", 2, name.strip() or "Student"
        while nm in self.students and self.students[nm].connected:
            nm = f"{base} ({n})"
            n += 1
        st = self.students.get(nm) or Student(nm)
        st.connected = True
        self.students[nm] = st
        return st

    def leave(self, name: str) -> None:
        if name in self.students:
            self.students[name].connected = False

    def progress(self, name: str, results: list) -> None:
        st = self.students.setdefault(name, Student(name))
        st.results = [list(r) for r in results]
        st.total = len(results)
        st.passed = sum(1 for r in results if r[1])
        st.updated = datetime.now().strftime("%H:%M:%S")

    def alerts(self, name: str, total: int, critical: int) -> None:
        st = self.students.setdefault(name, Student(name))
        st.alerts = int(total)
        st.critical = int(critical)

    def report_html(self) -> str:
        rows = []
        for st in sorted(self.students.values(), key=lambda s: s.name.lower()):
            failed = ", ".join(escape(r[0]) for r in st.results if not r[1]) or "—"
            rows.append(f"<tr><td>{escape(st.name)}</td><td>{st.passed} / {st.total}</td><td>{st.percent}%</td>"
                        f"<td>{escape(st.updated)}</td><td>{failed}</td></tr>")
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(self.lab_title)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:32px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px 10px;text-align:left}}th{{background:#f0f3f7}}</style></head><body>
<h1>{escape(self.lab_title)}</h1><p>{datetime.now():%Y-%m-%d %H:%M}</p>
<table><tr><th>Student</th><th>Checks</th><th>%</th><th>Last check</th><th>Not done yet</th></tr>
{''.join(rows)}</table></body></html>"""
