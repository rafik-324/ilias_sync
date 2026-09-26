"""Seite Übersicht: Status, Kennzahlen, in ILIAS geänderte Dateien, zuletzt geladene Dateien."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
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

from ilias_sync.api import ChangedFile, DownloadedFile, IliasSyncError, ItemStatus, NetworkError
from ilias_sync.gui import logic, theme
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.icons import IconState, status_badge
from ilias_sync.gui.tray import open_path
from ilias_sync.gui.widgets import Card, ElideDelegate, ErrorListDialog, Page, Spinner, ProgressPanel, button, error_label, hint_label, role_label, show_error
from ilias_sync.gui.worker import run_task

log = logging.getLogger(__name__)

STATUS_TEXT = {
    ItemStatus.OK: "",
    ItemStatus.UPDATED: "Aktualisiert",
    ItemStatus.USER_MODIFIED: "Von dir geändert",
    ItemStatus.USER_REMOVED: "Entfernt",
    ItemStatus.ERROR: "Fehler",
    ItemStatus.ILIAS_CHANGED: "In ILIAS geändert",
}

CHANGED_ROW = 44
COURSE_COL = 180
FOLDER_COL = 230
CHANGED_VISIBLE_ROWS = 2   # weitere per Scrollen, damit die Dateiliste Platz behält

HERO_TEXT = {
    IconState.READY: "Alles aktuell",
    IconState.SYNCING: "Synchronisiert ...",
    IconState.ERROR: "Es gab Probleme",
    IconState.LOGIN: "Anmeldung nötig",
    IconState.PAUSED: "Sync pausiert",
}


def list_table(headers: list[str]) -> QTableWidget:
    """Schlichte Tabelle ohne Gitter, mit ruhigen Zeilen."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(36)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.horizontalHeader().setHighlightSections(False)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    return t


def download_all_changed(api, ref_ids: list[int]) -> list[str]:
    """Lädt alle neuen Versionen nacheinander (im Worker). Liefert Fehlertexte einzelner Dateien.

    Bei Netzwerkfehlern wird abgebrochen, die übrigen würden ebenfalls scheitern.
    """
    failed: list[str] = []
    for ref_id in ref_ids:
        try:
            api.download_changed(ref_id)
        except NetworkError:
            raise
        except IliasSyncError as e:
            failed.append(str(e))
    return failed


def _cell_widget(*widgets: QWidget) -> QWidget:
    cell = QWidget()
    cl = QHBoxLayout(cell)
    cl.setContentsMargins(4, 4, 4, 4)
    cl.setSpacing(6)
    for w in widgets:
        cl.addWidget(w)
    return cell


class StatTile(Card):
    def __init__(self, label: str) -> None:
        super().__init__(margins=14, spacing=0)
        self.value = role_label("0", "stat")
        self.lay.addWidget(self.value)
        self.lay.addWidget(role_label(label, "muted"))

    def set(self, value: int, color: str | None = None) -> None:
        self.value.setText(str(value))
        self.value.setStyleSheet(f"color: {color};" if color else "")


