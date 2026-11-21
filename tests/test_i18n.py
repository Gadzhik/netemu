"""UI translations: placeholders survive, no stale keys, fallback to English, language switch."""
import re

import pytest

from netemu import i18n

PH = re.compile(r"\{[^}]*\}|%[vm]")
EXTRA = [c for c in i18n.LANGUAGES if c not in ("ru", "en")]


@pytest.mark.parametrize("code", EXTRA)
def test_translation_table(code):
    t = i18n.table(code)
    assert len(t) >= 0.9 * len(i18n.EN)
    for k, v in t.items():
        assert k in i18n.EN, f"stale key: {k!r}"
        assert sorted(PH.findall(k)) == sorted(PH.findall(v)), k
        assert k.count("\n") == v.count("\n"), k
        assert v.strip(), k


def test_fallback_and_switch():
    try:
        i18n.set_language("uk")
        assert i18n.tr("&Файл") == "&Файл" and i18n.tr("Пристрої") == "Пристрої"
        assert i18n.tr("Устройства") == "Пристрої"
        assert i18n.tr("Нет такого ключа {x}", x=1) == "Нет такого ключа 1"
        assert i18n.content_language() == "en"
        i18n.set_language("kk")
        assert i18n.tr("Устройства") == "Құрылғылар" and i18n.tr("Сохранено в {path}", path="a") == "Сақталды: a"
        i18n.set_language("xx")
        assert i18n.language() == "ru" and i18n.content_language() == "ru"
    finally:
        i18n.set_language("ru")


def test_ukrainian_menus(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    i18n.set_language("uk")
    try:
        from netemu.gui.main_window import MainWindow
        w = MainWindow()
        qtbot.addWidget(w)
        titles = [a.text() for a in w.menuBar().actions()]
        assert "&Інструменти" in titles and "&Довідка" in titles
        w.shutdown()
    finally:
        i18n.set_language("ru")
