"""Wiederverwendbare Widgets für Hauptfenster und Setup Wizard."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import ActionKind, InvalidCredentialsError, NetworkError, PlannedAction, Progress
from ilias_sync.gui import logic, theme
from ilias_sync.gui.controller import error_text
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.worker import run_task

def action_color(kind: ActionKind) -> str:
    """Farbe einer geplanten Aktion, passend zum aktuellen Farbmodus."""
    t = theme.tokens()
    return {
        ActionKind.NEW: t.success,
        ActionKind.CHANGED: t.accent,
        ActionKind.UPDATE: t.accent,
        ActionKind.CONFLICT: t.warning,
        ActionKind.SKIP_REMOVED: t.faint,
        ActionKind.SKIP_IGNORED: t.faint,
        ActionKind.CREATE_DIR: t.muted,
        ActionKind.UNASSIGNED: t.warning,
        ActionKind.SKIP_TOO_LONG: t.danger,
    }[kind]


class IntervalBox(QComboBox):
    """Auswahl des Sync Intervalls in sinnvollen Stufen statt Minuten. value() und setValue()
    arbeiten in Minuten wie Config.sync_interval_minutes. Ein Wert außerhalb der Stufen
    (z. B. aus einer älteren Config) bleibt als eigener Eintrag erhalten."""

    valueChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for minutes in logic.INTERVAL_PRESETS:
            self.addItem(logic.interval_text(minutes), minutes)
        self.currentIndexChanged.connect(lambda _i: self.valueChanged.emit(self.value()))

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()   # wie ComboBox: Mausrad scrollt die Seite, ändert nicht die Auswahl

    def value(self) -> int:
        return int(self.currentData())

    def setValue(self, minutes: int) -> None:  # noqa: N802
        idx = self.findData(minutes)
        if idx < 0:
            pos = sum(1 for i in range(self.count()) if int(self.itemData(i)) < minutes)
            self.insertItem(pos, logic.interval_text(minutes), minutes)
            idx = pos
        self.setCurrentIndex(idx)


class ElideDelegate(QStyledItemDelegate):
    """Kürzt Text in einer Tabellenspalte auf eigene Art: vorn (Ordner, Ende sichtbar) oder
    rechts mit sichtbarer Dateiendung (Dateinamen)."""

    PADDING = 14   # Reserve (Innenabstand aus dem Stylesheet), damit Qt nicht zusätzlich kürzt

    def __init__(self, mode: Qt.TextElideMode, keep_suffix: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.keep_suffix = keep_suffix

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        option.textElideMode = self.mode
        if self.keep_suffix:
            # Textbereich wie der Stil ihn zeichnet (inklusive Innenabstand aus dem Stylesheet)
            widget = option.widget
            style = widget.style() if widget is not None else QApplication.style()
            rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, widget)
            width = rect.width() - self.PADDING
            option.text = logic.elide_keep_suffix(option.text, width, option.fontMetrics.horizontalAdvance)


class ComboBox(QComboBox):
    """Dropdown, dessen Auswahl sich nicht durch Mausrad oder Trackpad ändert.

    In scrollbaren Tabellen und Seiten sprang die Auswahl sonst beim Scrollen um.
    Das Rad scrollt stattdessen den umgebenden Bereich, gewählt wird per Klick oder Tastatur.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class SpinBox(QSpinBox):
    """Zahlenfeld ohne Mausrad Änderung, aus demselben Grund wie ComboBox."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class ErrorListDialog(QDialog):
    """Einzelne Fehler des letzten Syncs, eine Zeile pro Fehler, kopierbar."""

    def __init__(self, errors: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fehler beim letzten Sync")
        self.setWindowIcon(app_icon())
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        summary = role_label(logic.error_summary(errors) or "Keine Fehler.", "section")
        summary.setWordWrap(True)
        lay.addWidget(summary)
        self.text = QPlainTextEdit("\n".join(errors))
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setObjectName("logView")
        lay.addWidget(self.text, 1)
        lay.addWidget(hint_label("Pfad zu lang: ein kürzerer Basisordner hilft (Einstellungen). "
                                 "Verbindungsfehler: beim nächsten Sync wird es erneut versucht."))
        buttons = QDialogButtonBox()
        copy = buttons.addButton("Kopieren", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        buttons.addButton("Schließen", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


def error_label() -> QLabel:
    """Fehlerhinweis als dezente rote Box, standardmässig versteckt."""
    lbl = QLabel()
    theme.set_role(lbl, "banner-error")
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.hide()
    return lbl


def set_muted(lbl: QLabel) -> None:
    """Abgeschwächte Textfarbe, lesbar im hellen und dunklen Modus."""
    theme.set_role(lbl, "muted")


def hint_label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    set_muted(lbl)
    lbl.setWordWrap(True)
    return lbl


def role_label(text: str, role: str) -> QLabel:
    lbl = QLabel(text)
    theme.set_role(lbl, role)
    return lbl


def show_error(lbl: QLabel, text: str | None) -> None:
    lbl.setText(text or "")
    lbl.setVisible(bool(text))


def button(text: str, variant: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if variant:
        theme.set_variant(btn, variant)
    return btn


# ---------------- Layout Bausteine ----------------

class Card(QFrame):
    """Weisse bzw. dunkle Fläche mit Rand und runden Ecken."""

    def __init__(self, parent: QWidget | None = None, margins: int = 16, spacing: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(margins, margins, margins, margins)
        self.lay.setSpacing(spacing)


class Page(QWidget):
    """Seite im Hauptfenster: Titel, Untertitel, rechts optionale Aktionen, darunter Inhalt."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        outer.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = role_label(title, "title")
        titles.addWidget(self.title)
        self.subtitle = role_label(subtitle, "subtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(bool(subtitle))
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body, 1)


