"""Hauptfenster mit den Tabs Übersicht, Kurse, Vorschau, Einstellungen, Log."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from ilias_sync.gui.controller import Controller
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.tab_courses import CoursesTab
from ilias_sync.gui.tab_log import LogTab
from ilias_sync.gui.tab_overview import OverviewTab
from ilias_sync.gui.tab_preview import PreviewTab
from ilias_sync.gui.tab_settings import SettingsTab

TAB_NAMES = ["overview", "courses", "preview", "settings", "log"]


class MainWindow(QMainWindow):
    login_requested = Signal()

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle("ILIAS Sync")
        self.setWindowIcon(app_icon())
        self.resize(820, 580)
        self.setMinimumSize(640, 420)

        self.overview = OverviewTab(controller)
        self.courses = CoursesTab(controller)
        self.preview = PreviewTab(controller)
        self.settings = SettingsTab(controller)
        self.log = LogTab(controller)
        self.settings.login_requested.connect(self.login_requested)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.overview, "Übersicht")
        self.tabs.addTab(self.courses, "Kurse")
        self.tabs.addTab(self.preview, "Vorschau")
        self.tabs.addTab(self.settings, "Einstellungen")
        self.tabs.addTab(self.log, "Log")
        self._current_index = 0
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        self.c.config_changed.connect(self._mark_unassigned)
        self._mark_unassigned()

        geometry = QSettings().value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

    def _mark_unassigned(self) -> None:
        cfg = self.c.config
        n = sum(1 for c in cfg.courses if c.local_dir is None) if cfg else 0
        self.tabs.setTabText(1, f"Kurse ({n} neu)" if n else "Kurse")

    def _can_leave(self, widget) -> bool:
        if widget in (self.courses, self.settings):
            return widget.confirm_leave()
        return True

    def _on_tab_changed(self, index: int) -> None:
        previous = self.tabs.widget(self._current_index)
        if index != self._current_index and not self._can_leave(previous):
            # Wechsel rückgängig machen, ohne das Signal erneut auszulösen
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self._current_index)
            self.tabs.blockSignals(False)
            return
        self._current_index = index

    def show_tab(self, name: str) -> None:
        if name in TAB_NAMES:
            index = TAB_NAMES.index(name)
            self.tabs.setCurrentIndex(index)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        # Schliessen versteckt nur das Fenster, die App läuft im Tray weiter
        if not self._can_leave(self.tabs.currentWidget()):
            event.ignore()
            return
        QSettings().setValue("window/geometry", self.saveGeometry())
        event.ignore()
        self.hide()
