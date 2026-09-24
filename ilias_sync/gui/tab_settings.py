"""Tab Einstellungen: Basisordner, Intervall, Parallelität, Autostart, Konto."""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import ConfigError
from ilias_sync.gui import logic
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.widgets import error_label, hint_label, set_muted, show_error

log = logging.getLogger(__name__)


class SettingsTab(QWidget):
    login_requested = Signal()

    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller
        self.dirty = False
        self._loading = False

        self.error = error_label()

        # Ablage
        self.base_dir = QLineEdit()
        self.base_dir.setReadOnly(True)
        btn_base = QPushButton("Ändern ...")
        btn_base.clicked.connect(self._change_base_dir)
        base_row = QHBoxLayout()
        base_row.addWidget(self.base_dir, 1)
        base_row.addWidget(btn_base)
        self.root_name = QLineEdit()
        self.root_name.textEdited.connect(self._changed)

        box_storage = QGroupBox("Ablage")
        f1 = QFormLayout(box_storage)
        f1.addRow("Basisordner", base_row)
        f1.addRow("Name des Uni Ordners", self.root_name)
        f1.addRow("", hint_label("Wird unter dem Basisordner angelegt bzw. weiterverwendet."))

        # Sync
        self.interval = QSpinBox()
        self.interval.setRange(15, 1440)
        self.interval.setSingleStep(15)
        self.interval.setSuffix(" Minuten")
        self.interval.valueChanged.connect(self._changed)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 5)
        self.parallel.setToolTip("Gleichzeitige Downloads. Höhere Werte belasten den ILIAS Server stärker.")
        self.parallel.valueChanged.connect(self._changed)
        self.pattern = QLineEdit()
        self.pattern.textEdited.connect(self._changed)
        self.pattern_hint = hint_label()
        self.autostart = QCheckBox("Beim Anmelden am Computer starten")
        self.autostart.toggled.connect(self._changed)

        box_sync = QGroupBox("Synchronisierung")
        f2 = QFormLayout(box_sync)
        f2.addRow("Intervall", self.interval)
        f2.addRow("Parallelität", self.parallel)
        f2.addRow("Semester Muster", self.pattern)
        f2.addRow("", self.pattern_hint)
        f2.addRow("", self.autostart)

        # Ignorieren
        self.ignore = QListWidget()
        self.ignore.setMaximumHeight(110)
        btn_ign_add = QPushButton("Hinzufügen")
        btn_ign_add.clicked.connect(self._add_ignore)
        btn_ign_del = QPushButton("Entfernen")
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
        v3.addWidget(hint_label("Diese Ordner werden nie betreten oder beschrieben, z. B. eigene Arbeit. "
                                "Muster mit * erlaubt."))
        v3.addLayout(ign_row)

        # Konto
        self.account = QLabel()
        self.btn_login = QPushButton("Anmelden ...")
        self.btn_login.clicked.connect(self.login_requested)
        self.btn_logout = QPushButton("Abmelden")
        self.btn_logout.clicked.connect(self._logout)
        acc_row = QHBoxLayout()
        acc_row.addWidget(self.account, 1)
        acc_row.addWidget(self.btn_login)
        acc_row.addWidget(self.btn_logout)
        box_account = QGroupBox("Konto")
        v4 = QVBoxLayout(box_account)
        v4.addLayout(acc_row)

        # Speichern
        self.btn_discard = QPushButton("Verwerfen")
        self.btn_discard.clicked.connect(self.load)
        self.btn_save = QPushButton("Speichern")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self.save)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.btn_discard)
        save_row.addWidget(self.btn_save)

        content = QWidget()
        inner = QVBoxLayout(content)
        inner.addWidget(self.error)
        inner.addWidget(box_storage)
        inner.addWidget(box_sync)
        inner.addWidget(box_ignore)
        inner.addWidget(box_account)
        inner.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll, 1)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.addLayout(save_row)
        lay.addWidget(bottom)

        self.c.config_changed.connect(self._on_config_changed)
        self.c.state_changed.connect(self._refresh_account)
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
        try:
            self.autostart.setChecked(self.c.api.autostart_enabled())
        except Exception:  # noqa: BLE001
            log.exception("Autostart Status nicht lesbar")
        self._check_pattern()
        self._refresh_account()
        self._loading = False
        self._set_dirty(False)

    def _refresh_account(self) -> None:
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
            self.pattern_hint.setStyleSheet("color: #c42b1c;")
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