# ---------------- Fortschritt ----------------

class Spinner(QWidget):
    """Flüssiger Lade Kreis: Bogen, der sich stufenlos dreht (QVariantAnimation im Takt der
    Qt Animationen, etwa 60 Bilder pro Sekunde). Läuft nur, solange er sichtbar ist, und
    zeichnet in den Farben des aktuellen Themes."""

    PERIOD_MS = 900

    def __init__(self, size: int = 16, background: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._background = background
        self._angle = 0.0
        self._wanted = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(self.PERIOD_MS)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_value)

    def start(self) -> None:
        self._wanted = True
        if self.isVisible() and not self.is_running():
            self._anim.start()

    def stop(self) -> None:
        self._wanted = False
        self._anim.stop()
        self._angle = 0.0
        self.update()

    def is_running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._wanted and not self.is_running():
            self._anim.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        # versteckt keine Last erzeugen, beim nächsten Anzeigen geht es weiter
        self._anim.stop()
        super().hideEvent(event)

    def _on_value(self, value) -> None:
        self._angle = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = theme.tokens()
        size = self.width()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._background:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.accent_soft))
            p.drawEllipse(QRectF(0, 0, size, size))
        width = max(1.6, size / (12 if self._background else 8))
        inset = size * (0.26 if self._background else 0.0) + width / 2 + 0.5
        rect = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        track = QColor(t.accent)
        track.setAlphaF(0.18)
        p.setPen(QPen(track, width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        p.setPen(QPen(QColor(t.accent), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # Qt misst Winkel gegen den Uhrzeigersinn in 1/16 Grad, daher negativ für Drehung im Uhrzeigersinn
        p.drawArc(rect, int((90 - self._angle) * 16), int(-100 * 16))
        p.end()


class ProgressPanel(QWidget):
    """Fortschrittsbalken mit Phase und Detail, optional mit Abbrechen."""

    cancel_clicked = Signal()

    def __init__(self, with_cancel: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.label = hint_label()
        self.cancel_btn = button("Abbrechen", "ghost")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.setVisible(with_cancel)

        self.spinner = Spinner(14)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label, 1)
        row.addWidget(self.cancel_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.bar)
        lay.addLayout(row)
        self.hide()

    def start(self, text: str = "Bitte warten ...") -> None:
        self.bar.setRange(0, 0)
        self.label.setText(text)
        self.cancel_btn.setEnabled(True)
        self.show()
        self.spinner.start()

    def update_progress(self, p: Progress) -> None:
        self.bar.setRange(0, p.total or 0)   # ohne Gesamtzahl unbestimmter Balken
        if p.total:
            self.bar.setValue(p.current)
        self.label.setText(logic.progress_text(p))

    def stop(self) -> None:
        self.spinner.stop()
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
        self.summary = role_label("", "section")
        self.summary.setWordWrap(True)
        # gut sichtbar: Dateien, die wegen zu langer Pfade nicht geladen würden
        self.too_long = error_label()
        self.too_long.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Aktion", "Größe"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)

        self.legend = role_label("", "faint")
        self.legend.setWordWrap(True)
        self._update_legend()

        card = Card(margins=0, spacing=0)
        card.lay.addWidget(self.tree)

        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.only_changes)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.too_long)
        lay.addWidget(card, 1)
        lay.addWidget(self.legend)

    def _update_legend(self) -> None:
        self.legend.setText("&nbsp;&nbsp;&nbsp;".join(
            f'<span style="color:{action_color(k)}">●</span>&nbsp;{logic.ACTION_LABELS[k]}'
            for k in logic.LEGEND_KINDS
        ))

    def set_actions(self, actions: list[PlannedAction], root: Path | None) -> None:
        self.actions = actions
        self.root = root
        summary = logic.summarize(actions)
        self.summary.setText(summary.text() if actions else "Keine Änderungen, alles aktuell.")
        show_error(self.too_long, logic.too_long_text(summary.too_long))
        self._rebuild()

    def clear(self) -> None:
        self.actions = []
        self.tree.clear()
        self.summary.setText("")
        show_error(self.too_long, None)

    def _rebuild(self) -> None:
        self.tree.clear()
        self._update_legend()
        tree = logic.build_preview_tree(self.actions, self.root, self.only_changes.isChecked())
        self._add_children(self.tree.invisibleRootItem(), tree, depth=0)

    def _add_children(self, parent: QTreeWidgetItem, node: logic.TreeNode, depth: int) -> None:
        for child in node.sorted_children():
            item = QTreeWidgetItem(parent, [child.name, "", ""])
            if child.action is not None:
                kind = child.action.kind
                item.setText(1, logic.ACTION_LABELS[kind])
                item.setText(2, logic.format_size(child.action.size))
                item.setForeground(1, QBrush(QColor(action_color(kind))))
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
        self.button = button("Anmelden", "primary")
        self.button.setDefault(True)
        self.button.clicked.connect(self.submit)
        self.password.returnPressed.connect(self.submit)
        self.status = QLabel()
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(14)
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
        theme.set_role(self.status, "error" if error else ("success" if ok else "muted"))


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
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(14)
        lay.addWidget(role_label("Bei ILIAS anmelden", "hero"))
        hint = hint_label("Die Anmeldung ist abgelaufen oder das Passwort hat sich geändert.")
        lay.addWidget(hint)
        lay.addWidget(self.form)
        lay.addWidget(buttons)
        if user:
            self.form.password.setFocus()
