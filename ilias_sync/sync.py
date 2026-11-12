"""
Sync planen und ausführen: Mapper Ergebnis + Status Index + Dateisystem -> Aktionen.

Regeln laut CLAUDE.md "Download und Sicherheitsregeln" (schmal, kein Inhaltsvergleich):
  * Nicht im Index, Ziel frei:                 NEW, laden
  * Nicht im Index, Ziel belegt:               CONFLICT, vorhandene Datei bleibt, ILIAS Datei
                                               kommt als <name>_ilias_<Datum> daneben
  * Im Index, lokale Datei fehlt:              SKIP_REMOVED, als user_removed merken, nie neu laden
  * Im Index, aber keine einzige Datei des
    Kurses liegt noch lokal:                   Index veraltet (Kurs oder Uni Ordner weg, z. B. nach
                                               Neuinstallation), wie nicht im Index behandeln
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
import traceback
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import requests

from . import debug, downloader, mapper
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
    """Nur Dateien, dazu die Dateien aus Übungen (Aufgabenstellung, Vorgaben, Musterlösung).
    Andere ILIAS Objekttypen werden nie geladen (Entscheidung des Nutzers). Schalter pro Kurs beachten."""
    if item.obj.type != "file" or not item.obj.url:
        return False
    course = next((c for c in cfg.courses if c.ref_id == item.course.ref_id), None)
    if course is None:
        return True
    if item.obj.exercise:
        return course.download_exercises
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


def stale_courses(index: dict[int, Item], root: Path) -> set[int]:
    """Kurse, deren Index Einträge alle ins Leere zeigen: keine einzige als geladen vermerkte
    Datei des Kurses liegt noch im Uni Ordner. Dann hat nicht der Nutzer einzelne Dateien
    entfernt, sondern Kursordner oder Uni Ordner sind weg (z. B. Neuinstallation und neu
    angelegter Ordner unter demselben Pfad, der Index im Datenverzeichnis blieb erhalten).
    Solche Einträge gelten als veraltet, die Dateien werden neu geladen. Einzelne entfernte
    Dateien in einem sonst vorhandenen Kurs bleiben user_removed."""
    present: dict[int, bool] = {}
    for entry in index.values():
        if entry.status == ItemStatus.ERROR or present.get(entry.course_ref_id):
            continue
        try:
            present[entry.course_ref_id] = str_to_path(root, entry.local_path).is_file()
        except OSError:
            present[entry.course_ref_id] = False
    return {ref_id for ref_id, found in present.items() if not found}


def plan(mapping: MappingResult, st: State, root: Path, cfg: Config, problems: list[str] | None = None) -> list[Job]:
    """Aktionen für alle gewünschten Dateien. Liest Dateisystem und Index, schreibt nichts.
    Dateien, die nicht ablegbar sind (Pfad zu lang), landen als Text in problems."""
    index = st.all_items()
    stale = stale_courses(index, root)
    if stale:
        log.info("Index veraltet für %d Kurse (keine geladene Datei mehr vorhanden), Dateien werden neu geladen",
                 len(stale))
    jobs: list[Job] = []
    for item in mapping.items:
        if not wanted(item, cfg):
            continue
        try:
            job = _plan_item(item, index.get(item.obj.ref_id), stale, root, problems)
        except OSError as e:
            # Ein nicht lesbarer Zielordner betrifft nur diese Datei
            text = f"{'/'.join(item.rel_path)}: Zielordner nicht lesbar ({e.strerror or type(e).__name__})"
            log.warning("Übersprungen: %s", text)
            if problems is not None:
                problems.append(text)
            continue
        if job is not None:
            jobs.append(job)
    _debug_plan(index, stale, jobs, mapping, root)
    return jobs


def _plan_item(item: MappedItem, entry: Item | None, stale: set[int], root: Path,
               problems: list[str] | None) -> Job | None:
    if entry is not None and entry.status != ItemStatus.ERROR and entry.course_ref_id not in stale:
        local = str_to_path(root, entry.local_path)
        if entry.status == ItemStatus.USER_REMOVED:
            return Job(ActionKind.SKIP_REMOVED, item, local, entry)
        if not local.is_file():
            return Job(ActionKind.SKIP_REMOVED, item, local, entry, mark_removed=True)
        if entry.status == ItemStatus.ILIAS_CHANGED or ilias_changed(entry, item):
            return Job(ActionKind.CHANGED, item, local, entry)
        return None
    try:
        target = fit_path(root.joinpath(*item.rel_path))
    except ValueError:
        wanted_target = root.joinpath(*item.rel_path)
        text = too_long_text(wanted_target, item)
        log.warning("Übersprungen: %s", text)
        if problems is not None:
            problems.append(text)
        # In der Vorschau sichtbar, beim Sync nur als Fehler gemeldet
        return Job(ActionKind.SKIP_TOO_LONG, item, wanted_target, entry)
    kind = ActionKind.CONFLICT if target.exists() else ActionKind.NEW
    return Job(kind, item, target, entry)


def _debug_plan(index: dict[int, Item], stale: set[int], jobs: list[Job], mapping: MappingResult,
                root: Path) -> None:
    """Pro Kurs: was der Index weiß und was geplant ist. Zeigt veraltete Index Einträge sofort."""
    if not debug.enabled():
        return
    indexed = Counter(e.course_ref_id for e in index.values())
    removed = Counter(e.course_ref_id for e in index.values() if e.status == ItemStatus.USER_REMOVED)
    planned: dict[int, Counter] = {}
    for job in jobs:
        planned.setdefault(job.item.course.ref_id, Counter())[job.kind.value] += 1
    debug.log.info("Plan für %s: %d Einträge im Index", root, len(index))
    for cm in mapping.courses:
        ref_id = cm.course.ref_id
        debug.log.info("  Kurs %s (%s) -> %s/%s [%s]: Index %d (davon user_removed %d)%s, geplant %s",
                       ref_id, cm.course.name, cm.semester_dir, cm.course_dir, cm.source, indexed[ref_id],
                       removed[ref_id], ", VERALTET, wird neu geladen" if ref_id in stale else "",
                       dict(planned.get(ref_id, {})) or "nichts")
    for error in mapping.errors:
        debug.log.warning("  %s", error)


def too_long_text(target: Path, item: MappedItem) -> str:
    return f"Pfad zu lang ({len(str(target))} Zeichen): {'/'.join(item.rel_path)}"


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


def occupied_sync_dirs(root: Path) -> list[str]:
    """Ordner im Uni Ordner, in die der Sync schreiben würde (NN_semester, _ohne_semester)
    und die schon Inhalt haben. Andere Ordner des Nutzers zählen nicht, sie werden nie angefasst."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    occupied = []
    for e in entries:
        name = unicodedata.normalize("NFC", e.name)
        if not e.is_dir() or not (mapper.SEMESTER_DIR_RE.match(name) or name.lower() == mapper.NO_SEMESTER_DIR):
            continue
        try:
            if any(c.name != ".DS_Store" for c in e.iterdir()):
                occupied.append(name)
        except OSError:
            occupied.append(name)
    return sorted(occupied)


