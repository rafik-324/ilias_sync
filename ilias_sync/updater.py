"""
App Updates (GUI_KONTEXT.md 3.5): nach neuer Version fragen, Installer laden und prüfen,
die laufende App per Hilfsskript ersetzen.

Ohne Qt, nur requests und stdlib. Einziger Kontakt außer ILIAS: gesendet werden nur
Plattform, eigene Version und der App Schlüssel. Weder die signierte Download URL noch
der Schlüssel landen im Log.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import __version__, config
from .api import Cancelled, IliasSyncError, NetworkError, Progress, ProgressCallback, UpdateInfo
from .auth import USER_AGENT

log = logging.getLogger(__name__)

UPDATE_URL = os.environ.get("ILIAS_SYNC_UPDATE_URL", "https://ilias-sync.lovable.app/api/update")

# App Geheimnis für den Header X-App-Key. Kein echter Schutz (steht im Programm),
# hält nur Fremde vom Update Endpunkt fern.
APP_KEY = "jMqs9POHljmd4kh4SVc6ssGEZvrtWltpeBhNkOJvFw4"

TIMEOUT = (10, 30)           # Verbindungsaufbau, Lesen (Sekunden)
CHUNK = 1024 * 1024
WAIT_SECONDS = 60            # so lange wartet das Hilfsskript auf das Ende der App
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


# ---------------- Hilfen ----------------

def platform_key() -> str | None:
    """'mac', 'win' oder None (andere Systeme bekommen keine Updates)."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform == "win32":
        return "win"
    return None


def parse_version(text: str) -> tuple[int, ...]:
    """'0.10.1' -> (0, 10, 1). Wirft ValueError bei anderem Format."""
    text = text.strip()
    if not VERSION_RE.match(text):
        raise ValueError(f"ungültige Version: {text!r}")
    parts = [int(p) for p in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # 1.0 == 1.0.0
        parts.pop()
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def is_different(remote: str, local: str) -> bool:
    """Andere Version als die installierte, auch eine ältere (Zurückrollen, Wunsch des Nutzers 2026-10-10)."""
    return parse_version(remote) != parse_version(local)


def installer_name(version: str, platform: str) -> str:
    if platform == "mac":
        return f"ILIAS-Sync-{version}.dmg"
    return f"ILIAS-Sync-{version}-windows.zip"


def updates_dir() -> Path:
    return config.data_dir() / "updates"


def _check_url(url: str) -> None:
    """Nur HTTPS. Ausnahme: http auf localhost für Tests."""
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return
    raise IliasSyncError("Der Update Server nutzt keine sichere Verbindung, Update abgebrochen.")


def _get(url: str, **kwargs) -> requests.Response:
    """Einziger Netzwerkzugriff des Moduls (in Tests ersetzbar)."""
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    return requests.get(url, headers=headers, timeout=TIMEOUT, verify=True, **kwargs)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_manifest(current: str = __version__, download: bool = False) -> dict | None:
    """Manifest vom Update Server. None, wenn es nichts gibt (403, 404, andere Plattform)."""
    platform = platform_key()
    if platform is None:
        log.info("Updates: keine Updates für diese Plattform")
        return None
    _check_url(UPDATE_URL)
    params = {"platform": platform, "version": current}
    if download:
        params["download"] = "1"
    resp = _get(UPDATE_URL, params=params, headers={"X-App-Key": APP_KEY})
    if resp.status_code == 403:
        log.warning("Updates: Server lehnt den App Schlüssel ab (403)")
        return None
    if resp.status_code == 404:
        log.info("Updates: keine Datei für Plattform %s (404)", platform)
        return None
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("version"), str) \
            or not VERSION_RE.match(data["version"].strip()):
        log.warning("Updates: Antwort des Servers unvollständig")
        raise NetworkError("Der Update Server hat ungültig geantwortet. Bitte später erneut versuchen.")
    data["version"] = data["version"].strip()
    return data


def _info(data: dict) -> UpdateInfo:
    size = data.get("size")
    return UpdateInfo(
        version=data["version"],
        notes=data.get("notes") if isinstance(data.get("notes"), str) else "",
        size=size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else None,
        published_at=_parse_time(data.get("published_at")),
    )


# ---------------- Prüfen ----------------

