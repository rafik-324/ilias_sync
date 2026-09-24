"""
Dateien herunterladen, ohne je etwas zu überschreiben oder zu löschen.

Ablauf pro Datei:
  1. Existiert das Ziel schon: nichts tun
  2. In <name>.part im Zielordner schreiben (nie halbfertige Dateien unter dem echten Namen)
  3. Liefert ILIAS statt der Datei eine HTML Seite (Session abgelaufen): einmal neu
     einloggen und wiederholen
  4. Erst nach vollständigem Download atomar umbenennen (os.replace)

Status Index und Versionierung (_alt_, _ilias_) folgen mit Meilenstein 3.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from .auth import TIMEOUT
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


def part_path(target: Path) -> Path:
    return target.with_name(target.name + ".part")


def _looks_like_login_page(r: requests.Response, target: Path) -> bool:
    content_type = r.headers.get("Content-Type", "").lower()
    return "text/html" in content_type and target.suffix.lower() not in HTML_SUFFIXES


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

    target.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(target)
    try:
        for attempt in (1, 2):
            if throttle:
                throttle.wait()
            with session.get(url, stream=True, timeout=TIMEOUT) as r:
                r.raise_for_status()
                if _looks_like_login_page(r, target):
                    if attempt == 1 and relogin is not None:
                        log.info("HTML statt Datei erhalten, neuer Login")
                        relogin()
                        continue
                    raise DownloadError("ILIAS lieferte eine Webseite statt der Datei (Session abgelaufen?)")
                size = 0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(CHUNK_SIZE):
                        f.write(chunk)
                        size += len(chunk)
            break
        # Zwischen Prüfung und Umbenennen könnte der Nutzer eine Datei angelegt haben
        if target.exists():
            part.unlink(missing_ok=True)
            return DownloadResult(target, "vorhanden")
        os.replace(part, target)
        return DownloadResult(target, "neu", size=size)
    except (requests.RequestException, OSError, DownloadError) as e:
        part.unlink(missing_ok=True)
        message = str(e) if isinstance(e, DownloadError) else type(e).__name__
        log.warning("Download fehlgeschlagen (%s): %s", target.name, message)
        return DownloadResult(target, "fehler", error=message)
