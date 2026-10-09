"""
Dialog "Neues Update verfügbar" (GUI_KONTEXT.md 3.5).

Nicht modal: Sync und Fenster bleiben bedienbar. Der Download läuft über den
Controller im Worker, danach startet install_update und die App beendet sich.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ilias_sync.api import IliasSyncError, Progress, UpdateInfo
from ilias_sync.gui import logic
from ilias_sync.gui.controller import Controller, error_text
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.widgets import ProgressPanel, button, error_label, hint_label, role_label, show_error

log = logging.getLogger(__name__)

HINT_SYNC = "Wird nach dem laufenden Sync möglich."
HINT_MANUAL = "Bitte die neue Version manuell installieren."
HINT_INSTALL_AFTER_SYNC = "Der Download ist fertig. Die Installation startet nach dem laufenden Sync."


class UpdateDialog(QDialog):
    def __init__(self, controller: Controller, info: UpdateInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller
        self.info = info
        self._installer: Path | None = None   # fertig geladen, wartet auf das Ende eines Syncs
        self._installing = False
        self.setWindowTitle("Neues Update verfügbar")
        self.setWindowIcon(app_icon())
        self.setModal(False)
        self.setMinimumWidth(460)

        self.title = role_label("Neues Update verfügbar", "hero")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.notes = hint_label()
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hint = hint_label()
        self.hint.hide()
        self.error = error_label()
        self.progress = ProgressPanel(with_cancel=True)
        self.progress.cancel_clicked.connect(self.c.cancel_update_download)

        self.btn_later = button("Später", "ghost")
        self.btn_later.clicked.connect(self._later)
        self.btn_install = button("Jetzt installieren", "primary")
        self.btn_install.clicked.connect(self._install)
        self.btn_install.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_later)
        row.addWidget(self.btn_install)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(12)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addWidget(self.notes)
        lay.addWidget(self.hint)
        lay.addWidget(self.error)
        lay.addWidget(self.progress)
        lay.addSpacing(4)
        lay.addLayout(row)

        self.c.busy_changed.connect(self._on_busy_changed)
        self.c.update_download_progress.connect(self._on_progress)
        self.c.update_downloaded.connect(self._on_downloaded)
        self.c.update_download_failed.connect(self._on_failed)
        self.set_info(info)

    # ---------------- Anzeige ----------------

    def set_info(self, info: UpdateInfo) -> None:
        """Neue oder dieselbe Version anzeigen (Dialog wird wiederverwendet)."""
        self.info = info
        self.text.setText(logic.update_dialog_text(info.version, self.c.app_version()))
        notes = info.notes.strip()
        if info.size:
            notes = f"{notes}\n\nGröße: {logic.format_size(info.size)}".strip()
        self.notes.setText(notes)
        self.notes.setVisible(bool(notes))
        self._refresh()

    def _refresh(self) -> None:
        can_install = self.c.can_self_update()
        downloading = self.c.updating
        self.btn_install.setVisible(can_install)
        self.btn_install.setEnabled(can_install and not downloading and not self._installing
                                    and self._installer is None and not self.c.syncing)
        self.btn_later.setEnabled(not downloading and not self._installing)
        if not can_install:
            self._set_hint(HINT_MANUAL)
        elif self._installer is not None and self.c.syncing:
            self._set_hint(HINT_INSTALL_AFTER_SYNC)
        elif self.c.syncing and not downloading:
            self._set_hint(HINT_SYNC)
        else:
            self._set_hint(None)

    def _set_hint(self, text: str | None) -> None:
        self.hint.setText(text or "")
        self.hint.setVisible(bool(text))

    def _on_busy_changed(self, _busy: bool) -> None:
        if self._installer is not None and not self.c.syncing:
            self._run_install(self._installer)
            return
        self._refresh()

    # ---------------- Aktionen ----------------

    def _later(self) -> None:
        self.c.snooze_update(self.info.version)
        self.close()

    def _install(self) -> None:
        if self.c.syncing or not self.c.can_self_update():
            return
        show_error(self.error, None)
        if not self.c.start_update_download(self.info):
            return
        self.progress.start("Update wird heruntergeladen ...")
        self._refresh()

    def _on_progress(self, p: Progress) -> None:
        self.progress.update_progress(p)

    def _on_downloaded(self, path: Path) -> None:
        self.progress.stop()
        if self.c.syncing:
            # Sync wurde während des Downloads gestartet: erst danach installieren
            self._installer = Path(path)
            self._refresh()
            return
        self._run_install(Path(path))

    def _run_install(self, path: Path) -> None:
        self._installer = None
        self._installing = True
        self._set_hint("Update wird installiert, ILIAS Sync startet gleich neu ...")
        self._refresh()
        try:
            self.c.install_update(path)
        except Exception as e:  # noqa: BLE001, Fehler im Dialog anzeigen
            if isinstance(e, IliasSyncError):
                log.warning("Update Installation fehlgeschlagen: %s", e)
            else:
                log.exception("Update Installation fehlgeschlagen")
            self._installing = False
            self._failed(error_text(e))

    def _on_failed(self, text: str, cancelled: bool) -> None:
        self.progress.stop()
        if cancelled:
            show_error(self.error, None)
            self._refresh()
            self._set_hint(text)
            self.btn_install.setText("Jetzt installieren")
            return
        self._failed(text)

    def _failed(self, text: str) -> None:
        show_error(self.error, text)
        self.btn_install.setText("Erneut versuchen")
        self._refresh()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Schliessen während des Downloads bricht ihn ab
        if self.c.updating:
            self.c.cancel_update_download()
        super().closeEvent(event)
