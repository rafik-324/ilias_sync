"""Tab Kurse: Zuordnung ILIAS Kurs zu lokalem Ordner, Objekttypen, Regeln."""

from __future__ import annotations

import logging
from copy import deepcopy

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import Config, ConfigError, CourseConfig, IliasCourse, SyncResult
from ilias_sync.gui import logic, theme
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.icons import dot
from ilias_sync.gui.rules_dialog import RulesDialog
from ilias_sync.gui.widgets import Card, Page, ProgressPanel, button, error_label, hint_label, role_label, show_error
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
COL_NAME, COL_SEM, COL_DIR, COL_TYPES, COL_RULES = range(5)

STANDARD_KATEGORIEN = ["01_Vorlesung", "02_Übung", "03_Tutorium", "03_Übungsblätter", "04_Abgaben"]


class ContentsPopup(QFrame):
    """Aufklappfeld mit einem Schalter pro Inhaltstyp, Änderungen gelten sofort."""

    def __init__(self, course: CourseConfig, on_change, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(role_label("Was soll geladen werden?", "section"))
        for field, title in TYPE_COLUMNS:
            box = QCheckBox(title)
            box.setChecked(getattr(course, field))
            box.toggled.connect(lambda on, f=field: on_change(f, on))
            lay.addWidget(box)


class ContentsButton(QPushButton):
    def __init__(self, course: CourseConfig, on_change) -> None:
        super().__init__()
        self.course = course
        self._on_change = on_change
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Inhaltstypen für diesen Kurs wählen")
        self.clicked.connect(self._open)
        self.refresh()

    def refresh(self) -> None:
        flags = [(title, getattr(self.course, field)) for field, title in TYPE_COLUMNS]
        self.setText(logic.contents_summary(flags) + "  ▾")

    def _changed(self, field: str, on: bool) -> None:
        self._on_change(self.course, field, on)
        self.refresh()

    def _open(self) -> None:
        popup = ContentsPopup(self.course, self._changed, self)
        popup.adjustSize()
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))
        popup.show()


DIR_COL_MIN = 250
DIR_COL_MAX = 380


def _combo_width(combo: QComboBox) -> int:
    """Nötige Spaltenbreite für den längsten Eintrag.

    sizeHint berücksichtigt das Stylesheet, zieht aber den Pfeilbereich
    (padding-right plus drop-down) nur einmal ab, daher 24 px Zuschlag, dazu der Zellrand.
    """
    combo.ensurePolished()
    policy = combo.sizeAdjustPolicy()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    width = combo.sizeHint().width()
    combo.setSizeAdjustPolicy(policy)
    return width + 24 + 12 + 4


def _cell(widget: QWidget, center: bool = False) -> QWidget:
    """Zell Widget mit etwas Luft, damit Eingaben nicht am Rand kleben."""
    cell = QWidget()
    cl = QHBoxLayout(cell)
    cl.setContentsMargins(6, 4, 6, 4)
    if center:
        cl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cl.addWidget(widget)
    return cell


