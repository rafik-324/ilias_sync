"""Tab Kurse: Zuordnung ILIAS Kurs zu lokalem Ordner, Objekttypen, Regeln."""

from __future__ import annotations

import logging
import re
from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import Config, ConfigError, CourseConfig, IliasCourse, SyncResult
from ilias_sync.gui import logic
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.rules_dialog import RulesDialog
from ilias_sync.gui.widgets import ProgressPanel, error_label, hint_label, show_error
from ilias_sync.gui.worker import run_task

log = logging.getLogger(__name__)

NICHT_ZUGEORDNET = "Nicht zugeordnet"
NEUER_ORDNER = "Neuer Ordner ..."

# (Config Feld, Spaltentitel)
TYPE_COLUMNS: list[tuple[str, str]] = [
    ("download_files", "Dateien"),
    ("download_videos", "Videos"),
    ("download_exercises", "Übungen"),
    ("download_weblinks", "Weblinks"),
    ("download_forums", "Foren"),
    ("download_other", "Sonstiges"),
]
COL_NAME, COL_SEM, COL_DIR = 0, 1, 2
COL_FIRST_TYPE = 3
COL_RULES = COL_FIRST_TYPE + len(TYPE_COLUMNS)

STANDARD_KATEGORIEN = ["01_Vorlesung", "02_Übung", "03_Tutorium", "03_Übungsblätter", "04_Abgaben"]
UNASSIGNED_BG = QColor(255, 196, 0, 45)


def semester_of(name: str, pattern: str) -> str | None:
    try:
        m = re.search(pattern, name)
    except re.error:
        return None
    if not m or len(m.groups()) < 2:
        return None
    return f"{m.group(1)} {m.group(2)}"


