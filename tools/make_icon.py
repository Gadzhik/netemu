"""Write netemu/resources/netemu.ico (all Windows sizes) and netemu.png from the application icon.

Each size is rendered separately from the vector artwork, so small icons stay sharp.
Run after changing _app_icon_svg in netemu/gui/icons.py:  python tools/make_icon.py"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    buf.close()
    return bytes(data)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """ICO container with PNG-compressed entries (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    return header + entries + blobs


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from netemu.gui.icons import app_pixmap
    out = ROOT / "netemu" / "resources"
    out.mkdir(parents=True, exist_ok=True)
    app_pixmap(256).save(str(out / "netemu.png"))
    (out / "netemu.ico").write_bytes(build_ico([(s, png_bytes(app_pixmap(s))) for s in SIZES]))
    print("written", out / "netemu.ico", "sizes", ", ".join(map(str, SIZES)))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
