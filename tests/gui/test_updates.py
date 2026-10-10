"""App Updates in der GUI (GUI_KONTEXT.md 3.5) gegen das Fake Backend."""

from datetime import datetime, timedelta

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ilias_sync import __version__  # noqa: E402
from ilias_sync.gui import controller as controller_mod  # noqa: E402
from ilias_sync.gui import logic  # noqa: E402
from ilias_sync.gui.controller import Controller  # noqa: E402
from ilias_sync.gui.fake_backend import FAKE_UPDATE_VERSION, FakeApi  # noqa: E402
from ilias_sync.gui.tray import ZIEL_UPDATE, Tray  # noqa: E402
from ilias_sync.gui.update_dialog import HINT_MANUAL, HINT_SYNC, UpdateDialog  # noqa: E402
from ilias_sync.gui.window import MainWindow  # noqa: E402

NOW = datetime(2026, 10, 10, 9, 0)


def _api(tmp_path, *fail, delay=0.0):
    return FakeApi(delay=delay, workdir=tmp_path / "w", fail=set(fail))


def _info(fake):
    return fake.check_for_update()


# ---------------- Logik ----------------

def test_update_prompt_regel():
    assert logic.update_prompt_due("0.2.0", None, None, NOW)
    assert not logic.update_prompt_due("0.2.0", "0.2.0", NOW - timedelta(hours=23), NOW)
    assert logic.update_prompt_due("0.2.0", "0.2.0", NOW - timedelta(hours=24), NOW)
    assert logic.update_prompt_due("0.3.0", "0.2.0", NOW - timedelta(minutes=1), NOW)   # neuere Version sofort
    assert logic.parse_iso("kaputt") is None and logic.parse_iso(None) is None
    assert logic.parse_iso(NOW.isoformat()) == NOW


def test_update_texte():
    assert logic.update_dialog_text("0.2.0", "0.1.0") == "ILIAS Sync 0.2.0 ist da (du hast 0.1.0)."
    assert logic.update_menu_text("0.2.0") == "Update auf 0.2.0 installieren …"
    assert logic.update_notify_text("0.2.0") == "Update auf 0.2.0 verfügbar"
    assert logic.up_to_date_text("0.1.0") == "ILIAS Sync ist aktuell (0.1.0)"


# ---------------- Fake Backend ----------------

def test_fake_backend_updates(tmp_path):
    import threading
    fake = _api(tmp_path)
    assert fake.app_version() == __version__
    assert fake.can_self_update()
    assert fake.check_for_update() is None
    fake.fail.add("update")
    info = fake.check_for_update()
    assert info.version == FAKE_UPDATE_VERSION
    steps = []
    path = fake.download_update(info, steps.append, threading.Event())
    assert path.is_file() and steps[-1].current == steps[-1].total
    fake.install_update(path)
    assert fake.installed_update == path

    cancel = threading.Event()
    cancel.set()
    from ilias_sync.api import Cancelled, IliasSyncError, NetworkError
    with pytest.raises(Cancelled):
        fake.download_update(info, lambda p: None, cancel)
    fake.fail.add("update_checksum")
    with pytest.raises(IliasSyncError):
        fake.download_update(info, lambda p: None, threading.Event())
    fake.fail.add("update_offline")
    with pytest.raises(NetworkError):
        fake.check_for_update()
    assert not _api(tmp_path, "no_self_update").can_self_update()


# ---------------- Controller ----------------

