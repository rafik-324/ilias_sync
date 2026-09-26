"""macOS Hilfen und Mac spezifische Anpassungen."""

from PySide6.QtWidgets import QComboBox

from ilias_sync.gui import macos
from ilias_sync.gui.tab_courses import DIR_COL_MIN, _combo_width


def test_macos_hilfen_werfen_nie(qapp):
    # auf macOS echte Aufrufe, anderswo ohne Wirkung. In keinem Fall ein Fehler
    macos.hide_dock_icon()
    macos.activate_app()


def test_ordnerspalte_passt_zum_laengsten_namen(qapp):
    combo = QComboBox()
    combo.addItem("01_kurz")
    short = _combo_width(combo)
    combo.addItem("03_programmier_praktikum_mit_sehr_langem_namen")
    assert _combo_width(combo) > short
    assert _combo_width(combo) > combo.fontMetrics().horizontalAdvance(combo.itemText(1))
    assert DIR_COL_MIN >= 200
