"""Dialog zum Bearbeiten der Config Regeln eines Kurses."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ilias_sync.api import CourseRule
from ilias_sync.gui.icons import app_icon
from ilias_sync.gui.widgets import ComboBox, button, error_label, hint_label, show_error

TYP_ORDNER = "ILIAS Ordner"
TYP_DATEI = "Dateiname"


class RulesDialog(QDialog):
    def __init__(self, course_name: str, rules: list[CourseRule], targets: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Regeln: {course_name}")
        self.setWindowIcon(app_icon())
        self.resize(640, 380)
        self._targets = targets

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Gilt für", "Muster (Glob)", "Zielordner"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for rule in rules:
            self._add_row(rule)

        btn_add = button("Hinzufügen")
        btn_add.clicked.connect(lambda: self._add_row(CourseRule(target="", match_folder="")))
        btn_del = button("Entfernen", "ghost")
        btn_del.clicked.connect(self._remove_row)
        btn_up = button("Nach oben", "ghost")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = button("Nach unten", "ghost")
        btn_down.clicked.connect(lambda: self._move(1))
        side = QVBoxLayout()
        for b in (btn_add, btn_del, btn_up, btn_down):
            side.addWidget(b)
        side.addStretch(1)

        mid = QHBoxLayout()
        mid.addWidget(self.table, 1)
        mid.addLayout(side)

        self.error = error_label()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Regeln haben Vorrang vor der automatischen Kategorieerkennung. "
            "Die erste passende Regel gewinnt."
        ))
        lay.addWidget(hint_label(
            "Beispiele: Ordner \"Lecture Slides\" nach 01_Vorlesung, Datei \"*blatt*.pdf\" nach 03_Übungsblätter. "
            "* steht für beliebig viele Zeichen."
        ))
        lay.addLayout(mid, 1)
        lay.addWidget(self.error)
        lay.addWidget(buttons)

    def _add_row(self, rule: CourseRule) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        typ = ComboBox()
        typ.addItems([TYP_ORDNER, TYP_DATEI])
        typ.setCurrentText(TYP_DATEI if rule.match_file is not None else TYP_ORDNER)
        self.table.setCellWidget(row, 0, typ)
        self.table.setItem(row, 1, QTableWidgetItem(rule.match_file or rule.match_folder or ""))
        target = ComboBox()
        target.setEditable(True)
        target.addItems(self._targets)
        target.setCurrentText(rule.target)
        self.table.setCellWidget(row, 2, target)
        self.table.selectRow(row)

    def _row(self, row: int) -> tuple[str, str, str]:
        typ = self.table.cellWidget(row, 0).currentText()
        item = self.table.item(row, 1)
        pattern = item.text().strip() if item else ""
        target = self.table.cellWidget(row, 2).currentText().strip()
        return typ, pattern, target

    def _set_row(self, row: int, values: tuple[str, str, str]) -> None:
        self.table.cellWidget(row, 0).setCurrentText(values[0])
        self.table.item(row, 1).setText(values[1])
        self.table.cellWidget(row, 2).setCurrentText(values[2])

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        other = row + delta
        if row < 0 or not 0 <= other < self.table.rowCount():
            return
        a, b = self._row(row), self._row(other)
        self._set_row(row, b)
        self._set_row(other, a)
        self.table.selectRow(other)

    def rules(self) -> list[CourseRule]:
        result = []
        for row in range(self.table.rowCount()):
            typ, pattern, target = self._row(row)
            if typ == TYP_DATEI:
                result.append(CourseRule(target=target, match_file=pattern))
            else:
                result.append(CourseRule(target=target, match_folder=pattern))
        return result

    def _accept(self) -> None:
        for row in range(self.table.rowCount()):
            _, pattern, target = self._row(row)
            if not pattern or not target:
                show_error(self.error, f"Regel {row + 1}: Muster und Zielordner dürfen nicht leer sein.")
                self.table.selectRow(row)
                return
        self.accept()
