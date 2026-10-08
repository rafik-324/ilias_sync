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


def test_dock_icon_nur_bei_offenem_fenster(qtbot, monkeypatch):
    from PySide6.QtWidgets import QWidget

    from ilias_sync.gui import app as gui_app

    calls = []
    monkeypatch.setattr(gui_app, "show_dock_icon", lambda: calls.append("an"))
    monkeypatch.setattr(gui_app, "hide_dock_icon", lambda: calls.append("aus"))
    monkeypatch.setattr(gui_app, "activate_app", lambda: None)
    dock = gui_app.DockIcon()
    fenster, wizard = QWidget(), QWidget()
    qtbot.addWidget(fenster)
    qtbot.addWidget(wizard)
    dock.watch(fenster)
    dock.watch(wizard)
    fenster.show()
    wizard.show()
    assert calls == ["an"]
    fenster.hide()
    qtbot.wait(10)
    assert calls == ["an"]          # Wizard ist noch offen
    wizard.hide()
    qtbot.waitUntil(lambda: calls == ["an", "aus"])
