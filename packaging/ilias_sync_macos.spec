# PyInstaller Spec für macOS: ILIAS Sync.app ohne Dock Icon (nur Menüleiste).
#
# Bauen (aus dem Projektordner, Ausgabe außerhalb von OneDrive):
#   ~/.venvs/ilias-sync/bin/pyinstaller packaging/ilias_sync_macos.spec --noconfirm \
#       --distpath ~/dev/ilias_sync-build/dist --workpath ~/dev/ilias_sync-build/build
# ruff: noqa

import os
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

PROJECT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, PROJECT)
import ilias_sync

APP_NAME = "ILIAS Sync"
BUNDLE_ID = "de.rafik.ilias-sync"

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[PROJECT],
    # keyring findet seine Backends über Entry Points, die brauchen die Paket Metadaten
    datas=copy_metadata("keyring"),
    hiddenimports=collect_submodules("keyring.backends"),
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
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=None,               # Platzhalter, eigenes .icns folgt
    bundle_identifier=BUNDLE_ID,
    version=ilias_sync.__version__,
    info_plist={
        "LSUIElement": True,             # kein Dock Icon, nur Menüleiste
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": ilias_sync.__version__,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
    },
)
