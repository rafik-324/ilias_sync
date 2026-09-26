"""Widget Tests mit pytest-qt gegen das Fake Backend."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox, QPushButton  # noqa: E402

from ilias_sync.gui.controller import Controller  # noqa: E402
from ilias_sync.gui.fake_backend import FakeApi  # noqa: E402
from ilias_sync.gui.icons import IconState, app_icon, tray_icon  # noqa: E402
from ilias_sync.gui.setup_wizard import SetupWizard  # noqa: E402
from ilias_sync.gui.tray import ZIEL_GEAENDERT, Tray  # noqa: E402
from ilias_sync.gui.window import MainWindow  # noqa: E402


def test_icons_fuer_alle_zustaende(qtbot):
    for state in IconState:
        assert not tray_icon(state, dark=False).isNull()
        assert not tray_icon(state, dark=True).isNull()
    assert not app_icon().isNull()


def test_sync_ueber_controller(qtbot, fake):
    c = Controller(fake)
    assert c.icon_state() == IconState.READY
    with qtbot.waitSignal(c.sync_finished, timeout=10_000) as blocker:
        assert c.start_sync()
        assert c.icon_state() == IconState.SYNCING
        assert not c.start_sync()   # nur ein Sync gleichzeitig
    result, automatic = blocker.args
    assert result.run.new > 0
    assert automatic is False
    assert not c.busy


def test_sync_fehler_setzt_fehlerzustand(qtbot, tmp_path):
    api = FakeApi(delay=0.0, workdir=tmp_path / "ilias_sync_fake", fail={"sync_errors"})
    c = Controller(api)
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        c.start_sync()
    assert c.icon_state() == IconState.ERROR


def test_offline_automatisch_ohne_fehlermeldung(qtbot, tmp_path):
    api = FakeApi(delay=0.0, workdir=tmp_path / "ilias_sync_fake", fail={"offline"})
    c = Controller(api)
    errors = []
    c.error.connect(lambda text, auto: errors.append(text))
    with qtbot.waitSignal(c.busy_changed, timeout=10_000, check_params_cb=lambda b: b is False):
        c.start_sync(automatic=True)
    assert c.offline
    assert errors == []
    assert "Offline" in c.status_text()


def test_config_fehler_wird_angezeigt(qtbot, tmp_path):
    api = FakeApi(delay=0.0, workdir=tmp_path / "ilias_sync_fake", fail={"config"})
    c = Controller(api)
    assert c.config_error and "max_parallel" in c.config_error
    assert c.icon_state() == IconState.ERROR


def test_abbrechen(qtbot, tmp_path):
    # Das Backend darf per Cancelled abbrechen oder ein Ergebnis mit cancelled=True liefern,
    # die GUI muss danach in jedem Fall wieder bereit sein
    api = FakeApi(delay=0.2, workdir=tmp_path / "ilias_sync_fake")
    c = Controller(api)
    with qtbot.waitSignal(c.busy_changed, timeout=10_000, check_params_cb=lambda b: b is False):
        c.start_sync()
        qtbot.wait(300)
        c.cancel()
    assert not c.busy
    assert c.icon_state() == IconState.READY


def test_abbrechen_waehrend_download(qtbot, tmp_path):
    api = FakeApi(delay=0.05, workdir=tmp_path / "ilias_sync_fake")
    c = Controller(api)
    c.progress.connect(lambda p: c.cancel() if p.phase == "Herunterladen" and p.current == 2 else None)
    with qtbot.waitSignal(c.sync_finished, timeout=10_000) as blocker:
        c.start_sync()
    assert blocker.args[0].run.cancelled
    assert blocker.args[0].run.new == 2


def test_hauptfenster_tabs(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.show_tab("courses")
    assert w.current_page() is w.courses
    assert w.courses.table.rowCount() == 6
    w.show_tab("log")
    assert w.current_page() is w.log
    # Übersicht zeigt die Beispieldaten
    assert w.overview.table.rowCount() == 5


def test_ungespeicherte_aenderungen_verhindern_tabwechsel(qtbot, fake, monkeypatch):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.show_tab("settings")
    w.settings.interval.setValue(120)
    assert w.settings.dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    w.select(0)
    assert w.current_page() is w.settings
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    w.select(0)
    assert w.current_page() is w.overview
    assert fake.load_config().sync_interval_minutes == 120


def test_fenster_schliessen_versteckt_nur(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.show()
    w.close()
    assert not w.isVisible()


def test_nicht_zugeordneter_kurs_nach_sync(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        c.start_sync()
    assert w.courses.table.rowCount() == 7
    assert "neu" in w.nav.item(1).text()


def test_wizard_login_seite(qtbot, fake_first_run):
    wiz = SetupWizard(fake_first_run)
    qtbot.addWidget(wiz)
    wiz.show()
    page = wiz.currentPage()
    assert not page.isComplete()
    page.form.user.setText("musterstudi")
    page.form.password.setText("falsch")
    page.form.submit()
    qtbot.waitUntil(lambda: "falsch" in page.form.status.text(), timeout=5000)
    assert not page.isComplete()
    assert page.form.password.text() == ""
    page.form.password.setText("geheim")
    page.form.submit()
    qtbot.waitUntil(page.isComplete, timeout=5000)


# ---------------- In ILIAS geändert ----------------

def _changed_buttons(w, row):
    cell = w.overview.changed_table.cellWidget(row, 3)
    return cell.findChildren(QPushButton)


def test_uebersicht_zeigt_geaenderte_dateien(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    ov = w.overview
    assert not ov.changed_card.isHidden()
    assert ov.changed_table.rowCount() == 2
    assert ov.changed_title.text() == "In ILIAS geändert (2)"
    assert ov.stat_changed.value.text() == "2"


def test_geaenderte_datei_herunterladen_und_ignorieren(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    ov = w.overview
    load, _skip = _changed_buttons(w, 0)
    with qtbot.waitSignal(c.files_changed, timeout=10_000):
        load.click()
    assert ov.changed_table.rowCount() == 1
    # neue Version liegt in der Liste der geladenen Dateien
    assert any("_ilias_" in f.name for f in fake.recent_files())

    _load, skip = _changed_buttons(w, 0)
    skip.click()
    assert fake.changed_items() == []
    assert ov.changed_card.isHidden()
    assert ov.stat_changed.value.text() == "0"


def test_alle_geaenderten_herunterladen(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    assert not w.overview.btn_all_changed.isHidden()
    with qtbot.waitSignal(c.files_changed, timeout=10_000):
        w.overview.btn_all_changed.click()
    assert fake.changed_items() == []
    assert w.overview.changed_card.isHidden()


def test_tray_meldet_geaenderte_dateien(qtbot, fake):
    c = Controller(fake)
    tray = Tray(c)
    assert tray.act_changed.isVisible()
    assert tray.act_changed.text() == "2 Dateien in ILIAS geändert"

    shown = []
    tray.notify = lambda text, target, error=False: shown.append((text, target))
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        c.start_sync()
    assert tray.act_changed.text() == "3 Dateien in ILIAS geändert"
    text, _target = shown[-1]
    assert "1 Datei in Mathematik Vorkurs wurde in ILIAS geändert" in text

    fake.dismiss_changed(fake.changed_items()[0].ref_id)
    c.changed_handled()
    assert tray.act_changed.text() == "2 Dateien in ILIAS geändert"
    for f in fake.changed_items():
        fake.dismiss_changed(f.ref_id)
    c.changed_handled()
    assert not tray.act_changed.isVisible()

    opened = []
    tray.open_window.connect(opened.append)
    tray.act_changed.trigger()
    assert opened == [ZIEL_GEAENDERT]


def test_fenster_oeffnet_bereich_geaendert(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.show_tab("log")
    w.show_tab(ZIEL_GEAENDERT)
    assert w.current_page() is w.overview
