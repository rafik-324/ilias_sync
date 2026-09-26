"""
Sync: Crawl Ergebnis und Status Index abgleichen, dann ausführen.

Zwei Schritte, damit der Dry Run genau das zeigt, was der Sync tun würde:
  1. plan(): liest nur (Index, lokale Dateien), schreibt nichts
  2. execute(): lädt neue Dateien, trägt alles in den Index ein

Regeln (CLAUDE.md, Download und Sicherheitsregeln):
  * nie löschen, nie überschreiben
  * neue Datei in ILIAS: laden
  * lokal schon vorhanden, aber nicht im Index (z. B. selbst geladen): nicht laden,
    in den Index übernehmen
  * in ILIAS geändert: nicht laden, nur als ilias_changed melden. Der Nutzer lädt
    per download_changed (neue Version als <name>_ilias_<Datum> daneben) oder
    ignoriert die Änderung
  * vom Nutzer verschoben oder gelöscht: nie wieder von selbst laden (redownload)
  * vom Nutzer verändert (Hash): nicht anfassen, nur Status user_modified
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import requests

from . import downloader, state
from .api import Config, Progress
from .crawler import Throttle
from .mapper import MappedItem, MappingResult
from .platform_utils import fit_path

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv"}
# Lokale Datei gilt erst als verändert, wenn sie deutlich nach dem Download geschrieben wurde
MTIME_TOLERANCE = 2.0


class Kind(Enum):
    NEW = "new"                    # wird geladen
    ADOPT = "adopt"                # liegt lokal schon, wird nur in den Index übernommen
    CHANGED = "changed"            # in ILIAS geändert, wird nur gemeldet
    REMOVED = "removed"            # vom Nutzer entfernt (neu erkannt oder schon bekannt), nicht laden
    UNCHANGED = "unchanged"        # nichts zu tun (ggf. Status user_modified)
    DISABLED = "disabled"          # Objekttyp für den Kurs abgeschaltet


@dataclass
class FileAction:
    kind: Kind
    item: MappedItem
    target: Path                   # absoluter Pfad: Ziel (NEW, ADOPT) oder vorhandene Datei laut Index
    index: state.Item | None = None
    user_modified: bool = False    # nur UNCHANGED/CHANGED: lokale Datei weicht vom Index ab


@dataclass
class SyncSummary:
    new: list[state.Item] = field(default_factory=list)
    adopted: int = 0
    changed: list[state.Item] = field(default_factory=list)   # in diesem Lauf neu erkannte Änderungen
    removed: int = 0                                          # in diesem Lauf neu als entfernt erkannt
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    run_id: int | None = None


ProgressFn = Callable[[Progress], None]


def is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_SUFFIXES


def _enabled(item: MappedItem, cfg: Config) -> bool:
    course = next((c for c in cfg.courses if c.ref_id == item.course.ref_id), None)
    if course is None:
        return True
    if is_video(item.obj.name):
        return course.download_videos
    return course.download_files


def _ilias_differs(index: state.Item, obj_mtime: datetime | None, obj_size: int | None) -> bool:
    """ILIAS Werte weichen von der geladenen (bzw. schon gemeldeten) Version ab. Unbekannte Werte zählen nicht."""
    if index.status == "ilias_changed":
        ref_mtime, ref_size = index.new_mtime, index.new_size
    else:
        ref_mtime, ref_size = index.ilias_mtime, index.size
    if obj_mtime and ref_mtime and obj_mtime != ref_mtime:
        return True
    return bool(obj_size and ref_size and obj_size != ref_size)


def _user_modified(path: Path, index: state.Item) -> bool:
    """Hash nur prüfen, wenn die Datei nach dem Download geschrieben wurde (spart Zeit bei großen Videos)."""
    if not index.sha256:
        return False
    try:
        if path.stat().st_mtime <= index.downloaded_at.timestamp() + MTIME_TOLERANCE:
            return False
        return state.sha256_file(path) != index.sha256
    except OSError:
        return False


# ---------------- 1. Planen (nur lesen) ----------------

def plan(mapping: MappingResult, st: state.State, root: Path, cfg: Config) -> list[FileAction]:
    actions: list[FileAction] = []
    for item in mapping.items:
        obj = item.obj
        if obj.type != "file" or not obj.url:
            continue   # weitere Objekttypen folgen mit Meilenstein 5
        index = st.get(obj.ref_id)
        target = root.joinpath(*item.rel_path)
        if not _enabled(item, cfg):
            actions.append(FileAction(Kind.DISABLED, item, target, index))
            continue

        if index is None or index.status == "error":
            try:
                target = fit_path(target)
            except ValueError:
                pass   # Fehler meldet der Download
            kind = Kind.ADOPT if target.exists() else Kind.NEW
            actions.append(FileAction(kind, item, target, index))
            continue

        local = state.from_index_path(root, index.local_path)
        if index.status == "user_removed" or not local.exists():
            actions.append(FileAction(Kind.REMOVED, item, local, index))
            continue
        modified = index.status == "user_modified" or _user_modified(local, index)
        if index.status == "ilias_changed" or _ilias_differs(index, obj.mtime, obj.size):
            actions.append(FileAction(Kind.CHANGED, item, local, index, modified))
        else:
            actions.append(FileAction(Kind.UNCHANGED, item, local, index, modified))
    return actions


# ---------------- 2. Ausführen ----------------

def _rel(root: Path, path: Path) -> str:
    return state.to_index_path(path.relative_to(root).parts)


def _index_item(action: FileAction, root: Path, path: Path, sha: str | None,
                downloaded_at: datetime, status: str) -> state.Item:
    obj = action.item.obj
    return state.Item(
        ref_id=obj.ref_id,
        course_ref_id=action.item.course.ref_id,
        type=obj.type,
        ilias_path=obj.ilias_path,
        local_path=_rel(root, path),
        ilias_mtime=obj.mtime,
        size=obj.size,
        sha256=sha,
        downloaded_at=downloaded_at,
        status=status,
        url=obj.url,
    )


def _adopt(action: FileAction, root: Path, st: state.State) -> None:
    """Vorhandene lokale Datei übernehmen. Zeit = Änderungszeit der Datei, damit sie nicht als "neu" erscheint."""
    path = action.target
    stat = path.stat()
    st.put(_index_item(action, root, path, state.sha256_file(path),
                       datetime.fromtimestamp(stat.st_mtime), "ok"))


def _apply_without_download(action: FileAction, root: Path, st: state.State, summary: SyncSummary) -> None:
    index, obj = action.index, action.item.obj
    if action.kind == Kind.ADOPT:
        _adopt(action, root, st)
        summary.adopted += 1
    elif action.kind == Kind.REMOVED and index and index.status != "user_removed":
        st.set_status(obj.ref_id, "user_removed")
        summary.removed += 1
    elif action.kind == Kind.CHANGED and index:
        if st.mark_changed(obj.ref_id, obj.mtime, obj.size):
            summary.changed.append(st.get(obj.ref_id))
        if obj.url and obj.url != index.url:
            _update_url(st, obj.ref_id, obj.url)
    elif action.kind == Kind.UNCHANGED and index:
        if action.user_modified and index.status != "user_modified":
            st.set_status(obj.ref_id, "user_modified")
        if obj.url and obj.url != index.url:
            _update_url(st, obj.ref_id, obj.url)


def _update_url(st: state.State, ref_id: int, url: str) -> None:
    item = st.get(ref_id)
    if item:
        item.url = url
        st.put(item)


def execute(
    actions: list[FileAction],
    session: requests.Session,
    st: state.State,
    root: Path,
    *,
    max_parallel: int = 3,
    throttle: Throttle | None = None,
    relogin: Callable[[], None] | None = None,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> SyncSummary:
    """Plan ausführen. Wirft nicht bei einzelnen Fehlern, die stehen in summary.errors."""
    cancel = cancel or threading.Event()
    summary = SyncSummary(run_id=st.start_run())
    try:
        for action in actions:
            if action.kind != Kind.NEW:
                _apply_without_download(action, root, st, summary)

        todo = [a for a in actions if a.kind == Kind.NEW]
        done = 0
        if progress:
            progress(Progress("Herunterladen", 0, len(todo)))
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            pending: dict[Future, FileAction] = {}
            queue = list(reversed(todo))
            while queue or pending:
                while queue and len(pending) < max_parallel and not cancel.is_set():
                    a = queue.pop()
                    pending[pool.submit(downloader.download_file, session, a.item.obj.url, a.target,
                                        throttle, relogin)] = a
                if not pending:
                    break
                finished, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    action = pending.pop(future)
                    _record_download(action, future.result(), root, st, summary)
                    done += 1
                    if progress:
                        progress(Progress("Herunterladen", done, len(todo), action.item.obj.name))
        summary.cancelled = cancel.is_set() and done < len(todo)
    finally:
        st.finish_run(summary.run_id, new=len(summary.new), updated=0, errors=len(summary.errors),
                      cancelled=summary.cancelled)
    return summary


def _record_download(action: FileAction, result: downloader.DownloadResult, root: Path,
                     st: state.State, summary: SyncSummary) -> None:
    now = datetime.now()
    if result.status == "neu":
        item = _index_item(action, root, result.path, result.sha256, now, "ok")
        st.put(item)
        summary.new.append(item)
    elif result.status == "vorhanden":
        # Während des Syncs lokal entstanden: übernehmen, nicht überschreiben
        action.target = result.path
        _adopt(action, root, st)
        summary.adopted += 1
    else:
        summary.errors.append(f"{action.item.obj.ilias_path}: {result.error}")
        try:
            st.put(_index_item(action, root, result.path, None, now, "error"))
        except ValueError:
            pass   # Pfad außerhalb des Uni Ordners (sollte nicht vorkommen), dann nur melden


# ---------------- 3. Auf Wunsch des Nutzers laden ----------------

class ItemNotAvailable(Exception):
    """Objekt fehlt im Index oder hat keinen passenden Status. Text ist anzeigbar."""


def _download_for_user(session: requests.Session, st: state.State, root: Path, ref_id: int,
                       expected_status: str, throttle: Throttle | None,
                       relogin: Callable[[], None] | None) -> state.Item:
    index = st.get(ref_id)
    if index is None or index.status != expected_status:
        raise ItemNotAvailable("Datei ist nicht (mehr) in der Liste. Bitte zuerst synchronisieren.")
    if not index.url:
        raise ItemNotAvailable("Für diese Datei ist kein Download bekannt. Bitte zuerst synchronisieren.")
    existing = state.from_index_path(root, index.local_path)
    target = downloader.versioned_path(existing, date.today()) if existing.exists() else existing
    result = downloader.download_file(session, index.url, target, throttle, relogin)
    if result.status == "vorhanden":
        # Zwischen Prüfung und Download angelegt: mit Datum daneben erneut versuchen
        result = downloader.download_file(session, index.url, downloader.versioned_path(existing, date.today()),
                                          throttle, relogin)
    if result.status != "neu":
        raise ItemNotAvailable(f"Download fehlgeschlagen: {result.error or 'Datei existiert bereits'}")

    changed = expected_status == "ilias_changed"
    item = state.Item(
        ref_id=index.ref_id,
        course_ref_id=index.course_ref_id,
        type=index.type,
        ilias_path=index.ilias_path,
        local_path=_rel(root, result.path),
        ilias_mtime=index.new_mtime if changed else index.ilias_mtime,
        size=index.new_size if changed else index.size,
        sha256=result.sha256,
        downloaded_at=datetime.now(),
        status="updated" if changed else "ok",
        url=index.url,
    )
    st.put(item)
    return item


def download_changed(session: requests.Session, st: state.State, root: Path, ref_id: int,
                     throttle: Throttle | None = None, relogin: Callable[[], None] | None = None) -> state.Item:
    """Neue ILIAS Version als <name>_ilias_<Datum> neben die vorhandene Datei legen, die unverändert bleibt."""
    return _download_for_user(session, st, root, ref_id, "ilias_changed", throttle, relogin)


def redownload(session: requests.Session, st: state.State, root: Path, ref_id: int,
               throttle: Throttle | None = None, relogin: Callable[[], None] | None = None) -> state.Item:
    """Vom Nutzer entfernte Datei erneut an den alten Ort laden (ist dort inzwischen etwas, mit Datum daneben)."""
    return _download_for_user(session, st, root, ref_id, "user_removed", throttle, relogin)
