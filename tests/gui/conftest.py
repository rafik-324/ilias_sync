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
