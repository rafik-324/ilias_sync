"""Tray Icon (Windows Infobereich) bzw. Menüleisten Icon (macOS)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ilias_sync.api import SyncResult
from ilias_sync.gui import logic
from ilias_sync.gui.controller import Controller
from ilias_sync.gui.icons import SYNC_FRAMES, IconState, tray_icon

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"

# Ziele für Klicks auf Benachrichtigungen
ZIEL_UEBERSICHT = "overview"
ZIEL_GEAENDERT = "changed"      # Übersicht, Bereich "In ILIAS geändert"
ZIEL_KURSE = "courses"
ZIEL_LOG = "log"
ZIEL_LOGIN = "login"


def open_path(path: Path) -> bool:
    """Öffnet Datei oder Ordner mit dem Standardprogramm. False, wenn nicht vorhanden."""
    if not path.exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class Tray(QObject):
    open_window = Signal(str)     # Tab Name
    open_login = Signal()
    quit_requested = Signal()
    info = Signal(str)            # Hinweis an den Nutzer ohne Fenster (z. B. Datei fehlt)

    def __init__(self, controller: Controller, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._frame = 0
        self._notify_target = ZIEL_UEBERSICHT

        self.icon = QSystemTrayIcon(tray_icon(IconState.READY), self)
        self.icon.activated.connect(self._on_activated)
        self.icon.messageClicked.connect(self._on_message_clicked)

        self._anim = QTimer(self)
        self._anim.setInterval(300)
        self._anim.timeout.connect(self._next_frame)

        self._build_menu()

        controller.state_changed.connect(self.refresh)
        controller.sync_finished.connect(self._on_sync_finished)
        controller.error.connect(self._on_error)
        controller.login_required.connect(self._on_login_required)
        # Anzahl nur bei neuen Daten abfragen, nicht bei jedem Fortschrittsschritt
        controller.files_changed.connect(self.refresh_changed)
        controller.config_changed.connect(self.refresh_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.refresh())
        self.refresh()
        self.refresh_changed()

    # ---------------- Menü ----------------

    def _build_menu(self) -> None:
        m = QMenu()
        self.act_sync = QAction("Jetzt synchronisieren", m)
        self.act_sync.triggered.connect(self._sync_now)
        m.addAction(self.act_sync)

        self.act_last = QAction("Letzter Sync: noch nie", m)
        self.act_last.setEnabled(False)
        m.addAction(self.act_last)

        self.menu_new = QMenu("Neue Dateien", m)
        self.menu_new.aboutToShow.connect(self._fill_new_files)
        m.addMenu(self.menu_new)

        self.act_changed = QAction("", m)
        self.act_changed.triggered.connect(lambda: self.open_window.emit(ZIEL_GEAENDERT))
        self.act_changed.setVisible(False)
        m.addAction(self.act_changed)

        m.addSeparator()
        act_folder = QAction("Uni Ordner öffnen", m)
        act_folder.triggered.connect(self._open_root)
        m.addAction(act_folder)

        act_window = QAction("Fenster öffnen", m)
        act_window.triggered.connect(lambda: self.open_window.emit(ZIEL_UEBERSICHT))
        m.addAction(act_window)

        self.act_pause = QAction("Sync pausieren", m)
        self.act_pause.triggered.connect(self._toggle_pause)
        m.addAction(self.act_pause)

        m.addSeparator()
        act_quit = QAction("Beenden", m)
        act_quit.triggered.connect(self.quit_requested)
        m.addAction(act_quit)

        if not IS_MAC:
            # runde Ecken im eigenen Stil (unter macOS zeichnet das System das Menü)
            m.setWindowFlags(m.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
            m.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.menu = m
        self.icon.setContextMenu(m)

    def _fill_new_files(self) -> None:
        self.menu_new.clear()
        try:
            files = self.controller.api.recent_files(10)
        except Exception:  # noqa: BLE001, Menü darf nie abstürzen
            log.exception("Neue Dateien konnten nicht geladen werden")
            files = []
        if not files:
            a = self.menu_new.addAction("Noch keine Dateien")
            a.setEnabled(False)
            return
        for f in files:
            label = f"{f.name} ({logic.course_display_name(f.course_name)})"
            a = self.menu_new.addAction(label)
            a.setToolTip(str(f.local_path))
            a.triggered.connect(lambda _=False, p=f.local_path: self._open_file(p))

    # ---------------- Aktualisierung ----------------

    def refresh(self) -> None:
        c = self.controller
        state = c.icon_state()
        if state == IconState.SYNCING:
            if not self._anim.isActive():
                self._frame = 0
                self._anim.start()
        else:
            self._anim.stop()
            self.icon.setIcon(tray_icon(state))

        tooltip = f"ILIAS Sync: {c.status_text()}"
        p = c.last_progress
        if state == IconState.SYNCING and p and p.total:
            tooltip += f" {p.current}/{p.total}"
        self.icon.setToolTip(tooltip)

        self.act_sync.setEnabled(not c.busy and c.config is not None)
        self.act_pause.setText("Sync fortsetzen" if c.paused else "Sync pausieren")
        self.act_pause.setEnabled(c.config is not None)

        run = c.api.last_run() if c.config is not None else None
        if run is None:
            self.act_last.setText("Letzter Sync: noch nie")
        else:
            when = logic.relative_time(run.finished_at or run.started_at)
            n = run.new
            files = "neue Datei" if n == 1 else "neue Dateien"
            self.act_last.setText(f"Letzter Sync: {when} ({n} {files})")

    def refresh_changed(self) -> None:
        n = self.controller.changed_count()
        self.act_changed.setText(logic.changed_count_text(n))
        self.act_changed.setVisible(n > 0)

    def _next_frame(self) -> None:
        self._frame = (self._frame + 1) % SYNC_FRAMES
        self.icon.setIcon(tray_icon(IconState.SYNCING, self._frame))

    # ---------------- Aktionen ----------------

    def _sync_now(self) -> None:
        if self.controller.login_needed:
            self.open_login.emit()
            return
        self.controller.start_sync()

    def _toggle_pause(self) -> None:
        self.controller.set_paused(not self.controller.paused)

    def _open_root(self) -> None:
        root = self.controller.api.root_dir()
        if root is None or not open_path(root):
            self.notify("Der Uni Ordner wurde nicht gefunden. Bitte in den Einstellungen prüfen.", ZIEL_UEBERSICHT)

    def _open_file(self, path: Path) -> None:
        if not open_path(path):
            self.notify(f"{path.name} ist nicht mehr am gespeicherten Ort. "
                        "Im Fenster kann die Datei erneut heruntergeladen werden.", ZIEL_UEBERSICHT)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Windows: Linksklick öffnet das Fenster. macOS: Klick zeigt immer das Menü
        if not IS_MAC and reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_window.emit(ZIEL_UEBERSICHT)

    # ---------------- Benachrichtigungen ----------------

    def notify(self, text: str, target: str, error: bool = False) -> None:
        self._notify_target = target
        icon = QSystemTrayIcon.MessageIcon.Warning if error else QSystemTrayIcon.MessageIcon.Information
        self.icon.showMessage("ILIAS Sync", text, icon, 8000)

    def _on_message_clicked(self) -> None:
        if self._notify_target == ZIEL_LOGIN:
            self.open_login.emit()
        else:
            self.open_window.emit(self._notify_target)

    def _on_sync_finished(self, result: SyncResult, _automatic: bool) -> None:
        # Mehrere Meldungen würden sich unter Windows gegenseitig ersetzen,
        # daher alles in einer Benachrichtigung bündeln
        lines: list[str] = []
        target = ZIEL_UEBERSICHT
        if text := logic.bundle_new_files(result.new_files):
            lines.append(text)
        if text := logic.bundle_changed_files(result.changed_files):
            lines.append(text)
            target = ZIEL_GEAENDERT
        if text := logic.unassigned_course_text([c.name for c in result.new_unassigned_courses]):
            lines.append(text)
            target = ZIEL_KURSE
        if result.errors:
            n = len(result.errors)
            lines.append(f"{n} {'Fehler' if n == 1 else 'Fehler'} beim Sync, Details im Log.")
            target = ZIEL_LOG
        if lines:
            self.notify("\n".join(lines), target, error=bool(result.errors))

    def _on_error(self, text: str, _automatic: bool) -> None:
        self.notify(f"Sync fehlgeschlagen: {text}", ZIEL_LOG, error=True)

    def _on_login_required(self) -> None:
        self.notify("Anmeldung bei ILIAS nötig.", ZIEL_LOGIN, error=True)

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()