class CoursesTab(Page):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__("Kurse", "Welcher ILIAS Kurs in welchem Ordner landet und was davon geladen wird.", parent)
        self.c = controller
        self.cfg: Config | None = None
        self.dirty = False
        self._filling = False

        self.hint = role_label("", "banner")
        self.hint.setWordWrap(True)
        self.hint.hide()

        self.table = QTableWidget(0, COL_RULES + 1)
        self.table.setHorizontalHeaderLabels(["ILIAS Kurs", "Semester", "Lokaler Ordner", "Inhalte", ""])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setWordWrap(False)
        self.table.setIconSize(QSize(8, 8))
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, width in ((COL_SEM, 92), (COL_DIR, DIR_COL_MIN), (COL_TYPES, 150), (COL_RULES, 128)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self._cancel_fetch)
        self._fetch_task = None
        self.error = error_label()

        self.btn_fetch = button("Kurse aktualisieren")
        self.btn_fetch.setToolTip("Kursliste neu aus ILIAS abrufen")
        self.btn_fetch.clicked.connect(self._fetch)
        self.btn_discard = button("Verwerfen", "ghost")
        self.btn_discard.clicked.connect(self.load)
        self.btn_save = button("Speichern", "primary")
        self.btn_save.clicked.connect(self.save)

        self.actions.addWidget(self.btn_fetch)

        buttons = QHBoxLayout()
        buttons.addWidget(hint_label(
            "Ein anderer Ordner ändert nur die Zuordnung. Vorhandene Dateien werden nicht verschoben. "
            "Die Semester Ordner stehen in den Einstellungen."
        ), 1)
        buttons.addWidget(self.btn_discard)
        buttons.addWidget(self.btn_save)

        courses_card = Card(margins=0, spacing=0)
        courses_card.lay.addWidget(self.table)

        self.body.addWidget(self.hint)
        self.body.addWidget(courses_card, 1)
        self.body.addWidget(self.progress)
        self.body.addWidget(self.error)
        self.body.addLayout(buttons)

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
        dir_width = DIR_COL_MIN
        for row, course in enumerate(courses):
            sem = logic.semester_of(course.ilias_name, self._pattern())
            name_item = QTableWidgetItem(logic.course_display_name(course.ilias_name, self._pattern()))
            name_item.setToolTip(f"{course.ilias_name}\nref_id {course.ref_id}")
            name_item.setData(Qt.ItemDataRole.UserRole, course.ref_id)
            self.table.setItem(row, COL_NAME, name_item)
            self.table.setItem(row, COL_SEM, QTableWidgetItem(sem or "unbekannt"))

            combo = self._dir_combo(course, sem)
            dir_width = max(dir_width, _combo_width(combo))
            self.table.setCellWidget(row, COL_DIR, _cell(combo))

            self.table.setCellWidget(row, COL_TYPES, _cell(ContentsButton(course, self._set_type)))

            n = len(course.rules)
            btn = button(f"Regeln ({n})" if n else "Regeln", None if n else "ghost")
            btn.clicked.connect(lambda _=False, c=course: self._edit_rules(c))
            self.table.setCellWidget(row, COL_RULES, _cell(btn))

            if course.local_dir is None:
                unassigned += 1
                name_item.setIcon(QIcon(dot(theme.tokens().warning, 8)))
                name_item.setToolTip(f"Noch keinem Ordner zugeordnet\n{course.ilias_name}")

        # Spalte so breit, dass lange Ordnernamen nicht abgeschnitten werden
        self.table.setColumnWidth(COL_DIR, min(dir_width, DIR_COL_MAX))

        if unassigned:
            kurs = "Ein Kurs ist" if unassigned == 1 else f"{unassigned} Kurse sind"
            self.hint.setText(
                f"{kurs} noch keinem Ordner zugeordnet. Bis zur Zuordnung wird unter "
                "<i>Semesterordner/_Neue_Kurse</i> abgelegt."
            )
        self.hint.setVisible(bool(unassigned))
        self._filling = False

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
        combo.setToolTip(combo.currentText())
        combo.currentTextChanged.connect(combo.setToolTip)
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
        sem_dir = self._semester_dir(logic.semester_of(course.ilias_name, self._pattern()))
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

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.btn_save.setEnabled(dirty)
        self.btn_discard.setEnabled(dirty)

    # ---------------- Speichern ----------------

    def save(self) -> bool:
        if self.cfg is None or self.c.config is None:
            return False
        # Nur die Kurse übernehmen, alles andere (z. B. Semester Ordner) gehört den Einstellungen
        cfg = deepcopy(self.c.config)
        cfg.courses = deepcopy(self.cfg.courses)
        try:
            self.c.save_config(cfg)
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
        self._fetch_task = run_task(self.c.api.fetch_courses, with_progress=True,
                                    on_progress=self.progress.update_progress,
                                    on_done=self._fetched, on_error=self._fetch_failed)

    def _cancel_fetch(self) -> None:
        if self._fetch_task is not None:
            self._fetch_task.cancel()

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
