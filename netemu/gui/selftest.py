"""Self-test of an installed / packaged NetEmu:  NetEmu --self-test <output folder>

Runs on the real windowing system of the OS (used by CI on Windows, Linux under Xvfb and macOS):
opens the multi-vendor demo, lets the network converge, pings across all vendors from a PC
command line, opens a router CLI, saves screenshots and report.json, then exits with 0 when
everything worked and 1 otherwise."""
from __future__ import annotations

import json
import platform
import sys
import traceback
from pathlib import Path


def run(app, out_dir: str) -> int:
    from PySide6.QtCore import QTimer

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = {"platform": platform.platform(), "python": sys.version.split()[0], "qt_platform": app.platformName(),
              "checks": {}, "ok": False}
    state = {"code": 1}

    def check(name, ok, detail=""):
        report["checks"][name] = {"ok": bool(ok), "detail": str(detail)}

    def step():
        try:
            from .. import __version__
            from .main_window import MainWindow
            report["version"] = __version__
            w = MainWindow()
            w.resize(1280, 800)
            w.show()
            app.processEvents()
            check("window", w.isVisible())
            w.load_demo()
            w.center_view()
            eng = w.topology.engine
            eng.run(max_time=60000)                       # OSPF/STP converge (simulated minute)
            check("demo_loaded", len(w.topology.devices) >= 8, f"{len(w.topology.devices)} devices")
            pc0 = next(d for d in w.topology.devices.values() if d.name == "PC0")
            buf = []
            job = pc0.create_cli().execute("ping 192.168.2.10", buf.append)
            if job is not None:
                eng.run_until(lambda: job.done)
            text = "".join(buf)
            check("ping_across_vendors", "Received = 4" in text, text.strip().splitlines()[-1] if text else "")
            r1 = next(d for d in w.topology.devices.values() if d.name == "R1")
            dlg = w.open_device(r1)
            dlg.tabs.setCurrentWidget(dlg.terminal.parentWidget())
            for line in ("enable", "show ip route"):
                dlg.terminal.run_command(line)
            app.processEvents()
            check("router_cli", "Gateway of last resort" in dlg.terminal.toPlainText())
            ok_main = w.grab().save(str(out / "main.png"))
            ok_cli = dlg.grab().save(str(out / "cli.png"))
            check("screenshots", ok_main and ok_cli)
            dlg.close()
            w._saved_snapshot = w._snapshot()
            w.shutdown()
            w.close()
            report["ok"] = all(c["ok"] for c in report["checks"].values())
            state["code"] = 0 if report["ok"] else 1
        except Exception:                                            # report, never hang CI
            report["error"] = traceback.format_exc()
            state["code"] = 1
        finally:
            (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
            app.exit(state["code"])

    QTimer.singleShot(300, step)
    QTimer.singleShot(120000, lambda: app.exit(2))                   # watchdog
    return app.exec()
