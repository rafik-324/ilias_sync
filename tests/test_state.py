"""Status Index Tests: Einträge, Läufe, eine Datenbank pro Uni Ordner."""

import sqlite3
from datetime import datetime

from ilias_sync import state
from ilias_sync.api import ItemStatus, SyncRun


def item(ref_id: int, status=ItemStatus.OK, downloaded_at=datetime(2026, 9, 25, 10, 0)) -> state.Item:
    return state.Item(ref_id, 1, "Kurs", "file", f"Kurs / d{ref_id}.pdf", f"01_SEMESTER/01_kurs/d{ref_id}.pdf",
                      datetime(2026, 9, 1, 12, 30), 1024, "ab" * 32, downloaded_at, status)


def test_put_get_roundtrip(tmp_path):
    with state.State(tmp_path / "i.sqlite3") as st:
        assert st.is_empty()
        st.put(item(7))
        assert st.get(7) == item(7)
        assert st.get(8) is None
        assert not st.is_empty()


def test_persists_and_replaces(tmp_path):
    with state.State(tmp_path / "i.sqlite3") as st:
        st.put(item(7))
        st.put(item(7, ItemStatus.UPDATED))
    with state.State(tmp_path / "i.sqlite3") as st:
        assert st.get(7).status == ItemStatus.UPDATED
        st.set_status(7, ItemStatus.USER_REMOVED)
        assert [i.ref_id for i in st.removed()] == [7]


def test_recent_newest_first_without_removed(tmp_path):
    with state.State(tmp_path / "i.sqlite3") as st:
        st.put(item(1, downloaded_at=datetime(2026, 9, 1)))
        st.put(item(2, downloaded_at=datetime(2026, 9, 3)))
        st.put(item(3, ItemStatus.USER_REMOVED, datetime(2026, 9, 4)))
        st.put(item(4, ItemStatus.ERROR, None))
        assert [i.ref_id for i in st.recent()] == [2, 1]
        assert [i.ref_id for i in st.recent(limit=1)] == [2]


def test_runs(tmp_path):
    with state.State(tmp_path / "i.sqlite3") as st:
        assert st.last_run() is None
        run_id = st.start_run(datetime(2026, 9, 25, 9, 0))
        assert st.last_run() is None  # noch nicht fertig
        assert not st.is_empty()
        run = SyncRun(datetime(2026, 9, 25, 9, 0), datetime(2026, 9, 25, 9, 5), 3, 1, 0, cancelled=True)
        st.finish_run(run_id, run)
        assert st.last_run() == run


def test_db_file_per_root(tmp_path):
    a = state.db_file(tmp_path, tmp_path / "Uni")
    assert a == state.db_file(tmp_path, tmp_path / "Uni")
    assert a != state.db_file(tmp_path, tmp_path / "Uni2")
    assert a.parent == tmp_path


def test_rel_paths(tmp_path):
    rel = state.rel_to_str(("01_SEMESTER", "02_Übung", "a.pdf"))
    assert rel == "01_SEMESTER/02_Übung/a.pdf"
    assert state.str_to_path(tmp_path, rel) == tmp_path / "01_SEMESTER" / "02_Übung" / "a.pdf"


def test_old_database_gets_new_columns(tmp_path):
    """Index vom Windows Rechner (ohne neue Spalten) wird beim Öffnen ergänzt, Daten bleiben."""
    path = tmp_path / "alt.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE items (ref_id INTEGER PRIMARY KEY, course_ref_id INTEGER NOT NULL, course_name TEXT NOT NULL,
            type TEXT NOT NULL, ilias_path TEXT NOT NULL, local_path TEXT NOT NULL, ilias_mtime TEXT, size INTEGER,
            sha256 TEXT, downloaded_at TEXT, status TEXT NOT NULL);
        INSERT INTO items VALUES (1, 100, 'Marketing', 'file', 'M / a.pdf', '01_SEMESTER/a.pdf',
            '2026-09-01T12:00:00', 10, 'abc', '2026-09-25T12:00:00', 'ok');
    """)
    con.commit()
    con.close()
    with state.State(path) as st:
        item = st.get(1)
        assert item.local_path == "01_SEMESTER/a.pdf" and item.url is None and item.new_mtime is None
        st.set_url(1, "u1")
        assert st.mark_changed(1, None, 20, datetime(2026, 9, 26))
        assert [i.ref_id for i in st.changed()] == [1]
        assert st.get(1).url == "u1"
