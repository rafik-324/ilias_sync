"""
Sync planen und ausführen: Mapper Ergebnis + Status Index + Dateisystem -> Aktionen.

Regeln laut CLAUDE.md "Download und Sicherheitsregeln":
  * Nicht im Index, Ziel frei:                 NEW, laden
  * Nicht im Index, Ziel belegt:               CONFLICT, laden und vergleichen. Gleicher
                                               Inhalt: vorhandene Datei übernehmen, sonst
                                               neue Version als <name>_ilias_<Datum>
  * Im Index, lokale Datei fehlt:              SKIP_REMOVED, als user_removed merken, nie neu laden
  * Im Index, in ILIAS unverändert:            nichts
  * In ILIAS geändert, lokal unverändert:      UPDATE, alte Version zu <name>_alt_<Datum>
  * In ILIAS geändert, lokal vom Nutzer
    geändert (Hash weicht ab):                 CONFLICT, neue Version als <name>_ilias_<Datum>

Planen (dry_run) schreibt nichts, weder Dateien noch Index. Gelöscht wird nie etwas
außer eigenen .part Dateien.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from . import downloader
from .api import (
    ActionKind, Config, DownloadedFile, IliasCourse, ItemStatus, PlannedAction, Progress, SyncResult, SyncRun,
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
    target: Path                  # Ziel (NEW), vorhandene lokale Datei (UPDATE, CONFLICT, SKIP_REMOVED)
    entry: Item | None            # Index Eintrag, falls vorhanden
    mark_removed: bool = False    # SKIP_REMOVED erstmals erkannt, Status beim Sync speichern


def is_video(item: MappedItem) -> bool:
    return is_video_name(item.obj.name)


def ilias_path(item: MappedItem) -> str:
    return " / ".join([*item.obj.path, item.obj.name])


def wanted(item: MappedItem, cfg: Config) -> bool:
    """Nur Dateien (weitere Objekttypen folgen mit Meilenstein 5), Schalter pro Kurs beachten."""
    if item.obj.type != "file" or not item.obj.url:
        return False
    course = next((c for c in cfg.courses if c.ref_id == item.course.ref_id), None)
    if course is None:
        return True
    return course.download_videos if is_video(item) else course.download_files


def ilias_changed(entry: Item, item: MappedItem) -> bool:
    obj = item.obj
    if obj.mtime and entry.ilias_mtime and obj.mtime != entry.ilias_mtime:
        return True
    return obj.size is not None and entry.size is not None and obj.size != entry.size


def plan(mapping: MappingResult, st: State, root: Path, cfg: Config) -> list[Job]:
    """Aktionen für alle gewünschten Dateien. Liest Dateisystem und Index, schreibt nichts."""
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
            elif ilias_changed(entry, item):
                modified = entry.sha256 is not None and downloader.file_sha256(local) != entry.sha256
                jobs.append(Job(ActionKind.CONFLICT if modified else ActionKind.UPDATE, item, local, entry))
            continue
        try:
            target = fit_path(root.joinpath(*item.rel_path))
        except ValueError as e:
            log.warning("Übersprungen: %s", e)
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
    status: str                   # "neu", "aktualisiert", "konflikt", "übernommen", "fehler", "abgebrochen"
    path: Path | None = None      # wohin die ILIAS Datei geschrieben wurde
    sha256: str | None = None
    error: str | None = None


def _fetch_part(session, job: Job, part: Path, throttle, relogin) -> str:
    _, sha = downloader.fetch_to_part(session, job.item.obj.url, part, job.target, throttle, relogin)
    return sha


def run_job(job: Job, session: requests.Session, throttle: Throttle | None, relogin: Callable[[], None] | None,
            today: date, cancel: threading.Event) -> Outcome:
    """Eine Aktion ausführen. Wirft nie, Fehler stehen im Ergebnis."""
    if cancel.is_set():
        return Outcome(job, "abgebrochen")
    url = job.item.obj.url
    if job.kind == ActionKind.NEW:
        r = downloader.download_file(session, url, job.target, throttle, relogin)
        if r.status == "neu":
            return Outcome(job, "neu", r.path, r.sha256)
        return Outcome(job, "fehler", error=r.error or "Ziel wurde während des Syncs angelegt")

    if job.kind == ActionKind.CONFLICT and job.entry is not None and job.entry.status != ItemStatus.ERROR:
        # Nutzer hat die Datei geändert: seine Version bleibt, neue daneben
        r = downloader.download_file(session, url, downloader.versioned_path(job.target, "ilias", today),
                                     throttle, relogin)
        if r.status == "neu":
            return Outcome(job, "konflikt", r.path, r.sha256)
        return Outcome(job, "fehler", error=r.error)

    # UPDATE oder unbekannte Datei am Ziel: erst vollständig laden, dann umbenennen
    part = downloader.part_path(job.target)
    try:
        sha = _fetch_part(session, job, part, throttle, relogin)
        if job.kind == ActionKind.UPDATE:
            if job.target.exists():
                os.replace(job.target, downloader.versioned_path(job.target, "alt", today))
            if job.target.exists():   # Nutzer war schneller: nichts überschreiben
                new_path = downloader.versioned_path(job.target, "ilias", today)
                os.replace(part, new_path)
                return Outcome(job, "konflikt", new_path, sha)
            os.replace(part, job.target)
            return Outcome(job, "aktualisiert", job.target, sha)
        # Datei lag schon am Ziel, aber nicht im Index
        if job.target.is_file() and downloader.file_sha256(job.target) == sha:
            part.unlink(missing_ok=True)
            return Outcome(job, "übernommen", job.target, sha)
        new_path = downloader.versioned_path(job.target, "ilias", today)
        os.replace(part, new_path)
        return Outcome(job, "konflikt", new_path, sha)
    except (requests.RequestException, OSError, downloader.DownloadError, ValueError) as e:
        part.unlink(missing_ok=True)
        log.warning("Sync fehlgeschlagen (%s): %s", job.target.name, downloader.error_text(e))
        return Outcome(job, "fehler", error=downloader.error_text(e))


def _record(st: State, root: Path, o: Outcome, now: datetime) -> DownloadedFile | None:
    """Ergebnis in den Index schreiben, geladene Datei für die GUI zurückgeben."""
    item, entry = o.job.item, o.job.entry
    if o.status == "abgebrochen":
        return None
    if o.status == "fehler":
        if entry is None or entry.status == ItemStatus.ERROR:
            st.put(Item(item.obj.ref_id, item.course.ref_id, item.course.name, item.obj.type, ilias_path(item),
                        rel_to_str(o.job.target.relative_to(root).parts), None, None, None, None, ItemStatus.ERROR))
        return None

    if o.status == "konflikt" and entry is not None and entry.status != ItemStatus.ERROR:
        # Lokale Datei des Nutzers bleibt der Bezug, Hash bleibt der der alten ILIAS Version
        st.put(Item(entry.ref_id, entry.course_ref_id, item.course.name, entry.type, ilias_path(item),
                    entry.local_path, item.obj.mtime, item.obj.size, entry.sha256, now, ItemStatus.USER_MODIFIED))
        status = ItemStatus.USER_MODIFIED
    else:
        status = ItemStatus.UPDATED if o.status == "aktualisiert" else ItemStatus.OK
        st.put(Item(item.obj.ref_id, item.course.ref_id, item.course.name, item.obj.type, ilias_path(item),
                    rel_to_str(o.path.relative_to(root).parts), item.obj.mtime, item.obj.size, o.sha256, now,
                    status))
    if o.status == "übernommen":
        return None
    return DownloadedFile(item.obj.ref_id, item.course.name, o.path.name, o.path, now, status)


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

    for job in jobs:
        if job.mark_removed:
            st.set_status(job.entry.ref_id, ItemStatus.USER_REMOVED)
    todo = [j for j in jobs if j.kind in (ActionKind.NEW, ActionKind.UPDATE, ActionKind.CONFLICT)]

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
        counts = {s: sum(o.status == s for o in outcomes) for s in ("neu", "aktualisiert", "konflikt", "fehler")}
        run = SyncRun(started, datetime.now(), new=counts["neu"] + counts["konflikt"],
                      updated=counts["aktualisiert"], errors=counts["fehler"], cancelled=cancel.is_set())
        st.finish_run(run_id, run)

    errors = [f"{o.job.target.name}: {o.error}" for o in outcomes if o.status == "fehler"]
    unassigned = [IliasCourse(cm.course.ref_id, cm.course.name, cm.semester, cm.course.url)
                  for cm in unassigned_courses(mapping)]
    return SyncResult(run, new_files, unassigned, errors), outcomes
