"""
Echte Umsetzung der Fassade api.Api (Vertrag: GUI_KONTEXT.md Abschnitt 3).

Die GUI bekommt die Klasse über `from ilias_sync.api import Api`, api.py lädt
dieses Modul erst dann (sonst gäbe es Importschleifen, weil config, state und
sync die Datentypen aus api.py nutzen).

Alles hier ist synchron und Qt frei. Fehler aus den Backend Modulen werden in
die Hierarchie IliasSyncError übersetzt, deren Text direkt anzeigbar ist und
nie Passwörter, Cookies oder Session URLs enthält.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import keyring.errors
import requests

from . import auth, config, crawler, mapper, platform_utils, state, sync
from .api import (
    BaseDirCheck, Cancelled, ChangedFile, Config, ConfigError, DownloadedFile, IliasCourse, IliasSyncError,
    InvalidCredentialsError, LoginError, MappingSuggestion, NetworkError, PlannedAction, Progress,
    ProgressCallback, SessionState, SyncResult, SyncRun,
)

log = logging.getLogger(__name__)

LOG_FILE_NAME = "ilias-sync.log"


@contextmanager
def translate_errors() -> Iterator[None]:
    """Fehler der Backend Module in anzeigbare IliasSyncError übersetzen."""
    try:
        yield
    except IliasSyncError:
        raise
    except auth.InvalidCredentials as e:
        raise InvalidCredentialsError(str(e)) from None
    except auth.LoginError as e:
        raise LoginError(str(e)) from None
    except crawler.SessionExpired as e:
        raise LoginError(str(e)) from None
    except crawler.CrawlCancelled:
        raise Cancelled("Abgebrochen.") from None
    except sync.NotAvailable as e:
        raise IliasSyncError(str(e)) from None
    except requests.RequestException as e:
        # Technische Ursache nur ins Log, der Text ist für Nutzer
        log.warning("Netzwerkfehler: %s", type(e).__name__)
        if isinstance(e, requests.exceptions.SSLError):
            raise NetworkError("Die sichere Verbindung zu ILIAS ist fehlgeschlagen. "
                               "Bitte später erneut versuchen.") from None
        if isinstance(e, requests.Timeout):
            raise NetworkError("ILIAS antwortet gerade nicht. Bitte später erneut versuchen.") from None
        raise NetworkError("ILIAS ist nicht erreichbar. Bitte Internetverbindung prüfen.") from None
    except keyring.errors.KeyringError as e:
        log.warning("Schlüsselbund Fehler: %s", type(e).__name__)
        raise LoginError("Auf den Schlüsselbund kann gerade nicht zugegriffen werden.") from None


class Api:
    """Fassade für die GUI. Eine Instanz pro App, hält die ILIAS Session."""

    def __init__(self, config_path: Path | None = None, data_dir: Path | None = None,
                 cookie_path: Path | None = None):
        # Pfade nur für Tests überschreibbar, sonst platformdirs
        self._config_path = config_path
        self._data_dir = data_dir
        self._cookie_path = cookie_path
        self._session: requests.Session | None = None
        self._session_lock = threading.Lock()

    # ---------------- Hilfen ----------------

    def _cfg(self) -> Config:
        return config.load_config(self._config_path)

    def _root(self, cfg: Config) -> Path | None:
        return cfg.base_dir / cfg.root_folder_name if cfg.base_dir else None

    def _require_root(self, cfg: Config) -> Path:
        root = self._root(cfg)
        if root is None:
            raise ConfigError("Es ist noch kein Basisordner eingerichtet.")
        return root

    def _state(self, root: Path) -> state.State:
        return state.State(state.db_file(self._data_dir or config.data_dir(), root))

    def _stored_credentials(self) -> auth.Credentials:
        """Zugangsdaten für einen Login im Hintergrund: Uni-ID aus der Config, Passwort aus dem Schlüsselbund."""
        user = self.stored_username()
        password = auth.load_password(user) if user else None
        if not user or not password:
            raise auth.InvalidCredentials("Anmeldung bei ILIAS nötig.")
        return auth.Credentials(user, password)

    def _logged_in_session(self) -> requests.Session:
        """Gültige Session, bei Bedarf mit gespeicherten Zugangsdaten neu eingeloggt."""
        with self._session_lock:
            if self._session is None:
                self._session = auth.get_session(self._stored_credentials, self._cookie_path)
            elif not auth.check_session(self._session):
                auth.relogin(self._session, self._stored_credentials)
            return self._session

    def _relogin(self, s: requests.Session) -> Callable[[], None]:
        lock = threading.Lock()

        def relogin() -> None:
            with lock:
                auth.relogin(s, self._stored_credentials)
        return relogin

    def _crawl(self, cfg: Config, s: requests.Session, progress: ProgressCallback,
               cancel: threading.Event) -> crawler.CrawlResult:
        c = crawler.Crawler(
            s,
            max_parallel=cfg.max_parallel,
            semester_pattern=cfg.semester_pattern,
            relogin=self._relogin(s),
            cancel=cancel,
            on_page=lambda n: progress(Progress("Durchsuchen", n, None, "ILIAS Seiten gelesen")),
        )
        progress(Progress("Kurse abrufen", 0, None))
        courses = c.fetch_courses()
        return c.crawl(courses)

    # ---------------- Konto ----------------

    def stored_username(self) -> str | None:
        try:
            return self._cfg().username
        except ConfigError:
            return None

    def has_stored_password(self) -> bool:
        user = self.stored_username()
        if not user:
            return False
        try:
            return auth.load_password(user) is not None
        except keyring.errors.KeyringError:
            return False

    def login(self, user: str, password: str, remember: bool = True) -> None:
        user = user.strip()
        with translate_errors():
            s = auth.make_session(self._cookie_path)
            try:
                auth.login(s, user, password)
            except auth.InvalidCredentials:
                auth.forget_password(user)
                raise
            auth.save_cookies(s)
            if remember:
                auth.store_password(user, password)
            else:
                auth.forget_password(user)
        with self._session_lock:
            self._session = s
        cfg = self._cfg()
        if cfg.username != user:
            cfg.username = user
            config.save_config(cfg, self._config_path)

    def check_session(self) -> SessionState:
        try:
            with translate_errors():
                self._logged_in_session()
            return SessionState.OK
        except NetworkError:
            return SessionState.OFFLINE
        except (InvalidCredentialsError, LoginError):
            return SessionState.LOGIN_REQUIRED

    def logout(self) -> None:
        user = self.stored_username()
        with translate_errors():
            if user:
                auth.forget_password(user)
            auth.clear_cookies(self._cookie_path)
        with self._session_lock:
            self._session = None

    # ---------------- Config ----------------

    def config_exists(self) -> bool:
        path = self._config_path or config.config_file()
        if not config.config_exists(path):
            return False
        try:
            return self._cfg().base_dir is not None
        except ConfigError:
            return True   # vorhanden, aber fehlerhaft: load_config meldet den Fehler

    def load_config(self) -> Config:
        return self._cfg()

    def save_config(self, cfg: Config) -> None:
        config.save_config(cfg, self._config_path)

    # ---------------- Basispfad ----------------

    def check_base_dir(self, path: Path) -> BaseDirCheck:
        path = Path(path).expanduser()
        try:
            root_name = self._cfg().root_folder_name
        except ConfigError:
            root_name = Config(base_dir=None).root_folder_name
        exists = path.is_dir()
        writable = exists and os.access(path, os.W_OK)
        root = path / root_name
        has_root = root.is_dir()
        semesters, is_empty = [], True
        if has_root:
            try:
                entries = [e for e in root.iterdir() if e.name != ".DS_Store"]
            except OSError:
                entries = []
            semesters = sorted(e.name for e in entries if e.is_dir() and mapper.SEMESTER_DIR_RE.match(e.name))
            is_empty = not entries
        message = None
        if path.exists() and not exists:
            message = "Das ist kein Ordner."
        elif not exists:
            message = "Der Ordner existiert nicht. Er kann angelegt werden."
        elif not writable:
            message = "In diesen Ordner darf nicht geschrieben werden."
        elif not is_empty:
            message = (f"{root_name} enthält schon Dateien. Der erste Sync braucht einen leeren "
                       "oder neuen Uni Ordner.")
        return BaseDirCheck(exists, writable, has_root, semesters, message, is_empty)

    def create_base_structure(self, path: Path) -> None:
        path = Path(path).expanduser()
        cfg = self._cfg()
        try:
            (path / cfg.root_folder_name).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(f"Ordner konnte nicht angelegt werden: {e.strerror or type(e).__name__}") from None
        cfg.base_dir = path
        config.save_config(cfg, self._config_path)

    # ---------------- Kurse ----------------

    def fetch_courses(self, progress: ProgressCallback, cancel: threading.Event) -> list[IliasCourse]:
        cfg = self._cfg()
        progress(Progress("Anmelden", 0, None))
        with translate_errors():
            s = self._logged_in_session()
            c = crawler.Crawler(s, max_parallel=cfg.max_parallel, semester_pattern=cfg.semester_pattern,
                                relogin=self._relogin(s), cancel=cancel)
            progress(Progress("Kurse abrufen", 0, None))
            courses = c.fetch_courses()
        progress(Progress("Kurse abrufen", len(courses), len(courses)))
        # Gruppen haben kein Semester im Namen, dann gilt die Überschrift auf dem Dashboard
        return [IliasCourse(k.ref_id, k.name, mapper.course_semester(k), k.url) for k in courses]

    def suggest_mapping(self, courses: list[IliasCourse]) -> list[MappingSuggestion]:
        cfg = self._cfg()
        m = mapper.Mapper(cfg, self._root(cfg))
        crawl_courses = [crawler.Course("crs", k.ref_id, k.name, k.url, k.semester, k.semester) for k in courses]
        result = crawler.CrawlResult(courses=crawl_courses)
        semester_dirs = m.semester_dirs(result)
        new_dirs = {c.ref_id: c.local_dir for c in m.suggest_courses(result)}
        suggestions = []
        for course, k in zip(courses, crawl_courses):
            cm = m.map_course(k, semester_dirs)
            if cm.source == "config":
                suggestions.append(MappingSuggestion(course, cm.semester_dir, cm.course_dir, cm.exists, 1.0))
            elif cm.source == "fuzzy":
                _, score = mapper.best_course_dir(k.name, [cm.course_dir], cfg.semester_pattern)
                suggestions.append(MappingSuggestion(course, cm.semester_dir, cm.course_dir, True, score))
            elif k.ref_id in new_dirs:
                suggestions.append(MappingSuggestion(course, cm.semester_dir, new_dirs[k.ref_id], False, 0.0))
            else:
                suggestions.append(MappingSuggestion(course, cm.semester_dir, cm.course_dir, False, 0.0))
        return suggestions

    def local_course_dirs(self, semester_dir: str) -> list[str]:
        cfg = self._cfg()
        root = self._root(cfg)
        if root is None:
            return []
        tree = mapper.LocalTree(root, cfg.ignore_local_dirs)
        return [d for d in tree.subdirs((semester_dir,)) if not d.startswith("_")]

    # ---------------- Sync ----------------

    def _prepare(self, cfg: Config, st: state.State, root: Path, progress: ProgressCallback,
                 cancel: threading.Event, save_suggestions: bool):
        progress(Progress("Anmelden", 0, None))
        s = self._logged_in_session()
        result = self._crawl(cfg, s, progress, cancel)
        suggestions = mapper.Mapper(cfg, root).suggest_courses(result)
        if suggestions:
            cfg.courses += suggestions
            if save_suggestions:
                config.save_config(cfg, self._config_path)
        mapping = mapper.Mapper(cfg, root).map_crawl(result)
        problems: list[str] = []
        jobs = sync.plan(mapping, st, root, cfg, problems)
        for p in problems:
            log.warning("Nicht ablegbar: %s", p)
        return s, mapping, jobs, problems

    def dry_run(self, progress: ProgressCallback, cancel: threading.Event) -> list[PlannedAction]:
        cfg = self._cfg()
        root = self._require_root(cfg)
        with translate_errors(), self._state(root) as st:
            _, mapping, jobs, _ = self._prepare(cfg, st, root, progress, cancel, save_suggestions=False)
            return sync.planned_actions(jobs, mapping, root)

    def sync(self, progress: ProgressCallback, cancel: threading.Event) -> SyncResult:
        started = time.monotonic()
        log.info("Sync gestartet")
        try:
            result = self._sync(progress, cancel)
        except Cancelled:
            log.info("Sync abgebrochen nach %.0f s", time.monotonic() - started)
            raise
        except IliasSyncError as e:
            log.warning("Sync fehlgeschlagen nach %.0f s: %s", time.monotonic() - started, e)
            raise
        log.info("Sync %s: %d neu, %d in ILIAS geändert, %d Fehler, Dauer %.0f s",
                 "abgebrochen" if result.run.cancelled else "fertig", len(result.new_files),
                 len(result.changed_files), len(result.errors), time.monotonic() - started)
        for error in result.errors:
            log.warning("Sync Fehler: %s", error)
        return result

    def _sync(self, progress: ProgressCallback, cancel: threading.Event) -> SyncResult:
        cfg = self._cfg()
        root = self._require_root(cfg)
        with translate_errors(), self._state(root) as st:
            if problem := sync.check_first_sync(st, root):
                raise ConfigError(problem)
            s, mapping, jobs, problems = self._prepare(cfg, st, root, progress, cancel, save_suggestions=True)
            result, _ = sync.execute(jobs, mapping, s, st, root, cfg.max_parallel, progress, cancel,
                                     relogin=self._relogin(s), throttle=crawler.Throttle(crawler.DEFAULT_DELAY))
        result.errors += [f"Nicht ablegbar: {p}" for p in problems]
        return result

    def _load_for_user(self, ref_id: int, load: Callable) -> DownloadedFile:
        cfg = self._cfg()
        root = self._require_root(cfg)
        with translate_errors(), self._state(root) as st:
            s = self._logged_in_session()
            return load(s, st, root, ref_id, crawler.Throttle(crawler.DEFAULT_DELAY), self._relogin(s))

    def redownload(self, ref_id: int) -> DownloadedFile:
        return self._load_for_user(ref_id, sync.redownload)

    def download_changed(self, ref_id: int) -> DownloadedFile:
        return self._load_for_user(ref_id, sync.download_changed)

    def dismiss_changed(self, ref_id: int) -> None:
        root = self._require_root(self._cfg())
        with self._state(root) as st:
            st.dismiss_changed(ref_id)

    # ---------------- Status ----------------

    def _read_state(self, read: Callable[[state.State, Path], object], empty):
        try:
            root = self._root(self._cfg())
        except ConfigError:
            return empty
        if root is None:
            return empty
        with self._state(root) as st:
            return read(st, root)

    @staticmethod
    def _downloaded(item: state.Item, root: Path) -> DownloadedFile:
        path = state.str_to_path(root, item.local_path)
        return DownloadedFile(item.ref_id, item.course_name, path.name, path,
                              item.downloaded_at or datetime.now(), item.status)

    def last_run(self) -> SyncRun | None:
        return self._read_state(lambda st, root: st.last_run(), None)

    def recent_files(self, limit: int = 50) -> list[DownloadedFile]:
        return self._read_state(lambda st, root: [self._downloaded(i, root) for i in st.recent(limit)], [])

    def removed_items(self) -> list[DownloadedFile]:
        return self._read_state(lambda st, root: [self._downloaded(i, root) for i in st.removed()], [])

    def changed_items(self) -> list[ChangedFile]:
        return self._read_state(lambda st, root: [sync.changed_file(i, root) for i in st.changed()], [])

    # ---------------- Pfade ----------------

    def root_dir(self) -> Path | None:
        try:
            return self._root(self._cfg())
        except ConfigError:
            return None

    def log_dir(self) -> Path:
        return config.log_dir()

    def log_file(self) -> Path:
        return config.log_dir() / LOG_FILE_NAME

    # ---------------- Autostart ----------------

    def autostart_enabled(self) -> bool:
        return platform_utils.autostart_enabled()

    def set_autostart(self, enabled: bool) -> None:
        try:
            platform_utils.set_autostart(enabled)
        except OSError as e:
            raise ConfigError(f"Autostart konnte nicht geändert werden: {e.strerror or e}") from None
        cfg = self._cfg()
        if cfg.autostart != enabled:
            cfg.autostart = enabled
            config.save_config(cfg, self._config_path)