class OverviewTab(Page):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__("Übersicht", "Neue Materialien aus ILIAS landen automatisch in deinem Uni Ordner.", parent)
        self.c = controller
        self._files: list[DownloadedFile] = []
        self._removed: list[DownloadedFile] = []
        self._changed: list[ChangedFile] = []

        self.btn_folder = button("Uni Ordner öffnen")
        self.btn_folder.clicked.connect(self._open_root)
        self.actions.addWidget(self.btn_folder)

        # Status Karte
        hero = Card(margins=20, spacing=14)
        top = QHBoxLayout()
        top.setSpacing(16)
        self.badge = QLabel()
        self.badge.setFixedSize(48, 48)
        top.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        # während eines Syncs statt des Symbols ein flüssiger Lade Kreis
        self.spinner = Spinner(48, background=True)
        self.spinner.hide()
        top.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.status_text = role_label("", "hero")
        texts.addWidget(self.status_text)
        self.times = role_label("", "muted")
        texts.addWidget(self.times)
        top.addLayout(texts, 1)
        self.btn_sync = button("Jetzt synchronisieren", "primary")
        self.btn_sync.setMinimumHeight(36)
        self.btn_sync.clicked.connect(self._sync)
        top.addWidget(self.btn_sync, 0, Qt.AlignmentFlag.AlignVCenter)
        hero.lay.addLayout(top)
        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self.c.cancel)
        hero.lay.addWidget(self.progress)
        self.body.addWidget(hero)

        # Fehler
        self.error = error_label()
        self.btn_error_details = button("Details anzeigen")
        self.btn_error_details.clicked.connect(self._show_error_details)
        self.btn_hide_error = button("Ausblenden", "ghost")
        self.btn_hide_error.clicked.connect(self.c.clear_error)
        err_row = QHBoxLayout()
        err_row.addWidget(self.error, 1)
        err_row.addWidget(self.btn_error_details, 0, Qt.AlignmentFlag.AlignTop)
        err_row.addWidget(self.btn_hide_error, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(err_row)

        # Kennzahlen
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.stat_new = StatTile("Neu beim letzten Sync")
        self.stat_changed = StatTile("In ILIAS geändert")
        self.stat_errors = StatTile("Fehler")
        for tile in (self.stat_new, self.stat_changed, self.stat_errors):
            stats.addWidget(tile)
        self.body.addLayout(stats)

        # In ILIAS geändert: wird nie automatisch geladen, der Nutzer entscheidet
        self.changed_card = Card(margins=0, spacing=0)
        chead = QHBoxLayout()
        chead.setContentsMargins(18, 10, 18, 0)
        self.changed_title = role_label("", "section")
        chead.addWidget(self.changed_title)
        chead.addStretch(1)
        self.btn_all_changed = button("Alle herunterladen")
        self.btn_all_changed.clicked.connect(self._download_all_changed)
        chead.addWidget(self.btn_all_changed)
        self.changed_card.lay.addLayout(chead)
        changed_hint = hint_label(
            "Wird nicht automatisch geladen. Die neue Version kommt als <i>name_ilias_Datum</i> neben deine Datei."
        )
        changed_hint.setToolTip("Die vorhandene Datei bleibt unverändert. Ignorieren blendet den Eintrag aus, "
                                "bis sich die Datei in ILIAS erneut ändert.")
        changed_hint.setContentsMargins(18, 0, 18, 4)
        self.changed_card.lay.addWidget(changed_hint)
        self.changed_table = list_table(["Datei", "Kurs", "Geändert in ILIAS", ""])
        ch = self.changed_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            ch.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        ch.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.changed_table.setColumnWidth(3, 250)
        self.changed_table.verticalHeader().setDefaultSectionSize(CHANGED_ROW)
        self.changed_card.lay.addWidget(self.changed_table)
        self.changed_card.hide()
        self.body.addWidget(self.changed_card)

        # Zuletzt geladen
        recent = Card(margins=0, spacing=0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 14, 18, 6)
        head.addWidget(role_label("Zuletzt heruntergeladen", "section"))
        head.addStretch(1)
        self.count_label = role_label("", "faint")
        head.addWidget(self.count_label)
        recent.lay.addLayout(head)
        self.table = list_table(["Datei", "Kurs", "Ordner", "Zeit", ""])
        h = self.table.horizontalHeader()
        # Datei am breitesten, Kurs und Ordner fest (vom Nutzer verschiebbar)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, COURSE_COL)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, FOLDER_COL)
        # Datei: Endung bleibt sichtbar. Ordner: vorn kürzen, das Ende ist aussagekräftig
        self.table.setItemDelegateForColumn(0, ElideDelegate(Qt.TextElideMode.ElideRight, True, self.table))
        self.table.setItemDelegateForColumn(1, ElideDelegate(Qt.TextElideMode.ElideRight, False, self.table))
        self.table.setItemDelegateForColumn(2, ElideDelegate(Qt.TextElideMode.ElideLeft, False, self.table))
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellDoubleClicked.connect(lambda row, _c: self._open_file(row))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.setToolTip("Doppelklick öffnet die Datei, Rechtsklick zeigt sie im Ordner")
        recent.lay.addWidget(self.table, 1)
        self.empty_hint = hint_label("Noch keine Dateien heruntergeladen. Starte den ersten Sync oben.")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setContentsMargins(0, 30, 0, 36)
        recent.lay.addWidget(self.empty_hint)
        self.body.addWidget(recent, 1)

        # Vom Nutzer entfernt
        self.removed_card = Card(margins=12, spacing=8)
        self.removed_toggle = QToolButton()
        self.removed_toggle.setCheckable(True)
        self.removed_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.removed_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.removed_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.removed_toggle.toggled.connect(self._toggle_removed)
        self.removed_card.lay.addWidget(self.removed_toggle)
        self.removed_hint = hint_label(
            "Diese Dateien hast du verschoben oder gelöscht. Sie werden nicht automatisch erneut geladen."
        )
        self.removed_hint.hide()
        self.removed_card.lay.addWidget(self.removed_hint)
        self.removed_table = list_table(["Datei", "Kurs", "Zuletzt geladen", ""])
        rh = self.removed_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.removed_table.verticalHeader().setDefaultSectionSize(44)
        rh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.removed_table.setColumnWidth(3, 190)
        self.removed_table.hide()
        self.removed_card.lay.addWidget(self.removed_table)
        self.body.addWidget(self.removed_card)

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
        # Animation des Status Symbols während eines Syncs

        self.refresh_status()
        self.reload_files()
        self._on_busy(self.c.busy)

    # ---------------- Anzeige ----------------

    def _badge_colors(self, state: IconState) -> tuple[str, str]:
        t = theme.tokens()
        if state == IconState.READY and self.c.offline:
            return t.muted, t.surface2
        return {
            IconState.READY: (t.success, t.success_soft),
            IconState.SYNCING: (t.accent, t.accent_soft),
            IconState.ERROR: (t.danger, t.danger_soft),
            IconState.LOGIN: (t.warning, t.warning_soft),
            IconState.PAUSED: (t.muted, t.surface2),
        }[state]

    def _draw_badge(self) -> None:
        state = self.c.icon_state()
        syncing = state == IconState.SYNCING
        self.badge.setVisible(not syncing)
        self.spinner.setVisible(syncing)
        if syncing:
            self.spinner.start()
            return
        self.spinner.stop()
        fg, bg = self._badge_colors(state)
        self.badge.setPixmap(status_badge(state, fg, bg, 48))

    def refresh_status(self) -> None:
        c = self.c
        state = c.icon_state()
        self._draw_badge()
        if state == IconState.READY and c.offline:
            self.status_text.setText("Offline")
        elif state == IconState.SYNCING and c.last_progress:
            self.status_text.setText(f"{c.last_progress.phase} ...")
        else:
            self.status_text.setText(HERO_TEXT[state])

        run = c.last_run
        last = logic.relative_time(run.finished_at or run.started_at) if run else "noch nie"
        if c.paused:
            nxt = "pausiert"
        else:
            nxt = logic.future_time(c.next_sync_at)
        self.times.setText(f"Letzter Sync {last}    Nächster Sync {nxt}")

        t = theme.tokens()
        self.stat_new.set(run.new if run else 0, t.success if run and run.new else None)
        self.stat_errors.set(run.errors if run else 0, t.danger if run and run.errors else None)

        show_error(self.error, c.config_error or c.last_error)
        self.btn_hide_error.setVisible(bool(c.last_error) and not c.config_error)
        self.btn_error_details.setVisible(bool(c.last_errors) and not c.config_error)
        self.btn_sync.setEnabled(c.can_sync())
        self.btn_sync.setText("Anmelden ..." if c.login_needed else "Jetzt synchronisieren")

    def reload_files(self) -> None:
        try:
            self._files = self.c.api.recent_files(50) if self.c.config else []
            self._removed = self.c.api.removed_items() if self.c.config else []
            self._changed = self.c.api.changed_items() if self.c.config else []
        except Exception:  # noqa: BLE001, Anzeige darf nie abstürzen
            log.exception("Dateiliste konnte nicht geladen werden")
            self._files, self._removed, self._changed = [], [], []
        root = self.c.api.root_dir() if self.c.config else None
        t = theme.tokens()
        muted = QBrush(QColor(t.muted))

        self.table.setRowCount(len(self._files))
        for row, f in enumerate(self._files):
            cells = [
                f.name,
                logic.course_short_name(f.course_name),
                logic.relative_to_root(f.local_path, root),
                logic.relative_time(f.downloaded_at),
                STATUS_TEXT.get(f.status, f.status.value),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                # voller Text als Tooltip, weil die Spalten gekürzt anzeigen
                if col == 0:
                    item.setToolTip(f"{f.name}\n{f.local_path}")
                elif col == 1:
                    item.setToolTip(f.course_name)
                elif col == 2:
                    item.setToolTip(str(f.local_path.parent))
                if col in (2, 3):
                    item.setForeground(muted)
                if col == 4 and text:
                    color = t.danger if f.status == ItemStatus.ERROR else t.accent
                    item.setForeground(QBrush(QColor(color)))
                self.table.setItem(row, col, item)
        self.table.setVisible(bool(self._files))
        self.empty_hint.setVisible(not self._files)
        n = len(self._files)
        self.count_label.setText(f"{n} {'Datei' if n == 1 else 'Dateien'}" if n else "")

        self.removed_toggle.setText(f"Vom Nutzer entfernt ({len(self._removed)})")
        self.removed_card.setVisible(bool(self._removed))
        if not self._removed:
            self.removed_toggle.setChecked(False)
        self.removed_table.setRowCount(len(self._removed))
        for row, f in enumerate(self._removed):
            self.removed_table.setItem(row, 0, QTableWidgetItem(f.name))
            self.removed_table.setItem(row, 1, QTableWidgetItem(logic.course_display_name(f.course_name)))
            self.removed_table.setItem(row, 2, QTableWidgetItem(logic.relative_time(f.downloaded_at)))
            btn = button("Erneut herunterladen")
            btn.clicked.connect(lambda _=False, r=f.ref_id, b=btn: self._redownload(r, b))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(4, 4, 4, 4)
            cl.addWidget(btn)
            self.removed_table.setCellWidget(row, 3, cell)
        # Höhe passend zum Inhalt (höchstens drei Zeilen), damit die Dateiliste Platz behält
        rows = min(len(self._removed), 3)
        self.removed_table.setFixedHeight(self.removed_table.horizontalHeader().sizeHint().height() + rows * 44 + 4)

        self._fill_changed()

    def _fill_changed(self) -> None:
        n = len(self._changed)
        t = theme.tokens()
        self.stat_changed.set(n, t.accent if n else None)
        self.changed_title.setText(f"In ILIAS geändert ({n})")
        self.changed_card.setVisible(bool(n))
        self.btn_all_changed.setEnabled(True)
        self.btn_all_changed.setText("Alle herunterladen")
        self.btn_all_changed.setVisible(n > 1)
        muted = QBrush(QColor(t.muted))
        self.changed_table.setRowCount(n)
        for row, f in enumerate(self._changed):
            name = QTableWidgetItem(f.name)
            name.setToolTip(str(f.local_path))
            self.changed_table.setItem(row, 0, name)
            self.changed_table.setItem(row, 1, QTableWidgetItem(logic.course_display_name(f.course_name)))
            when = QTableWidgetItem(logic.relative_time(f.new_mtime or f.detected_at))
            if f.old_mtime:
                when.setToolTip(f"Geladene Version: {f.old_mtime:%d.%m.%Y %H:%M}")
            when.setForeground(muted)
            self.changed_table.setItem(row, 2, when)
            btn_load = button("Herunterladen")
            btn_load.setToolTip("Neue Version neben die vorhandene Datei legen")
            btn_skip = button("Ignorieren", "ghost")
            btn_skip.setToolTip("Nicht mehr melden, bis sich die Datei in ILIAS erneut ändert")
            btn_load.clicked.connect(lambda _=False, r=f.ref_id, b=btn_load: self._download_changed(r, b))
            btn_skip.clicked.connect(lambda _=False, r=f.ref_id: self._dismiss_changed(r))
            self.changed_table.setCellWidget(row, 3, _cell_widget(btn_load, btn_skip))
        rows = min(n, CHANGED_VISIBLE_ROWS)
        header = self.changed_table.horizontalHeader().sizeHint().height()
        self.changed_table.setFixedHeight(header + rows * CHANGED_ROW + 4)

    def focus_changed(self) -> None:
        """Aus Tray oder Benachrichtigung: Bereich "In ILIAS geändert" in den Blick holen."""
        self.reload_files()
        if self.changed_card.isVisible():
            self.changed_table.setFocus()
            self.changed_table.scrollToTop()

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

    def _show_error_details(self) -> None:
        ErrorListDialog(self.c.last_errors, self).exec()

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

    def _download_changed(self, ref_id: int, btn: QPushButton) -> None:
        btn.setEnabled(False)
        btn.setText("Lädt ...")

        def fail(e: Exception) -> None:
            btn.setEnabled(True)
            btn.setText("Herunterladen")
            QMessageBox.warning(self, "Neue Version herunterladen", error_text(e))

        run_task(self.c.api.download_changed, ref_id, on_done=lambda _: self.c.changed_handled(), on_error=fail)

    def _dismiss_changed(self, ref_id: int) -> None:
        try:
            self.c.api.dismiss_changed(ref_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Ignorieren fehlgeschlagen")
            QMessageBox.warning(self, "Ignorieren", error_text(e))
        self.c.changed_handled()

    def _download_all_changed(self) -> None:
        ref_ids = [f.ref_id for f in self._changed]
        if not ref_ids:
            return
        self.btn_all_changed.setEnabled(False)
        self.btn_all_changed.setText("Lädt ...")

        def done(failed: list[str]) -> None:
            self.c.changed_handled()
            if failed:
                QMessageBox.warning(self, "Neue Versionen herunterladen",
                                    "Nicht alle Dateien konnten geladen werden:\n" + "\n".join(failed))

        def fail(e: Exception) -> None:
            # einzelne Dateien können schon geladen sein, daher Liste neu laden
            self.c.changed_handled()
            QMessageBox.warning(self, "Neue Versionen herunterladen", error_text(e))

        run_task(download_all_changed, self.c.api, ref_ids, on_done=done, on_error=fail)

    def _redownload(self, ref_id: int, btn: QPushButton) -> None:
        btn.setEnabled(False)
        btn.setText("Lädt ...")

        def fail(e: Exception) -> None:
            btn.setEnabled(True)
            btn.setText("Erneut herunterladen")
            QMessageBox.warning(self, "Erneut herunterladen", error_text(e))

        run_task(self.c.api.redownload, ref_id, on_done=lambda _: self.c.redownloaded(), on_error=fail)
