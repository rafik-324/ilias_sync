"""
Setup Wizard beim ersten Start.

1. Anmelden
2. Basisordner festlegen
3. Kurse zuordnen
4. Vorschau (Dry Run)
5. Erster Sync
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from ilias_sync.api import BaseDirCheck, CourseConfig, IliasCourse, MappingSuggestion, PlannedAction
from ilias_sync.gui import theme
from ilias_sync.gui.controller import error_text
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.widgets import (
    LoginForm,
    PreviewTree,
    ProgressPanel,
    button,
    error_label,
    hint_label,
    role_label,
    show_error,
)
from ilias_sync.gui.worker import run_task

log = logging.getLogger(__name__)

UNSICHER_AB = 0.7
NICHT_ZUGEORDNET = "Nicht zugeordnet"
FARBE_UNSICHER = QColor(255, 196, 0, 60)


# ---------------- 1. Anmelden ----------------

class LoginPage(QWizardPage):
    def __init__(self, api) -> None:
        super().__init__()
        self.setTitle("Bei ILIAS anmelden")
        self.setSubTitle("Mit der Uni-ID und dem Passwort der Universität Mannheim.")
        self._ok = False
        self.form = LoginForm(api, api.stored_username())
        self.form.logged_in.connect(self._logged_in)
        lay = QVBoxLayout(self)
        lay.addWidget(self.form)
        lay.addStretch(1)
        lay.addWidget(hint_label(
            "Das Passwort wird nur im Schlüsselbund des Systems gespeichert, "
            "nie in Dateien oder Logs."
        ))

    def _logged_in(self, user: str) -> None:
        self._ok = True
        self.wizard().username = user
        self.completeChanged.emit()
        QTimer.singleShot(400, self.wizard().next)

    def isComplete(self) -> bool:  # noqa: N802
        return self._ok


# ---------------- 2. Basisordner ----------------

class BaseDirPage(QWizardPage):
    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.setTitle("Basisordner festlegen")
        self.setSubTitle("Unter diesem Ordner legt ILIAS Sync alle Unterordner selbst an. "
                         "Der erste Sync braucht einen neuen oder leeren Uni Ordner.")
        self._check: BaseDirCheck | None = None
        try:
            self.root_name = api.load_config().root_folder_name
        except Exception:  # noqa: BLE001, ungültige Config: Standardname verwenden
            self.root_name = "01_Universität_Mannheim"

        self.path = QLineEdit()
        self.path.setPlaceholderText("z. B. C:\\Users\\name\\OneDrive oder ~/Documents")
        self.path.textChanged.connect(self._schedule_check)
        browse = button("Durchsuchen ...")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.structure = QLabel()
        self.structure.setTextFormat(Qt.TextFormat.PlainText)
        theme.set_role(self.structure, "code")

        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.status)
        lay.addSpacing(6)
        lay.addWidget(role_label("So wird es aussehen", "section"))
        lay.addWidget(self.structure)
        lay.addStretch(1)
        lay.addWidget(hint_label("Ein Uni Ordner, in dem schon Dateien liegen, wird nicht befüllt. "
                                 "So kommt nichts mit deinen eigenen Unterlagen durcheinander. "
                                 "Der Ordner kann später in den Einstellungen geändert werden."))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._run_check)

    def initializePage(self) -> None:  # noqa: N802
        self._run_check()

    def _browse(self) -> None:
        start = self.path.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Basisordner wählen", start)
        if chosen:
            self.path.setText(chosen)

    def _schedule_check(self) -> None:
        self._check = None
        self.completeChanged.emit()
        self._timer.start()

    def _selected(self) -> Path | None:
        text = self.path.text().strip()
        return Path(text).expanduser() if text else None

    def _run_check(self) -> None:
        path = self._selected()
        if path is None:
            self._check = None
            self.status.setText("Bitte einen Ordner wählen.")
            theme.set_role(self.status, "muted")
            self.structure.setText("")
            self.completeChanged.emit()
            return
        self._check = self.api.check_base_dir(path)
        c = self._check
        if c.exists and not c.writable:
            self.status.setText(c.message or "In diesen Ordner darf nicht geschrieben werden.")
            theme.set_role(self.status, "error")
        elif not c.exists:
            self.status.setText("Der Ordner existiert noch nicht und wird nach Rückfrage angelegt.")
            theme.set_role(self.status, "warning")
        elif not c.is_empty:
            self.status.setText(
                f"Hier gibt es schon einen Ordner {self.root_name} mit Inhalt. Der erste Sync läuft nur in "
                "einen neuen oder leeren Uni Ordner. Bitte einen anderen Basisordner wählen."
            )
            theme.set_role(self.status, "error")
        elif c.has_root_folder:
            self.status.setText(f"Leerer Ordner {self.root_name} gefunden. Er wird verwendet.")
            theme.set_role(self.status, "success")
        else:
            self.status.setText(f"Ordner ist nutzbar. {self.root_name} wird darin angelegt.")
            theme.set_role(self.status, "success")
        if c.is_empty:
            self.structure.setText("\n".join([
                str(path),
                f"└── {self.root_name}/",
                "    └── 01_SEMESTER/",
                "        └── NN_kursname/NN_Kategorie/ ...",
            ]))
        else:
            self.structure.setText("")
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        c = self._check
        return c is not None and (not c.exists or c.writable) and c.is_empty

    def validatePage(self) -> bool:  # noqa: N802
        path = self._selected()
        c = self._check
        if path is None or c is None:
            return False
        if not c.exists or not c.has_root_folder:
            target = path / self.root_name
            answer = QMessageBox.question(self, "Ordner anlegen", f"Der Ordner\n{target}\nwird angelegt. Fortfahren?")
            if answer != QMessageBox.StandardButton.Yes:
                return False
            try:
                self.api.create_base_structure(path)
            except Exception as e:  # noqa: BLE001
                log.exception("Basisstruktur konnte nicht angelegt werden")
                QMessageBox.warning(self, "Ordner anlegen", error_text(e))
                return False
        try:
            cfg = self.api.load_config()
            cfg.base_dir = path
            cfg.username = self.wizard().username
            self.api.save_config(cfg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Speichern", error_text(e))
            return False
        return True


# ---------------- 3. Kurse zuordnen ----------------

class CoursesPage(QWizardPage):
    COL_NAME, COL_SEM, COL_DIR, COL_STATUS = range(4)

    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.setTitle("Kurse zuordnen")
        self.setSubTitle("Deine ILIAS Kurse und die passenden lokalen Ordner. Bitte prüfen und bei Bedarf korrigieren.")
        self._suggestions: list[MappingSuggestion] = []
        self._loaded = False
        self._generation = 0

        self.progress = ProgressPanel(with_cancel=False)
        self.error = error_label()
        self.retry = button("Erneut versuchen")
        self.retry.clicked.connect(self._load)
        self.retry.hide()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ILIAS Kurs", "Semester", "Lokaler Ordner", "Zuordnung"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self.COL_SEM, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(self.COL_DIR, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        lay = QVBoxLayout(self)
        lay.addWidget(self.progress)
        lay.addWidget(self.error)
        lay.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint_label(
            "Gelb markierte Zuordnungen sind unsicher. Neue Ordner werden erst beim Sync angelegt. "
            "\"Nicht zugeordnet\" legt Dateien vorerst unter _Neue_Kurse ab."
        ))

    def initializePage(self) -> None:  # noqa: N802
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        self._generation += 1
        gen = self._generation
        self._loaded = False
        self.completeChanged.emit()
        self.retry.hide()
        show_error(self.error, None)
        self.table.setRowCount(0)
        self.progress.start("Kurse werden aus ILIAS abgerufen ...")

        def fetch_and_suggest(progress, cancel) -> list[MappingSuggestion]:
            # suggest_mapping durchsucht das Dateisystem, deshalb ebenfalls im Worker
            courses: list[IliasCourse] = self.api.fetch_courses(progress, cancel)
            return self.api.suggest_mapping(courses)

        def done(suggestions: list[MappingSuggestion]) -> None:
            if gen != self._generation:
                return
            self.progress.stop()
            self._fill(suggestions)

        def fail(e: Exception) -> None:
            if gen != self._generation:
                return
            self.progress.stop()
            show_error(self.error, error_text(e))
            self.retry.show()

        run_task(fetch_and_suggest, with_progress=True,
                 on_progress=self.progress.update_progress, on_done=done, on_error=fail)

    def _fill(self, suggestions: list[MappingSuggestion]) -> None:
        self._suggestions = suggestions
        self.table.setRowCount(len(suggestions))
        for row, s in enumerate(suggestions):
            self.table.setItem(row, self.COL_NAME, QTableWidgetItem(s.course.name))
            self.table.setItem(row, self.COL_SEM, QTableWidgetItem(f"{s.course.semester or '?'} → {s.semester_dir}"))

            combo = QComboBox()
            combo.setEditable(True)
            combo.addItem(NICHT_ZUGEORDNET)
            try:
                existing = self.api.local_course_dirs(s.semester_dir)
            except Exception:  # noqa: BLE001
                existing = []
            for name in existing:
                combo.addItem(name)
            if s.local_dir not in existing:
                combo.addItem(s.local_dir)
            combo.setCurrentText(s.local_dir)
            self.table.setCellWidget(row, self.COL_DIR, combo)

            if s.exists and s.score >= UNSICHER_AB:
                status = QTableWidgetItem(f"Vorhanden ({s.score:.0%})")
                status.setForeground(QBrush(QColor("#107c10")))
            elif s.exists:
                status = QTableWidgetItem(f"Unsicher ({s.score:.0%}), bitte prüfen")
                for col in (self.COL_NAME, self.COL_SEM):
                    self.table.item(row, col).setBackground(QBrush(FARBE_UNSICHER))
                status.setBackground(QBrush(FARBE_UNSICHER))
            else:
                status = QTableWidgetItem("Neu anlegen")
            self.table.setItem(row, self.COL_STATUS, status)
        self._loaded = True
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return self._loaded

    def validatePage(self) -> bool:  # noqa: N802
        try:
            cfg = self.api.load_config()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Kurse", error_text(e))
            return False
        known = {c.ref_id: c for c in cfg.courses}
        for row, s in enumerate(self._suggestions):
            text = self.table.cellWidget(row, self.COL_DIR).currentText().strip()
            local = None if text in ("", NICHT_ZUGEORDNET) else text
            if local and any(ch in local for ch in '<>:"/\\|?*'):
                QMessageBox.warning(self, "Kurse", f'Ordnername "{local}" enthält unzulässige Zeichen.')
                return False
            course = known.get(s.course.ref_id)
            if course is None:
                course = CourseConfig(ref_id=s.course.ref_id, ilias_name=s.course.name, local_dir=local)
                cfg.courses.append(course)
            else:
                course.local_dir = local
            if s.course.semester:
                cfg.semesters.setdefault(s.course.semester, s.semester_dir)
        try:
            self.api.save_config(cfg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Kurse", error_text(e))
            return False
        return True


# ---------------- 4. Vorschau ----------------

class PreviewPage(QWizardPage):
    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.setTitle("Vorschau")
        self.setSubTitle("So würden die Dateien einsortiert. Es wurde noch nichts geschrieben.")
        self._done = False
        self._generation = 0
        self._task = None
        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self._cancel)
        self.error = error_label()
        self.tree = PreviewTree()
        lay = QVBoxLayout(self)
        lay.addWidget(self.progress)
        lay.addWidget(self.error)
        lay.addWidget(self.tree, 1)

    def initializePage(self) -> None:  # noqa: N802
        self._generation += 1
        gen = self._generation
        self._done = False
        self.completeChanged.emit()
        self.tree.clear()
        show_error(self.error, None)
        self.progress.start("Vorschau wird erstellt ...")

        def done(actions: list[PlannedAction]) -> None:
            if gen != self._generation:
                return
            self.progress.stop()
            self.tree.set_actions(actions, self.api.root_dir())
            self._done = True
            self.completeChanged.emit()

        def fail(e: Exception) -> None:
            if gen != self._generation:
                return
            self.progress.stop()
            show_error(self.error, f"{error_text(e)} Die Vorschau ist optional, du kannst trotzdem fortfahren.")
            self._done = True
            self.completeChanged.emit()

        self._task = run_task(self.api.dry_run, with_progress=True,
                              on_progress=self.progress.update_progress, on_done=done, on_error=fail)

    def _cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def cleanupPage(self) -> None:  # noqa: N802
        self._cancel()
        self._generation += 1

    def isComplete(self) -> bool:  # noqa: N802
        return self._done


# ---------------- 5. Erster Sync ----------------

class FinishPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Fertig eingerichtet")
        self.setSubTitle("ILIAS Sync läuft ab jetzt im Hintergrund.")
        self.start_now = QCheckBox("Synchronisierung jetzt starten")
        self.start_now.setChecked(True)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Beim ersten Sync werden alle Materialien deiner Kurse geladen. Das kann je nach Anzahl "
            "der Videos einige Zeit dauern und viel Speicherplatz belegen. Videos lassen sich pro Kurs "
            "im Fenster unter Kurse abschalten."
        ))
        lay.addSpacing(12)
        lay.addWidget(self.start_now)
        lay.addStretch(1)
        where = "in der Menüleiste oben rechts" if sys.platform == "darwin" else "im Infobereich der Taskleiste"
        lay.addWidget(hint_label(f"Das Symbol von ILIAS Sync findest du {where}. "
                                 "Dort kannst du jederzeit synchronisieren oder das Fenster öffnen."))


# ---------------- Wizard ----------------

class SetupWizard(QWizard):
    def __init__(self, api) -> None:
        super().__init__()
        self.api = api
        self.username: str | None = api.stored_username()
        self.setWindowTitle("ILIAS Sync einrichten")
        self.setWindowIcon(app_icon())
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(760, 560)
        self.setButtonText(QWizard.WizardButton.NextButton, "Weiter")
        self.setButtonText(QWizard.WizardButton.BackButton, "Zurück")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Fertig")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Abbrechen")

        self.addPage(LoginPage(api))
        self.addPage(BaseDirPage(api))
        self.addPage(CoursesPage(api))
        self.addPage(PreviewPage(api))
        self.finish_page = FinishPage()
        self.addPage(self.finish_page)

        # macOS: Abbrechen links neben Zurück und Weiter, Hauptknopf ganz rechts
        if sys.platform == "darwin":
            wb = QWizard.WizardButton
            self.setButtonLayout([wb.Stretch, wb.CancelButton, wb.BackButton, wb.NextButton,
                                  wb.CommitButton, wb.FinishButton])

        # grössere Seitentitel im Stil des Hauptfensters. Untertitel steht im selben
        # Label, weil ModernStyle ihn sonst eingerückt unter den Titel setzt
        self.setTitleFormat(Qt.TextFormat.RichText)
        for page_id in self.pageIds():
            page = self.page(page_id)
            page.setTitle(f'<span style="font-size:20px; font-weight:600;">{page.title()}</span>'
                          f'<br><span style="font-size:13px; font-weight:400;">{page.subTitle()}</span>')
            page.setSubTitle("")

    def start_sync_requested(self) -> bool:
        return self.finish_page.start_now.isChecked()

    def reject(self) -> None:
        answer = QMessageBox.question(
            self, "Einrichtung abbrechen",
            "Ohne Einrichtung kann ILIAS Sync nichts herunterladen. Einrichtung abbrechen und beenden?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            super().reject()
