"""Build NetEmu for the current OS and package it.

    python tools/build.py            # Windows, Linux or macOS

1. regenerates the application icon and the command lists in docs/,
2. runs PyInstaller (netemu.spec) -> dist/NetEmu (dist/NetEmu.app on macOS),
3. packs a portable archive into release/:
   Windows  NetEmu-<version>-windows-x64.zip  (+ setup.exe when Inno Setup's ISCC is installed)
   Linux    NetEmu-<version>-linux-x86_64.tar.gz
   macOS    NetEmu-<version>-macos.zip  (NetEmu.app)"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(*cmd, env=None) -> None:
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, env=env)


def find_iscc() -> str | None:
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 str(Path(local) / "Programs") if local else None):
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").exists():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    return None


def main() -> int:
    from netemu import __version__ as version
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    run(sys.executable, "tools/make_icon.py", env=env)
    run(sys.executable, "tools/gen_docs.py")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "netemu.spec")
    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    system = platform.system()
    if system == "Windows":
        archive = shutil.make_archive(str(out / f"NetEmu-{version}-windows-x64"), "zip", ROOT / "dist", "NetEmu")
        print("archive:", archive)
        iscc = find_iscc()
        if iscc:
            run(iscc, f"/DAppVersion={version}", "installer/netemu.iss")
            print("installer:", out / f"NetEmu-{version}-setup.exe")
        else:
            print("Inno Setup (ISCC.exe) not found: skipped the setup.exe. Install it from https://jrsoftware.org/isinfo.php")
    elif system == "Darwin":
        archive = shutil.make_archive(str(out / f"NetEmu-{version}-macos"), "zip", ROOT / "dist", "NetEmu.app")
        print("archive:", archive)
    else:
        name = out / f"NetEmu-{version}-linux-{platform.machine() or 'x86_64'}.tar.gz"
        with tarfile.open(name, "w:gz") as tf:
            tf.add(ROOT / "dist" / "NetEmu", arcname="NetEmu")
        print("archive:", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
