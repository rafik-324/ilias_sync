"""Tab Einstellungen: Basisordner, Intervall, Parallelität, Autostart, Konto."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import ConfigError
from ilias_sync.gui import logic, theme
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.icons import dot
from ilias_sync.gui.widgets import IntervalBox, Page, SpinBox, button, error_label, hint_label, set_muted, show_error

log = logging.getLogger(__name__)


def _form(box: QGroupBox) -> QFormLayout:
    form = QFormLayout(box)
    form.setContentsMargins(0, 8, 0, 0)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return form


class SettingsTab(Page):
    login_requested = Signal()

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__("Einstellungen", "Ablage, Synchronisierung und Konto.", parent)
        self.c = controller
        self.dirty = False
        self._loading = False
        self._account_key: tuple | None = None

        self.btn_sync = button("Jetzt synchronisieren", "primary")
        self.btn_sync.clicked.connect(self._sync)
        self.actions.addWidget(self.btn_sync)

        self.error = error_label()

        # Ablage
        self.base_dir = QLineEdit()
        self.base_dir.setReadOnly(True)
        btn_base = button("Ändern ...")
        btn_base.clicked.connect(self._change_base_dir)
        base_row = QHBoxLayout()
        base_row.addWidget(self.base_dir, 1)
        base_row.addWidget(btn_base)
        self.root_name = QLineEdit()
        self.root_name.textEdited.connect(self._changed)

        box_storage = QGroupBox("Ablage")
        f1 = _form(box_storage)
        f1.addRow("Basisordner", base_row)
        self.root_name.setPlaceholderText("leer: direkt im Basisordner")
        f1.addRow("Unterordner (optional)", self.root_name)
        f1.addRow("", hint_label("Leer lassen, dann ist der Basisordner selbst der Uni Ordner. Ein neuer Basisordner "
                                 "oder Unterordner bedeutet einen neuen Uni Ordner, der erste Sync dorthin braucht "
                                 "einen leeren Ordner."))

        # Sync
        self.interval = IntervalBox()
        self.interval.valueChanged.connect(self._changed)
        self.parallel = SpinBox()
        self.parallel.setRange(1, 5)
        self.parallel.setToolTip("Gleichzeitige Downloads. Höhere Werte belasten den ILIAS Server stärker.")
        self.parallel.valueChanged.connect(self._changed)
        self.pattern = QLineEdit()
        self.pattern.textEdited.connect(self._changed)
        self.pattern_hint = hint_label()
        self.autostart = QCheckBox("Beim Anmelden am Computer starten")
        self.autostart.toggled.connect(self._changed)

        box_sync = QGroupBox("Synchronisierung")
        f2 = _form(box_sync)
        f2.addRow("Intervall", self.interval)
        f2.addRow("Parallelität", self.parallel)
        f2.addRow("Semester Muster", self.pattern)
        f2.addRow("", self.pattern_hint)
        f2.addRow("", self.autostart)

        # Semester Ordner
        self.sem_table = QTableWidget(0, 2)
        self.sem_table.setHorizontalHeaderLabels(["Semester (aus Kursname)", "Ordner"])
        self.sem_table.verticalHeader().hide()
        self.sem_table.verticalHeader().setDefaultSectionSize(34)
        self.sem_table.setShowGrid(False)
        self.sem_table.horizontalHeader().setHighlightSections(False)
        self.sem_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sem_table.setMaximumHeight(150)
        self.sem_table.itemChanged.connect(self._changed)
        box_sem = QGroupBox("Semester Ordner")
        v_sem = QVBoxLayout(box_sem)
        v_sem.setContentsMargins(0, 8, 0, 0)
        v_sem.setSpacing(10)
        v_sem.addWidget(hint_label("Welches Semester in welchem Ordner landet, z. B. HWS 2026 in 01_semester. "
                                   "Leere Einträge werden beim nächsten Sync mit der nächsten Nummer angelegt."))
        v_sem.addWidget(self.sem_table)

        # Ignorieren
        self.ignore = QListWidget()
        self.ignore.setObjectName("plainList")
        self.ignore.setMaximumHeight(130)
        btn_ign_add = button("Hinzufügen")
        btn_ign_add.clicked.connect(self._add_ignore)
        btn_ign_del = button("Entfernen", "ghost")
        btn_ign_del.clicked.connect(self._remove_ignore)
        ign_buttons = QVBoxLayout()
        ign_buttons.addWidget(btn_ign_add)
        ign_buttons.addWidget(btn_ign_del)
        ign_buttons.addStretch(1)
        ign_row = QHBoxLayout()
        ign_row.addWidget(self.ignore, 1)
        ign_row.addLayout(ign_buttons)
        box_ignore = QGroupBox("Ignorierte lokale Ordner")
        v3 = QVBoxLayout(box_ignore)
        v3.setContentsMargins(0, 8, 0, 0)
        v3.setSpacing(10)
        v3.addWidget(hint_label("Diese Ordner werden nie betreten oder beschrieben, z. B. eigene Arbeit. "
                                "Muster mit * erlaubt."))
        v3.addLayout(ign_row)

        # Konto
        self.account = QLabel()
        self.btn_login = button("Anmelden ...", "primary")
        self.btn_login.clicked.connect(self.login_requested)
        self.btn_logout = button("Abmelden", "danger")
        self.btn_logout.clicked.connect(self._logout)
        acc_row = QHBoxLayout()
        acc_row.addWidget(self.account, 1)
        acc_row.addWidget(self.btn_login)
        acc_row.addWidget(self.btn_logout)
        box_account = QGroupBox("Konto")
        v4 = QVBoxLayout(box_account)
        v4.setContentsMargins(0, 8, 0, 0)
        v4.addLayout(acc_row)

        # Speichern
        self.btn_discard = button("Verwerfen", "ghost")
        self.btn_discard.clicked.connect(self.load)
        self.btn_save = button("Speichern", "primary")
        self.btn_save.clicked.connect(self.save)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.btn_discard)
        save_row.addWidget(self.btn_save)

        content = QWidget()
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 8, 0)
        inner.setSpacing(4)
        inner.addWidget(self.error)
        inner.addWidget(box_storage)
        inner.addWidget(box_sync)
        inner.addWidget(box_sem)
        inner.addWidget(box_ignore)
        inner.addWidget(box_account)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)

        self.body.addWidget(scroll, 1)
        self.body.addLayout(save_row)

        self.c.config_changed.connect(self._on_config_changed)
        self.c.state_changed.connect(self._refresh_state)
        self.load()

    # ---------------- Laden ----------------

    def _on_config_changed(self) -> None:
        if not self.dirty:
            self.load()

    def load(self) -> None:
        self._loading = True
        cfg = self.c.config
        show_error(self.error, self.c.config_error)
        if cfg is not None:
            self.base_dir.setText(str(cfg.base_dir or ""))
            self.root_name.setText(cfg.root_folder_name)
            self.interval.setValue(cfg.sync_interval_minutes)
            self.parallel.setValue(cfg.max_parallel)
            self.pattern.setText(cfg.semester_pattern)
            self.ignore.clear()
            self.ignore.addItems(cfg.ignore_local_dirs)
            self._fill_semesters(logic.semester_rows(cfg))
        try:
            self.autostart.setChecked(self.c.api.autostart_enabled())
        except Exception:  # noqa: BLE001
            log.exception("Autostart Status nicht lesbar")
        self._check_pattern()
        self._refresh_account(force=True)
        self._refresh_state()
        self._loading = False
        self._set_dirty(False)

    def _fill_semesters(self, rows: list[tuple[str, str]]) -> None:
        self.sem_table.blockSignals(True)
        self.sem_table.setRowCount(len(rows))
        for row, (sem, folder) in enumerate(rows):
            key = QTableWidgetItem(sem)
            key.setFlags(key.flags() & ~Qt.ItemFlag.ItemIsEditable)
            val = QTableWidgetItem(folder)
            if not folder:
                key.setIcon(QIcon(dot(theme.tokens().warning, 8)))
                val.setToolTip("Leer lassen: wird beim nächsten Sync automatisch mit der nächsten Nummer angelegt")
            self.sem_table.setItem(row, 0, key)
            self.sem_table.setItem(row, 1, val)
        self.sem_table.blockSignals(False)

    def _semesters(self) -> dict[str, str]:
        result = {}
        for row in range(self.sem_table.rowCount()):
            folder = self.sem_table.item(row, 1).text().strip()
            if folder:
                result[self.sem_table.item(row, 0).text()] = folder
        return result

    def _refresh_state(self) -> None:
        c = self.c
        self.btn_sync.setEnabled(c.can_sync())
        self.btn_sync.setText("Anmelden ..." if c.login_needed else "Jetzt synchronisieren")
        self._refresh_account()

    def _refresh_account(self, force: bool = False) -> None:
        # Schlüsselbund nur lesen, wenn sich der Anmeldezustand geändert hat,
        # nicht bei jedem Fortschritt eines Syncs
        key = (self.c.login_needed,)
        if not force and key == self._account_key:
            return
        self._account_key = key
        user = self.c.api.stored_username()
        if self.c.login_needed or not user:
            self.account.setText(f"Uni-ID: {user}, Anmeldung nötig" if user else "Nicht angemeldet")
        else:
            pw = "Passwort im Schlüsselbund" if self.c.api.has_stored_password() else "Passwort nicht gespeichert"
            self.account.setText(f"Uni-ID: {user} ({pw})")
        self.btn_login.setVisible(self.c.login_needed or not user)
        self.btn_logout.setVisible(bool(user) and not self.c.login_needed)

    # ---------------- Bearbeiten ----------------

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        self._check_pattern()
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self.dirty = dirty
        self.btn_save.setEnabled(dirty)
        self.btn_discard.setEnabled(dirty)

    def _check_pattern(self) -> None:
        err = logic.regex_error(self.pattern.text())
        if err:
            self.pattern_hint.setText(f"Ungültig: {err}")
            theme.set_role(self.pattern_hint, "error")
        else:
            self.pattern_hint.setText(r"Erkennt z. B. (HWS 2026) oder (FSS 2027). Gruppe 1: Semesterart, Gruppe 2: Jahr.")
            set_muted(self.pattern_hint)

    def _change_base_dir(self) -> None:
        current = self.base_dir.text() or str(Path.home())
        answer = QMessageBox.warning(
            self, "Basisordner ändern",
            "Vorhandene Dateien werden nicht verschoben. Neue Downloads landen im neuen Ordner.\n\n"
            "Trotzdem ändern?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path = QFileDialog.getExistingDirectory(self, "Basisordner wählen", current)
        if not path:
            return
        check = self.c.api.check_base_dir(Path(path))
        if not check.exists or not check.writable:
            QMessageBox.warning(self, "Basisordner", check.message or "Der Ordner ist nicht nutzbar.")
            return
        if not check.is_empty:
            answer = QMessageBox.warning(
                self, "Basisordner",
                "Der Uni Ordner an dieser Stelle enthält schon Dateien. Hat ILIAS Sync ihn nicht selbst "
                "befüllt, wird der erste Sync dorthin verweigert, damit nichts mit deinen eigenen Unterlagen "
                "durcheinanderkommt.\n\nTrotzdem diesen Ordner wählen?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.base_dir.setText(path)
        self._set_dirty(True)

    def _add_ignore(self) -> None:
        text, ok = QInputDialog.getText(self, "Ordner ignorieren", "Ordnername oder Muster (z. B. 04_code oder *-MacBook*):")
        if ok and text.strip():
            self.ignore.addItem(text.strip())
            self._set_dirty(True)

    def _remove_ignore(self) -> None:
        row = self.ignore.currentRow()
        if row >= 0:
            self.ignore.takeItem(row)
            self._set_dirty(True)

    # ---------------- Speichern ----------------

    def save(self) -> bool:
        if self.c.config is None:
            show_error(self.error, "Die Config konnte nicht geladen werden. Bitte die Config Datei prüfen.")
            return False
        cfg = deepcopy(self.c.config)
        cfg.base_dir = Path(self.base_dir.text()) if self.base_dir.text() else None
        cfg.root_folder_name = self.root_name.text().strip()
        cfg.sync_interval_minutes = self.interval.value()
        cfg.max_parallel = self.parallel.value()
        cfg.semester_pattern = self.pattern.text()
        cfg.ignore_local_dirs = [self.ignore.item(i).text() for i in range(self.ignore.count())]
        cfg.semesters = self._semesters()
        cfg.autostart = self.autostart.isChecked()

        errors = logic.validate_settings(cfg)
        if errors:
            show_error(self.error, "\n".join(errors))
            return False
        try:
            if cfg.autostart != self.c.api.autostart_enabled():
                self.c.api.set_autostart(cfg.autostart)
            self.c.save_config(cfg)
        except ConfigError as e:
            show_error(self.error, str(e))
            return False
        except Exception as e:  # noqa: BLE001
            log.exception("Einstellungen konnten nicht gespeichert werden")
            show_error(self.error, error_text(e))
            return False
        show_error(self.error, None)
        self._set_dirty(False)
        return True

    def confirm_leave(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Ungespeicherte Änderungen",
            "Die Einstellungen sind noch nicht gespeichert. Jetzt speichern?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self.load()
            return True
        return False

    def _sync(self) -> None:
        if self.c.login_needed:
            self.login_requested.emit()
            return
        # Der Sync nutzt die gespeicherte Config, offene Änderungen also vorher klären
        if not self.confirm_leave():
            return
        self.c.start_sync()

    def _logout(self) -> None:
        answer = QMessageBox.question(
            self, "Abmelden",
            "Passwort und Session werden von diesem Gerät entfernt. "
            "Heruntergeladene Dateien bleiben erhalten.\n\nAbmelden?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.c.api.logout()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Abmelden", error_text(e))
            return
        self.c.login_needed = True
        self.c.state_changed.emit()