class CoursesTab(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller
        self.cfg: Config | None = None
        self.dirty = False
        self._filling = False

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("background: rgba(255,196,0,0.18); padding: 6px; border-radius: 4px;")
        self.hint.hide()

        self.table = QTableWidget(0, COL_RULES + 1)
        self.table.setHorizontalHeaderLabels(
            ["ILIAS Kurs", "Semester", "Lokaler Ordner"] + [t for _, t in TYPE_COLUMNS] + [""]
        )
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in range(COL_SEM, COL_RULES + 1):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        # Semester Zuordnung
        sem_title = QLabel("Semester Ordner")
        sem_title.setStyleSheet("font-weight: 600;")
        self.sem_table = QTableWidget(0, 2)
        self.sem_table.setHorizontalHeaderLabels(["Semester (aus Kursname)", "Ordner"])
        self.sem_table.verticalHeader().hide()
        self.sem_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sem_table.setMaximumHeight(120)
        self.sem_table.itemChanged.connect(self._on_sem_changed)

        self.progress = ProgressPanel()
        self.error = error_label()

        self.btn_fetch = QPushButton("Kurse aktualisieren")
        self.btn_fetch.setToolTip("Kursliste neu aus ILIAS abrufen")
        self.btn_fetch.clicked.connect(self._fetch)
        self.btn_discard = QPushButton("Verwerfen")
        self.btn_discard.clicked.connect(self.load)
        self.btn_save = QPushButton("Speichern")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.save)

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_fetch)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_discard)
        buttons.addWidget(self.btn_save)

        lay = QVBoxLayout(self)
        lay.addWidget(self.hint)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint_label(
            "Ein anderer Ordner ändert nur die Zuordnung. Vorhandene Dateien werden nicht verschoben."
        ))
        lay.addWidget(sem_title)
        lay.addWidget(self.sem_table)
        lay.addWidget(self.progress)
        lay.addWidget(self.error)
        lay.addLayout(buttons)

        self.c.config_changed.connect(self._on_config_changed)
        self.c.sync_finished.connect(self._on_sync_finished)
        self.load()

    # ---------------- Laden ----------------

    def _on_config_changed(self) -> None:
        if not self.dirty:
            self.load()

    def load(self) -> None:
        self.cfg = deepcopy(self.c.config)
        self._set_dirty(False)
        show_error(self.error, self.c.config_error)
        self._fill()

    def _pattern(self) -> str:
        return self.cfg.semester_pattern if self.cfg else r"\((HWS|FSS) (\d{4})\)"

    def _sorted_courses(self) -> list[CourseConfig]:
        if self.cfg is None:
            return []
        # nicht zugeordnete oben, dann nach Semester Ordner und Name
        return sorted(self.cfg.courses, key=lambda c: (c.local_dir is not None, c.local_dir or c.ilias_name))

    def _fill(self) -> None:
        self._filling = True
        courses = self._sorted_courses()
        self.table.setRowCount(len(courses))
        unassigned = 0
        for row, course in enumerate(courses):
            sem = semester_of(course.ilias_name, self._pattern())
            name_item = QTableWidgetItem(logic.course_display_name(course.ilias_name, self._pattern()))
            name_item.setToolTip(f"{course.ilias_name}\nref_id {course.ref_id}")
            name_item.setData(Qt.ItemDataRole.UserRole, course.ref_id)
            self.table.setItem(row, COL_NAME, name_item)
            self.table.setItem(row, COL_SEM, QTableWidgetItem(sem or "unbekannt"))

            self.table.setCellWidget(row, COL_DIR, self._dir_combo(course, sem))

            for i, (field, _title) in enumerate(TYPE_COLUMNS):
                box = QCheckBox()
                box.setChecked(getattr(course, field))
                box.toggled.connect(lambda on, c=course, f=field: self._set_type(c, f, on))
                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(0, 0, 0, 0)
                cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cl.addWidget(box)
                self.table.setCellWidget(row, COL_FIRST_TYPE + i, cell)

            n = len(course.rules)
            btn = QPushButton(f"Regeln ({n})" if n else "Regeln")
            btn.clicked.connect(lambda _=False, c=course: self._edit_rules(c))
            self.table.setCellWidget(row, COL_RULES, btn)

            if course.local_dir is None:
                unassigned += 1
                for col in (COL_NAME, COL_SEM):
                    self.table.item(row, col).setBackground(QBrush(UNASSIGNED_BG))

        if unassigned:
            kurs = "Ein Kurs ist" if unassigned == 1 else f"{unassigned} Kurse sind"
            self.hint.setText(
                f"{kurs} noch keinem Ordner zugeordnet. Bis zur Zuordnung wird unter "
                "<i>Semesterordner/_Neue_Kurse</i> abgelegt."
            )
        self.hint.setVisible(bool(unassigned))
        self._fill_semesters()
        self._filling = False

    def _fill_semesters(self) -> None:
        semesters = dict(self.cfg.semesters) if self.cfg else {}
        # Semester aus Kursnamen ergänzen, die noch keinen Ordner haben
        if self.cfg:
            for course in self.cfg.courses:
                sem = semester_of(course.ilias_name, self._pattern())
                if sem and sem not in semesters:
                    semesters[sem] = ""
        self.sem_table.setRowCount(len(semesters))
        for row, (sem, folder) in enumerate(sorted(semesters.items(), key=lambda kv: (kv[1] == "", kv[1]))):
            key = QTableWidgetItem(sem)
            key.setFlags(key.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.sem_table.setItem(row, 0, key)
            val = QTableWidgetItem(folder)
            if not folder:
                val.setBackground(QBrush(UNASSIGNED_BG))
                val.setToolTip("Leer lassen: wird beim nächsten Sync automatisch mit der nächsten Nummer angelegt")
            self.sem_table.setItem(row, 1, val)

    def _semester_dir(self, sem: str | None) -> str | None:
        if not sem or self.cfg is None:
            return None
        return self.cfg.semesters.get(sem) or None

    def _dir_combo(self, course: CourseConfig, sem: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem(NICHT_ZUGEORDNET, None)
        sem_dir = self._semester_dir(sem)
        existing: list[str] = []
        if sem_dir:
            try:
                existing = self.c.api.local_course_dirs(sem_dir)
            except Exception:  # noqa: BLE001
                log.exception("Kursordner konnten nicht gelesen werden")
        if course.local_dir and course.local_dir not in existing:
            existing.append(course.local_dir)
        for name in sorted(existing):
            combo.addItem(name, name)
        combo.insertSeparator(combo.count())
        combo.addItem(NEUER_ORDNER, NEUER_ORDNER)
        idx = combo.findData(course.local_dir) if course.local_dir else 0
        combo.setCurrentIndex(max(idx, 0))
        combo.setProperty("previous", combo.currentIndex())
        combo.currentIndexChanged.connect(lambda _i, c=course, cb=combo, e=existing: self._dir_chosen(c, cb, e))
        return combo

    # ---------------- Bearbeiten ----------------

    def _dir_chosen(self, course: CourseConfig, combo: QComboBox, existing: list[str]) -> None:
        if self._filling:
            return
        data = combo.currentData()
        if data == NEUER_ORDNER:
            suggestion = logic.suggest_course_dir(course.ilias_name, existing, self._pattern())
            name, ok = QInputDialog.getText(
                self, "Neuer Kursordner",
                "Name des Ordners (wird beim nächsten Sync angelegt):", text=suggestion,
            )
            name = name.strip()
            if not ok or not name:
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.property("previous"))
                combo.blockSignals(False)
                return
            if any(ch in name for ch in '<>:"/\\|?*'):
                QMessageBox.warning(self, "Neuer Kursordner", 'Der Name darf keines dieser Zeichen enthalten: <>:"/\\|?*')
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.property("previous"))
                combo.blockSignals(False)
                return
            course.local_dir = name
            self._set_dirty(True)
            self._fill()
            return
        course.local_dir = data
        combo.setProperty("previous", combo.currentIndex())
        self._set_dirty(True)
        self._fill()

    def _set_type(self, course: CourseConfig, field: str, on: bool) -> None:
        setattr(course, field, on)
        self._set_dirty(True)

    def _edit_rules(self, course: CourseConfig) -> None:
        targets = list(STANDARD_KATEGORIEN)
        root = self.c.api.root_dir()
        sem_dir = self._semester_dir(semester_of(course.ilias_name, self._pattern()))
        if root and sem_dir and course.local_dir:
            folder = root / sem_dir / course.local_dir
            if folder.is_dir():
                found = sorted(p.name for p in folder.iterdir() if p.is_dir())
                targets = found + [t for t in targets if t not in found]
        dlg = RulesDialog(logic.course_display_name(course.ilias_name, self._pattern()), course.rules, targets, self)
        if dlg.exec():
            course.rules = dlg.rules()
            self._set_dirty(True)
            self._fill()

    def _on_sem_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or self.cfg is None or item.column() != 1:
            return
        sem = self.sem_table.item(item.row(), 0).text()
        value = item.text().strip()
        if value:
            self.cfg.semesters[sem] = value
        else:
            self.cfg.semesters.pop(sem, None)
        self._set_dirty(True)
        self._fill()

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.btn_save.setEnabled(dirty)
        self.btn_discard.setEnabled(dirty)

    # ---------------- Speichern ----------------

    def save(self) -> bool:
        if self.cfg is None:
            return False
        try:
            self.c.save_config(self.cfg)
        except ConfigError as e:
            show_error(self.error, str(e))
            return False
        show_error(self.error, None)
        self._set_dirty(False)
        self.load()
        return True

    def confirm_leave(self) -> bool:
        """Fragt bei ungespeicherten Änderungen nach. True = darf verlassen werden."""
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Ungespeicherte Änderungen",
            "Die Änderungen an den Kursen sind noch nicht gespeichert. Jetzt speichern?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self.load()
            return True
        return False

    # ---------------- Kurse abrufen ----------------

    def _merge_courses(self, courses: list[IliasCourse]) -> int:
        """Neue ILIAS Kurse als nicht zugeordnet aufnehmen. Gibt die Anzahl zurück."""
        if self.cfg is None:
            return 0
        known = {c.ref_id for c in self.cfg.courses}
        added = 0
        for course in courses:
            if course.ref_id not in known:
                self.cfg.courses.append(CourseConfig(ref_id=course.ref_id, ilias_name=course.name, local_dir=None))
                added += 1
        return added

    def _fetch(self) -> None:
        self.btn_fetch.setEnabled(False)
        self.progress.start("Kurse werden abgerufen ...")
        task = run_task(self.c.api.fetch_courses, with_progress=True,
                        on_progress=self.progress.update_progress,
                        on_done=self._fetched, on_error=self._fetch_failed)
        self.progress.cancel_clicked.connect(task.cancel)

    def _fetched(self, courses: list[IliasCourse]) -> None:
        self.progress.stop()
        self.btn_fetch.setEnabled(True)
        added = self._merge_courses(courses)
        if added:
            self._set_dirty(True)
            self._fill()
        QMessageBox.information(
            self, "Kurse aktualisiert",
            f"{len(courses)} Kurse in ILIAS gefunden, davon {added} neu." if added
            else f"{len(courses)} Kurse in ILIAS gefunden, keine neuen.",
        )

    def _fetch_failed(self, e: Exception) -> None:
        self.progress.stop()
        self.btn_fetch.setEnabled(True)
        show_error(self.error, error_text(e))

    def _on_sync_finished(self, result: SyncResult, _automatic: bool) -> None:
        if result.new_unassigned_courses and self._merge_courses(result.new_unassigned_courses):
            # sofort speichern, damit der Kurs dauerhaft als nicht zugeordnet auftaucht
            if not self.dirty:
                self.save()
            else:
                self._fill()
