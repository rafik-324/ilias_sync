"""Hauptfenster: Seitenleiste mit Navigation, rechts die jeweilige Seite."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ilias_sync import __version__
from ilias_sync.gui import logic, theme
from ilias_sync.gui.controller import Controller
from ilias_sync.gui.icons import IconState, app_icon, dot, nav_icon
from ilias_sync.gui.macos import activate_app
from ilias_sync.gui.tab_courses import CoursesTab
from ilias_sync.gui.tab_log import LogTab
from ilias_sync.gui.tab_overview import OverviewTab
from ilias_sync.gui.tab_preview import PreviewTab
from ilias_sync.gui.tab_settings import SettingsTab
from ilias_sync.gui.tray import ZIEL_GEAENDERT
from ilias_sync.gui.widgets import role_label

TAB_NAMES = ["overview", "courses", "preview", "settings", "log"]
NAV_LABELS = ["Übersicht", "Kurse", "Vorschau", "Einstellungen", "Log"]


class MainWindow(QMainWindow):
    login_requested = Signal()
    quit_requested = Signal()

    def __init__(self, controller: Controller) -> None:
        super().__init__()
        self.c = controller
        self.setWindowTitle("ILIAS Sync")
        self.setWindowIcon(app_icon())
        self.resize(1040, 760)   # Platz für "In ILIAS geändert" und mehrere zuletzt geladene Dateien
        self.setMinimumSize(820, 520)

        self.overview = OverviewTab(controller)
        self.courses = CoursesTab(controller)
        self.preview = PreviewTab(controller)
        self.settings = SettingsTab(controller)
        self.log = LogTab(controller)
        self.settings.login_requested.connect(self.login_requested)

        self.pages = QStackedWidget()
        for page in (self.overview, self.courses, self.preview, self.settings, self.log):
            self.pages.addWidget(page)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_sidebar())
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(central)

        # Cmd+W / Cmd+Q auf macOS (Strg+F4 bzw. Strg+Q anderswo). Beenden läuft über
        # denselben Weg wie im Tray Menü, damit ein laufender Sync sauber abbricht
        QShortcut(QKeySequence.StandardKey.Close, self, self.close)
        QShortcut(QKeySequence.StandardKey.Quit, self, self.quit_requested.emit)

        self._current_index = 0
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._on_nav_changed)

        self.c.config_changed.connect(self._mark_unassigned)
        self.c.state_changed.connect(self._refresh_status)
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._refresh_theme())
        self._mark_unassigned()
        self._refresh_status()

        geometry = QSettings().value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

    # ---------------- Seitenleiste ----------------

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(212)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 18, 0, 14)
        v.setSpacing(0)

        brand = QHBoxLayout()
        brand.setContentsMargins(20, 0, 16, 0)
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(28, 28))
        brand.addWidget(logo)
        name = QLabel("ILIAS Sync")
        name.setObjectName("brand")
        brand.addWidget(name, 1)
        v.addLayout(brand)
        v.addSpacing(22)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setCursor(Qt.CursorShape.PointingHandCursor)
        for label in NAV_LABELS:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 38))
            self.nav.addItem(item)
        self._update_nav_icons()
        v.addWidget(self.nav, 1)

        # Status unten in der Seitenleiste
        pill = QFrame()
        pill.setObjectName("statusPill")
        pl = QVBoxLayout(pill)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.status_dot = QLabel()
        row.addWidget(self.status_dot)
        self.status_label = role_label("", "section")
        row.addWidget(self.status_label, 1)
        pl.addLayout(row)
        self.status_sub = role_label("", "faint")
        self.status_sub.setWordWrap(True)
        pl.addWidget(self.status_sub)

        wrap = QVBoxLayout()
        wrap.setContentsMargins(12, 0, 12, 0)
        wrap.addWidget(pill)
        v.addLayout(wrap)
        v.addSpacing(10)
        version = role_label(f"Version {__version__}", "faint")
        version.setContentsMargins(22, 0, 0, 0)
        v.addWidget(version)
        return side

    def _update_nav_icons(self) -> None:
        t = theme.tokens()
        for i, name in enumerate(TAB_NAMES):
            self.nav.item(i).setIcon(nav_icon(name, t.muted, t.accent))

    def _refresh_theme(self) -> None:
        self._update_nav_icons()
        self._refresh_status()

    def _refresh_status(self) -> None:
        t = theme.tokens()
        state = self.c.icon_state()
        color = {
            IconState.READY: t.success,
            IconState.SYNCING: t.accent,
            IconState.ERROR: t.danger,
            IconState.LOGIN: t.warning,
            IconState.PAUSED: t.faint,
        }[state]
        if state == IconState.READY and self.c.offline:
            color = t.faint
        self.status_dot.setPixmap(dot(color, 8))
        text = {
            IconState.READY: "Offline" if self.c.offline else "Bereit",
            IconState.SYNCING: "Vorschau läuft" if self.c.previewing else "Synchronisiert",
            IconState.ERROR: "Fehler",
            IconState.LOGIN: "Anmeldung nötig",
            IconState.PAUSED: "Pausiert",
        }[state]
        self.status_label.setText(text)
        run = self.c.last_run
        when = logic.relative_time(run.finished_at or run.started_at) if run else "noch nie"
        self.status_sub.setText(f"Letzter Sync {when}")

    def _mark_unassigned(self) -> None:
        cfg = self.c.config
        n = sum(1 for c in cfg.courses if c.local_dir is None) if cfg else 0
        self.nav.item(1).setText(f"Kurse ({n} neu)" if n else "Kurse")

    # ---------------- Navigation ----------------

    def current_page(self) -> QWidget:
        return self.pages.currentWidget()

    def _can_leave(self, widget) -> bool:
        if widget in (self.courses, self.settings):
            return widget.confirm_leave()
        return True

    def _on_nav_changed(self, index: int) -> None:
        if index < 0:
            return
        previous = self.pages.widget(self._current_index)
        if index != self._current_index and not self._can_leave(previous):
            # Wechsel rückgängig machen, ohne das Signal erneut auszulösen
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(self._current_index)
            self.nav.blockSignals(False)
            return
        self._current_index = index
        self.pages.setCurrentIndex(index)

    def select(self, index: int) -> None:
        self.nav.setCurrentRow(index)

    def show_tab(self, name: str) -> None:
        if name in TAB_NAMES:
            self.select(TAB_NAMES.index(name))
        elif name == ZIEL_GEAENDERT:
            # Übersicht, Bereich "In ILIAS geändert" hervorheben
            self.select(0)
            self.overview.focus_changed()
        self.showNormal()
        activate_app()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        # Schliessen versteckt nur das Fenster, die App läuft im Tray weiter
        if not self._can_leave(self.current_page()):
            event.ignore()
            return
        QSettings().setValue("window/geometry", self.saveGeometry())
        event.ignore()
        self.hide()
