"""Tab Vorschau: Dry Run als Baum, bevor echt synchronisiert wird."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ilias_sync.api import PlannedAction
from ilias_sync.gui.controller import Controller
from ilias_sync.gui.widgets import PreviewTree, ProgressPanel, error_label, hint_label, show_error


class PreviewTab(QWidget):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.c = controller

        self.btn_preview = QPushButton("Vorschau erstellen")
        self.btn_preview.clicked.connect(self._start)
        self.btn_sync = QPushButton("Jetzt so synchronisieren")
        self.btn_sync.setEnabled(False)
        self.btn_sync.clicked.connect(self._sync)

        top = QHBoxLayout()
        top.addWidget(self.btn_preview)
        top.addWidget(hint_label("Zeigt, wie neue Dateien einsortiert würden. Es wird nichts geschrieben."), 1)

        self.progress = ProgressPanel()
        self.progress.cancel_clicked.connect(self.c.cancel)
        self.error = error_label()
        self.tree = PreviewTree()

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.btn_sync)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.progress)
        lay.addWidget(self.error)
        lay.addWidget(self.tree, 1)
        lay.addLayout(bottom)

        self._waiting = False
        self.c.dry_run_finished.connect(self._done)
        self.c.dry_run_failed.connect(self._failed)
        self.c.progress.connect(self._progress)
        self.c.busy_changed.connect(self._update_buttons)
        self.c.sync_finished.connect(lambda *_: self._invalidate())
        self._update_buttons()

    def _start(self) -> None:
        show_error(self.error, None)
        if self.c.start_dry_run():
            self._waiting = True
            self.tree.clear()
            self.progress.start("Vorschau wird erstellt ...")

    def _progress(self, p) -> None:
        if self._waiting:
            self.progress.update_progress(p)

    def _done(self, actions: list[PlannedAction]) -> None:
        self._waiting = False
        self.progress.stop()
        self.tree.set_actions(actions, self.c.api.root_dir())
        self._update_buttons()

    def _failed(self, text: str) -> None:
        self._waiting = False
        self.progress.stop()
        show_error(self.error, text)
        self._update_buttons()

    def _invalidate(self) -> None:
        # nach einem echten Sync ist die Vorschau veraltet
        if self.tree.actions:
            self.tree.clear()
            self.tree.summary.setText("Die Vorschau ist nach dem letzten Sync veraltet. Bitte neu erstellen.")
        self._update_buttons()

    def _sync(self) -> None:
        if self.c.start_sync():
            self.btn_sync.setEnabled(False)

    def _update_buttons(self, *_args) -> None:
        self.btn_preview.setEnabled(self.c.can_sync())
        self.btn_sync.setEnabled(self.c.can_sync() and bool(self.tree.actions) and not self.c.login_needed)
