"""Wiederverwendbare Widgets für Hauptfenster und Setup Wizard."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import ActionKind, InvalidCredentialsError, NetworkError, PlannedAction, Progress
from ilias_sync.gui import logic
from ilias_sync.gui.controller import error_text
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.worker import run_task

FEHLER_STIL = "color: #c42b1c;"

ACTION_COLORS: dict[ActionKind, str] = {
    ActionKind.NEW: "#107c10",
    ActionKind.UPDATE: "#0063b1",
    ActionKind.CONFLICT: "#ca5010",
    ActionKind.SKIP_REMOVED: "#8a8886",
    ActionKind.SKIP_IGNORED: "#8a8886",
    ActionKind.CREATE_DIR: "#5c2d91",
    ActionKind.UNASSIGNED: "#c19c00",
}


def error_label() -> QLabel:
    lbl = QLabel()
    lbl.setStyleSheet(FEHLER_STIL)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.hide()
    return lbl


def set_muted(lbl: QLabel) -> None:
    """Abgeschwächte Textfarbe, lesbar im hellen und dunklen Modus."""
    lbl.setStyleSheet("")
    pal = lbl.palette()
    color = QColor(pal.color(QPalette.ColorRole.WindowText))
    color.setAlphaF(0.62)
    pal.setColor(QPalette.ColorRole.WindowText, color)
    lbl.setPalette(pal)


def hint_label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    set_muted(lbl)
    lbl.setWordWrap(True)
    return lbl


def show_error(lbl: QLabel, text: str | None) -> None:
    lbl.setText(text or "")
    lbl.setVisible(bool(text))


# ---------------- Fortschritt ----------------

class ProgressPanel(QWidget):
    """Fortschrittsbalken mit Phase und Detail, optional mit Abbrechen."""

    cancel_clicked = Signal()

    def __init__(self, with_cancel: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(10)
        self.label = hint_label()
        self.cancel_btn = QPushButton("Abbrechen")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(with_cancel)

        row = QHBoxLayout()
        row.addWidget(self.bar, 1)
        row.addWidget(self.cancel_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(row)
        lay.addWidget(self.label)
        self.hide()

    def start(self, text: str = "Bitte warten ...") -> None:
        self.bar.setRange(0, 0)
        self.label.setText(text)
        self.cancel_btn.setEnabled(True)
        self.show()

    def update_progress(self, p: Progress) -> None:
        if p.total:
            self.bar.setRange(0, p.total)
            self.bar.setValue(p.current)
            text = f"{p.phase} {p.current} von {p.total}"
        else:
            self.bar.setRange(0, 0)
            text = f"{p.phase} ..."
        if p.detail:
            text += f": {p.detail}"
        self.label.setText(text)

    def stop(self) -> None:
        self.hide()

    def _cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.label.setText("Wird abgebrochen ...")
        self.cancel_clicked.emit()


# ---------------- Vorschau ----------------

class PreviewTree(QWidget):
    """Baum der geplanten Aktionen nach lokalem Zielpfad."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.actions: list[PlannedAction] = []
        self.root: Path | None = None

        self.only_changes = QCheckBox("Nur Änderungen")
        self.only_changes.setChecked(True)
        self.only_changes.toggled.connect(self._rebuild)
        self.summary = QLabel()
        self.summary.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Aktion", "Größe"])
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)

        legend = QLabel(" ".join(
            f'<span style="color:{ACTION_COLORS[k]}">●</span> {logic.ACTION_LABELS[k]}&nbsp;&nbsp;'
            for k in ActionKind
        ))
        legend.setWordWrap(True)
        legend.setStyleSheet("font-size: 11px;")

        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.only_changes)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.tree, 1)
        lay.addWidget(legend)

    def set_actions(self, actions: list[PlannedAction], root: Path | None) -> None:
        self.actions = actions
        self.root = root
        self.summary.setText(logic.summarize(actions).text() if actions else "Keine Änderungen, alles aktuell.")
        self._rebuild()

    def clear(self) -> None:
        self.actions = []
        self.tree.clear()
        self.summary.setText("")

    def _rebuild(self) -> None:
        self.tree.clear()
        tree = logic.build_preview_tree(self.actions, self.root, self.only_changes.isChecked())
        self._add_children(self.tree.invisibleRootItem(), tree, depth=0)

    def _add_children(self, parent: QTreeWidgetItem, node: logic.TreeNode, depth: int) -> None:
        for child in node.sorted_children():
            item = QTreeWidgetItem(parent, [child.name, "", ""])
            if child.action is not None:
                kind = child.action.kind
                item.setText(1, logic.ACTION_LABELS[kind])
                item.setText(2, logic.format_size(child.action.size))
                item.setForeground(1, QBrush(QColor(ACTION_COLORS[kind])))
                item.setToolTip(0, child.action.ilias_path)
                item.setToolTip(1, logic.ACTION_HINTS[kind])
            else:
                count = child.count_files()
                item.setText(2, f"{count} {'Datei' if count == 1 else 'Dateien'}")
            self._add_children(item, child, depth + 1)
            # Semester, Kurs und Kategorie aufklappen
            item.setExpanded(depth < 3)


