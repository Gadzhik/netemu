"""Update check against the GitHub releases of NetEmu.

At start-up (unless turned off in Help → Check for updates at start-up) the program asks the
GitHub API for the latest release.  When it is newer than the running version the user is
told so and can download the package for their system in the browser, postpone, or skip that
version.  Only an anonymous HTTPS request is made; nothing is sent about the user."""
from __future__ import annotations

import json
import platform
import re

from PySide6.QtCore import QObject, QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMessageBox

from .. import __version__
from ..i18n import tr

REPO = "Gadzhik/netemu"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"


def version_tuple(text: str) -> tuple:
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:4]) if nums else (0,)


def is_newer(tag: str, current: str = __version__) -> bool:
    return version_tuple(tag) > version_tuple(current)


def pick_asset(release: dict, system: str | None = None) -> str | None:
    """Download URL of the package that fits this OS (installer preferred on Windows)."""
    system = (system or platform.system()).lower()
    want = {"windows": ("-setup.exe", "-windows-x64.zip"), "linux": ("-linux-x86_64.tar.gz", ".tar.gz"),
            "darwin": ("-macos.zip",)}.get(system, ())
    assets = release.get("assets") or []
    for suffix in want:
        for a in assets:
            if str(a.get("name", "")).endswith(suffix):
                return a.get("browser_download_url")
    return None


class UpdateChecker(QObject):
    found = Signal(str, str)          # version, download URL (or the releases page)
    upToDate = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.net = QNetworkAccessManager(self)
        self._manual = False

    @staticmethod
    def enabled() -> bool:
        return str(QSettings().value("check_updates", "true")).lower() != "false"

    @staticmethod
    def set_enabled(on: bool) -> None:
        QSettings().setValue("check_updates", "true" if on else "false")

    def check(self, manual: bool = False) -> None:
        self._manual = manual
        req = QNetworkRequest(QUrl(API_URL))
        req.setRawHeader(b"User-Agent", b"NetEmu-update-check")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        reply = self.net.get(req)
        reply.finished.connect(lambda r=reply: self._finished(r))

    def _finished(self, reply) -> None:
        try:
            if reply.error() != QNetworkReply.NoError:
                self.failed.emit(reply.errorString())
                return
            self.handle(json.loads(bytes(reply.readAll()).decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as ex:
            self.failed.emit(str(ex))
        finally:
            reply.deleteLater()

    def handle(self, release: dict) -> None:
        tag = str(release.get("tag_name", ""))
        skipped = str(QSettings().value("skip_version", ""))
        if tag and is_newer(tag) and (self._manual or tag != skipped):
            self.found.emit(tag.lstrip("v"), pick_asset(release) or release.get("html_url") or RELEASES_URL)
        else:
            self.upToDate.emit()


def install(window, auto: bool = True) -> UpdateChecker:
    """Wire the checker into the main window: start-up check and the Help menu actions."""
    checker = UpdateChecker(window)

    def found(version, url):
        box = QMessageBox(window)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(tr("Обновление NetEmu"))
        box.setText(tr("Доступна новая версия NetEmu {new} (у вас {cur}).", new=version, cur=__version__))
        box.setInformativeText(tr("Скачать её сейчас? Файл для вашей системы откроется в браузере."))
        dl = box.addButton(tr("Скачать"), QMessageBox.AcceptRole)
        box.addButton(tr("Позже"), QMessageBox.RejectRole)
        skip = box.addButton(tr("Пропустить эту версию"), QMessageBox.DestructiveRole)
        window.statusBar().showMessage(tr("Доступна новая версия NetEmu {new}", new=version), 15000)
        if getattr(window, "_update_silent", False):
            window._update_offer = (version, url)
            return
        box.exec()
        if box.clickedButton() is dl:
            QDesktopServices.openUrl(QUrl(url))
        elif box.clickedButton() is skip:
            QSettings().setValue("skip_version", "v" + version)

    def up_to_date():
        if checker._manual:
            QMessageBox.information(window, tr("Обновление NetEmu"),
                                    tr("У вас последняя версия NetEmu ({cur}).", cur=__version__))

    def failed(err):
        if checker._manual:
            QMessageBox.warning(window, tr("Обновление NetEmu"), tr("Не удалось проверить обновления:\n{err}", err=err))

    checker.found.connect(found)
    checker.upToDate.connect(up_to_date)
    checker.failed.connect(failed)
    if auto and UpdateChecker.enabled():
        QTimer.singleShot(4000, lambda: checker.check(False))
    return checker
