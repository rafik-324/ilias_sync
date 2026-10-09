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
