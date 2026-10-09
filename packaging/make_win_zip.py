"""Windows Release: den PyInstaller Ordner "ILIAS Sync" als ILIAS-Sync-<version>-windows.zip packen.

Aufruf aus dem Projektordner:
    python packaging/make_win_zip.py "<dist>/ILIAS Sync" [AUSGABEORDNER]
Standard für AUSGABEORDNER ist der Ordner über "ILIAS Sync" (also <dist>).
Das ZIP hat den Ordner "ILIAS Sync" als Wurzel, so erwartet es das Update Skript in updater.py.
Ausgabe: Pfad, Größe und SHA-256 (als JSON).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from release_info import release_info  # noqa: E402

from ilias_sync import __version__  # noqa: E402

ROOT_NAME = "ILIAS Sync"


def make_zip(app_dir: Path, out_dir: Path, version: str = __version__) -> Path:
    """Packt app_dir mit Wurzelordner "ILIAS Sync" und gibt den Pfad des ZIP zurück."""
    if not (app_dir / f"{ROOT_NAME}.exe").is_file():
        raise SystemExit(f"Fehler: {app_dir} enthält keine {ROOT_NAME}.exe")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"ILIAS-Sync-{version}-windows.zip"
    part = target.with_name(target.name + ".part")
    with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, f"{ROOT_NAME}/{path.relative_to(app_dir).as_posix()}")
    part.replace(target)
    return target


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 1
    app_dir = Path(argv[0]).resolve()
    out_dir = Path(argv[1]).resolve() if len(argv) == 2 else app_dir.parent
    target = make_zip(app_dir, out_dir)
    info = release_info(target)
    info["path"] = str(target)
    info["version"] = __version__
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
