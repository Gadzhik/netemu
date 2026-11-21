"""Dark theme and UI scale.

Kept apart from test_gui.py on purpose: QApplication.setFont() reaches every widget alive in the process,
and after the many windows the GUI tests leave behind it crashed with heap corruption on Windows +
Python 3.11 in CI. This file runs before test_gui.py, so only its own window exists."""
import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from netemu.gui.main_window import MainWindow  # noqa: E402


def test_dark_theme_and_ui_scale(qtbot, qapp, monkeypatch):
    from netemu.gui import theme
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    win = MainWindow()
    qtbot.addWidget(win)
    r = win.topology.add_device("cisco-2911")
    term = win.open_device(r).terminal
    base = qapp.font().pointSizeF()
    try:
        win.choose_theme("dark")
        assert theme.is_dark() and win.view.backgroundBrush().color().name() == theme.DARK["canvas"]
        win.choose_scale(1.5)
        assert abs(qapp.font().pointSizeF() - theme.STATE["base_pt"] * 1.5) < 0.01
        assert abs(term.font().pointSizeF() - 15) < 0.01
    finally:
        win.choose_theme("light")
        win.choose_scale(1.0)
    assert not theme.is_dark() and abs(qapp.font().pointSizeF() - base) < 0.01
    win._saved_snapshot = win._snapshot()
    win.shutdown()