def check_first_sync(st: State, root: Path) -> str | None:
    """Fehlertext, wenn der erste Sync in Ordner mit fremdem Inhalt laufen würde.
    Der erste Sync lädt alles einmal und legt Semesterordner und _ohne_semester selbst an,
    diese dürfen daher noch nicht mit Inhalt existieren. Andere Ordner im Uni Ordner stören nicht."""
    if not st.is_empty() or not root.exists():
        return None
    if not root.is_dir():
        return f"{root} ist kein Ordner."
    if occupied := occupied_sync_dirs(root):
        return (f"Der erste Sync braucht neue oder leere Semesterordner, in {root} gibt es schon "
                f"{', '.join(occupied)} mit Inhalt. Bitte einen anderen Uni Ordner wählen.")
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
    try:
        return _run_job(job, session, throttle, relogin, today)
    except Exception as e:
        # Unerwarteter Fehler bei einer Datei: nur diese Datei schlägt fehl
        log.warning("Download fehlgeschlagen (%s): %s", job.target.name, type(e).__name__)
        debug.log.error("Download fehlgeschlagen (%s):\n%s", ilias_path(job.item),
                        "".join(traceback.format_exception(e)))
        return Outcome(job, "fehler", error="Unerwarteter Fehler, Details im Log")


def _run_job(job: Job, session: requests.Session, throttle: Throttle | None, relogin: Callable[[], None] | None,
             today: date) -> Outcome:
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
    extra_errors: list[str] | None = None,
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
        # extra_errors: z. B. zu lange Pfade aus plan(), zählen wie Download Fehler
        run = SyncRun(started, datetime.now(), new=counts["neu"], updated=0,
                      errors=counts["fehler"] + len(extra_errors or []), cancelled=cancel.is_set())
        st.finish_run(run_id, run)

    errors = [*(extra_errors or []), *(f"{o.job.target.name}: {o.error}" for o in outcomes if o.status == "fehler")]
    if debug.enabled():
        per_course: dict[str, Counter] = {}
        for o in outcomes:
            per_course.setdefault(o.job.item.course.name, Counter())[o.status] += 1
        for name, counts in per_course.items():
            debug.log.info("Ergebnis %s: %s", name, dict(counts))
        for o in outcomes:
            if o.status == "fehler":
                debug.log.warning("  Fehler %s: %s", ilias_path(o.job.item), o.error)
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
