import os

# Widget Tests ohne sichtbare Fenster
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from ilias_sync.gui.fake_backend import FakeApi  # noqa: E402


@pytest.fixture
def fake(tmp_path):
    return FakeApi(delay=0.0, workdir=tmp_path / "ilias_sync_fake")


@pytest.fixture
def fake_first_run(tmp_path):
    return FakeApi(first_run=True, delay=0.0, workdir=tmp_path / "ilias_sync_fake")


@pytest.fixture(autouse=True)
def _qsettings_im_tmp(tmp_path):
    """QSettings (Fenstergeometrie, Update "Später") in ein Temp Verzeichnis lenken,
    damit Tests nie die Einstellungen der echten App lesen oder schreiben."""
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "qsettings"))
    yield


@pytest.fixture(autouse=True)
def _kein_harter_exit(monkeypatch):
    """Der Beenden Watchdog (gui.app._hard_exit, os._exit nach 15 s) würde sonst mitten im
    Testlauf pytest selbst beenden, still mit Exit Code 0 (unter Windows gesehen)."""
    import ilias_sync.gui.app as gui_app

    monkeypatch.setattr(gui_app, "_hard_exit", lambda: None)
