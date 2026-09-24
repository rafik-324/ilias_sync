import threading
from pathlib import Path

import pytest

from ilias_sync.api import (
    ActionKind,
    Cancelled,
    ConfigError,
    InvalidCredentialsError,
    NetworkError,
    SessionState,
)
from ilias_sync.gui.fake_backend import FakeApi


def _noop(_p):
    pass


def test_dry_run_schreibt_nichts(fake):
    before = sorted(p for p in fake.workdir.rglob("*") if p.is_file())
    actions = fake.dry_run(_noop, threading.Event())
    after = sorted(p for p in fake.workdir.rglob("*") if p.is_file())
    assert actions
    # nur die Logdatei darf sich ändern, keine Downloads
    assert [p for p in after if p.suffix != ".log"] == [p for p in before if p.suffix != ".log"]
    kinds = {a.kind for a in actions}
    assert {ActionKind.NEW, ActionKind.UPDATE, ActionKind.CONFLICT, ActionKind.UNASSIGNED} <= kinds


def test_sync_und_zweiter_sync(fake):
    result = fake.sync(_noop, threading.Event())
    assert result.run.new > 0
    assert result.new_unassigned_courses
    for f in result.new_files:
        assert f.local_path.is_file()
        assert fake.workdir in f.local_path.parents
    second = fake.sync(_noop, threading.Event())
    assert second.run.new == 1   # pro weiterem Sync eine neue Folie


def test_sync_abbrechen(fake):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        fake.sync(_noop, cancel)


def test_fehlerfaelle(tmp_path):
    api = FakeApi(delay=0.0, workdir=tmp_path / "ilias_sync_fake", fail={"offline", "config"})
    with pytest.raises(NetworkError):
        api.fetch_courses(_noop, threading.Event())
    assert api.check_session() == SessionState.OFFLINE
    with pytest.raises(ConfigError):
        api.load_config()


def test_falsches_passwort_leert_keyring(fake_first_run):
    with pytest.raises(InvalidCredentialsError):
        fake_first_run.login("musterstudi", "falsch")
    assert not fake_first_run.has_stored_password()
    fake_first_run.login("musterstudi", "geheim")
    assert fake_first_run.stored_username() == "musterstudi"


def test_schreibt_nie_ausserhalb_des_temp_verzeichnisses(fake_first_run, tmp_path):
    fremd = tmp_path / "echter_ordner"
    fremd.mkdir()
    fake_first_run.create_base_structure(fremd)
    fake_first_run.login("musterstudi", "geheim")
    fake_first_run.sync(_noop, threading.Event())
    assert list(fremd.iterdir()) == []


def test_redownload(fake):
    removed = fake.removed_items()
    assert len(removed) == 1
    entry = fake.redownload(removed[0].ref_id)
    assert entry.local_path.is_file()
    assert fake.removed_items() == []


def test_mapping_vorschlaege(fake):
    courses = fake.fetch_courses(_noop, threading.Event())
    suggestions = fake.suggest_mapping(courses)
    by_name = {s.course.name: s for s in suggestions}
    assert by_name["Marketing (HWS 2025)"].local_dir == "01_marketing"
    assert by_name["Marketing (HWS 2025)"].exists
    assert not by_name["Statistik I (FSS 2026)"].exists


def test_local_course_dirs(fake):
    dirs = fake.local_course_dirs("01_SEMESTER")
    assert "01_marketing" in dirs
    assert isinstance(dirs[0], str)
    assert fake.local_course_dirs("99_SEMESTER") == []


def test_workdir_ist_temp(fake):
    assert isinstance(fake.workdir, Path)