# ---------------- Login ----------------

class LoginForm(QWidget):
    """Uni-ID und Passwort. Das Passwort wird nach dem Versuch sofort geleert."""

    logged_in = Signal(str)   # Uni-ID
    busy_changed = Signal(bool)

    def __init__(self, api, user: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.user = QLineEdit(user or "")
        self.user.setPlaceholderText("z. B. musterma")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("Passwort im Schlüsselbund speichern")
        self.remember.setChecked(True)
        self.remember.setToolTip("Nötig für den automatischen Sync im Hintergrund.")
        self.button = QPushButton("Anmelden")
        self.button.setDefault(True)
        self.button.clicked.connect(self.submit)
        self.password.returnPressed.connect(self.submit)
        self.status = QLabel()
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Uni-ID", self.user)
        form.addRow("Passwort", self.password)
        form.addRow("", self.remember)
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addWidget(self.status, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(form)
        lay.addLayout(row)
        self._busy = False

    def submit(self) -> None:
        if self._busy:
            return
        user = self.user.text().strip()
        if not user or not self.password.text():
            self._set_status("Bitte Uni-ID und Passwort eingeben.", error=True)
            return
        self._set_busy(True)
        self._set_status("Anmeldung läuft ...")
        # Passwort nur als Argument weitergeben, danach sofort aus dem Feld entfernen
        run_task(self.api.login, user, self.password.text(), self.remember.isChecked(),
                 on_done=lambda _: self._ok(user), on_error=self._fail)
        self.password.clear()

    def _ok(self, user: str) -> None:
        self._set_busy(False)
        self._set_status("Anmeldung erfolgreich.", ok=True)
        self.logged_in.emit(user)

    def _fail(self, e: Exception) -> None:
        self._set_busy(False)
        if isinstance(e, InvalidCredentialsError):
            text = "Uni-ID oder Passwort falsch. Bitte erneut versuchen."
        elif isinstance(e, NetworkError):
            text = f"Keine Verbindung zu ILIAS. {error_text(e)}"
        else:
            text = error_text(e)
        self._set_status(text, error=True)
        self.password.setFocus()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.button.setEnabled(not busy)
        self.user.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.busy_changed.emit(busy)

    def _set_status(self, text: str, error: bool = False, ok: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(FEHLER_STIL if error else ("color: #107c10;" if ok else ""))


class LoginDialog(QDialog):
    def __init__(self, api, user: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bei ILIAS anmelden")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(420)
        self.form = LoginForm(api, user)
        self.form.logged_in.connect(lambda _: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Die Anmeldung bei ILIAS ist abgelaufen oder das Passwort hat sich geändert."))
        lay.addWidget(self.form)
        lay.addWidget(buttons)
        if user:
            self.form.password.setFocus()
