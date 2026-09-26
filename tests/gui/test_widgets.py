"""Widget Tests mit pytest-qt gegen das Fake Backend."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox, QPushButton  # noqa: E402

from ilias_sync.gui.controller import Controller  # noqa: E402
from ilias_sync.gui.fake_backend import FakeApi  # noqa: E402
from ilias_sync.gui.icons import IconState, app_icon, tray_icon  # noqa: E402
from ilias_sync.gui.setup_wizard import BaseDirPage, SetupWizard  # noqa: E402
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
    assert w.courses.table.rowCount() == 7
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
    assert w.courses.table.rowCount() == 8
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


def test_einstellungen_jetzt_synchronisieren(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.show_tab("settings")
    assert w.settings.btn_sync.isEnabled()
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        w.settings.btn_sync.click()
        assert not w.settings.btn_sync.isEnabled()   # während des Syncs gesperrt
    assert w.settings.btn_sync.isEnabled()


def test_einstellungen_sync_fragt_nach_ungespeicherten_aenderungen(qtbot, fake, monkeypatch):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.settings.interval.setValue(90)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    w.settings.btn_sync.click()
    assert not c.busy
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        w.settings.btn_sync.click()
    assert fake.load_config().sync_interval_minutes == 90


def test_semester_ordner_in_den_einstellungen(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    assert not hasattr(w.courses, "sem_table")
    t = w.settings.sem_table
    assert t.rowCount() == 1 and t.item(0, 0).text() == "HWS 2025"
    t.item(0, 1).setText("01_SEM")
    assert w.settings.dirty
    assert w.settings.save()
    assert fake.load_config().semesters == {"HWS 2025": "01_SEM"}


def test_kurse_speichern_ueberschreibt_semester_nicht(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    w.courses.cfg.courses[0].download_videos = False
    w.courses._set_dirty(True)
    cfg = fake.load_config()
    cfg.semesters = {"HWS 2025": "01_SEM"}
    c.save_config(cfg)          # z. B. in den Einstellungen gespeichert
    assert w.courses.save()
    saved = fake.load_config()
    assert saved.semesters == {"HWS 2025": "01_SEM"}
    assert saved.courses[0].download_videos is False


def test_wizard_basisordner_mit_inhalt_wird_abgelehnt(qtbot, fake_first_run, tmp_path):
    page = BaseDirPage(fake_first_run)
    qtbot.addWidget(page)
    voll = tmp_path / "voll"
    (voll / "01_Universität_Mannheim" / "01_SEMESTER").mkdir(parents=True)
    page.path.setText(str(voll))
    page._run_check()
    assert not page.isComplete()
    assert "Inhalt" in page.status.text()

    leer = tmp_path / "leer"
    (leer / "01_Universität_Mannheim").mkdir(parents=True)
    page.path.setText(str(leer))
    page._run_check()
    assert page.isComplete()


def test_wizard_schlaegt_keinen_vorhandenen_ordner_vor(qtbot, fake_first_run):
    page = BaseDirPage(fake_first_run)
    qtbot.addWidget(page)
    page.initializePage()
    assert page.path.text() == ""


# ---------------- Vorschau ----------------

def test_vorschau_zeigt_fortschritt_auch_ohne_knopf(qtbot, tmp_path):
    c = Controller(FakeApi(delay=0.02, workdir=tmp_path / "w"))
    w = MainWindow(c)
    qtbot.addWidget(w)
    with qtbot.waitSignal(c.dry_run_finished, timeout=10_000):
        assert c.start_dry_run()
        assert not w.preview.progress.isHidden()
        assert w.status_label.text() == "Vorschau läuft"
    assert w.preview.progress.isHidden()
    assert w.preview.tree.actions


def test_vorschau_login_noetig_wird_angezeigt(qtbot, tmp_path):
    from ilias_sync.api import InvalidCredentialsError

    class OhneLogin(FakeApi):
        def dry_run(self, progress, cancel):
            raise InvalidCredentialsError("Anmeldung bei ILIAS nötig.")

    c = Controller(OhneLogin(delay=0.0, workdir=tmp_path / "w"))
    w = MainWindow(c)
    qtbot.addWidget(w)
    with qtbot.waitSignal(c.login_required, timeout=10_000):
        w.preview.btn_preview.click()
    assert c.login_needed
    assert w.preview.error.text() == "Anmeldung bei ILIAS nötig."
    assert w.preview.progress.isHidden()


# ---------------- Mausrad, Spalten ----------------

def _wheel(widget):
    from PySide6.QtCore import QPoint, QPointF, Qt as _Qt
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(widget.rect().center())
    return QWheelEvent(pos, QPointF(widget.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, -120),
                       _Qt.MouseButton.NoButton, _Qt.KeyboardModifier.NoModifier, _Qt.ScrollPhase.NoScrollPhase, False)


def test_mausrad_aendert_auswahl_nicht(qtbot):
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QApplication
    from ilias_sync.gui.widgets import ComboBox, SpinBox
    combo = ComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["a", "b", "c"])
    spin = SpinBox()
    qtbot.addWidget(spin)
    spin.setRange(0, 10)
    spin.setValue(5)
    for w in (combo, spin):
        ev = _wheel(w)
        QApplication.sendEvent(w, ev)
        assert not ev.isAccepted()   # geht an den scrollbaren Bereich weiter
    assert combo.currentIndex() == 0
    assert spin.value() == 5
    assert combo.focusPolicy() == _Qt.FocusPolicy.StrongFocus


def test_kurse_ordner_dropdowns_ohne_mausrad(qtbot, fake):
    from PySide6.QtWidgets import QComboBox
    from ilias_sync.gui.widgets import ComboBox
    w = MainWindow(Controller(fake))
    qtbot.addWidget(w)
    combos = w.courses.table.findChildren(QComboBox)
    assert combos and all(isinstance(c, ComboBox) for c in combos)
    assert w.settings.interval.__class__.__name__ == "SpinBox"


def test_semester_spalte_nur_bei_mehreren_semestern(qtbot, fake):
    from ilias_sync.gui.tab_courses import COL_NAME, COL_SEM
    # ohne den Lizenzkurs gibt es nur HWS 2025: Spalte ausgeblendet, Semester im Tooltip
    cfg = fake.load_config()
    cfg.courses = [c for c in cfg.courses if c.ref_id != 1008]
    fake.save_config(cfg)
    w = MainWindow(Controller(fake))
    qtbot.addWidget(w)
    assert w.courses.table.isColumnHidden(COL_SEM)
    assert "Semester HWS 2025" in w.courses.table.item(0, COL_NAME).toolTip()


def test_kurs_ohne_semester_auf_seite_kurse(qtbot, fake):
    from PySide6.QtWidgets import QComboBox
    from ilias_sync.gui.tab_courses import COL_DIR, COL_NAME, COL_SEM, COL_TYPES
    w = MainWindow(Controller(fake))
    qtbot.addWidget(w)
    t = w.courses.table
    row = next(r for r in range(t.rowCount()) if "License" in t.item(r, COL_NAME).text())
    assert not t.isColumnHidden(COL_SEM)            # zwei "Semester": HWS 2025 und ohne
    assert t.item(row, COL_SEM).text() == "ohne Semester"
    assert "Ohne Semester" in t.item(row, COL_NAME).toolTip()
    combo = t.cellWidget(row, COL_DIR).findChild(QComboBox)
    assert combo.currentText() == "license_information_student_university_of_mannheim"
    # Schalter bedienbar: Videos aus, dann speichern
    btn = t.cellWidget(row, COL_TYPES).findChildren(QPushButton)[0]
    assert btn.course.ref_id == 1008
    btn._changed("download_videos", False)
    assert w.courses.btn_save.isEnabled()
    assert w.courses.save()
    assert not next(c for c in fake.load_config().courses if c.ref_id == 1008).download_videos


# ---------------- Abnahme: Wizard, Beenden, zweiter Start ----------------


def _ja_ausser_pfade_zu_lang(parent, title, *a, **k):
    """Rückfragen im Wizard bejahen (Ordner anlegen), die Warnung zu langen Pfaden übergehen."""
    return QMessageBox.StandardButton.No if title == "Pfade zu lang" else QMessageBox.StandardButton.Yes


def test_wizard_kompletter_durchlauf_mit_fehlerfaellen(qtbot, fake_first_run, monkeypatch):
    api = fake_first_run
    monkeypatch.setattr(QMessageBox, "question", _ja_ausser_pfade_zu_lang)
    wiz = SetupWizard(api)
    qtbot.addWidget(wiz)
    wiz.show()

    # 1. Login: erst falsches Passwort, dann richtig
    page = wiz.currentPage()
    page.form.user.setText("musterstudi")
    page.form.password.setText("falsch")
    page.form.submit()
    qtbot.waitUntil(lambda: "falsch" in page.form.status.text(), timeout=5000)
    page.form.password.setText("geheim")
    page.form.submit()
    qtbot.waitUntil(page.isComplete, timeout=5000)
    wiz.next()

    # 2. Basisordner: noch nicht vorhanden, wird nach Rückfrage angelegt
    page = wiz.currentPage()
    base = api.workdir / "Uni"
    page.path.setText(str(base))
    page._run_check()
    assert page.isComplete()
    wiz.next()
    assert (base / "01_Universität_Mannheim").is_dir()

    # 3. Kurse: zuerst offline, dann Erneut versuchen
    page = wiz.currentPage()
    api.fail.add("offline")
    page._load()
    qtbot.waitUntil(lambda: not page.retry.isHidden(), timeout=5000)
    assert "nicht erreichbar" in page.error.text()
    api.fail.discard("offline")
    page.retry.click()
    qtbot.waitUntil(page.isComplete, timeout=10_000)
    assert page.table.rowCount() == 8
    wiz.next()

    # 4. Vorschau
    page = wiz.currentPage()
    qtbot.waitUntil(page.isComplete, timeout=10_000)
    assert page.tree.actions
    wiz.next()

    # 5. Fertig: Config enthält Basisordner, Uni-ID und Kurszuordnungen
    wiz.accept()
    cfg = api.load_config()
    assert cfg.base_dir == base
    assert cfg.username == "musterstudi"
    assert {c.ref_id for c in cfg.courses} >= {1001, 1002, 1007}


def test_beenden_waehrend_sync_bricht_sauber_ab(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from ilias_sync.gui.app import GuiApp
    api = FakeApi(delay=0.05, workdir=tmp_path / "w")
    g = GuiApp(api)
    quit_called = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_called.append(True))
    asked = []

    def frage(*a, **k):
        asked.append(a[2] if len(a) > 2 else "")
        return QMessageBox.StandardButton.No if len(asked) == 1 else QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", frage)

    assert g.controller.start_sync()
    g.quit()                       # erste Rückfrage: Nein, App läuft weiter
    assert not quit_called and g.controller.busy
    import time
    t0 = time.monotonic()
    g.quit()                       # zweite Rückfrage: Ja
    assert time.monotonic() - t0 < 2, "Beenden hing am Warten auf den Worker Thread"
    assert quit_called
    assert not g.controller._task or not g.controller._task.is_running()
    assert "abgebrochen" in asked[0]
    g.tray.hide()


def test_zweiter_start_weckt_erste_instanz(qtbot, monkeypatch):
    import uuid
    from ilias_sync.gui import app as gui_app
    # eigener Name, damit eine echte laufende App nicht geweckt wird
    monkeypatch.setattr(gui_app, "_server_name", lambda: f"ilias-sync-test-{uuid.uuid4().hex}")
    woken = []
    first = gui_app.SingleInstance(lambda: woken.append(True))
    assert not first.already_running()
    first.listen()
    second = gui_app.SingleInstance(lambda: None)
    second.name = first.name
    assert second.already_running()
    qtbot.waitUntil(lambda: bool(woken), timeout=3000)
    first.server.close()


# ---------------- Logo und Icons ----------------

def test_logo_assets_vorhanden_und_ladbar(qtbot):
    from ilias_sync.gui import icons
    for name in icons.ASSET_FILES:
        assert icons.asset_path(name).is_file(), name
    assert not icons.app_icon().isNull() and not icons.app_icon(dark=True).isNull()
    assert not icons.brand_pixmap(30, dark=False).isNull()
    assert not icons.brand_pixmap(30, dark=True).isNull()
    for state in IconState:
        for frame in range(icons.SYNC_FRAMES):
            assert not icons.tray_icon(state, frame, dark=False).isNull()
    assert not icons.tray_icon(IconState.SYNCING, animate=False).isNull()
    # Animation kehrt nach einem Zyklus in die Ruhelage zurück
    assert icons.sync_pose(0) == (0.0, 1.0, 0.0)


def test_einzelinstanz_und_settings_je_testumgebung(monkeypatch, tmp_path):
    from ilias_sync.gui import app as gui_app
    monkeypatch.delenv("ILIAS_SYNC_HOME", raising=False)
    normal = gui_app._server_name()
    assert gui_app.instance_suffix() == ""
    assert normal.startswith("ilias-sync-") and normal.count("-") >= 2

    monkeypatch.setenv("ILIAS_SYNC_HOME", str(tmp_path / "a"))
    a = gui_app._server_name()
    assert a != normal and a.startswith(normal + "-")
    assert gui_app._server_name() == a            # stabil
    monkeypatch.setenv("ILIAS_SYNC_HOME", str(tmp_path / "b"))
    assert gui_app._server_name() not in (a, normal)
    assert len(gui_app.instance_suffix()) == 8



# ---------------- Pfad zu lang ----------------

def test_vorschau_warnt_bei_zu_langen_pfaden(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    with qtbot.waitSignal(c.dry_run_finished, timeout=10_000):
        w.preview.btn_preview.click()
    tree = w.preview.tree
    assert not tree.too_long.isHidden()
    assert tree.too_long.text().startswith("1 Datei nicht ablegbar (Pfad zu lang).")
    assert "kürzerer Basisordner" in tree.too_long.text()
    assert "1 nicht ablegbar (Pfad zu lang)" in tree.summary.text()
    assert "Pfad zu lang" in tree.legend.text()


def test_sync_zaehlt_zu_lange_pfade_als_fehler(qtbot, fake):
    import threading
    result = fake.sync(lambda p: None, threading.Event())
    too_long = [e for e in result.errors if e.startswith("Pfad zu lang (")]
    assert len(too_long) == 1 and result.run.errors == len(result.errors)


# ---------------- Wizard Fenstergröße ----------------

def _wizard_bis_ende(qtbot, wiz, api, sizes):
    page = wiz.currentPage()
    sizes.append(wiz.size())
    page.form.user.setText("musterstudi")
    page.form.password.setText("geheim")
    page.form.submit()
    qtbot.waitUntil(page.isComplete, timeout=5000)
    wiz.next()
    page = wiz.currentPage()
    page.path.setText(str(api.workdir / ("sehr_langer_ordnername_" * 8) / "Uni"))
    page._run_check()
    qtbot.wait(20)
    sizes.append(wiz.size())
    wiz.next()
    for _ in range(2):          # Kurse, Vorschau
        page = wiz.currentPage()
        qtbot.waitUntil(page.isComplete, timeout=10_000)
        qtbot.wait(20)
        sizes.append(wiz.size())
        wiz.next()
    qtbot.wait(20)
    sizes.append(wiz.size())    # Fertig eingerichtet
    wiz.back()
    qtbot.wait(20)
    sizes.append(wiz.size())


def test_wizard_groesse_bleibt_auf_allen_seiten_gleich(qtbot, fake_first_run, monkeypatch):
    from ilias_sync.gui.setup_wizard import START_SIZE
    monkeypatch.setattr(QMessageBox, "question", _ja_ausser_pfade_zu_lang)
    wiz = SetupWizard(fake_first_run)
    qtbot.addWidget(wiz)
    wiz.show()
    sizes = []
    _wizard_bis_ende(qtbot, wiz, fake_first_run, sizes)
    assert len(sizes) == 6
    assert all(s == START_SIZE for s in sizes), sizes


def test_wizard_mindestgroesse_verhindert_wachsen_ohne_umbruch(qtbot, fake_first_run, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from ilias_sync.gui import setup_wizard
    monkeypatch.setattr(QMessageBox, "question", _ja_ausser_pfade_zu_lang)
    # späterer Fehler simuliert: langes Label ohne Umbruch auf der letzten Seite, Umbruch Schutz aus.
    # Die explizite Mindestgröße muss das Fenster trotzdem bei der Startgröße halten
    orig_init = setup_wizard.FinishPage.__init__

    def init(self):
        orig_init(self)
        lang = QLabel("sehr langer Text ohne Umbruch " * 20)
        self.layout().insertWidget(0, lang)
    monkeypatch.setattr(setup_wizard.FinishPage, "__init__", init)
    monkeypatch.setattr(setup_wizard.SetupWizard, "_wrap_labels", lambda self: None)
    wiz = SetupWizard(fake_first_run)
    qtbot.addWidget(wiz)
    wiz.show()
    sizes = []
    _wizard_bis_ende(qtbot, wiz, fake_first_run, sizes)
    assert sizes[-2] == setup_wizard.START_SIZE, sizes



# ---------------- Fehler Details, zu lange Pfade im Wizard, Cloud Hinweis ----------------

def test_fehler_zusammenfassung_und_details(qtbot, tmp_path, monkeypatch):
    from ilias_sync.gui.widgets import ErrorListDialog
    c = Controller(FakeApi(delay=0.0, workdir=tmp_path / "w", fail={"sync_errors"}))
    w = MainWindow(c)
    qtbot.addWidget(w)
    with qtbot.waitSignal(c.sync_finished, timeout=10_000):
        c.start_sync()
    # ein Verbindungsfehler (sync_errors) und eine Datei mit zu langem Pfad
    assert c.last_error == "2 Dateien nicht geladen: 1 Pfad zu lang, 1 Verbindungsfehler"
    assert ";" not in w.overview.error.text()
    assert not w.overview.btn_error_details.isHidden()
    shown = []
    monkeypatch.setattr(ErrorListDialog, "exec", lambda self: shown.append(self.text.toPlainText()))
    w.overview.btn_error_details.click()
    assert shown and len(shown[0].splitlines()) == 2
    c.clear_error()
    assert c.last_errors == [] and w.overview.btn_error_details.isHidden()


def test_wizard_warnt_vor_zu_langen_pfaden_und_geht_zurueck(qtbot, fake_first_run, monkeypatch):
    from ilias_sync.gui.setup_wizard import BaseDirPage, PreviewPage
    antworten = []

    def frage(parent, title, *a, **k):
        antworten.append(title)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", frage)
    wiz = SetupWizard(fake_first_run)
    qtbot.addWidget(wiz)
    wiz.show()
    page = wiz.currentPage()
    page.form.user.setText("musterstudi")
    page.form.password.setText("geheim")
    page.form.submit()
    qtbot.waitUntil(page.isComplete, timeout=5000)
    wiz.next()
    wiz.currentPage().path.setText(str(fake_first_run.workdir / "Uni"))
    wiz.currentPage()._run_check()
    wiz.next()
    qtbot.waitUntil(wiz.currentPage().isComplete, timeout=10_000)
    wiz.next()
    page = wiz.currentPage()
    assert isinstance(page, PreviewPage)
    qtbot.waitUntil(page.isComplete, timeout=10_000)
    assert page.too_long == 1 and not page.btn_shorter.isHidden()
    wiz.next()                  # Rückfrage mit Ja: zurück zum Basisordner
    assert "Pfade zu lang" in antworten
    qtbot.waitUntil(lambda: isinstance(wiz.currentPage(), BaseDirPage), timeout=3000)


@pytest.mark.parametrize("path, cloud", [
    ("/Users/x/Library/CloudStorage/OneDrive-Persönlich/Uni", True),
    ("/Users/x/Library/Mobile Documents/com~apple~CloudDocs/Uni", True),
    ("/Users/x/Library/CloudStorage/GoogleDrive-a@b.de/Meine Ablage", True),
    ("C:\\Users\\x\\OneDrive\\Uni", True),
    ("C:\\Users\\x\\Dropbox\\Uni", True),
    ("/Users/x/Documents/Uni", False),
])
def test_basisordner_cloud_hinweis(path, cloud):
    from ilias_sync.gui import logic
    hints = logic.base_dir_hints(path)
    assert any("Cloud" in h for h in hints) == cloud


def test_basisordner_langer_pfad_hinweis():
    from ilias_sync.gui import logic
    assert logic.base_dir_hints("/Users/x/Uni") == []
    lang = "/Users/x/" + "a" * 90
    assert any("lang" in h for h in logic.base_dir_hints(lang))