def check(current: str = __version__) -> UpdateInfo | None:
    """Andere Version auf dem Server (neuer oder älter) oder None, wenn sie der installierten entspricht.
    Eine ältere Version wird angeboten, damit ein fehlerhaftes Release zurückgenommen werden kann."""
    data = fetch_manifest(current)
    if data is None:
        return None
    if not is_different(data["version"], current):
        log.info("Updates: aktuell (%s, Server %s)", current, data["version"])
        return None
    log.info("Updates: Version %s verfügbar (installiert %s)", data["version"], current)
    return _info(data)


# ---------------- Laden ----------------

def download(info: UpdateInfo, progress: ProgressCallback, cancel: threading.Event,
             dest_dir: Path | None = None) -> Path:
    """Installer laden, SHA-256 prüfen, atomar ablegen. Gibt den Pfad zurück."""
    platform = platform_key()
    data = fetch_manifest(download=True)
    if data is None or platform is None:
        raise IliasSyncError("Das Update ist auf dem Server nicht mehr verfügbar.")
    if data["version"] != info.version:
        log.info("Updates: Server hat inzwischen Version %s statt %s", data["version"], info.version)
    url = data.get("url")
    sha256 = str(data.get("sha256") or "").strip().lower()
    if not isinstance(url, str) or not url or not SHA256_RE.match(sha256):
        log.warning("Updates: Download Link oder Prüfsumme fehlt")
        raise NetworkError("Der Update Server hat ungültig geantwortet. Bitte später erneut versuchen.")
    _check_url(url)
    size = _info(data).size

    dest_dir = dest_dir or updates_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / installer_name(data["version"], platform)
    part = target.with_name(target.name + ".part")

    log.info("Updates: lade %s", target.name)
    digest = hashlib.sha256()
    done = 0
    ok = False
    try:
        # Signierter Link: ohne App Schlüssel, er geht an einen anderen Server
        with _get(url, stream=True) as resp:
            resp.raise_for_status()
            _check_url(resp.url or url)  # auch nach Weiterleitungen nur HTTPS
            progress(Progress(phase="Update laden", current=0, total=size))
            with open(part, "wb") as f:
                for chunk in resp.iter_content(CHUNK):
                    if cancel.is_set():
                        raise Cancelled("Abgebrochen.")
                    if not chunk:
                        continue
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if size is not None and done > size:
                        raise IliasSyncError("Das Update ist größer als angekündigt, Download abgebrochen.")
                    progress(Progress(phase="Update laden", current=done, total=size))
                f.flush()
                os.fsync(f.fileno())
        if cancel.is_set():
            raise Cancelled("Abgebrochen.")
        if digest.hexdigest() != sha256:
            log.warning("Updates: Prüfsumme stimmt nicht (%d Bytes geladen)", done)
            raise IliasSyncError("Das geladene Update ist beschädigt (Prüfsumme falsch) und wurde gelöscht. "
                                 "Bitte später erneut versuchen.")
        os.replace(part, target)
        ok = True
    finally:
        if not ok:
            part.unlink(missing_ok=True)
    log.info("Updates: %s geladen und geprüft (%d Bytes)", target.name, done)
    return target


# ---------------- Installieren ----------------

def app_location() -> Path | None:
    """Was ersetzt wird: macOS das .app Bundle, Windows der Ordner mit der exe. None ohne Build."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if platform_key() == "mac":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return None
    if platform_key() == "win":
        return exe.parent
    return None


def can_self_update() -> bool:
    """Nur im gebauten Programm und wenn App und ihr Elternordner beschreibbar sind."""
    app = app_location()
    if app is None:
        return False
    return os.access(app, os.W_OK) and os.access(app.parent, os.W_OK)


def mac_script(pid: int, app: Path, dmg: Path, log_file: Path) -> str:
    """Bash Skript: wartet auf das Ende der App, tauscht das .app Bundle aus, startet neu."""
    q = shlex.quote
    return f"""#!/bin/bash
# ILIAS Sync Update Hilfsskript (automatisch erzeugt)
set -u
PID={int(pid)}
APP={q(str(app))}
DMG={q(str(dmg))}
LOG={q(str(log_file))}
NEW="$APP.neu"
MNT=""
MOVED=0
TRASHED=""

