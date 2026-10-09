"""
Zentraler Zustand der GUI.

Tray, Hauptfenster und Scheduler greifen über den Controller auf das Backend
zu. Er stellt sicher, dass immer nur ein Sync oder Dry Run läuft, und leitet
den Zustand (bereit, synchronisiert, Fehler, Login nötig, pausiert) ab.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

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
    UpdateInfo,
)
from ilias_sync.gui import logic
from ilias_sync.gui.icons import IconState
from ilias_sync.gui.worker import Task, run_task

log = logging.getLogger(__name__)

UNERWARTET = "Unerwarteter Fehler, Details im Log."
PROGRESS_INTERVAL_MS = 100   # höchstens 10 Fortschritts Updates pro Sekunde an die Anzeige

# App Updates (GUI_KONTEXT.md 3.5): erste Prüfung 30 s nach dem Start, danach alle 24 Stunden
UPDATE_FIRST_DELAY_MS = 30_000
UPDATE_INTERVAL_MS = 24 * 60 * 60 * 1000
# QSettings Schlüssel: zuletzt automatisch gezeigte bzw. mit "Später" vertagte Version
KEY_UPDATE_VERSION = "updates/shown_version"
KEY_UPDATE_AT = "updates/shown_at"


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
    # App Updates
    update_changed = Signal()              # update_info hat sich geändert (Tray Eintrag)
    update_prompt = Signal(object)         # UpdateInfo, automatisch gefunden und Popup fällig
    update_check_result = Signal(object, str)   # manuelle Prüfung: UpdateInfo oder None, Fehlertext
    update_download_progress = Signal(object)   # Progress
    update_downloaded = Signal(object)          # Path des Installers
    update_download_failed = Signal(str, bool)  # Text, abgebrochen
    quit_requested = Signal()              # App beenden (nach install_update)

    def __init__(self, api, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.config: Config | None = None
        self.config_error: str | None = None
        self.last_error: str | None = None     # anzeigbare Kurzfassung
        self.last_errors: list[str] = []       # einzelne Fehler des letzten Syncs für "Details"
        self.last_result: SyncResult | None = None
        self.last_progress: Progress | None = None
        self.login_needed = False
        self.offline = False
        self.next_sync_at: datetime | None = None
        # Letzter Lauf aus dem Index, zwischengespeichert: Tray, Fenster und Übersicht
        # fragen ihn bei jedem Fortschritt ab, das soll keine Datenbankabfrage sein
        self.last_run: SyncRun | None = None
        self._task: Task | None = None
        self._pending_progress: Progress | None = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(PROGRESS_INTERVAL_MS)
        self._progress_timer.timeout.connect(self._flush_progress)
        self._task_kind: str | None = None   # "sync" oder "dry_run"

        self.update_info: UpdateInfo | None = None
        self.now = datetime.now                # für Tests ersetzbar
        self._update_check: Task | None = None
        self._update_check_manual = False
        self._update_download: Task | None = None
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self._on_update_timer)
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
        if automatic and (self.paused or self.login_needed or self.updating):
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
        self._progress_timer.stop()
        self._pending_progress = None
        self._task = None
        self._task_kind = None
        self.last_progress = None
        self.busy_changed.emit(False)

    def _on_progress(self, p: Progress) -> None:
        # Drosseln: erster Stand sofort, danach höchstens alle 100 ms der jeweils letzte
        self.last_progress = p
        self._pending_progress = p
        if not self._progress_timer.isActive():
            self._flush_progress()
            self._progress_timer.start()

    def _flush_progress(self) -> None:
        p, self._pending_progress = self._pending_progress, None
        if p is None:
            return
        self.progress.emit(p)
        self.state_changed.emit()
        self._progress_timer.start()

    def _on_done(self, kind: str, result, automatic: bool) -> None:
        self._finish_task()
        self.offline = False
        if kind == "sync":
            self.refresh_last_run()
            assert isinstance(result, SyncResult)
            self.last_result = result
            self.last_errors = list(result.errors)
            self.last_error = logic.error_summary(result.errors)
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
                self.last_errors = [text]
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
        self.last_errors = []
        self.reload_config()

    def clear_error(self) -> None:
        self.last_error = None
        self.last_errors = []
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

    # ---------------- App Updates ----------------

    def app_version(self) -> str:
        try:
            return self.api.app_version()
        except Exception:  # noqa: BLE001, Anzeige darf nie abstürzen
            log.exception("App Version nicht lesbar")
            return "?"

    def can_self_update(self) -> bool:
        try:
            return bool(self.api.can_self_update())
        except Exception:  # noqa: BLE001
            log.exception("can_self_update fehlgeschlagen")
            return False

    @property
    def checking_update(self) -> bool:
        return self._update_check is not None

    @property
    def updating(self) -> bool:
        """Ein Update wird gerade heruntergeladen."""
        return self._update_download is not None

    def start_update_checks(self, first_delay_ms: int = UPDATE_FIRST_DELAY_MS) -> None:
        """Automatische Prüfung: einmal nach first_delay_ms, danach alle 24 Stunden.
        Ob geprüft wird, entscheidet Config.check_updates erst beim Auslösen."""
        self._update_timer.start(first_delay_ms)

    def _on_update_timer(self) -> None:
        self._update_timer.start(UPDATE_INTERVAL_MS)
        self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool = False) -> bool:
        """Fragt den Update Server im Hintergrund. Automatisch nur mit check_updates.
        Gibt False zurück, wenn nicht geprüft wird. Eine manuelle Prüfung während einer
        laufenden automatischen bekommt deren Ergebnis."""
        if self.checking_update:
            self._update_check_manual |= manual
            return manual
        if not manual and (self.config is None or not self.config.check_updates):
            return False
        self._update_check_manual = manual
        self._update_check = run_task(
            self.api.check_for_update,
            on_done=self._on_update_checked,
            on_error=self._on_update_check_failed,
        )
        return True

    def _on_update_checked(self, info: UpdateInfo | None) -> None:
        self._update_check = None
        self.update_info = info
        self.update_changed.emit()
        if info is None:
            log.info("Update Prüfung: ILIAS Sync ist aktuell")
        else:
            log.info("Update verfügbar: %s", info.version)
        if self._update_check_manual:
            self.update_check_result.emit(info, "")
        elif info is not None and self._prompt_due(info.version):
            self._remember_shown(info.version)
            self.update_prompt.emit(info)

    def _on_update_check_failed(self, e: Exception) -> None:
        self._update_check = None
        # nie als Fehler Benachrichtigung, nur ins Log (bei manueller Prüfung zusätzlich anzeigen)
        log.warning("Update Prüfung fehlgeschlagen: %s", e)
        if self._update_check_manual:
            self.update_check_result.emit(None, error_text(e))

    def _prompt_due(self, version: str) -> bool:
        settings = QSettings()
        shown_version = settings.value(KEY_UPDATE_VERSION)
        shown_at = logic.parse_iso(settings.value(KEY_UPDATE_AT))
        return logic.update_prompt_due(version, shown_version, shown_at, self.now())

    def _remember_shown(self, version: str) -> None:
        settings = QSettings()
        settings.setValue(KEY_UPDATE_VERSION, version)
        settings.setValue(KEY_UPDATE_AT, self.now().isoformat(timespec="seconds"))

    def snooze_update(self, version: str) -> None:
        """"Später": dieselbe Version erst nach 24 Stunden wieder automatisch zeigen."""
        self._remember_shown(version)
        log.info("Update %s auf später verschoben", version)

    def start_update_download(self, info: UpdateInfo) -> bool:
        if self.updating:
            return False
        log.info("Update %s wird heruntergeladen", info.version)
        self._update_download = run_task(
            lambda progress, cancel: self.api.download_update(info, progress, cancel),
            with_progress=True,
            on_progress=self.update_download_progress.emit,
            on_done=self._on_update_downloaded,
            on_error=self._on_update_download_failed,
        )
        self.state_changed.emit()
        return True

    def cancel_update_download(self) -> None:
        if self._update_download is not None:
            log.info("Update Download abgebrochen")
            self._update_download.cancel()

    def _on_update_downloaded(self, path: Path) -> None:
        self._update_download = None
        self.update_downloaded.emit(path)
        self.state_changed.emit()

    def _on_update_download_failed(self, e: Exception) -> None:
        self._update_download = None
        cancelled = isinstance(e, Cancelled)
        if not cancelled:
            log.warning("Update Download fehlgeschlagen: %s", e)
        self.update_download_failed.emit("Download abgebrochen." if cancelled else error_text(e), cancelled)
        self.state_changed.emit()

    def install_update(self, installer: Path) -> None:
        """Startet die Installation und beendet danach die App. Wirft IliasSyncError."""
        self.api.install_update(installer)
        log.info("Update wird installiert, App wird beendet")
        self.quit_requested.emit()

    # ---------------- Beenden ----------------

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Laufende Vorgänge abbrechen und kurz warten. .part Dateien sind harmlos."""
        self._update_timer.stop()
        if self._update_check is not None:
            self._update_check.wait(1000)   # nicht abbrechbar, kurzer Netzwerkaufruf
        if self._update_download is not None:
            self._update_download.cancel()
            self._update_download.wait(timeout_ms)
        if self._task is not None:
            self._task.cancel()
            self._task.wait(timeout_ms)
