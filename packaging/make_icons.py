"""
App Icons für den Build aus ilias_sync/gui/assets/app_icon.svg erzeugen.

Ergebnis unter packaging/icons/ (wird committet, der Build braucht dieses Skript nicht):
  png/app_icon_<N>.png   16 bis 1024, randlos wie im Design
  ilias_sync.icns        macOS, mit Rand nach Apples Raster (Inhalt 824 von 1024),
                         sonst wirkt das Icon im Finder zu groß. Braucht iconutil (nur macOS)
  ilias_sync.ico         Windows, randlos, PNG Einträge 16 bis 256

Nur PySide6 (QSvgRenderer), keine zusätzliche Abhängigkeit. Aufruf aus dem Projektordner:
  python packaging/make_icons.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "ilias_sync" / "gui" / "assets" / "app_icon.svg"
OUT = ROOT / "packaging" / "icons"

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# macOS iconset: Dateiname -> Pixelgröße
ICONSET = {
    "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
}
MAC_CONTENT = 824 / 1024


def render(size: int, content: float = 1.0) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    inner = size * content
    offset = (size - inner) / 2
    QSvgRenderer(str(SVG)).render(p, QRectF(offset, offset, inner, inner))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    """ICO mit PNG Einträgen (ab Windows Vista unterstützt)."""
    images = [png_bytes(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in zip(sizes, images):
        dim = 0 if size >= 256 else size   # 0 steht für 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    path.write_bytes(header + entries + blobs)


def write_icns(path: Path) -> bool:
    if shutil.which("iconutil") is None:
        print("iconutil nicht gefunden (nur macOS), .icns übersprungen")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "ilias_sync.iconset"
        iconset.mkdir()
        for name, size in ICONSET.items():
            render(size, MAC_CONTENT).save(str(iconset / name), "PNG")
        subprocess.run(["iconutil", "-c", "icns", "-o", str(path), str(iconset)], check=True)
    return True


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    (OUT / "png").mkdir(parents=True, exist_ok=True)
    for size in PNG_SIZES:
        render(size).save(str(OUT / "png" / f"app_icon_{size}.png"), "PNG")
    write_ico(OUT / "ilias_sync.ico", ICO_SIZES)
    write_icns(OUT / "ilias_sync.icns")
    for f in sorted(OUT.rglob("*")):
        if f.is_file():
            print(f.relative_to(ROOT), f.stat().st_size, "Bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
