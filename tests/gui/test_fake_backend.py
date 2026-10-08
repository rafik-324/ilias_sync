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
    assert {ActionKind.NEW, ActionKind.CHANGED, ActionKind.CONFLICT, ActionKind.UNASSIGNED} <= kinds
    # UPDATE erzeugt das Backend nicht mehr
    assert ActionKind.UPDATE not in kinds


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


def test_geaenderte_dateien_werden_nur_gemeldet(fake):
    before = {c.ref_id for c in fake.changed_items()}
    assert len(before) == 2
    paths = {c.ref_id: c.local_path for c in fake.changed_items()}
    contents = {r: p.read_bytes() for r, p in paths.items()}
    result = fake.sync(_noop, threading.Event())
    # eine weitere Datei wird als geändert erkannt, aber nicht geladen
    assert len(result.changed_files) == 1
    assert result.changed_files[0].ref_id not in before
    assert len(fake.changed_items()) == 3
    assert {f.ref_id for f in result.new_files}.isdisjoint(c.ref_id for c in fake.changed_items())
    assert {r: p.read_bytes() for r, p in paths.items()} == contents
    # zweiter Sync meldet dieselben Änderungen nicht erneut
    assert fake.sync(_noop, threading.Event()).changed_files == []


def test_geaenderte_datei_herunterladen_legt_daneben(fake):
    changed = fake.changed_items()[0]
    original = changed.local_path.read_bytes()
    entry = fake.download_changed(changed.ref_id)
    assert entry.local_path.parent == changed.local_path.parent
    assert "_ilias_" in entry.name and entry.local_path.suffix == changed.local_path.suffix
    assert entry.local_path.is_file()
    assert changed.local_path.read_bytes() == original
    assert changed.ref_id not in {c.ref_id for c in fake.changed_items()}


def test_name_belegt_legt_ilias_datei_daneben(fake):
    actions = fake.dry_run(_noop, threading.Event())
    conflict = next(a for a in actions if a.kind == ActionKind.CONFLICT)
    own = conflict.local_path.read_bytes()
    result = fake.sync(_noop, threading.Event())
    assert conflict.local_path.read_bytes() == own
    loaded = next(f for f in result.new_files if f.local_path.parent == conflict.local_path.parent
                  and "_ilias_" in f.name)
    assert loaded.local_path.is_file()
    assert not any("_alt_" in p.name for p in fake.workdir.rglob("*"))


def test_geaenderte_datei_ignorieren(fake):
    changed = fake.changed_items()[0]
    fake.dismiss_changed(changed.ref_id)
    assert changed.ref_id not in {c.ref_id for c in fake.changed_items()}
    assert not any("_ilias_" in p.name for p in changed.local_path.parent.iterdir())


def test_geaenderte_datei_offline(tmp_path):
    api = FakeApi(delay=0.0, workdir=tmp_path / "w", fail={"offline"})
    with pytest.raises(NetworkError):
        api.download_changed(api.changed_items()[0].ref_id)
    assert len(api.changed_items()) == 2


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


def test_erster_sync_nur_in_leeren_uni_ordner(fake_first_run, tmp_path):
    import threading

    import pytest

    from ilias_sync.api import ConfigError

    base = fake_first_run.workdir / "Uni"
    (base / "eigene_Notizen").mkdir(parents=True)
    assert fake_first_run.check_base_dir(base).is_empty     # andere Ordner stören nicht
    (base / "01_semester" / "kurs").mkdir(parents=True)
    check = fake_first_run.check_base_dir(base)
    assert check.has_root_folder and not check.is_empty
    fake_first_run.create_base_structure(base)
    with pytest.raises(ConfigError):
        fake_first_run.sync(lambda p: None, threading.Event())


def test_check_base_dir_leer(fake_first_run, tmp_path):
    assert fake_first_run.check_base_dir(tmp_path / "gibt_es_nicht").is_empty


def test_fake_neue_namen_nach_namensregel(fake):
    actions = fake.dry_run(_noop, threading.Event())
    root = fake.root_dir()
    for a in actions:
        rel = a.local_path.relative_to(root)
        if a.kind in (ActionKind.NEW, ActionKind.CONFLICT):
            # Dateiname nach Regel, vorhandene Nutzerordner davor dürfen alte Schreibweise haben
            assert rel.name == rel.name.lower() and "ü" not in rel.name, rel
        if a.kind == ActionKind.UNASSIGNED:
            assert rel.parts[:2] == ("02_semester", "_neue_kurse"), rel
            assert all(p == p.lower() for p in rel.parts), rel
    assert any(a.kind == ActionKind.CREATE_DIR and a.local_path.name == "03_uebung" for a in actions)
