"""
Zentraler Zustand der GUI.

Tray, Hauptfenster und Scheduler greifen über den Controller auf das Backend
zu. Er stellt sicher, dass immer nur ein Sync oder Dry Run läuft, und leitet
den Zustand (bereit, synchronisiert, Fehler, Login nötig, pausiert) ab.
"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from ilias_sync.api import (
    Cancelled,
    Config,
    ConfigError,
    IliasSyncError,
    InvalidCredentialsError,
    LoginError,
    NetworkError,
    Progress,
    SessionState,
    SyncResult,
    SyncRun,
)
from ilias_sync.gui.icons import IconState
from ilias_sync.gui.worker import Task, run_task

log = logging.getLogger(__name__)

UNERWARTET = "Unerwarteter Fehler, Details im Log."


def error_text(e: Exception) -> str:
    """Anzeigbarer Fehlertext. Nur eigene Fehler werden wörtlich gezeigt."""
    if isinstance(e, IliasSyncError):
        return str(e) or UNERWARTET
    return UNERWARTET


class Controller(QObject):
    state_changed = Signal()
    progress = Signal(object)              # Progress
    busy_changed = Signal(bool)
    sync_finished = Signal(object, bool)   # SyncResult, automatisch ausgelöst
    dry_run_finished = Signal(object)      # list[PlannedAction]
    dry_run_failed = Signal(str)
    error = Signal(str, bool)              # Text, automatisch ausgelöst
    login_required = Signal()
    config_changed = Signal()
    files_changed = Signal()               # recent_files / removed_items / changed_items neu laden

    def __init__(self, api, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.config: Config | None = None
        self.config_error: str | None = None
        self.last_error: str | None = None
        self.last_result: SyncResult | None = None
        self.last_progress: Progress | None = None
        self.login_needed = False
        self.offline = False
        self.next_sync_at: datetime | None = None
        # Letzter Lauf aus dem Index, zwischengespeichert: Tray, Fenster und Übersicht
        # fragen ihn bei jedem Fortschritt ab, das soll keine Datenbankabfrage sein
        self.last_run: SyncRun | None = None
        self._task: Task | None = None
        self._task_kind: str | None = None   # "sync" oder "dry_run"
        self.reload_config()

    # ---------------- Config ----------------

    def reload_config(self) -> None:
        try:
            self.config = self.api.load_config()
            self.config_error = None
        except ConfigError as e:
            self.config = None
            self.config_error = str(e)
            log.warning("Config Fehler: %s", e)
        self.refresh_last_run()
        self.config_changed.emit()
        self.state_changed.emit()

    def save_config(self, cfg: Config) -> None:
        """Speichert die Config. Wirft ConfigError zur Anzeige im Aufrufer."""
        self.api.save_config(cfg)
        self.config = cfg
        self.config_error = None
        self.refresh_last_run()   # neuer Uni Ordner hat einen eigenen Index
        self.config_changed.emit()
        self.state_changed.emit()

    def refresh_last_run(self) -> None:
        """Letzten Lauf neu aus dem Backend lesen (nach Sync, Config Wechsel, Login)."""
        if self.config is None:
            self.last_run = None
            return
        try:
            self.last_run = self.api.last_run()
        except Exception:  # noqa: BLE001, Anzeige darf nie abstürzen
            log.exception("Letzter Sync konnte nicht gelesen werden")
            self.last_run = None

    @property
    def paused(self) -> bool:
        return bool(self.config and self.config.sync_paused)

    def set_paused(self, paused: bool) -> None:
        if self.config is None:
            return
        self.config.sync_paused = paused
        try:
            self.api.save_config(self.config)
        except IliasSyncError as e:
            self.error.emit(error_text(e), False)
        log.info("Sync %s", "pausiert" if paused else "fortgesetzt")
        self.config_changed.emit()
        self.state_changed.emit()

    # ---------------- Zustand ----------------

    @property
    def busy(self) -> bool:
        return self._task is not None

    @property
    def syncing(self) -> bool:
        return self._task_kind == "sync"

    @property
    def previewing(self) -> bool:
        return self._task_kind == "dry_run"

    def icon_state(self) -> IconState:
        if self.busy:
            return IconState.SYNCING
        if self.login_needed:
            return IconState.LOGIN
        if self.config_error or self.last_error:
            return IconState.ERROR
        if self.paused:
            return IconState.PAUSED
        return IconState.READY

    def status_text(self) -> str:
        state = self.icon_state()
        if state == IconState.SYNCING:
            p = self.last_progress
            if self._task_kind == "dry_run":
                return "Vorschau wird erstellt ..."
            return f"Synchronisiert ... ({p.phase})" if p else "Synchronisiert ..."
        if state == IconState.LOGIN:
            return "Anmeldung nötig"
        if state == IconState.ERROR:
            return "Fehler, Details im Fenster"
        if state == IconState.PAUSED:
            return "Pausiert"
        if self.offline:
            return "Offline, nächster Versuch beim nächsten Intervall"
        return "Bereit"

    def can_sync(self) -> bool:
        return not self.busy and self.config is not None and self.api.config_exists()

    # ---------------- Aktionen ----------------

    def start_sync(self, automatic: bool = False) -> bool:
        """Startet einen Sync. Gibt False zurück, wenn gerade nicht möglich."""
        if not self.can_sync():
            return False
        if automatic and (self.paused or self.login_needed):
            return False
        log.info("Sync gestartet (%s)", "automatisch" if automatic else "manuell")
        self._start("sync", self.api.sync, automatic)
        return True

    def start_dry_run(self) -> bool:
        if not self.can_sync():
            return False
        self._start("dry_run", self.api.dry_run, False)
        return True

    def _start(self, kind: str, fn, automatic: bool) -> None:
        self._task_kind = kind
        self.last_progress = None
        self._task = run_task(
            fn,
            with_progress=True,
            on_progress=self._on_progress,
            on_done=lambda r: self._on_done(kind, r, automatic),
            on_error=lambda e: self._on_error(kind, e, automatic),
        )
        self.busy_changed.emit(True)
        self.state_changed.emit()

    def cancel(self) -> None:
        if self._task is not None:
            log.info("Abbruch angefordert")
            self._task.cancel()

    def _finish_task(self) -> None:
        self._task = None
        self._task_kind = None
        self.last_progress = None
        self.busy_changed.emit(False)

    def _on_progress(self, p: Progress) -> None:
        self.last_progress = p
        self.progress.emit(p)
        self.state_changed.emit()

    def _on_done(self, kind: str, result, automatic: bool) -> None:
        self._finish_task()
        self.offline = False
        if kind == "sync":
            self.refresh_last_run()
            assert isinstance(result, SyncResult)
            self.last_result = result
            self.last_error = "; ".join(result.errors) if result.errors else None
            self.login_needed = False
            self.sync_finished.emit(result, automatic)
            self.files_changed.emit()
        else:
            self.dry_run_finished.emit(result)
        self.state_changed.emit()

    def _on_error(self, kind: str, e: Exception, automatic: bool) -> None:
        self._finish_task()
        text = error_text(e)
        if isinstance(e, Cancelled):
            log.info("Vorgang abgebrochen")
            if kind == "dry_run":
                self.dry_run_failed.emit("Vorschau abgebrochen.")
        elif isinstance(e, InvalidCredentialsError):
            self.login_needed = True
            if kind == "dry_run":
                self.dry_run_failed.emit(text)
            self.login_required.emit()
        elif isinstance(e, NetworkError):
            self.offline = True
            if kind == "dry_run":
                self.dry_run_failed.emit(text)
            elif not automatic:
                self.error.emit(text, automatic)
        else:
            if isinstance(e, LoginError):
                self.login_needed = True
            if kind == "dry_run":
                self.dry_run_failed.emit(text)
            else:
                self.last_error = text
                self.error.emit(text, automatic)
        if kind == "sync":
            self.refresh_last_run()
        self.files_changed.emit()
        self.state_changed.emit()

    # ---------------- Session ----------------

    def check_session(self) -> None:
        """Prüft die Session im Hintergrund (beim Start)."""
        def done(state: SessionState) -> None:
            self.login_needed = state == SessionState.LOGIN_REQUIRED
            self.offline = state == SessionState.OFFLINE
            if self.login_needed:
                self.login_required.emit()
            self.state_changed.emit()

        run_task(self.api.check_session, on_done=done, on_error=lambda e: log.warning("Session Prüfung: %s", e))

    def logged_in(self) -> None:
        """Nach erfolgreichem Login aus dem Dialog."""
        self.login_needed = False
        self.last_error = None
        self.reload_config()

    def clear_error(self) -> None:
        self.last_error = None
        self.state_changed.emit()

    def redownloaded(self) -> None:
        self.files_changed.emit()

    def changed_handled(self) -> None:
        """Nach Herunterladen oder Ignorieren einer in ILIAS geänderten Datei."""
        self.files_changed.emit()

    def changed_count(self) -> int:
        """Anzahl der in ILIAS geänderten, noch nicht behandelten Dateien."""
        if self.config is None:
            return 0
        try:
            return len(self.api.changed_items())
        except Exception:  # noqa: BLE001, Anzeige darf nie abstürzen
            log.exception("Geänderte Dateien konnten nicht gelesen werden")
            return 0

    # ---------------- Beenden ----------------

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Laufenden Vorgang abbrechen und kurz warten. .part Dateien sind harmlos."""
        if self._task is not None:
            self._task.cancel()
            self._task.wait(timeout_ms)
