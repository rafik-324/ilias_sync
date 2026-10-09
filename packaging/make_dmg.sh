#!/bin/bash
# Baut ILIAS Sync.app und packt sie als Installer ILIAS-Sync-<version>.dmg (nur Apple Silicon).
# Das DMG enthält die App und eine Verknüpfung zu "Programme", daneben liegt eine SHA-256 Datei.
#
# Aufruf aus dem Projektordner:
#   packaging/make_dmg.sh [AUSGABEORDNER]
# Standard für AUSGABEORDNER ist ~/dev/ilias_sync-build (außerhalb von OneDrive).
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$HOME/dev/ilias_sync-build}"
PYTHON="${ILIAS_SYNC_PYTHON:-$HOME/.venvs/ilias-sync/bin/python}"
APP_NAME="ILIAS Sync"

VERSION="$("$PYTHON" -c "import sys; sys.path.insert(0, '$PROJECT'); import ilias_sync; print(ilias_sync.__version__)")"
DMG="$OUT/ILIAS-Sync-$VERSION.dmg"

# Nur Apple Silicon: das Python der venv muss arm64 sein
if [ "$("$PYTHON" -c 'import platform; print(platform.machine())')" != "arm64" ]; then
    echo "Fehler: $PYTHON ist kein arm64 Python, der Build wäre nicht für Apple Silicon" >&2
    exit 1
fi

echo "== App bauen (Version $VERSION)"
cd "$PROJECT"
"$PYTHON" -m PyInstaller packaging/ilias_sync_macos.spec --noconfirm \
    --distpath "$OUT/dist" --workpath "$OUT/build"

APP="$OUT/dist/$APP_NAME.app"
codesign --verify --deep --strict "$APP"

echo "== DMG packen"
STAGE="$(mktemp -d "$OUT/dmg_stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Programme"
rm -f "$DMG" "$DMG.sha256"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null

(cd "$OUT" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")

echo "== Fertig"
echo "$DMG"
cat "$DMG.sha256"