def test_automatische_pruefung_zeigt_popup_hoechstens_einmal_pro_tag(qtbot, tmp_path):
    c = Controller(_api(tmp_path, "update"))
    c.now = lambda: NOW
    prompts = []
    c.update_prompt.connect(prompts.append)
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        assert c.check_for_updates()
    assert c.update_info.version == FAKE_UPDATE_VERSION
    assert len(prompts) == 1

    # gleich noch einmal: Version wird im Tray angeboten, aber kein zweites Popup
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        c.check_for_updates()
    assert len(prompts) == 1

    # "Später" und 23 Stunden danach: weiterhin nicht, nach 24 Stunden wieder
    c.snooze_update(FAKE_UPDATE_VERSION)
    c.now = lambda: NOW + timedelta(hours=23)
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        c.check_for_updates()
    assert len(prompts) == 1
    c.now = lambda: NOW + timedelta(hours=24)
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        c.check_for_updates()
    assert len(prompts) == 2

    # "Später" überlebt einen Neustart (QSettings)
    c2 = Controller(_api(tmp_path, "update"))
    c2.now = lambda: NOW + timedelta(hours=25)
    c2.update_prompt.connect(prompts.append)
    with qtbot.waitSignal(c2.update_changed, timeout=5000):
        c2.check_for_updates()
    assert len(prompts) == 2


def test_check_updates_aus_prueft_nicht_automatisch(qtbot, tmp_path):
    fake = _api(tmp_path, "update")
    cfg = fake.load_config()
    cfg.check_updates = False
    fake.save_config(cfg)
    c = Controller(fake)
    assert not c.check_for_updates()
    assert not c.checking_update
    # manuell geht trotzdem und zeigt immer ein Ergebnis
    with qtbot.waitSignal(c.update_check_result, timeout=5000) as blocker:
        assert c.check_for_updates(manual=True)
    info, error = blocker.args
    assert info.version == FAKE_UPDATE_VERSION and error == ""


def test_timer_startet_pruefung_und_danach_taeglich(qtbot, tmp_path):
    c = Controller(_api(tmp_path))
    calls = []
    c.check_for_updates = lambda manual=False: calls.append(manual)
    c.start_update_checks(first_delay_ms=10)
    assert c._update_timer.remainingTime() <= 10
    qtbot.waitUntil(lambda: calls == [False], timeout=2000)
    assert c._update_timer.isActive()
    assert c._update_timer.interval() == controller_mod.UPDATE_INTERVAL_MS
    assert controller_mod.UPDATE_FIRST_DELAY_MS == 3_000
    c.shutdown()
    assert not c._update_timer.isActive()


def test_pruefungsfehler_nur_im_log(qtbot, tmp_path, caplog):
    c = Controller(_api(tmp_path, "update", "update_offline"))
    errors, prompts = [], []
    c.error.connect(lambda *a: errors.append(a))
    c.update_prompt.connect(prompts.append)
    c.check_for_updates()
    qtbot.waitUntil(lambda: not c.checking_update, timeout=5000)
    assert errors == [] and prompts == [] and c.update_info is None
    assert "Update Prüfung fehlgeschlagen" in caplog.text

    with qtbot.waitSignal(c.update_check_result, timeout=5000) as blocker:
        c.check_for_updates(manual=True)
    assert blocker.args[0] is None and "nicht erreichbar" in blocker.args[1]


def test_manuell_aktuell(qtbot, tmp_path):
    c = Controller(_api(tmp_path))
    with qtbot.waitSignal(c.update_check_result, timeout=5000) as blocker:
        c.check_for_updates(manual=True)
    assert blocker.args == [None, ""]


# ---------------- Dialog ----------------

def _dialog(qtbot, fake):
    c = Controller(fake)
    dlg = UpdateDialog(c, _info(fake))
    qtbot.addWidget(dlg)
    dlg.show()
    return c, dlg


def test_dialog_verfuegbar(qtbot, tmp_path):
    fake = _api(tmp_path, "update")
    c, dlg = _dialog(qtbot, fake)
    assert dlg.windowTitle() == "Neues Update verfügbar"
    assert not dlg.isModal()
    assert dlg.text.text() == f"ILIAS Sync {FAKE_UPDATE_VERSION} ist da (du hast {__version__})."
    assert "Updates direkt aus der App" in dlg.notes.text()
    assert dlg.btn_install.isVisible() and dlg.btn_install.isEnabled()
    assert dlg.btn_later.isEnabled()


