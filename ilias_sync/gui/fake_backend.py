"""
Attrappe von ilias_sync.api.Api für Entwicklung und Tests der GUI.

Liefert realistische Daten nach der Beispielstruktur aus CLAUDE.md, simuliert
Fortschritt mit kleinen Pausen und schreibt ausschliesslich in ein temporäres
Verzeichnis.

Fehlerfälle lassen sich erzwingen, per Konstruktor Argument ``fail`` oder per
Umgebungsvariable ``ILIAS_FAKE_FAIL`` (kommagetrennt):

  password      Login schlägt mit falschem Passwort fehl
  offline       Netzwerkaufrufe werfen NetworkError
  config        load_config wirft ConfigError
  sync_errors   Sync meldet einzelne Fehler
  session       check_session meldet LOGIN_REQUIRED
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ilias_sync.gui import logic
from ilias_sync.api import (
    ActionKind,
    BaseDirCheck,
    Cancelled,
    ChangedFile,
    Config,
    ConfigError,
    CourseConfig,
    CourseRule,
    DownloadedFile,
    IliasCourse,
    InvalidCredentialsError,
    ItemStatus,
    MappingSuggestion,
    NetworkError,
    PlannedAction,
    Progress,
    ProgressCallback,
    SessionState,
    SyncResult,
    SyncRun,
    linux_name,
)

log = logging.getLogger("ilias_sync.fake")

# Namensregel (api.linux_name) gilt für alles, was das Tool neu anlegt. Vorhandene Ordner
# des Nutzers (01_SEMESTER, 01_Vorlesung, 02_Übung ...) behalten ihre Schreibweise, wie in der Praxis.

FAKE_PASSWORD_HINT = "Beim Fake Backend funktioniert jedes Passwort außer 'falsch'."

_COURSES: list[tuple[int, str, str | None, list[str]]] = [
    (1001, "Mathematik Vorkurs (HWS 2025)", "00_mathe_vorkurs", ["01_Vorlesung", "02_Übung"]),
    (1002, "Marketing (HWS 2025)", "01_marketing", ["01_Vorlesung", "02_Übung", "03_Tutorium"]),
    (1003, "Praktische Informatik I (HWS 2025)", "02_praktische_informatik_I",
     ["01_Vorlesung", "02_Übung", "03_Tutorium", "04_code"]),
    (1004, "Programmierpraktikum 1 (HWS 2025)", "03_programmier_praktikum_1",
     ["01_Vorlesung", "02_Übung", "03_Tutorium", "04_freiwillige_abgaben", "linux_data"]),
    (1005, "Wirtschaftsinformatik 1 (HWS 2025)", "04_wirtschaftsinformatik_1",
     ["01_vorlesung", "02_tutorium"]),
    (1006, "Formale Grundlagen (HWS 2025)", "05_formale_grundlagen",
     ["01_Vorlesung", "02_Übung", "03_Übungsblätter"]),
    (1007, "Statistik I (FSS 2026)", None, []),
    # Kurs ohne Semester (Lizenzkurs): Ordner unter _ohne_semester
    (1008, "License Information (Student University of Mannheim)",
     "license_information_student_university_of_mannheim", []),
]

# (Kurs ref_id, ILIAS Ordner, lokale Kategorie, Dateiname, Größe)
NO_SEMESTER_REF = 1008
# Das Backend prüft den ganzen Pfad gegen 250 Zeichen. Die Attrappe misst ab dem Uni Ordner,
# damit das Ergebnis nicht von der Länge des Temp Verzeichnisses abhängt
MAX_REL_PATH = 200

_FILES: list[tuple[int, str, str, str, int]] = [
    (1001, "Vorlesung", "01_Vorlesung", "Vorkurs_Folien_Tag1.pdf", 2_400_000),
    (1001, "Vorlesung", "01_Vorlesung", "Vorkurs_Folien_Tag2.pdf", 2_100_000),
    (1001, "Übungen", "02_Übung", "Aufgaben_Tag1.pdf", 310_000),
    (1002, "Lecture Slides", "01_Vorlesung", "01_Einführung.pdf", 5_800_000),
    (1002, "Lecture Slides", "01_Vorlesung", "02_Konsumentenverhalten.pdf", 7_200_000),
    (1002, "Lecture Slides", "01_Vorlesung", "03_Marktforschung.pdf", 6_900_000),
    (1002, "Übung", "02_Übung", "Fallstudie_1.pdf", 900_000),
    (1002, "Tutorium", "03_Tutorium", "Tutorium_Woche_1.pdf", 450_000),
    (1003, "Vorlesung", "01_Vorlesung", "PI1_Kapitel_01.pdf", 3_300_000),
    (1003, "Vorlesung", "01_Vorlesung", "PI1_Kapitel_02.pdf", 3_900_000),
    (1003, "Vorlesung/Aufzeichnungen", "01_Vorlesung", "Vorlesung_2025-09-08.mp4", 412_000_000),
    (1003, "Übungsblätter", "02_Übung", "Blatt_01.pdf", 280_000),
    (1003, "Übungsblätter", "02_Übung", "Blatt_02.pdf", 295_000),
    (1004, "Material", "01_Vorlesung", "Einrichtung_Linux.pdf", 1_100_000),
    (1004, "Aufgaben", "02_Übung", "Aufgabe_1.zip", 54_000),
    (1005, "Vorlesung", "01_vorlesung", "WI1_Teil_A.pdf", 8_100_000),
    (1005, "Tutorium", "02_tutorium", "Tut_01_Lösungen.pdf", 720_000),
    (1006, "Vorlesung", "01_Vorlesung", "FG_Skript.pdf", 4_600_000),
    (1006, "Übungsblätter", "03_Übungsblätter", "Übungsblatt_01.pdf", 190_000),
    (1006, "Übungsblätter", "03_Übungsblätter", "Übungsblatt_02.pdf", 205_000),
    (1006, "Übung", "02_Übung", "Präsenzübung_01.pdf", 160_000),
    (1007, "Vorlesung", "01_vorlesung", "Statistik_Kapitel_1.pdf", 2_900_000),
    (1008, "Matlab", "matlab", "Matlab_Lizenz_Anleitung.pdf", 150_000),
    # Name so lang, dass der Zielpfad über der Grenze liegt (ergibt SKIP_TOO_LONG)
    (1003, "Vorlesung", "01_Vorlesung",
     "Zusatzmaterial_" + "_".join(["Sehr_ausfuehrliche_Beschreibung_des_Kapitels"] * 5) + ".pdf", 1_300_000),
]


def _ilias_name(path: Path) -> Path:
    """Freier Name <name>_ilias_<Datum>.<ext> neben path, nie eine vorhandene Datei."""
    stem, suffix, day = path.stem, path.suffix, f"{datetime.now():%Y-%m-%d}"
    target = path.with_name(f"{stem}_ilias_{day}{suffix}")
    n = 2
    while target.exists():
        target = path.with_name(f"{stem}_ilias_{day}_{n}{suffix}")
        n += 1
    return target


@dataclass
class _Item:
    ref_id: int
    course_ref_id: int
    ilias_path: str
    local_path: Path
    size: int


class FakeApi:
    """Implementiert dieselben Methoden wie ilias_sync.api.Api."""

    def __init__(
        self,
        first_run: bool = False,
        fail: set[str] | None = None,
        delay: float = 0.05,
        workdir: Path | None = None,
    ) -> None:
        env = {x.strip() for x in os.environ.get("ILIAS_FAKE_FAIL", "").split(",") if x.strip()}
        self.fail = set(fail or ()) | env
        self.delay = delay
        self.workdir = workdir or Path(tempfile.gettempdir()) / "ilias_sync_fake"
        self._lock = threading.Lock()

        self._logged_in = not first_run and "session" not in self.fail
        self._username: str | None = None if first_run else "musterstudi"
        self._has_password = not first_run
        self._autostart = False
        self._sync_count = 0
        self._runs: list[SyncRun] = []
        self._downloaded: dict[int, DownloadedFile] = {}
        self._removed: set[int] = set()
        self._changed: dict[int, ChangedFile] = {}

        self._log_dir = self.workdir / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

        if first_run:
            self._config: Config | None = None
        else:
            self._config = self._default_config(self.workdir / "Uni")
            self._prepare_folders()
            self._seed_history()

    # ---------------- interne Helfer ----------------

    def _default_config(self, base: Path) -> Config:
        courses = [
            CourseConfig(ref_id=ref, ilias_name=name, local_dir=local)
            for ref, name, local, _ in _COURSES
            if local is not None
        ]
        courses[1].rules = [
            CourseRule(target="01_Vorlesung", match_folder="Lecture Slides"),
            CourseRule(target="03_Übungsblätter", match_file="*blatt*.pdf"),
        ]
        courses[2].download_videos = False
        return Config(
            base_dir=base,
            semesters={"HWS 2025": "01_SEMESTER"},
            courses=courses,
            ignore_local_dirs=["04_code", "linux_data", "*-MacBook*", "* von *"],
            username=self._username,
        )

    def _root(self) -> Path:
        assert self._config is not None and self._config.base_dir is not None
        return Path(self._config.base_dir) / self._config.root_folder_name

    def _prepare_folders(self) -> None:
        """Legt die Beispielstruktur im temporären Verzeichnis an (nur mkdir)."""
        if not self._inside_workdir(self._root()):
            return
        sem = self._root() / "01_SEMESTER"
        for ref, _, local, cats in _COURSES:
            if local is None or ref == NO_SEMESTER_REF:
                continue
            for cat in cats:
                (sem / local / cat).mkdir(parents=True, exist_ok=True)
        # Eigene Datei des Nutzers mit demselben Namen wie eine ILIAS Datei (ergibt CONFLICT)
        own = sem / "01_marketing" / "02_Übung" / "fallstudie_1.pdf"
        if not own.exists():
            own.write_text("Eigene Notizen, nicht aus ILIAS.\n", encoding="utf-8")

    def _seed_history(self) -> None:
        """Ein paar alte Einträge, damit Übersicht und Menü nicht leer sind."""
        now = datetime.now()
        items = self._items()
        for i, item in enumerate(items[:5]):
            self._write_file(item.local_path)
            self._downloaded[item.ref_id] = DownloadedFile(
                ref_id=item.ref_id,
                course_name=self._course_name(item.course_ref_id),
                name=item.local_path.name,
                local_path=item.local_path,
                downloaded_at=now - timedelta(days=1, minutes=17 * i),
                status=ItemStatus.OK,
            )
        # Eine Datei hat der Nutzer entfernt
        removed = items[5]
        self._removed.add(removed.ref_id)
        self._downloaded[removed.ref_id] = DownloadedFile(
            ref_id=removed.ref_id,
            course_name=self._course_name(removed.course_ref_id),
            name=removed.local_path.name,
            local_path=removed.local_path,
            downloaded_at=now - timedelta(days=3),
            status=ItemStatus.USER_REMOVED,
        )
        # Zwei geladene Dateien wurden danach in ILIAS geändert
        for item in (items[1], items[3]):
            self._mark_changed(item, now - timedelta(hours=20))
        self._runs.append(SyncRun(now - timedelta(days=1, minutes=2), now - timedelta(days=1), 5, 0, 0))

    def _mark_changed(self, item: _Item, detected_at: datetime) -> ChangedFile:
        loaded = self._downloaded[item.ref_id]
        entry = ChangedFile(
            ref_id=item.ref_id,
            course_name=self._course_name(item.course_ref_id),
            name=item.local_path.name,
            local_path=item.local_path,
            old_mtime=loaded.downloaded_at - timedelta(days=2),
            new_mtime=detected_at - timedelta(hours=1),
            new_size=item.size + 120_000,
            detected_at=detected_at,
        )
        self._changed[item.ref_id] = entry
        return entry

    def _pending_change(self) -> int | None:
        """Beim ersten Sync wird eine weitere geladene Datei als in ILIAS geändert erkannt."""
        if self._sync_count != 0:
            return None
        ref = self._items()[0].ref_id
        return ref if ref in self._downloaded and ref not in self._changed else None

    def _course_name(self, ref_id: int) -> str:
        return next(name for ref, name, _, _ in _COURSES if ref == ref_id)

    def _course_cfg(self, ref_id: int) -> CourseConfig | None:
        if self._config is None:
            return None
        return next((c for c in self._config.courses if c.ref_id == ref_id), None)

    def _items(self) -> list[_Item]:
        root = self._root()
        items = []
        for i, (ref, folder, cat, name, size) in enumerate(_FILES):
            cfg = self._course_cfg(ref)
            if cfg and cfg.local_dir and ref == NO_SEMESTER_REF:
                target = root / "_ohne_semester" / cfg.local_dir / cat / linux_name(name)
            elif cfg and cfg.local_dir:
                target = root / "01_SEMESTER" / cfg.local_dir / cat / linux_name(name)
            else:
                target = (root / "02_semester" / "_neue_kurse" / linux_name(self._course_name(ref))
                          / linux_name(folder) / linux_name(name))
            items.append(_Item(ref * 1000 + i, ref, f"{self._course_name(ref)} / {folder} / {name}", target, size))
        # ab dem zweiten Sync taucht pro Sync eine neue Folie in Marketing auf
        for n in range(1, self._sync_count + 1):
            name = f"{3 + n:02d}_Neue_Folien_Woche_{3 + n}.pdf"
            target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / linux_name(name)
            items.append(_Item(1002_900 + n, 1002, f"Marketing (HWS 2025) / Lecture Slides / {name}", target, 4_000_000))
        return items

    def _inside_workdir(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.workdir.resolve())
            return True
        except ValueError:
            return False

    def _write_file(self, path: Path) -> None:
        # Sicherheitsgurt: die Attrappe schreibt nie ausserhalb ihres Temp Verzeichnisses
        if not self._inside_workdir(path):
            log.info("Fake: nicht geschrieben (ausserhalb des Temp Verzeichnisses): %s", path.name)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                "Platzhalter aus dem Fake Backend von ILIAS Sync.\n", encoding="utf-8"
            )

    def _sleep(self, cancel: threading.Event | None, factor: float = 1.0) -> None:
        if cancel is not None and cancel.wait(self.delay * factor):
            raise Cancelled("Vorgang abgebrochen.")
        if cancel is None:
            time.sleep(self.delay * factor)

    def _check_online(self) -> None:
        if "offline" in self.fail:
            raise NetworkError("ILIAS ist nicht erreichbar. Bitte Internetverbindung prüfen.")

    # ---------------- Konto ----------------

    def stored_username(self) -> str | None:
        return self._username

    def has_stored_password(self) -> bool:
        return self._has_password

    def login(self, user: str, password: str, remember: bool = True) -> None:
        time.sleep(self.delay * 10)
        self._check_online()
        if "password" in self.fail or password == "falsch" or not password:
            self._has_password = False
            log.warning("Login fehlgeschlagen: falsche Zugangsdaten")
            raise InvalidCredentialsError("Uni-ID oder Passwort falsch.")
        self._username = user
        self._has_password = remember
        self._logged_in = True
        self.fail.discard("session")
        if self._config is not None:
            self._config.username = user
        log.info("Login erfolgreich")

    def check_session(self) -> SessionState:
        time.sleep(self.delay * 4)
        if "offline" in self.fail:
            return SessionState.OFFLINE
        if not self._logged_in or "session" in self.fail:
            return SessionState.LOGIN_REQUIRED
        return SessionState.OK

    def logout(self) -> None:
        self._logged_in = False
        self._has_password = False
        log.info("Abgemeldet, Passwort und Cookies entfernt")

    # ---------------- Config ----------------

    def config_exists(self) -> bool:
        return self._config is not None and self._config.base_dir is not None

    def load_config(self) -> Config:
        if "config" in self.fail:
            raise ConfigError("Config Datei ungültig: Zeile 7, 'max_parallel' muss zwischen 1 und 5 liegen.")
        if self._config is None:
            return Config(base_dir=None, username=self._username)
        return deepcopy(self._config)

    def save_config(self, cfg: Config) -> None:
        if not 1 <= cfg.max_parallel <= 5:
            raise ConfigError("'max_parallel' muss zwischen 1 und 5 liegen.")
        with self._lock:
            self._config = deepcopy(cfg)
        self.fail.discard("config")
        log.info("Config gespeichert")

    # ---------------- Basispfad ----------------

    def check_base_dir(self, path: Path) -> BaseDirCheck:
        path = Path(path).expanduser()
        exists = path.is_dir()
        writable = exists and os.access(path, os.W_OK)
        root_name = self._config.root_folder_name if self._config else ""
        root = path / root_name
        has_root = root.is_dir()
        semesters = sorted(p.name for p in root.iterdir() if p.is_dir()) if has_root else []
        is_empty = not has_root or not any(root.iterdir())
        message = None
        if not exists:
            message = "Der Ordner existiert nicht. Er kann angelegt werden."
        elif not writable:
            message = "In diesen Ordner darf nicht geschrieben werden."
        return BaseDirCheck(exists, writable, has_root, semesters, message, is_empty)

    def create_base_structure(self, path: Path) -> None:
        path = Path(path).expanduser()
        root_name = self._config.root_folder_name if self._config else ""
        if self._inside_workdir(path):
            (path / root_name).mkdir(parents=True, exist_ok=True)
        else:
            log.info("Fake: Basisstruktur nicht angelegt (ausserhalb des Temp Verzeichnisses)")
        if self._config is None:
            self._config = Config(base_dir=path, username=self._username)
        else:
            self._config.base_dir = path
        log.info("Basisstruktur angelegt")

    # ---------------- Kurse ----------------

    def fetch_courses(self, progress: ProgressCallback, cancel: threading.Event) -> list[IliasCourse]:
        self._check_online()
        courses = []
        for i, (ref, name, _, _) in enumerate(_COURSES):
            progress(Progress("Kurse abrufen", i, len(_COURSES), name))
            self._sleep(cancel, 2)
            semester = logic.semester_of(name, r"\((HWS|FSS) (\d{4})\)")
            courses.append(IliasCourse(ref, name, semester, f"https://ilias.uni-mannheim.de/goto.php?target=crs_{ref}"))
        progress(Progress("Kurse abrufen", len(_COURSES), len(_COURSES), None))
        return courses

    def suggest_mapping(self, courses: list[IliasCourse]) -> list[MappingSuggestion]:
        root = self._root() if self.config_exists() else None
        has_existing = root is not None and (root / "01_SEMESTER").is_dir()
        result = []
        new_counter: dict[str, int] = {}
        for course in courses:
            known = next((c for c in _COURSES if c[0] == course.ref_id), None)
            sem_dir = "01_SEMESTER" if course.semester == "HWS 2025" else "02_semester"
            if has_existing and known and known[2]:
                score = 0.62 if course.ref_id == 1004 else 0.93
                result.append(MappingSuggestion(course, sem_dir, known[2], True, score))
            else:
                n = new_counter.get(sem_dir, 0)
                new_counter[sem_dir] = n + 1
                name = linux_name(course.name[: course.name.rfind("(")])
                result.append(MappingSuggestion(course, sem_dir, f"{n + 1:02d}_{name}", False, 0.0))
        return result

    def local_course_dirs(self, semester_dir: str) -> list[str]:
        if not self.config_exists():
            return []
        folder = self._root() / semester_dir
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir() if p.is_dir() and not p.name.startswith("_"))

    # ---------------- Sync ----------------

    def _plan(self, progress: ProgressCallback, cancel: threading.Event) -> list[PlannedAction]:
        self._check_online()
        if self._config is None:
            raise ConfigError("Es ist noch kein Basisordner eingerichtet.")
        items = self._items()
        actions: list[PlannedAction] = []
        for i, item in enumerate(items):
            progress(Progress("Durchsuchen", i, len(items), item.ilias_path.split(" / ")[0]))
            self._sleep(cancel)
            cfg = self._course_cfg(item.course_ref_id)
            name = self._course_name(item.course_ref_id)
            if cfg is not None and not cfg.download_videos and item.local_path.suffix == ".mp4":
                continue
            if item.ref_id in self._removed:
                kind = ActionKind.SKIP_REMOVED
            elif item.ref_id in self._downloaded:
                if item.ref_id not in self._changed and item.ref_id != self._pending_change():
                    continue
                kind = ActionKind.CHANGED
            elif len(str(item.local_path.relative_to(self._root()))) > MAX_REL_PATH:
                kind = ActionKind.SKIP_TOO_LONG
            elif cfg is None or cfg.local_dir is None:
                kind = ActionKind.UNASSIGNED
            else:
                kind = ActionKind.CONFLICT if item.local_path.exists() else ActionKind.NEW
            actions.append(PlannedAction(kind, item.course_ref_id, name, item.ilias_path, item.local_path, item.size))
        if self._sync_count == 0:
            code = self._root() / "01_SEMESTER" / "02_praktische_informatik_I" / "04_code" / "main.py"
            actions.append(PlannedAction(ActionKind.SKIP_IGNORED, 1003, self._course_name(1003),
                                         "Praktische Informatik I (HWS 2025) / Code / main.py", code, 1200))
            new_dir = self._root() / "01_SEMESTER" / "04_wirtschaftsinformatik_1" / "03_uebung"
            actions.append(PlannedAction(ActionKind.CREATE_DIR, 1005, self._course_name(1005),
                                         "Wirtschaftsinformatik 1 (HWS 2025) / Übung", new_dir, None))
        progress(Progress("Durchsuchen", len(items), len(items), None))
        return actions

    def dry_run(self, progress: ProgressCallback, cancel: threading.Event) -> list[PlannedAction]:
        return self._plan(progress, cancel)

    def _check_first_sync(self) -> None:
        """Wie das echte Backend: der erste Sync läuft nur in einen leeren oder neuen Uni Ordner."""
        if self._runs or self._config is None:
            return
        root = self._root()
        if root.is_dir() and any(root.iterdir()):
            raise ConfigError(
                f"Der Ordner {root} enthält schon Dateien. Der erste Sync läuft nur in einen leeren "
                "oder neuen Uni Ordner. Bitte in den Einstellungen einen anderen Basisordner wählen."
            )

    def sync(self, progress: ProgressCallback, cancel: threading.Event) -> SyncResult:
        started = datetime.now()
        self._check_first_sync()
        progress(Progress("Anmelden", 0, None, None))
        self._sleep(cancel, 6)
        if not self._logged_in:
            raise InvalidCredentialsError("Anmeldung bei ILIAS nötig.")
        actions = self._plan(progress, cancel)
        # geänderte Dateien werden nur gemeldet, nie automatisch geladen
        todo = [a for a in actions if a.kind in (ActionKind.NEW, ActionKind.CONFLICT, ActionKind.UNASSIGNED)]
        new_files: list[DownloadedFile] = []
        errors: list[str] = []
        cancelled = False
        items = {i.local_path: i for i in self._items()}
        try:
            for i, action in enumerate(todo):
                progress(Progress("Herunterladen", i, len(todo), action.local_path.name))
                self._sleep(cancel, 3)
                if "sync_errors" in self.fail and i == 1:
                    errors.append(f"{action.local_path.name}: Download abgebrochen (Server antwortet nicht)")
                    log.error("Download fehlgeschlagen: %s", action.local_path.name)
                    continue
                # Name belegt: die vorhandene Datei bleibt, ILIAS Datei kommt als *_ilias_<Datum> daneben
                target = _ilias_name(action.local_path) if action.kind == ActionKind.CONFLICT else action.local_path
                self._write_file(target)
                item = items[action.local_path]
                entry = DownloadedFile(item.ref_id, action.course_name, target.name,
                                       target, datetime.now(), ItemStatus.OK)
                self._downloaded[item.ref_id] = entry
                new_files.append(entry)
                log.info("Heruntergeladen: %s", action.local_path.name)
        except Cancelled:
            cancelled = True
            log.info("Sync abgebrochen")
        progress(Progress("Herunterladen", len(todo), len(todo), None))
        # zu lange Zielpfade werden nicht geladen und zählen als Fehler (wie im Backend)
        for action in actions:
            if action.kind == ActionKind.SKIP_TOO_LONG:
                rel = action.local_path.relative_to(self._root())
                errors.append(f"Pfad zu lang ({len(str(action.local_path))} Zeichen): {rel}")
        changed_files = []
        if not cancelled:
            for action in actions:
                item = items.get(action.local_path)
                if action.kind == ActionKind.CHANGED and item and item.ref_id not in self._changed:
                    changed_files.append(self._mark_changed(item, datetime.now()))
                    log.info("In ILIAS geändert: %s", action.local_path.name)
        self._sync_count += 1
        run = SyncRun(started, datetime.now(), len(new_files), 0, len(errors), cancelled)
        self._runs.append(run)
        unassigned = []
        if any(a.kind == ActionKind.UNASSIGNED for a in actions):
            unassigned = [IliasCourse(1007, self._course_name(1007), "FSS 2026",
                                      "https://ilias.uni-mannheim.de/goto.php?target=crs_1007")]
        log.info("Sync fertig: %d neu, %d in ILIAS geändert, %d Fehler", run.new, len(changed_files), run.errors)
        return SyncResult(run, new_files, unassigned, errors, changed_files)

    def redownload(self, ref_id: int) -> DownloadedFile:
        time.sleep(self.delay * 10)
        self._check_online()
        entry = self._downloaded[ref_id]
        self._write_file(entry.local_path)
        self._removed.discard(ref_id)
        entry = DownloadedFile(ref_id, entry.course_name, entry.name, entry.local_path, datetime.now(), ItemStatus.OK)
        self._downloaded[ref_id] = entry
        log.info("Erneut heruntergeladen: %s", entry.name)
        return entry

    def download_changed(self, ref_id: int) -> DownloadedFile:
        time.sleep(self.delay * 10)
        self._check_online()
        changed = self._changed[ref_id]
        # neue Version als <name>_ilias_<Datum> daneben, die vorhandene Datei bleibt
        target = _ilias_name(changed.local_path)
        self._write_file(target)
        del self._changed[ref_id]
        entry = DownloadedFile(ref_id, changed.course_name, target.name, target, datetime.now(), ItemStatus.UPDATED)
        self._downloaded[ref_id] = entry
        log.info("Neue ILIAS Version geladen: %s", target.name)
        return entry

    def dismiss_changed(self, ref_id: int) -> None:
        entry = self._changed.pop(ref_id, None)
        if entry is not None:
            log.info("Änderung ignoriert: %s", entry.name)

    # ---------------- Status ----------------

    def last_run(self) -> SyncRun | None:
        return self._runs[-1] if self._runs else None

    def recent_files(self, limit: int = 50) -> list[DownloadedFile]:
        files = [f for f in self._downloaded.values() if f.status != ItemStatus.USER_REMOVED]
        return sorted(files, key=lambda f: f.downloaded_at, reverse=True)[:limit]

    def removed_items(self) -> list[DownloadedFile]:
        return [f for f in self._downloaded.values() if f.ref_id in self._removed]

    def changed_items(self) -> list[ChangedFile]:
        return sorted(self._changed.values(), key=lambda f: f.detected_at, reverse=True)

    # ---------------- Pfade ----------------

    def root_dir(self) -> Path | None:
        return self._root() if self.config_exists() else None

    def log_dir(self) -> Path:
        return self._log_dir

    def log_file(self) -> Path:
        return self._log_dir / "ilias_sync.log"

    # ---------------- Autostart ----------------

    def autostart_enabled(self) -> bool:
        return self._autostart

    def set_autostart(self, enabled: bool) -> None:
        self._autostart = enabled
        log.info("Autostart %s", "aktiviert" if enabled else "deaktiviert")
