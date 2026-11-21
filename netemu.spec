# PyInstaller spec: `pyinstaller netemu.spec` -> dist/NetEmu/NetEmu(.exe)
# Works unchanged on Windows, Linux and macOS (build on the target OS).
import sys

block_cipher = None

a = Analysis(
    ["run_netemu.py"],
    pathex=["."],
    datas=[("docs", "docs"),             # built-in help reads docs/<lang>/*.md
           ("netemu/resources/labs", "netemu/resources/labs")],   # ready-made labs
    hiddenimports=[
        "netemu.core.cli.cisco", "netemu.core.cli.huawei", "netemu.core.cli.juniper",
        "netemu.core.cli.mikrotik", "netemu.core.cli.host", "netemu.core.cli.linux",
        "netemu.core.cli.windows", "netemu.core.cli.extreme", "netemu.core.cli.asa", "netemu.core.cli.fortigate",
        "netemu.core.services.remote", "PySide6.QtSvg",
    ],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtPdf",
              "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NetEmu", console=False,
          icon="netemu/resources/netemu.ico")
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="NetEmu")
if sys.platform == "darwin":
    import os
    _icns = "netemu/resources/netemu.icns"          # optional: iconutil-made icon for macOS
    app = BUNDLE(coll, name="NetEmu.app", bundle_identifier="org.netemu.app",
                 icon=_icns if os.path.exists(_icns) else None)

# User-visible copies next to the executable: documentation and example projects.
import shutil
from pathlib import Path
_dist = Path(DISTPATH) / "NetEmu"
for _folder in ("docs", "examples"):
    shutil.copytree(_folder, _dist / _folder, dirs_exist_ok=True)
shutil.copy("LICENSE", _dist / "LICENSE.txt")
