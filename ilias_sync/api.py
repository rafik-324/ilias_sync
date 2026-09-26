"""
Fassade zwischen GUI und Backend.

Dieses Modul ist der verbindliche Vertrag aus GUI_KONTEXT.md Abschnitt 3.
Die GUI importiert ausschliesslich von hier. Alle Methoden sind synchron und
Qt frei. Langlaufende Methoden nehmen einen progress Callback und ein cancel
Token (threading.Event) entgegen.

Die Datentypen und Fehlerklassen sind fertig. Die Methoden von Api werden mit
den Backend Meilensteinen implementiert, bis dahin nutzt die GUI
ilias_sync.gui.fake_backend.FakeApi.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable


# ---------------- Datentypen ----------------

class SessionState(Enum):
    OK = "ok"                    # gültige Session
    LOGIN_REQUIRED = "login"     # kein Passwort gespeichert oder Relogin nicht möglich
    OFFLINE = "offline"          # Netzwerk nicht erreichbar


class ItemStatus(Enum):
    OK = "ok"
    UPDATED = "updated"
    USER_MODIFIED = "user_modified"
    USER_REMOVED = "user_removed"
    ERROR = "error"
    ILIAS_CHANGED = "ilias_changed"  # in ILIAS geändert, neue Version noch nicht geladen (Nutzer entscheidet)


class ActionKind(Enum):
    NEW = "new"                    # neue Datei
    # CHANGED = "changed" (in ILIAS geändert, nur gemeldet) laut GUI_KONTEXT.md trägt der GUI Agent
    # zusammen mit den Texten in gui/widgets.py und gui/logic.py ein
    UPDATE = "update"              # veraltet, wird vom Backend nicht mehr erzeugt
    CONFLICT = "conflict"          # veraltet, wird vom Backend nicht mehr erzeugt
    SKIP_REMOVED = "skip_removed"  # vom Nutzer entfernt, wird nicht geladen
    SKIP_IGNORED = "skip_ignored"  # liegt in [ignore]
    CREATE_DIR = "create_dir"
    UNASSIGNED = "unassigned"      # Kurs ohne Zuordnung, Ziel _Neue_Kurse


@dataclass
class CourseRule:
    target: str
    match_folder: str | None = None   # Glob auf ILIAS Ordnernamen
    match_file: str | None = None     # Glob auf Dateinamen


@dataclass
class CourseConfig:
    ref_id: int
    ilias_name: str
    local_dir: str | None             # None = noch nicht zugeordnet
    download_files: bool = True
    download_videos: bool = True
    download_exercises: bool = True
    download_weblinks: bool = True
    download_forums: bool = True
    download_other: bool = True       # Wiki, Test, Umfrage ... als Link Datei
    rules: list[CourseRule] = field(default_factory=list)


@dataclass
class Config:
    base_dir: Path | None
    root_folder_name: str = "01_Universität_Mannheim"
    sync_interval_minutes: int = 60
    max_parallel: int = 3             # 1 bis 5
    semester_pattern: str = r"\((HWS|FSS) (\d{4})\)"
    semesters: dict[str, str] = field(default_factory=dict)   # "HWS 2026" -> "01_SEMESTER"
    courses: list[CourseConfig] = field(default_factory=list)
    ignore_local_dirs: list[str] = field(default_factory=list)
    autostart: bool = False
    sync_paused: bool = False
    username: str | None = None       # Uni-ID, nie das Passwort


@dataclass
class IliasCourse:
    ref_id: int
    name: str                         # z. B. "Marketing (HWS 2026)"
    semester: str | None              # z. B. "HWS 2026", None wenn Muster nicht passt
    url: str                          # Permalink


@dataclass
class MappingSuggestion:
    course: IliasCourse
    semester_dir: str
    local_dir: str
    exists: bool                      # True = vorhandener Ordner gefunden
    score: float                      # 0.0 bis 1.0


@dataclass
class BaseDirCheck:
    exists: bool
    writable: bool
    has_root_folder: bool
    existing_semesters: list[str]
    message: str | None


@dataclass
class PlannedAction:
    kind: ActionKind
    course_ref_id: int
    course_name: str
    ilias_path: str                   # "Kurs / Ordner / Datei.pdf"
    local_path: Path                  # absolutes Ziel
    size: int | None


@dataclass
class DownloadedFile:
    ref_id: int
    course_name: str
    name: str
    local_path: Path
    downloaded_at: datetime
    status: ItemStatus


@dataclass
class ChangedFile:
    """Datei, die nach dem Herunterladen in ILIAS geändert wurde. Wird nie automatisch geladen."""
    ref_id: int
    course_name: str
    name: str
    local_path: Path                  # vorhandene lokale Datei (kann inzwischen fehlen)
    old_mtime: datetime | None        # ILIAS Änderungsdatum der geladenen Version
    new_mtime: datetime | None        # ILIAS Änderungsdatum der neuen Version
    new_size: int | None
    detected_at: datetime

@dataclass
class SyncRun:
    started_at: datetime
    finished_at: datetime | None
    new: int
    updated: int
    errors: int
    cancelled: bool = False


@dataclass
class SyncResult:
    run: SyncRun
    new_files: list[DownloadedFile]
    new_unassigned_courses: list[IliasCourse]
    errors: list[str]
    changed_files: list[ChangedFile] = field(default_factory=list)  # in diesem Lauf neu als geändert erkannt


@dataclass
class Progress:
    phase: str                        # "Anmelden", "Kurse abrufen", "Durchsuchen", "Herunterladen"
    current: int
    total: int | None                 # None = unbestimmt
    detail: str | None = None


ProgressCallback = Callable[[Progress], None]


# ---------------- Fehler ----------------

class IliasSyncError(Exception):
    """Basis. Der Text ist direkt anzeigbar und enthält keine Geheimnisse."""


class InvalidCredentialsError(IliasSyncError):
    """Falsches Passwort, Keyring Eintrag ist bereits gelöscht."""


class LoginError(IliasSyncError):
    """Login aus anderen Gründen fehlgeschlagen."""


class NetworkError(IliasSyncError):
    """Offline, Timeout oder TLS Fehler."""


class ConfigError(IliasSyncError):
    """Config ungültig, der Text nennt Feld oder Zeile."""


class Cancelled(IliasSyncError):
    """Durch das cancel Token abgebrochen."""


# ---------------- Fassade ----------------

class Api:
    """Echte Backend Anbindung. Wird mit den Backend Meilensteinen gefüllt."""

    # Konto
    def stored_username(self) -> str | None:
        raise NotImplementedError

    def has_stored_password(self) -> bool:
        raise NotImplementedError

    def login(self, user: str, password: str, remember: bool = True) -> None:
        raise NotImplementedError

    def check_session(self) -> SessionState:
        raise NotImplementedError

    def logout(self) -> None:
        raise NotImplementedError

    # Config
    def config_exists(self) -> bool:
        raise NotImplementedError

    def load_config(self) -> Config:
        raise NotImplementedError

    def save_config(self, cfg: Config) -> None:
        raise NotImplementedError

    # Basispfad
    def check_base_dir(self, path: Path) -> BaseDirCheck:
        raise NotImplementedError

    def create_base_structure(self, path: Path) -> None:
        raise NotImplementedError

    # Kurse
    def fetch_courses(self, progress: ProgressCallback, cancel: threading.Event) -> list[IliasCourse]:
        raise NotImplementedError

    def suggest_mapping(self, courses: list[IliasCourse]) -> list[MappingSuggestion]:
        raise NotImplementedError

    def local_course_dirs(self, semester_dir: str) -> list[str]:
        raise NotImplementedError

    # Sync
    def dry_run(self, progress: ProgressCallback, cancel: threading.Event) -> list[PlannedAction]:
        raise NotImplementedError

    def sync(self, progress: ProgressCallback, cancel: threading.Event) -> SyncResult:
        raise NotImplementedError

    def redownload(self, ref_id: int) -> DownloadedFile:
        raise NotImplementedError

    def download_changed(self, ref_id: int) -> DownloadedFile:
        """Neue ILIAS Version als <name>_ilias_<YYYY-MM-DD>.<ext> neben die vorhandene Datei legen."""
        raise NotImplementedError

    def dismiss_changed(self, ref_id: int) -> None:
        """Änderung ignorieren: nicht mehr melden, bis sich die Datei in ILIAS erneut ändert."""
        raise NotImplementedError

    # Status
    def last_run(self) -> SyncRun | None:
        raise NotImplementedError

    def recent_files(self, limit: int = 50) -> list[DownloadedFile]:
        raise NotImplementedError

    def removed_items(self) -> list[DownloadedFile]:
        raise NotImplementedError

    def changed_items(self) -> list[ChangedFile]:
        raise NotImplementedError

    # Pfade
    def root_dir(self) -> Path | None:
        raise NotImplementedError

    def log_dir(self) -> Path:
        raise NotImplementedError

    def log_file(self) -> Path:
        raise NotImplementedError

    # Autostart
    def autostart_enabled(self) -> bool:
        raise NotImplementedError

    def set_autostart(self, enabled: bool) -> None:
        raise NotImplementedError