def test_dialog_spaeter_merkt_version(qtbot, tmp_path):
    fake = _api(tmp_path, "update")
    c, dlg = _dialog(qtbot, fake)
    c.now = lambda: NOW
    dlg.btn_later.click()
    assert not dlg.isVisible()
    assert not c._prompt_due(FAKE_UPDATE_VERSION)


def test_dialog_waehrend_sync(qtbot, tmp_path):
    fake = _api(tmp_path, "update", delay=0.02)
    c, dlg = _dialog(qtbot, fake)
    with qtbot.waitSignal(c.sync_finished, timeout=20_000):
        assert c.start_sync()
        assert not dlg.btn_install.isEnabled()
        assert dlg.hint.text() == HINT_SYNC and dlg.hint.isVisible()
    assert dlg.btn_install.isEnabled()      # danach automatisch wieder aktiv
    assert not dlg.hint.isVisible()


def test_dialog_ohne_self_update(qtbot, tmp_path):
    fake = _api(tmp_path, "update", "no_self_update")
    c, dlg = _dialog(qtbot, fake)
    assert not dlg.btn_install.isVisible()
    assert dlg.hint.text() == HINT_MANUAL


def test_dialog_erfolg_installiert_und_beendet(qtbot, tmp_path):
    fake = _api(tmp_path, "update")
    c, dlg = _dialog(qtbot, fake)
    quits = []
    c.quit_requested.connect(lambda: quits.append(True))
    with qtbot.waitSignal(c.quit_requested, timeout=10_000):
        dlg.btn_install.click()
        assert c.updating
        assert dlg.progress.isVisible() and not dlg.btn_install.isEnabled()
    assert fake.installed_update is not None and fake.installed_update.is_file()
    assert quits == [True]


@pytest.mark.parametrize("fehler, text", [
    ("update_offline", "nicht erreichbar"),
    ("update_checksum", "Prüfsumme"),
    ("install_error", "nicht gestartet"),
])
def test_dialog_fehler_und_erneut_versuchen(qtbot, tmp_path, fehler, text):
    fake = _api(tmp_path, "update")
    c, dlg = _dialog(qtbot, fake)
    fake.fail.add(fehler)
    dlg.btn_install.click()
    qtbot.waitUntil(lambda: dlg.error.isVisible(), timeout=10_000)
    assert text in dlg.error.text()
    assert fake.installed_update is None
    assert dlg.btn_install.text() == "Erneut versuchen" and dlg.btn_install.isEnabled()
    assert not c.updating
    # erneut versuchen klappt, sobald der Fehler weg ist
    fake.fail.discard(fehler)
    with qtbot.waitSignal(c.quit_requested, timeout=10_000):
        dlg.btn_install.click()
    assert not dlg.error.isVisible()


def test_dialog_abbrechen(qtbot, tmp_path):
    fake = _api(tmp_path, "update", delay=0.05)
    c, dlg = _dialog(qtbot, fake)
    dlg.btn_install.click()
    assert c.updating
    with qtbot.waitSignal(c.update_download_failed, timeout=10_000) as blocker:
        dlg.progress.cancel_btn.click()
    assert blocker.args[1] is True
    assert not c.updating and fake.installed_update is None
    assert not dlg.progress.isVisible()
    assert dlg.btn_install.isEnabled() and not dlg.error.isVisible()


def test_sync_waehrend_download_installiert_danach(qtbot, tmp_path):
    fake = _api(tmp_path, "update", delay=0.02)
    c, dlg = _dialog(qtbot, fake)
    # automatischer Sync startet während des Downloads nicht
    dlg.btn_install.click()
    assert not c.start_sync(automatic=True)
    qtbot.waitUntil(lambda: not c.updating, timeout=10_000)
    # Download fertig, während ein manueller Sync läuft: Installation erst danach
    fake.installed_update = None
    c2, dlg2 = _dialog(qtbot, fake)
    dlg2.btn_install.click()
    assert c2.start_sync()
    qtbot.waitUntil(lambda: dlg2._installer is not None, timeout=10_000)
    assert c2.syncing and fake.installed_update is None
    assert "nach dem laufenden Sync" in dlg2.hint.text()
    with qtbot.waitSignal(c2.quit_requested, timeout=30_000):
        pass
    assert not c2.busy and fake.installed_update is not None


