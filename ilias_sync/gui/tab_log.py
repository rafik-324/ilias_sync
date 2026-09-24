"""Tab Log: letzte Meldungen aus der Logdatei (nur lesend)."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication, QTextCursor
from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QWidget

from ilias_sync.gui.controller import Controller
from ilias_sync.gui.tray import open_path
from ilias_sync.gui.widgets import Card, Page, button

MAX_LINES = 500

FILTER_ALLE = "Alle"
FILTER_WARNUNGEN = "Warnungen und Fehler"
FILTER_FEHLER = "Nur Fehler"


def read_tail(path: Path, max_lines: int = MAX_LINES) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return list(deque(fh, maxlen=max_lines))
    except OSError:
        return []


def filter_lines(lines: list[str], mode: str) -> list[str]:
    if mode == FILTER_FEHLER:
        keys = ("ERROR", "CRITICAL")
    elif mode == FILTER_WARNUNGEN:
        keys = ("WARNING", "ERROR", "CRITICAL")
    else:
        return lines
    return [line for line in lines if any(k in line for k in keys)]


class LogTab(Page):
    def __init__(self, controller: Controller, parent: QWidget | None = None) -> None:
        super().__init__("Log", "Letzte Meldungen. Passwörter und Cookies werden nie protokolliert.", parent)
        self.c = controller
        self._mtime: float | None = None
        self._lines: list[str] = []

        self.filter = QComboBox()
        self.filter.addItems([FILTER_ALLE, FILTER_WARNUNGEN, FILTER_FEHLER])
        self.filter.currentTextChanged.connect(lambda _t: self._render())
        btn_copy = button("Kopieren", "ghost")
        btn_copy.clicked.connect(self._copy)
        btn_folder = button("Log Ordner öffnen")
        btn_folder.clicked.connect(lambda: open_path(self.c.api.log_dir()))

        self.filter.setMinimumWidth(190)
        self.actions.addWidget(self.filter)
        self.actions.addWidget(btn_copy)
        self.actions.addWidget(btn_folder)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlaceholderText("Noch keine Meldungen.")
        self.text.setObjectName("logView")
        self.text.setFrameShape(QPlainTextEdit.Shape.NoFrame)

        card = Card(margins=4, spacing=0)
        card.lay.addWidget(self.text)
        self.body.addWidget(card, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.reload)

    def showEvent(self, event) -> None:  # noqa: N802
        self.reload(force=True)
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def reload(self, force: bool = False) -> None:
        path = self.c.api.log_file()
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        self._lines = read_tail(path)
        self._render()

    def _render(self) -> None:
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.text.setPlainText("".join(filter_lines(self._lines, self.filter.currentText())))
        if at_end:
            self.text.moveCursor(QTextCursor.MoveOperation.End)
            bar.setValue(bar.maximum())

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())
