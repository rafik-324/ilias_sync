"""
Sync planen und ausführen: Mapper Ergebnis + Status Index + Dateisystem -> Aktionen.

Regeln laut CLAUDE.md "Download und Sicherheitsregeln" (schmal, kein Inhaltsvergleich):
  * Nicht im Index, Ziel frei:                 NEW, laden
  * Nicht im Index, Ziel belegt:               CONFLICT, vorhandene Datei bleibt, ILIAS Datei
                                               kommt als <name>_ilias_<Datum> daneben
  * Im Index, lokale Datei fehlt:              SKIP_REMOVED, als user_removed merken, nie neu laden
  * Im Index, in ILIAS unverändert:            nichts
  * Im Index, in ILIAS geändert:               CHANGED, nicht laden, nur als ilias_changed melden.
                                               Der Nutzer lädt per download_changed (als
                                               <name>_ilias_<Datum> daneben) oder ignoriert

Planen (dry_run) schreibt nichts, weder Dateien noch Index. Gelöscht wird nie etwas
außer eigenen .part Dateien.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import requests

from . import downloader
from .api import (
    ActionKind, ChangedFile, Config, DownloadedFile, IliasCourse, ItemStatus, PlannedAction, Progress, SyncResult,
    SyncRun,
)
from .crawler import Throttle
from .mapper import CourseMapping, MappedItem, MappingResult, is_video_name
from .platform_utils import fit_path
from .state import Item, State, rel_to_str, str_to_path

log = logging.getLogger(__name__)


# ---------------- Planen ----------------

@dataclass
class Job:
    kind: ActionKind
    item: MappedItem
    target: Path                  # Ziel (NEW), belegtes Ziel (CONFLICT), vorhandene Datei (CHANGED, SKIP_REMOVED)
    entry: Item | None            # Index Eintrag, falls vorhanden
    mark_removed: bool = False    # SKIP_REMOVED erstmals erkannt, Status beim Sync speichern


def is_video(item: MappedItem) -> bool:
    return is_video_name(item.obj.name)


def ilias_path(item: MappedItem) -> str:
    return " / ".join([*item.obj.path, item.obj.name])


def wanted(item: MappedItem, cfg: Config) -> bool:
    """Nur Dateien, andere ILIAS Objekttypen werden nie geladen (Entscheidung des Nutzers).
    Schalter pro Kurs beachten."""
    if item.obj.type != "file" or not item.obj.url:
        return False
    course = next((c for c in cfg.courses if c.ref_id == item.course.ref_id), None)
    if course is None:
        return True
    return course.download_videos if is_video(item) else course.download_files


def ilias_changed(entry: Item, item: MappedItem) -> bool:
    """ILIAS Werte weichen von der geladenen (bzw. schon gemeldeten) Version ab."""
    obj = item.obj
    if entry.status == ItemStatus.ILIAS_CHANGED:
        mtime, size = entry.new_mtime, entry.new_size
    else:
        mtime, size = entry.ilias_mtime, entry.size
    if obj.mtime and mtime and obj.mtime != mtime:
        return True
    return obj.size is not None and size is not None and obj.size != size


def plan(mapping: MappingResult, st: State, root: Path, cfg: Config, problems: list[str] | None = None) -> list[Job]:
    """Aktionen für alle gewünschten Dateien. Liest Dateisystem und Index, schreibt nichts.
    Dateien, die nicht ablegbar sind (Pfad zu lang), landen als Text in problems."""
    index = st.all_items()
    jobs: list[Job] = []
    for item in mapping.items:
        if not wanted(item, cfg):
            continue
        entry = index.get(item.obj.ref_id)
        if entry is not None and entry.status != ItemStatus.ERROR:
            local = str_to_path(root, entry.local_path)
            if entry.status == ItemStatus.USER_REMOVED:
                jobs.append(Job(ActionKind.SKIP_REMOVED, item, local, entry))
            elif not local.is_file():
                jobs.append(Job(ActionKind.SKIP_REMOVED, item, local, entry, mark_removed=True))
            elif entry.status == ItemStatus.ILIAS_CHANGED or ilias_changed(entry, item):
                jobs.append(Job(ActionKind.CHANGED, item, local, entry))
            continue
        try:
            target = fit_path(root.joinpath(*item.rel_path))
        except ValueError:
            text = f"Pfad zu lang ({len(str(root.joinpath(*item.rel_path)))} Zeichen): {'/'.join(item.rel_path)}"
            log.warning("Übersprungen: %s", text)
            if problems is not None:
                problems.append(text)
            continue
        kind = ActionKind.CONFLICT if target.exists() else ActionKind.NEW
        jobs.append(Job(kind, item, target, entry))
    return jobs


def planned_actions(jobs: list[Job], mapping: MappingResult, root: Path) -> list[PlannedAction]:
    """Für GUI und Vorschau: Dateiaktionen, neue Ordner und nicht zugeordnete Kurse."""
    actions: list[PlannedAction] = []
    new_dirs: set[tuple[str, ...]] = set()
    for job in jobs:
        actions.append(PlannedAction(job.kind, job.item.course.ref_id, job.item.course.name,
                                     ilias_path(job.item), job.target, job.item.obj.size))
        if job.kind == ActionKind.NEW:
            new_dirs |= job.item.new_dirs
    for rel in sorted(new_dirs):
        actions.append(PlannedAction(ActionKind.CREATE_DIR, 0, "", "", root.joinpath(*rel), None))
    for cm in unassigned_courses(mapping):
        actions.append(PlannedAction(ActionKind.UNASSIGNED, cm.course.ref_id, cm.course.name, cm.course.name,
                                     root.joinpath(cm.semester_dir, *cm.course_dir.split("/")), None))
    return actions


def unassigned_courses(mapping: MappingResult) -> list[CourseMapping]:
    return [cm for cm in mapping.courses if cm.source == "neu"]


def check_first_sync(st: State, root: Path) -> str | None:
    """Fehlertext, wenn der erste Sync in einen Ordner mit fremdem Inhalt laufen würde.
    Der erste Sync lädt alles einmal und legt die Struktur selbst an, dafür muss der
    Uni Ordner neu oder leer sein."""
    if not st.is_empty() or not root.exists():
        return None
    if not root.is_dir():
        return f"{root} ist kein Ordner."
    if any(root.iterdir()):
        return (f"Der erste Sync braucht einen leeren oder neuen Uni Ordner, {root} enthält schon Dateien. "
                "Bitte in der Config einen anderen Basisordner (general.base_dir) wählen.")
    return None


# ---------------- Ausführen ----------------

@dataclass
class Outcome:
    job: Job
    status: str                   # "neu", "fehler", "abgebrochen"
    path: Path | None = None      # wohin die ILIAS Datei geschrieben wurde
    sha256: str | None = None
    error: str | None = None


def run_job(job: Job, session: requests.Session, throttle: Throttle | None, relogin: Callable[[], None] | None,
            today: date, cancel: threading.Event) -> Outcome:
    """Eine Aktion ausführen (NEW oder CONFLICT). Wirft nie, Fehler stehen im Ergebnis."""
    if cancel.is_set():
        return Outcome(job, "abgebrochen")
    url = job.item.obj.url
    target = job.target
    if job.kind == ActionKind.CONFLICT:
        target = downloader.versioned_path(job.target, "ilias", today)
    r = downloader.download_file(session, url, target, throttle, relogin)
    if r.status == "vorhanden":
        # Während des Syncs angelegt: nicht überschreiben, mit Datum daneben
        r = downloader.download_file(session, url, downloader.versioned_path(job.target, "ilias", today),
                                     throttle, relogin)
    if r.status == "neu":
        return Outcome(job, "neu", r.path, r.sha256)
    return Outcome(job, "fehler", error=r.error or "Ziel wurde während des Syncs angelegt")


def _record(st: State, root: Path, o: Outcome, now: datetime) -> DownloadedFile | None:
    """Ergebnis in den Index schreiben, geladene Datei für die GUI zurückgeben."""
    item, entry = o.job.item, o.job.entry
    if o.status == "abgebrochen":
        return None
    if o.status == "fehler":
        if entry is None or entry.status == ItemStatus.ERROR:
            st.put(Item(item.obj.ref_id, item.course.ref_id, item.course.name, item.obj.type, ilias_path(item),
                        rel_to_str(o.job.target.relative_to(root).parts), None, None, None, None, ItemStatus.ERROR,
                        url=item.obj.url))
        return None
    st.put(Item(item.obj.ref_id, item.course.ref_id, item.course.name, item.obj.type, ilias_path(item),
                rel_to_str(o.path.relative_to(root).parts), item.obj.mtime, item.obj.size, o.sha256, now,
                ItemStatus.OK, url=item.obj.url))
    return DownloadedFile(item.obj.ref_id, item.course.name, o.path.name, o.path, now, ItemStatus.OK)


def execute(
    jobs: list[Job],
    mapping: MappingResult,
    session: requests.Session,
    st: State,
    root: Path,
    max_parallel: int,
    progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
    relogin: Callable[[], None] | None = None,
    throttle: Throttle | None = None,
) -> tuple[SyncResult, list[Outcome]]:
    """Geplante Aktionen ausführen und im Index festhalten. Abbruch über cancel:
    laufende Downloads enden, geladene Dateien bleiben, .part Dateien werden entfernt."""
    cancel = cancel or threading.Event()
    started = datetime.now()
    today = started.date()
    run_id = st.start_run(started)

    changed: list[ChangedFile] = []
    for job in jobs:
        obj = job.item.obj
        if job.entry is not None and obj.url and obj.url != job.entry.url:
            st.set_url(job.entry.ref_id, obj.url)
        if job.mark_removed:
            st.set_status(job.entry.ref_id, ItemStatus.USER_REMOVED)
        elif job.kind == ActionKind.CHANGED and st.mark_changed(obj.ref_id, obj.mtime, obj.size, started):
            changed.append(changed_file(st.get(obj.ref_id), root))
    todo = [j for j in jobs if j.kind in (ActionKind.NEW, ActionKind.CONFLICT)]

    outcomes: list[Outcome] = []
    new_files: list[DownloadedFile] = []
    try:
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            futures = [pool.submit(run_job, j, session, throttle, relogin, today, cancel) for j in todo]
            try:
                for future in as_completed(futures):
                    o = future.result()
                    outcomes.append(o)
                    if f := _record(st, root, o, datetime.now()):
                        new_files.append(f)
                    if progress:
                        progress(Progress("Herunterladen", len(outcomes), len(todo), o.job.item.obj.name))
            except BaseException:
                # Strg+C oder Fehler: keine neuen Downloads mehr starten
                cancel.set()
                raise
    finally:
        counts = {s: sum(o.status == s for o in outcomes) for s in ("neu", "fehler")}
        run = SyncRun(started, datetime.now(), new=counts["neu"], updated=0, errors=counts["fehler"],
                      cancelled=cancel.is_set())
        st.finish_run(run_id, run)

    errors = [f"{o.job.target.name}: {o.error}" for o in outcomes if o.status == "fehler"]
    unassigned = [IliasCourse(cm.course.ref_id, cm.course.name, cm.semester, cm.course.url)
                  for cm in unassigned_courses(mapping)]
    return SyncResult(run, new_files, unassigned, errors, changed), outcomes


# ---------------- Auf Wunsch des Nutzers laden ----------------

def changed_file(entry: Item, root: Path) -> ChangedFile:
    return ChangedFile(entry.ref_id, entry.course_name, PurePosixPath(entry.local_path).name,
                       str_to_path(root, entry.local_path), entry.ilias_mtime, entry.new_mtime, entry.new_size,
                       entry.detected_at or datetime.now())


class NotAvailable(Exception):
    """Datei nicht (mehr) ladbar, Text ist anzeigbar."""


def _load_for_user(session: requests.Session, st: State, root: Path, ref_id: int, expected: ItemStatus,
                   throttle: Throttle | None, relogin: Callable[[], None] | None) -> DownloadedFile:
    entry = st.get(ref_id)
    if entry is None or entry.status != expected:
        raise NotAvailable("Die Datei ist nicht mehr in der Liste. Bitte zuerst synchronisieren.")
    if not entry.url:
        raise NotAvailable("Für diese Datei ist noch kein Download bekannt. Bitte zuerst synchronisieren.")
    today = date.today()
    existing = str_to_path(root, entry.local_path)
    target = downloader.versioned_path(existing, "ilias", today) if existing.exists() else existing
    r = downloader.download_file(session, entry.url, target, throttle, relogin)
    if r.status == "vorhanden":
        r = downloader.download_file(session, entry.url, downloader.versioned_path(existing, "ilias", today),
                                     throttle, relogin)
    if r.status != "neu":
        raise NotAvailable(f"Download fehlgeschlagen: {r.error or 'Datei existiert bereits'}")
    now = datetime.now()
    changed = expected == ItemStatus.ILIAS_CHANGED
    status = ItemStatus.UPDATED if changed else ItemStatus.OK
    st.put(Item(entry.ref_id, entry.course_ref_id, entry.course_name, entry.type, entry.ilias_path,
                rel_to_str(r.path.relative_to(root).parts),
                entry.new_mtime if changed else entry.ilias_mtime, entry.new_size if changed else entry.size,
                r.sha256, now, status, url=entry.url))
    return DownloadedFile(entry.ref_id, entry.course_name, r.path.name, r.path, now, status)


def download_changed(session: requests.Session, st: State, root: Path, ref_id: int,
                     throttle: Throttle | None = None, relogin: Callable[[], None] | None = None) -> DownloadedFile:
    """Neue ILIAS Version als <name>_ilias_<Datum> neben die vorhandene Datei legen, die unverändert bleibt."""
    return _load_for_user(session, st, root, ref_id, ItemStatus.ILIAS_CHANGED, throttle, relogin)


def redownload(session: requests.Session, st: State, root: Path, ref_id: int,
               throttle: Throttle | None = None, relogin: Callable[[], None] | None = None) -> DownloadedFile:
    """Vom Nutzer entfernte Datei erneut an den alten Ort laden, ist dort etwas, mit Datum daneben."""
    return _load_for_user(session, st, root, ref_id, ItemStatus.USER_REMOVED, throttle, relogin)
