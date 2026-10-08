# PyInstaller Spec für macOS: ILIAS Sync.app ohne Dock Icon (nur Menüleiste).
#
# Bauen (aus dem Projektordner, Ausgabe außerhalb von OneDrive):
#   ~/.venvs/ilias-sync/bin/pyinstaller packaging/ilias_sync_macos.spec --noconfirm \
#       --distpath ~/dev/ilias_sync-build/dist --workpath ~/dev/ilias_sync-build/build
#
# Signatur: Gibt es im Schlüsselbund das selbst erstellte Zertifikat "ILIAS Sync Lokal"
# (oder das in ILIAS_SYNC_SIGN_ID genannte), wird die App damit signiert. Dann bleibt die
# Code Identität über alle Builds gleich, und "Immer erlauben" für das Passwort im
# Schlüsselbund gilt auch nach Updates. Ohne Zertifikat: ad hoc Signatur wie bisher.
# ruff: noqa

import os
import subprocess
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

PROJECT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, PROJECT)
import ilias_sync

APP_NAME = "ILIAS Sync"
BUNDLE_ID = "io.github.rafik-324.ilias-sync"

# Logo und Tray Icons der GUI (SVG), geladen über Path(__file__).parent / "assets"
ASSETS = os.path.join(PROJECT, "ilias_sync", "gui", "assets")
# App Icon, erzeugt mit packaging/make_icons.py. Fehlt es noch, baut PyInstaller mit Standard Icon
ICNS = os.path.join(SPECPATH, "icons", "ilias_sync.icns")

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
    argv_emulation=False,
    target_arch=None,
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=ICNS if os.path.isfile(ICNS) else None,
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


# ---------------- Stabile Signatur ----------------

SIGN_ID = os.environ.get("ILIAS_SYNC_SIGN_ID", "ILIAS Sync Lokal")
APP_PATH = os.path.join(DISTPATH, f"{APP_NAME}.app")
if subprocess.run(["security", "find-certificate", "-c", SIGN_ID], capture_output=True).returncode == 0:
    subprocess.run(["codesign", "--force", "--deep", "--sign", SIGN_ID, APP_PATH], check=True)
    print(f"App signiert mit Zertifikat: {SIGN_ID}")
else:
    print(f"Zertifikat {SIGN_ID!r} nicht gefunden, App bleibt ad hoc signiert "
          "(Schlüsselbund fragt nach jedem Build erneut)")