# ---------------- Tray ----------------

def test_tray_eintrag_und_benachrichtigung(qtbot, tmp_path):
    fake = _api(tmp_path, "update")
    c = Controller(fake)
    c.now = lambda: NOW
    tray = Tray(c)
    assert not tray.act_update.isVisible()
    assert tray.menu.actions()[0] is tray.act_update
    assert tray.menu.actions()[1].isSeparator()

    shown = []
    tray.notify = lambda text, target, error=False: shown.append((text, target))
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        c.check_for_updates()
    assert tray.act_update.isVisible() and tray.sep_update.isVisible()
    assert tray.act_update.text() == f"Update auf {FAKE_UPDATE_VERSION} installieren …"
    assert shown == [(f"Update auf {FAKE_UPDATE_VERSION} verfügbar", ZIEL_UPDATE)]

    # Klick auf Eintrag und auf Benachrichtigung öffnen den Dialog
    opened = []
    tray.open_update.connect(lambda: opened.append(True))
    tray.act_update.trigger()
    tray._notify_target = ZIEL_UPDATE
    tray._on_message_clicked()
    assert opened == [True, True]

    # keine zweite Benachrichtigung für dieselbe Version
    c.update_prompt.emit(c.update_info)
    assert len(shown) == 1

    # Update weg (z. B. inzwischen installiert): Eintrag verschwindet
    fake.fail.discard("update")
    with qtbot.waitSignal(c.update_changed, timeout=5000):
        c.check_for_updates(manual=True)
    assert not tray.act_update.isVisible() and not tray.sep_update.isVisible()
    tray.hide()


# ---------------- Einstellungen ----------------

def test_einstellungen_speichern_check_updates(qtbot, fake):
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    s = w.settings
    assert s.check_updates.isChecked()
    assert s.version.text() == f"Version {__version__}"
    s.check_updates.setChecked(False)
    assert s.dirty
    assert s.save()
    assert fake.load_config().check_updates is False
    s.check_updates.setChecked(True)
    assert s.save()
    assert fake.load_config().check_updates is True


def test_einstellungen_jetzt_nach_updates_suchen(qtbot, tmp_path):
    fake = _api(tmp_path)
    c = Controller(fake)
    w = MainWindow(c)
    qtbot.addWidget(w)
    s = w.settings
    with qtbot.waitSignal(c.update_check_result, timeout=5000):
        s.btn_check_update.click()
        assert not s.btn_check_update.isEnabled()
    assert s.update_status.text() == f"ILIAS Sync ist aktuell ({__version__})"
    assert s.btn_check_update.isEnabled()

    fake.fail.add("update_offline")
    with qtbot.waitSignal(c.update_check_result, timeout=5000):
        s.btn_check_update.click()
    assert "fehlgeschlagen" in s.update_status.text()


# ---------------- App ----------------

def test_app_oeffnet_dialog_und_beendet_nach_installation(qtbot, tmp_path, monkeypatch):
    from ilias_sync.gui.app import GuiApp
    fake = _api(tmp_path, "update")
    g = GuiApp(fake)
    quit_called = []
    monkeypatch.setattr(QApplication, "quit", lambda: quit_called.append(True))
    # manuelle Prüfung öffnet den Dialog
    with qtbot.waitSignal(g.controller.update_check_result, timeout=5000):
        g.controller.check_for_updates(manual=True)
    dlg = g.update_dialog
    assert dlg is not None and dlg.isVisible()
    # Tray Eintrag öffnet denselben Dialog
    g.tray.act_update.trigger()
    assert g.update_dialog is dlg
    with qtbot.waitSignal(g.controller.quit_requested, timeout=10_000):
        dlg.btn_install.click()
    assert quit_called and fake.installed_update is not None
    g.tray.hide()
    dlg.close()
