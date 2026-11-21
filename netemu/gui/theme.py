"""Light / dark theme and interface scale (for projectors and high-DPI screens).

The choice is stored with QSettings ("theme": light | dark | system, "ui_scale": 1.0..2.0)
and applied to the whole application: Qt palette, the base font size, and the colours the
canvas items draw with (read from ``COLORS`` at paint time, so a change is live)."""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

THEMES = {"light": "Светлая", "dark": "Тёмная", "system": "Как в системе"}
SCALES = (1.0, 1.25, 1.5, 1.75, 2.0)

LIGHT = {"canvas": "#fafafa", "label": "#212121", "port_label": "#546e7a", "cable": "#263238",
         "cable_sel": "#1e88e5", "frame_text": "#37474f"}
DARK = {"canvas": "#1e2329", "label": "#e6e9ec", "port_label": "#9fb3c0", "cable": "#b0bec5",
        "cable_sel": "#64b5f6", "frame_text": "#cfd8dc"}
COLORS: dict[str, str] = dict(LIGHT)
STATE = {"theme": "light", "scale": 1.0, "base_pt": None}


def color(key: str) -> QColor:
    return QColor(COLORS[key])


def is_dark() -> bool:
    return COLORS["canvas"] == DARK["canvas"]


def _system_dark() -> bool:
    app = QApplication.instance()
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except (AttributeError, RuntimeError):
        return False


def _dark_palette() -> QPalette:
    p = QPalette()
    base, alt, text, mid = QColor("#1e2329"), QColor("#262c33"), QColor("#e6e9ec"), QColor("#2d343c")
    for role, c in ((QPalette.Window, QColor("#23292f")), (QPalette.WindowText, text), (QPalette.Base, base),
                    (QPalette.AlternateBase, alt), (QPalette.ToolTipBase, QColor("#2d343c")),
                    (QPalette.ToolTipText, text), (QPalette.Text, text), (QPalette.Button, mid),
                    (QPalette.ButtonText, text), (QPalette.BrightText, QColor("#ff5252")),
                    (QPalette.Highlight, QColor("#2f74c0")), (QPalette.HighlightedText, QColor("#ffffff")),
                    (QPalette.Link, QColor("#64b5f6")), (QPalette.PlaceholderText, QColor("#8a949e"))):
        p.setColor(role, c)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6b747c"))
    return p


def apply_theme(name: str) -> None:
    app = QApplication.instance()
    STATE["theme"] = name if name in THEMES else "light"
    dark = name == "dark" or (name == "system" and _system_dark())
    COLORS.clear()
    COLORS.update(DARK if dark else LIGHT)
    if app is not None:
        app.setPalette(_dark_palette() if dark else app.style().standardPalette())
        _refresh_views(app)


def apply_scale(factor: float) -> None:
    app = QApplication.instance()
    factor = min(max(float(factor), 0.75), 2.5)
    STATE["scale"] = factor
    if app is None:
        return
    if STATE["base_pt"] is None:
        STATE["base_pt"] = app.font().pointSizeF() if app.font().pointSizeF() > 0 else 9.0
    f = QFont(app.font())
    f.setPointSizeF(STATE["base_pt"] * factor)
    app.setFont(f)
    from .terminal import TerminalWidget
    for w in app.allWidgets():
        if isinstance(w, TerminalWidget):          # the rest follows app.setFont() by itself
            w.apply_scale()
    _refresh_views(app)


def _refresh_views(app) -> None:
    from PySide6.QtWidgets import QGraphicsView
    for w in app.allWidgets():
        if isinstance(w, QGraphicsView):
            w.setBackgroundBrush(color("canvas"))
            if w.scene() is not None:
                for it in w.scene().items():
                    if hasattr(it, "apply_scale"):
                        it.apply_scale()
                w.scene().update()
            w.viewport().update()


def load_settings() -> None:
    s = QSettings()
    apply_theme(str(s.value("theme", "light")))
    try:
        apply_scale(float(s.value("ui_scale", 1.0)))
    except (TypeError, ValueError):
        apply_scale(1.0)


def save_settings() -> None:
    s = QSettings()
    s.setValue("theme", STATE["theme"])
    s.setValue("ui_scale", STATE["scale"])
