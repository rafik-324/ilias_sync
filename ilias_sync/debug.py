"""
Debug Modus für die Fehlersuche bei anderen Nutzern.

Einschalten mit `--debug` oder der Umgebungsvariable ILIAS_SYNC_DEBUG=1. Dann schreibt
der Logger "ilias_sync.debug" zusätzlich in eine eigene Datei `ilias-sync-debug.log`
im Log Ordner: finale URL nach dem Login, gefundene Kurse mit ref_id, rohes HTML der
Kursliste, Erfolg oder Fehler pro Kurs mit Stacktrace und die Pfade des gespeicherten
Zustands (Config, Index, Cookies).

Auch im Debug Modus nie Passwörter, Cookies, SAMLResponse oder Session URLs: URLs
laufen über url(), die nur unkritische Parameter behält, Cookie und Config Dateien
werden nur mit Pfad genannt, nie mit Inhalt.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

DEBUG_ENV = "ILIAS_SYNC_DEBUG"
DEBUG_FILE_NAME = "ilias-sync-debug.log"
# Query Parameter, die in ILIAS URLs nur die Seite beschreiben und nichts Geheimes enthalten
SAFE_QUERY_KEYS = {"baseclass", "cmd", "cmdclass", "cmdnode", "target", "ref_id", "type", "item_ref_id"}

log = logging.getLogger("ilias_sync.debug")
log.propagate = False   # HTML Ausschnitte gehören nicht ins normale Log
log.setLevel(logging.DEBUG)
log.addHandler(logging.NullHandler())

_forced = False
_handler: logging.Handler | None = None


def enable() -> None:
    """Debug Modus für diesen Prozess einschalten (z. B. über --debug)."""
    global _forced
    _forced = True


def enabled() -> bool:
    return _forced or os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes", "ja", "on")


def setup(log_dir: Path) -> Path | None:
    """Debug Datei anlegen, falls der Modus an ist. Liefert ihren Pfad oder None."""
    global _handler
    if not enabled():
        return None
    path = log_dir / DEBUG_FILE_NAME
    if _handler is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
        log.addHandler(_handler)
    from . import __version__
    log.info("===== Debug Modus an: ILIAS Sync %s, Python %s, %s %s =====", __version__,
             platform.python_version(), platform.system(), platform.release())
    log.info("Programm: %s%s", sys.executable, " (gebaut)" if getattr(sys, "frozen", False) else "")
    return path


def url(value: str | None) -> str:
    """URL für das Debug Log: Pfad plus unkritische Parameter, alles andere entfernt."""
    if not value:
        return str(value)
    p = urlparse(value)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in SAFE_QUERY_KEYS]
    dropped = len(parse_qsl(p.query, keep_blank_values=True)) - len(query)
    text = f"{p.scheme}://{p.hostname}{p.path}"
    if query:
        text += "?" + urlencode(query)
    if dropped:
        text += f" (+{dropped} Parameter entfernt)"
    return text


def html(label: str, text: str | None) -> None:
    """Rohes HTML ins Debug Log, mit Markierung von Anfang und Ende."""
    if not enabled():
        return
    log.debug("----- HTML %s (%d Zeichen) -----\n%s\n----- Ende HTML %s -----",
              label, len(text or ""), text or "", label)


def describe_paths(config_path: Path, data_dir: Path, cookie_path: Path, root: Path | None,
                   index_path: Path | None) -> None:
    """Wo der gespeicherte Zustand liegt. Er bleibt bei einer Neuinstallation der App erhalten."""
    if not enabled():
        return
    log.info("Zustand: Config %s (%s)", config_path, "vorhanden" if config_path.exists() else "fehlt")
    log.info("Zustand: Datenordner %s", data_dir)
    log.info("Zustand: Cookies %s (%s)", cookie_path, "vorhanden" if cookie_path.exists() else "fehlt")
    try:
        indexes = sorted(p.name for p in data_dir.glob("index_*.sqlite3"))
    except OSError:
        indexes = []
    log.info("Zustand: Index Datenbanken im Datenordner: %s", ", ".join(indexes) or "keine")
    log.info("Zustand: Uni Ordner %s (%s)", root,
             "vorhanden" if root is not None and root.is_dir() else "fehlt")
    if index_path is not None:
        log.info("Zustand: Index für diesen Uni Ordner %s (%s)", index_path,
                 "vorhanden" if index_path.exists() else "neu")
