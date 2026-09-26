"""Status Index Tests: Einträge, Läufe, eine Datenbank pro Uni Ordner."""

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