log() {{ printf '%s %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }}

cleanup() {{
    if [ -n "$MNT" ]; then
        hdiutil detach "$MNT" -quiet >/dev/null 2>&1 || hdiutil detach "$MNT" -force -quiet >/dev/null 2>&1
        rmdir "$MNT" >/dev/null 2>&1
    fi
}}

fail() {{
    log "Fehler: $1"
    if [ "$MOVED" = 1 ] && [ ! -e "$APP" ] && [ -e "$TRASHED" ]; then
        mv "$TRASHED" "$APP" && log "alte App zurückgelegt"
    fi
    [ -e "$NEW" ] && rm -rf "$NEW"
    cleanup
    [ -e "$APP" ] && open "$APP"
    exit 1
}}

# Auf das Ende der App warten (höchstens {WAIT_SECONDS} Sekunden). Hängt sie beim Beenden,
# selbst beenden: erst freundlich (TERM), dann hart (KILL). Der Nutzer hat das Update gewählt
i=0
while kill -0 "$PID" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -eq {WAIT_SECONDS * 2} ]; then
        log "App läuft noch, wird beendet"
        kill -TERM "$PID" 2>/dev/null
    elif [ "$i" -eq {WAIT_SECONDS * 2 + 10} ]; then
        log "App reagiert nicht, wird hart beendet"
        kill -KILL "$PID" 2>/dev/null
    elif [ "$i" -gt {WAIT_SECONDS * 2 + 20} ]; then
        log "App läuft noch, Update abgebrochen"
        exit 1
    fi
    sleep 0.5
done
log "Update startet"

MNT="$(mktemp -d "${{TMPDIR:-/tmp}}/ilias-sync-update.XXXXXX")" || {{ MNT=""; fail "kein temporärer Ordner"; }}
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$MNT" "$DMG" >/dev/null 2>&1 \\
    || fail "DMG konnte nicht eingehängt werden"
SRC="$(find "$MNT" -maxdepth 1 -type d -name '*.app' | head -n 1)"
[ -n "$SRC" ] || fail "keine App im DMG"

[ -e "$NEW" ] && rm -rf "$NEW"
ditto "$SRC" "$NEW" || fail "neue App konnte nicht kopiert werden"

TRASH="$HOME/.Trash"
mkdir -p "$TRASH"
TRASHED="$TRASH/$(basename "$APP")"
if [ -e "$TRASHED" ]; then
    TRASHED="$TRASH/$(basename "$APP" .app) $(date '+%Y-%m-%d %H-%M-%S').app"
fi
mv "$APP" "$TRASHED" || fail "alte App konnte nicht in den Papierkorb"
MOVED=1
mv "$NEW" "$APP" || fail "neue App konnte nicht umbenannt werden"
cleanup
MNT=""
xattr -dr com.apple.quarantine "$APP" >/dev/null 2>&1 || true
log "Update installiert"
open "$APP"
exit 0
"""


def _ps(value: str) -> str:
    """Text als PowerShell Literal in einfachen Anführungszeichen."""
    return "'" + value.replace("'", "''") + "'"


def win_script(pid: int, app: Path, zip_file: Path, log_file: Path, exe_name: str) -> str:
    """PowerShell Skript: wartet auf das Ende der App, tauscht den Programmordner aus, startet neu."""
    app_s = str(app)
    return f"""# ILIAS Sync Update Hilfsskript (automatisch erzeugt)
$ErrorActionPreference = 'Stop'
$AppPid = {int(pid)}
$App = {_ps(app_s)}
$Zip = {_ps(str(zip_file))}
$LogFile = {_ps(str(log_file))}
$ExeName = {_ps(exe_name)}
$Neu = {_ps(app_s + ".neu")}
$Alt = {_ps(app_s + ".alt")}

function Write-Log($Text) {{
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Text
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}}

# Auf das Ende der App warten (höchstens {WAIT_SECONDS} Sekunden)
$deadline = (Get-Date).AddSeconds({WAIT_SECONDS})
while (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {{
    if ((Get-Date) -gt $deadline) {{
        # hängt die App beim Beenden: selbst beenden, der Nutzer hat das Update gewählt
        Write-Log 'App läuft noch, wird beendet'
        Stop-Process -Id $AppPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        if (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {{ Write-Log 'App läuft noch, Update abgebrochen'; exit 1 }}
        break
    }}
    Start-Sleep -Milliseconds 500
}}
Start-Sleep -Seconds 1
Write-Log 'Update startet'

$moved = $false
try {{
    if (Test-Path -LiteralPath $Neu) {{ Remove-Item -LiteralPath $Neu -Recurse -Force }}
    if (Test-Path -LiteralPath $Alt) {{ Remove-Item -LiteralPath $Alt -Recurse -Force }}
    Expand-Archive -LiteralPath $Zip -DestinationPath $Neu -Force
    $Src = Join-Path $Neu 'ILIAS Sync'
    if (-not (Test-Path -LiteralPath (Join-Path $Src $ExeName))) {{ throw 'keine App im ZIP' }}
    # Deinstallationsprogramm des Windows Installers (Inno Setup) übernehmen, es steckt nicht im ZIP
    Get-ChildItem -LiteralPath $App -Filter 'unins*' -File | Copy-Item -Destination $Src -Force
    Move-Item -LiteralPath $App -Destination $Alt
    $moved = $true
    Move-Item -LiteralPath $Src -Destination $App
    Remove-Item -LiteralPath $Neu -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Write-Log ('Fehler: ' + $_.Exception.Message)
    if ($moved -and -not (Test-Path -LiteralPath $App) -and (Test-Path -LiteralPath $Alt)) {{
        Move-Item -LiteralPath $Alt -Destination $App
        Write-Log 'alte App zurückgelegt'
    }}
    if (Test-Path -LiteralPath $Neu) {{ Remove-Item -LiteralPath $Neu -Recurse -Force -ErrorAction SilentlyContinue }}
    if (Test-Path -LiteralPath (Join-Path $App $ExeName)) {{ Start-Process -FilePath (Join-Path $App $ExeName) }}
    exit 1
}}

Start-Process -FilePath (Join-Path $App $ExeName)
Write-Log 'Update installiert'
# Alten Programmordner erst nach dem Start löschen (nur diesen, Nutzerdaten liegen woanders)
Start-Sleep -Seconds 5
Remove-Item -LiteralPath $Alt -Recurse -Force -ErrorAction SilentlyContinue
exit 0
"""


def _start_win_helper(args: list[str], cwd: Path) -> None:
    """PowerShell Hilfsskript unsichtbar und unabhängig von der App starten.

    Nicht DETACHED_PROCESS: ohne Konsole beendet sich powershell.exe sofort, ohne das Skript
    auszuführen. CREATE_NO_WINDOW gibt ihm eine unsichtbare Konsole. Zuerst mit
    CREATE_BREAKAWAY_FROM_JOB, damit das Skript nicht mit dem Job der App endet. Erlaubt
    der Job das nicht (Zugriff verweigert), ohne."""
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    for flags in (base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base):  # type: ignore[attr-defined]
        try:
            subprocess.Popen(
                args, cwd=str(cwd), creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        except PermissionError:
            if flags == base:
                raise
            log.info("Updates: Job erlaubt kein Loslösen, Hilfsskript startet ohne")


def install(installer: Path) -> None:
    """Hilfsskript schreiben und losgelöst starten. Die App muss sich danach selbst beenden."""
    if not can_self_update():
        raise IliasSyncError("Das Update kann nicht automatisch installiert werden. "
                             "Bitte das Update manuell installieren.")
    app = app_location()
    installer = Path(installer).resolve()
    if app is None or not installer.is_file():
        raise IliasSyncError("Die Update Datei fehlt. Bitte das Update erneut laden.")
    folder = installer.parent
    log_file = folder / "install_update.log"
    pid = os.getpid()
    try:
        if platform_key() == "mac":
            script = folder / "install_update.sh"
            script.write_text(mac_script(pid, app, installer, log_file), encoding="utf-8")
            subprocess.Popen(
                ["/bin/bash", str(script)],
                cwd=str(folder), start_new_session=True, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            script = folder / "install_update.ps1"
            # Mit BOM, sonst liest Windows PowerShell 5.1 Umlaute in Pfaden falsch
            script.write_text(win_script(pid, app, installer, log_file, Path(sys.executable).name),
                              encoding="utf-8-sig")
            _start_win_helper(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-WindowStyle", "Hidden", "-File", str(script)],
                folder,
            )
    except OSError as e:
        log.error("Updates: Hilfsskript nicht startbar: %s", type(e).__name__)
        raise IliasSyncError("Das Update konnte nicht gestartet werden.") from None
    log.info("Updates: Hilfsskript gestartet, App wird beendet")
