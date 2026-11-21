"""Update check: version comparison, choosing the package for the OS, offering the download."""
import pytest

pytest.importorskip("pytestqt")

from netemu import __version__
from netemu.gui.updater import UpdateChecker, is_newer, pick_asset, version_tuple

RELEASE = {"tag_name": "v99.0.0", "html_url": "https://github.com/Gadzhik/netemu/releases/tag/v99.0.0",
           "assets": [{"name": "NetEmu-99.0.0-windows-x64.zip", "browser_download_url": "https://x/win.zip"},
                      {"name": "NetEmu-99.0.0-setup.exe", "browser_download_url": "https://x/setup.exe"},
                      {"name": "NetEmu-99.0.0-linux-x86_64.tar.gz", "browser_download_url": "https://x/linux.tgz"},
                      {"name": "NetEmu-99.0.0-macos.zip", "browser_download_url": "https://x/mac.zip"}]}


def test_versions():
    assert version_tuple("v0.3.10") == (0, 3, 10) > version_tuple("0.3.9")
    assert is_newer("v99.0.0") and not is_newer("v" + __version__) and not is_newer("v0.0.1")


def test_pick_asset_per_os():
    assert pick_asset(RELEASE, "Windows") == "https://x/setup.exe"
    assert pick_asset(RELEASE, "Linux") == "https://x/linux.tgz"
    assert pick_asset(RELEASE, "Darwin") == "https://x/mac.zip"
    assert pick_asset({"assets": []}, "Linux") is None


def test_checker_signals(qtbot, monkeypatch):
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(QSettings, "value", lambda self, k, d=None: d)
    c = UpdateChecker()
    with qtbot.waitSignal(c.found) as sig:
        c.handle(RELEASE)
    assert sig.args[0] == "99.0.0" and sig.args[1].startswith("https://x/")
    with qtbot.waitSignal(c.upToDate):
        c.handle({"tag_name": "v" + __version__})
