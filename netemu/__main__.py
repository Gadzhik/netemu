"""Entry point: ``python -m netemu [project.netemu]``."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QLocale, QSettings
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow
    from .i18n import set_language

    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("NetEmu")
    app.setOrganizationName("NetEmu")
    app.setStyle("Fusion")
    from .gui.icons import app_icon
    app.setWindowIcon(app_icon())
    # --lang ru|en overrides the saved choice; first start follows the system locale
    lang = None
    if "--lang" in argv:
        i = argv.index("--lang")
        lang = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    lang = lang or QSettings().value("language") or         ("ru" if QLocale.system().language() in (QLocale.Russian, QLocale.Ukrainian, QLocale.Belarusian) else "en")
    set_language(str(lang))
    from .gui import theme
    if "--self-test" in argv:                     # packaged-build check (CI): English UI, light theme
        i = argv.index("--self-test")
        out = argv[i + 1] if i + 1 < len(argv) else "selftest"
        set_language("en")
        theme.apply_theme("light")
        from .gui.selftest import run as self_test
        return self_test(app, out)
    theme.load_settings()
    win = MainWindow()
    from .gui.updater import install as install_updater
    win.updater = install_updater(win)               # start-up check for a newer release on GitHub
    if len(argv) > 1:
        win.open_file(argv[1])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
