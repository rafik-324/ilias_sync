# PyInstaller Spec für Windows: "ILIAS Sync.exe" ohne Konsolenfenster (nur Infobereich).
#
# Bauen (aus dem Projektordner, Ausgabe außerhalb von OneDrive):
#   python -m PyInstaller packaging\ilias_sync_windows.spec --noconfirm `
#       --distpath <ordner>\dist --workpath <ordner>\build
#
# Ergebnis: Ordner "ILIAS Sync" mit "ILIAS Sync.exe" (onedir, startet schneller als onefile).
# ruff: noqa

import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

PROJECT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, PROJECT)

APP_NAME = "ILIAS Sync"

# Logo und Tray Icons der GUI, geladen über Path(__file__).parent / "assets"
ASSETS = os.path.join(PROJECT, "ilias_sync", "gui", "assets")
# App Icon, erzeugt mit packaging/make_icons.py. Fehlt es noch, baut PyInstaller mit Standard Icon
ICO = os.path.join(SPECPATH, "icons", "ilias_sync.ico")

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[PROJECT],
    # keyring findet seine Backends über Entry Points, die brauchen die Paket Metadaten
    datas=copy_metadata("keyring") + ([(ASSETS, "ilias_sync/gui/assets")] if os.path.isdir(ASSETS) else []),
    # QtSvg rendert die SVG Icons (QSvgRenderer), wird nur dynamisch gebraucht
    hiddenimports=collect_submodules("keyring.backends") + ["PySide6.QtSvg"],
    # Nur was die App braucht, spart viele MB
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=ICO if os.path.isfile(ICO) else None,
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
