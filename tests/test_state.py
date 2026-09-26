"""Status Index Tests: Objekte, Status, Sync Läufe, Pfade, Threads."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import pytest

from ilias_sync import state
from ilias_sync.state import Item, State


def item(ref_id: int = 1, **kw) -> Item:
    values = dict(
        ref_id=ref_id,
        course_ref_id=100,
        type="file",
        ilias_path="Marketing [V] (HWS 2026) / Dateien / Vorlesung / Skript.pdf",
        local_path="01_SEMESTER/01_marketing/01_Vorlesung/Skript.pdf",
        ilias_mtime=datetime(2026, 7, 10, 16, 57),
        size=1_500_000,
        sha256="ab" * 32,
        downloaded_at=datetime(2026, 9, 26, 13, 5),
        status="ok",
    )
    values.update(kw)
    return Item(**values)


@pytest.fixture
def db(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


def test_put_and_get_roundtrip(db):
    db.put(item())
    assert db.get(1) == item()
    assert db.get(2) is None


def test_put_replaces(db):
    db.put(item())
    db.put(item(size=2_000_000, status="updated"))
    got = db.get(1)
    assert got.size == 2_000_000
    assert got.status == "updated"
    assert len(db.items()) == 1


def test_optional_fields_none(db):
    db.put(item(ilias_mtime=None, size=None, sha256=None))
    got = db.get(1)
    assert got.ilias_mtime is None and got.size is None and got.sha256 is None


def test_umlauts_survive(db):
    db.put(item(local_path="01_SEMESTER/01_marketing/02_Übung/Ü1_Kernlösung.pdf"))
    assert db.get(1).local_path == "01_SEMESTER/01_marketing/02_Übung/Ü1_Kernlösung.pdf"


def test_set_status_and_filter(db):
    db.put(item(1))
    db.put(item(2, course_ref_id=200))
    db.put(item(3))
    db.set_status(2, "user_removed")
    assert [i.ref_id for i in db.items(status="user_removed")] == [2]
    assert [i.ref_id for i in db.items(course_ref_id=100)] == [1, 3]
    assert [i.ref_id for i in db.items(status="ok", course_ref_id=100)] == [1, 3]


def test_invalid_status_rejected(db):
    with pytest.raises(ValueError):
        item(status="kaputt")
    db.put(item())
    with pytest.raises(ValueError):
        db.set_status(1, "kaputt")


def test_recent_newest_first_without_removed(db):
    db.put(item(1, downloaded_at=datetime(2026, 9, 1)))
    db.put(item(2, downloaded_at=datetime(2026, 9, 3)))
    db.put(item(3, downloaded_at=datetime(2026, 9, 2), status="updated"))
    db.put(item(4, downloaded_at=datetime(2026, 9, 4), status="user_removed"))
    db.put(item(5, downloaded_at=datetime(2026, 9, 5), status="error"))
    assert [i.ref_id for i in db.recent()] == [2, 3, 1]
    assert [i.ref_id for i in db.recent(limit=1)] == [2]


def test_runs(db):
    assert db.last_run() is None
    run_id = db.start_run(datetime(2026, 9, 26, 12, 0))
    # Laufender Sync zählt noch nicht als letzter Lauf
    assert db.last_run() is None
    db.finish_run(run_id, new=5, updated=1, errors=2, finished_at=datetime(2026, 9, 26, 12, 3))
    second = db.start_run(datetime(2026, 9, 26, 13, 0))
    db.finish_run(second, new=0, updated=0, errors=0, cancelled=True, finished_at=datetime(2026, 9, 26, 13, 1))
    last = db.last_run()
    assert last.id == second
    assert last.cancelled
    assert last.started_at == datetime(2026, 9, 26, 13, 0)


def test_persists_across_instances(tmp_path):
    path = tmp_path / "unter" / "ordner" / "state.db"
    with State(path) as s:
        s.put(item())
        s.finish_run(s.start_run(), new=1, updated=0, errors=0)
    with State(path) as s:
        assert s.get(1) == item()
        assert s.last_run().new == 1


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "state.db"
    State(path).close()
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {state.SCHEMA_VERSION + 1}")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError):
        State(path)


def test_parallel_writes(db):
    def worker(start: int) -> None:
        for i in range(start, start + 50):
            db.put(item(i))

    threads = [threading.Thread(target=worker, args=(n * 50,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(db.items()) == 200


def test_index_paths_platform_independent(tmp_path):
    rel = ("01_SEMESTER", "01_marketing", "02_Übung", "Blatt 1.pdf")
    stored = state.to_index_path(rel)
    assert stored == "01_SEMESTER/01_marketing/02_Übung/Blatt 1.pdf"
    assert state.from_index_path(tmp_path, stored) == tmp_path.joinpath(*rel)


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert state.sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_default_location_in_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "data_dir", lambda: tmp_path)
    assert state.state_file() == Path(tmp_path) / "state.db"


def test_mark_changed_keeps_old_version(db):
    db.put(item())
    new_mtime = datetime(2026, 10, 3, 9, 0)
    assert db.mark_changed(1, new_mtime, 1_600_000, detected_at=datetime(2026, 10, 3, 12, 0))
    got = db.get(1)
    assert got.status == "ilias_changed"
    # Werte der geladenen Version bleiben, die neue steht daneben
    assert got.ilias_mtime == datetime(2026, 7, 10, 16, 57)
    assert got.size == 1_500_000
    assert (got.new_mtime, got.new_size) == (new_mtime, 1_600_000)
    assert got.detected_at == datetime(2026, 10, 3, 12, 0)
    assert [i.ref_id for i in db.items(status="ilias_changed")] == [1]


def test_mark_changed_reports_only_new_changes(db):
    db.put(item())
    assert db.mark_changed(1, datetime(2026, 10, 3), 1)
    assert not db.mark_changed(1, datetime(2026, 10, 3), 1)
    # Erneut geändert, bevor der Nutzer reagiert hat
    assert db.mark_changed(1, datetime(2026, 10, 5), 2)
    assert db.get(1).new_mtime == datetime(2026, 10, 5)


def test_mark_changed_unknown_item(db):
    with pytest.raises(KeyError):
        db.mark_changed(99, None, None)


def test_dismiss_changed(db):
    db.put(item())
    db.mark_changed(1, datetime(2026, 10, 3), 1_600_000)
    db.dismiss_changed(1)
    got = db.get(1)
    assert got.status == "ok"
    assert (got.ilias_mtime, got.size) == (datetime(2026, 10, 3), 1_600_000)
    assert got.new_mtime is None and got.new_size is None and got.detected_at is None
    assert got.sha256 == item().sha256


def test_dismiss_only_affects_changed(db):
    db.put(item(status="user_removed"))
    db.dismiss_changed(1)
    assert db.get(1).status == "user_removed"
