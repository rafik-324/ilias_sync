"""
Dateien herunterladen, ohne je etwas zu überschreiben oder zu löschen.

Ablauf pro Datei:
  1. Existiert das Ziel schon: nichts tun
  2. In <name>.part im Zielordner schreiben (nie halbfertige Dateien unter dem echten Namen)
  3. Liefert ILIAS statt der Datei eine HTML Seite (Session abgelaufen): einmal neu
     einloggen und wiederholen
  4. Erst nach vollständigem Download atomar umbenennen (os.replace)

Versionierung (_alt_, _ilias_) und Status Index steuert sync.py, hier liegen nur die Bausteine.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from .auth import TIMEOUT, session_lock
from .crawler import Throttle
from .platform_utils import fit_path

CHUNK_SIZE = 1 << 16
HTML_SUFFIXES = {".html", ".htm"}

log = logging.getLogger(__name__)


class DownloadError(Exception):
    """Download fehlgeschlagen, Text ohne Geheimnisse."""


@dataclass
class DownloadResult:
    path: Path
    status: str        # "neu", "vorhanden", "fehler"
    size: int = 0
    error: str | None = None
    sha256: str | None = None


def part_path(target: Path) -> Path:
    return target.with_name(target.name + ".part")


def _looks_like_login_page(r: requests.Response, target: Path) -> bool:
    content_type = r.headers.get("Content-Type", "").lower()
    return "text/html" in content_type and target.suffix.lower() not in HTML_SUFFIXES


def fetch_to_part(
    session: requests.Session,
    url: str,
    part: Path,
    name_hint: Path,
    throttle: Throttle | None = None,
    relogin: Callable[[], None] | None = None,
) -> tuple[int, str]:
    """In die .part Datei laden, (Größe, sha256) zurückgeben. name_hint dient der Erkennung
    von Login Seiten statt Dateien. Wirft requests, OS oder DownloadError, die .part Datei
    bleibt dann eventuell liegen und muss vom Aufrufer entfernt werden."""
    part.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        if throttle:
            throttle.wait()
        # Eine Session, ein Request zur Zeit (auch während der Datei Inhalt fließt)
        with session_lock(session), session.get(url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            if _looks_like_login_page(r, name_hint):
                if attempt == 1 and relogin is not None:
                    log.info("HTML statt Datei erhalten, neuer Login")
                    relogin()
                    continue
                raise DownloadError("ILIAS lieferte eine Webseite statt der Datei (Session abgelaufen?)")
            size = 0
            digest = hashlib.sha256()
            with open(part, "wb") as f:
                for chunk in r.iter_content(CHUNK_SIZE):
                    f.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            return size, digest.hexdigest()
    raise DownloadError("ILIAS lieferte eine Webseite statt der Datei (Session abgelaufen?)")


def error_text(e: Exception) -> str:
    """Verständlicher Fehlertext pro Datei, ohne Klassennamen. Die Ursache steht im Log."""
    if isinstance(e, (DownloadError, ValueError)):
        return str(e)
    if isinstance(e, requests.HTTPError) and e.response is not None:
        status = e.response.status_code
        if status in (401, 403):
            return f"Kein Zugriff in ILIAS (Fehler {status})"
        if status == 404:
            return "Datei in ILIAS nicht gefunden (Fehler 404)"
        if status >= 500:
            return f"ILIAS hatte ein Serverproblem (Fehler {status}), beim nächsten Sync erneut"
        return f"ILIAS hat den Download abgelehnt (Fehler {status})"
    if isinstance(e, requests.exceptions.SSLError):
        return "Sichere Verbindung zu ILIAS fehlgeschlagen"
    if isinstance(e, requests.Timeout):
        return "ILIAS hat nicht rechtzeitig geantwortet, beim nächsten Sync erneut"
    if isinstance(e, requests.ConnectionError):
        return "ILIAS war nicht erreichbar, beim nächsten Sync erneut"
    if isinstance(e, requests.RequestException):
        return "Download von ILIAS fehlgeschlagen, beim nächsten Sync erneut"
    if isinstance(e, PermissionError):
        return "Keine Schreibrechte im Zielordner"
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return "Nicht genug Speicherplatz"
    if isinstance(e, OSError):
        return "Datei konnte nicht gespeichert werden"
    return "Unerwarteter Fehler, Details im Log"


def download_file(
    session: requests.Session,
    url: str,
    target: Path,
    throttle: Throttle | None = None,
    relogin: Callable[[], None] | None = None,
) -> DownloadResult:
    """Eine Datei laden. Überschreibt nie, wirft nie, Fehler stehen im Ergebnis."""
    try:
        target = fit_path(target)
    except ValueError as e:
        return DownloadResult(target, "fehler", error=str(e))
    if target.exists():
        return DownloadResult(target, "vorhanden")

    part = part_path(target)
    try:
        size, sha = fetch_to_part(session, url, part, target, throttle, relogin)
        # Zwischen Prüfung und Umbenennen könnte der Nutzer eine Datei angelegt haben
        if target.exists():
            part.unlink(missing_ok=True)
            return DownloadResult(target, "vorhanden")
        os.replace(part, target)
        return DownloadResult(target, "neu", size=size, sha256=sha)
    except (requests.RequestException, OSError, DownloadError) as e:
        part.unlink(missing_ok=True)
        log.warning("Download fehlgeschlagen (%s): %s [%s: %s]", target.name, error_text(e), type(e).__name__,
                    getattr(e, "errno", "") or getattr(getattr(e, "response", None), "status_code", ""))
        return DownloadResult(target, "fehler", error=error_text(e))


# ---------------- Versionierung ----------------

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def versioned_path(path: Path, tag: str, day: date) -> Path:
    """<name>_<tag>_<YYYY-MM-DD>.<ext>, bei Kollision mit _2, _3 ... Nie ein vorhandener Pfad."""
    base = f"{path.stem}_{tag}_{day:%Y-%m-%d}"
    candidate = fit_path(path.with_name(base + path.suffix))
    n = 2
    while candidate.exists() or part_path(candidate).exists():
        candidate = fit_path(path.with_name(f"{base}_{n}{path.suffix}"))
        n += 1
    return candidate
