"""Render the README screenshots (English interface) into docs/images/.

    python tools/screenshots.py

Runs Qt off screen with the Windows fonts, so no window appears."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = ROOT / "docs" / "images"


def pump(app, n=5):
    for _ in range(n):
        app.processEvents()


def narrow(w, dock, width, app) -> None:
    from PySide6.QtCore import Qt
    pump(app)
    w.resizeDocks([w.palette_dock, dock], [230, width], Qt.Horizontal)
    pump(app)


def main() -> None:
    app = QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    from netemu.i18n import set_language
    set_language("en")
    from netemu.gui import theme
    from netemu.gui.controller import SIMULATION
    from netemu.gui.lab_panel import builtin_labs
    from netemu.gui.main_window import MainWindow
    theme.apply_theme("light")
    OUT.mkdir(parents=True, exist_ok=True)

    w = MainWindow()
    w.resize(1280, 800)
    w.show()

    # 1. multi-vendor demo + Cisco CLI
    w.load_demo()
    w.center_view()
    pump(app)
    w.grab().save(str(OUT / "main.png"))
    r1 = next(d for d in w.topology.devices.values() if d.name == "R1")
    dlg = w.open_device(r1)
    dlg.resize(820, 560)
    dlg.tabs.setCurrentWidget(dlg.terminal.parentWidget())
    w.topology.engine.run(max_time=w.topology.engine.now + 60000)
    for line in ("enable", "show ip interface brief", "show ip route"):
        dlg.terminal.run_command(line)
    pump(app)
    dlg.grab().save(str(OUT / "cli.png"))
    dlg.close()

    # 2. simulation mode: a ping in flight
    w.ctl.set_mode(SIMULATION)
    pc0 = next(d for d in w.topology.devices.values() if d.name == "PC0")
    pc0.create_cli().execute("ping -n 1 192.168.2.10", lambda s: None)
    for _ in range(9):
        w.sim_panel.step()
        pump(app, 2)
    narrow(w, w.sim_dock, 470, app)
    w.grab().save(str(OUT / "simulation.png"))
    w.ctl.set_mode("realtime")

    # 3. ready-made lab, teacher mode, checks run
    path = next(p for _t, p, _k in builtin_labs() if "inter-vlan" in p.name and "trouble" not in p.name)
    w._saved_snapshot = w._snapshot()
    w.open_builtin_lab(path)
    w.toggle_teacher(True)
    w.apply_solution()
    w.statusBar().clearMessage()
    narrow(w, w.lab_dock, 380, app)
    w.center_view()
    pump(app)
    w.grab().save(str(OUT / "lab.png"))
    w.toggle_teacher(False)

    # 4. dark theme on the big example
    w._saved_snapshot = w._snapshot()
    w.open_file(ROOT / "examples" / "complex-lab.netemu")
    w.lab_dock.hide()
    theme.apply_theme("dark")
    w.center_view()
    w.view.zoom(1.7)
    w.statusBar().clearMessage()
    pump(app)
    w.grab().save(str(OUT / "dark.png"))
    theme.apply_theme("light")
    w._saved_snapshot = w._snapshot()
    w.shutdown()
    print("screenshots:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
