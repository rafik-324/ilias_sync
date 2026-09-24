"""Tab Übersicht: Status, letzter Sync, zuletzt geladene Dateien."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import DownloadedFile, ItemStatus
from ilias_sync.gui import logic
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.icons import status_pixmap
from ilias_sync.gui.tray import open_path
from ilias_sync.gui.widgets import ProgressPanel, error_label, hint_label, show_error
from ilias_sync.gui.worker import run_task

log = logging.getLogger(__name__)

STATUS_TEXT = {
    ItemStatus.OK: "OK",
    ItemStatus.UPDATED: "Aktualisiert",
    ItemStatus.USER_MODIFIED: "Von dir geändert",
    ItemStatus.USER_REMOVED: "Entfernt",
    ItemStatus.ERROR: "Fehler",
}


def _readonly_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    return t


class OverviewTab(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller
        self._files: list[DownloadedFile] = []
        self._removed: list[DownloadedFile] = []

        # Statuszeile
        self.status_icon = QLabel()
        self.status_text = QLabel()
        f = self.status_text.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.status_text.setFont(f)
        self.last_sync = hint_label()
        self.next_sync = hint_label()
        self.last_sync.setWordWrap(False)
        self.next_sync.setWordWrap(False)

        self.btn_sync = QPushButton("Jetzt synchronisieren")
        self.btn_sync.clicked.connect(self._sync)
        self.btn_folder = QPushButton("Uni Ordner öffnen")
        self.btn_folder.clicked.connect(self._open_root)

        head = QHBoxLayout()
        head.addWidget(self.status_icon)
        head.addWidget(self.status_text)
        head.addStretch(1)
        head.addWidget(self.btn_sync)
        head.addWidget(self.btn_folder)

        times = QHBoxLayout()
        times.addWidget(self.last_sync)
        times.addSpacing(24)
        times.addWidget(self.next_sync)
        times.addStretch(1)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self.c.cancel)

        self.error = error_label()
        self.btn_hide_error = QPushButton("Ausblenden")
        self.btn_hide_error.setFlat(True)
        self.btn_hide_error.clicked.connect(self.c.clear_error)
        err_row = QHBoxLayout()
        err_row.addWidget(self.error, 1)
        err_row.addWidget(self.btn_hide_error, 0, Qt.AlignmentFlag.AlignTop)

        # Zuletzt geladen
        self.table = _readonly_table(["Datei", "Kurs", "Zielordner", "Zeit", "Status"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda row, _c: self._open_file(row))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.empty_hint = hint_label("Noch keine Dateien heruntergeladen.")

        # Vom Nutzer entfernt
        self.removed_toggle = QToolButton()
        self.removed_toggle.setCheckable(True)
        self.removed_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.removed_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.removed_toggle.setAutoRaise(True)
        self.removed_toggle.toggled.connect(self._toggle_removed)
        self.removed_table = _readonly_table(["Datei", "Kurs", "Zuletzt geladen", ""])
        rh = self.removed_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.removed_table.setMaximumHeight(160)
        self.removed_table.hide()
        self.removed_hint = hint_label(
            "Diese Dateien hast du verschoben oder gelöscht. Sie werden nicht automatisch erneut geladen."
        )
        self.removed_hint.hide()

        title = QLabel("Zuletzt heruntergeladen")
        title.setStyleSheet("font-weight: 600;")

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addLayout(times)
        lay.addWidget(self.progress)
        lay.addLayout(err_row)
        lay.addSpacing(8)
        lay.addWidget(title)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.empty_hint)
        lay.addWidget(self.removed_toggle)
        lay.addWidget(self.removed_hint)
        lay.addWidget(self.removed_table)

        self.c.state_changed.connect(self.refresh_status)
        self.c.progress.connect(self.progress.update_progress)
        self.c.busy_changed.connect(self._on_busy)
        self.c.files_changed.connect(self.reload_files)
        self.c.config_changed.connect(self.reload_files)

        # relative Zeitangaben regelmässig auffrischen
        self._clock = QTimer(self)
        self._clock.setInterval(30_000)
        self._clock.timeout.connect(self.refresh_status)
        self._clock.start()

        self.refresh_status()
        self.reload_files()
        self._on_busy(self.c.busy)

    # ---------------- Anzeige ----------------

    def refresh_status(self) -> None:
        c = self.c
        self.status_icon.setPixmap(status_pixmap(c.icon_state(), 24))
        self.status_text.setText(c.status_text())
        run = c.api.last_run() if c.config is not None else None
        if run is None:
            self.last_sync.setText("Letzter Sync: noch nie")
        else:
            text = f"Letzter Sync: {logic.relative_time(run.finished_at or run.started_at)}"
            text += f", {run.new} neu, {run.updated} aktualisiert"
            if run.errors:
                text += f", {run.errors} Fehler"
            if run.cancelled:
                text += " (abgebrochen)"
            self.last_sync.setText(text)
        if c.paused:
            self.next_sync.setText("Nächster Sync: pausiert")
        else:
            self.next_sync.setText(f"Nächster Sync: {logic.future_time(c.next_sync_at)}")
        show_error(self.error, c.config_error or c.last_error)
        self.btn_hide_error.setVisible(bool(c.last_error) and not c.config_error)
        self.btn_sync.setEnabled(c.can_sync())
        if c.login_needed:
            self.btn_sync.setText("Anmelden ...")
        else:
            self.btn_sync.setText("Jetzt synchronisieren")

    def reload_files(self) -> None:
        try:
            self._files = self.c.api.recent_files(50) if self.c.config else []
            self._removed = self.c.api.removed_items() if self.c.config else []
        except Exception:  # noqa: BLE001, Anzeige darf nie abstürzen
            log.exception("Dateiliste konnte nicht geladen werden")
            self._files, self._removed = [], []
        root = self.c.api.root_dir() if self.c.config else None

        self.table.setRowCount(len(self._files))
        for row, f in enumerate(self._files):
            cells = [
                f.name,
                logic.course_display_name(f.course_name),
                logic.relative_to_root(f.local_path, root),
                logic.relative_time(f.downloaded_at),
                STATUS_TEXT.get(f.status, f.status.value),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 2):
                    item.setToolTip(str(f.local_path))
                self.table.setItem(row, col, item)
        self.table.setVisible(bool(self._files))
        self.empty_hint.setVisible(not self._files)

        self.removed_toggle.setText(f"Vom Nutzer entfernt ({len(self._removed)})")
        self.removed_toggle.setVisible(bool(self._removed))
        if not self._removed:
            self.removed_toggle.setChecked(False)
        self.removed_table.setRowCount(len(self._removed))
        for row, f in enumerate(self._removed):
            self.removed_table.setItem(row, 0, QTableWidgetItem(f.name))
            self.removed_table.setItem(row, 1, QTableWidgetItem(logic.course_display_name(f.course_name)))
            self.removed_table.setItem(row, 2, QTableWidgetItem(logic.relative_time(f.downloaded_at)))
            btn = QPushButton("Erneut herunterladen")
            btn.clicked.connect(lambda _=False, r=f.ref_id, b=btn: self._redownload(r, b))
            self.removed_table.setCellWidget(row, 3, btn)

    def _toggle_removed(self, on: bool) -> None:
        self.removed_toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self.removed_table.setVisible(on)
        self.removed_hint.setVisible(on)

    def _on_busy(self, busy: bool) -> None:
        if busy and self.c.syncing:
            self.progress.start("Synchronisierung startet ...")
        else:
            self.progress.stop()
        self.refresh_status()

    # ---------------- Aktionen ----------------

    def _sync(self) -> None:
        if self.c.login_needed:
            self.c.login_required.emit()
        else:
            self.c.start_sync()

    def _open_root(self) -> None:
        root = self.c.api.root_dir()
        if root is None or not open_path(root):
            QMessageBox.information(self, "Uni Ordner", "Der Uni Ordner wurde nicht gefunden. Bitte in den Einstellungen prüfen.")

    def _open_file(self, row: int, folder: bool = False) -> None:
        if not 0 <= row < len(self._files):
            return
        path: Path = self._files[row].local_path
        target = path.parent if folder else path
        if not open_path(target):
            QMessageBox.information(
                self, "Datei nicht gefunden",
                f"{path.name} liegt nicht mehr am gespeicherten Ort. Sie wurde vermutlich verschoben oder gelöscht.",
            )

    def _context_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction("Datei öffnen", lambda: self._open_file(row))
        m.addAction("Ordner öffnen", lambda: self._open_file(row, folder=True))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _redownload(self, ref_id: int, btn: QPushButton) -> None:
        btn.setEnabled(False)
        btn.setText("Lädt ...")

        def fail(e: Exception) -> None:
            btn.setEnabled(True)
            btn.setText("Erneut herunterladen")
            QMessageBox.warning(self, "Erneut herunterladen", error_text(e))

        run_task(self.c.api.redownload, ref_id, on_done=lambda _: self.c.redownloaded(), on_error=fail)
