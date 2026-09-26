"""Sync Tests: Planen und Ausführen gegen Index und Dateisystem, ohne Netz."""

import os
import threading
import time
from datetime import date, datetime, timedelta

import pytest
import requests

from ilias_sync import state, sync
from ilias_sync.api import Config, CourseConfig
from ilias_sync.crawler import Course, IliasObject
from ilias_sync.mapper import MappedItem, MappingResult
from ilias_sync.state import State
from ilias_sync.sync import Kind

COURSE = Course("crs", 100, "Marketing [V] (HWS 2026)", "https://ilias/crs/100", "HWS 2026", "HWS 2026")
MTIME = datetime(2026, 7, 10, 16, 57)


def url(ref_id: int) -> str:
    return f"https://ilias.uni-mannheim.de/goto.php/file/{ref_id}/download"


def file_item(ref_id: int, name: str = "Skript.pdf", mtime=MTIME, size=1000,
              folder: tuple[str, ...] = ("01_SEMESTER", "01_marketing", "01_Vorlesung")) -> MappedItem:
    obj = IliasObject("file", ref_id, name, url(ref_id), [COURSE.name, "Vorlesung"], size, mtime)
    return MappedItem(obj, COURSE, (*folder, name), "Vorlesung", True)


def mapping(*items: MappedItem) -> MappingResult:
    return MappingResult(items=list(items), courses=[], semesters={})


def response(body: bytes) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = body
    r._content_consumed = True
    r.headers["Content-Type"] = "application/pdf"
    return r


class FakeSession:
    """Liefert pro URL einen festen Inhalt, zählt Abrufe."""

    def __init__(self, bodies: dict[str, bytes] | None = None, fail: set[str] | None = None):
        self.bodies = bodies or {}
        self.fail = fail or set()
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def get(self, u, stream, timeout):
        with self._lock:
            self.calls.append(u)
        if u in self.fail:
            raise requests.ConnectionError("offline")
        return response(self.bodies.get(u, b"%PDF " + u.encode()))


@pytest.fixture
def st(tmp_path):
    with State(tmp_path / "state.db") as s:
        yield s


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "01_Universität_Mannheim"
    r.mkdir()
    return r


def run(items, st, root, session=None, cfg=None, **kw):
    cfg = cfg or Config(base_dir=None)
    session = session or FakeSession()
    actions = sync.plan(mapping(*items), st, root, cfg)
    return actions, sync.execute(actions, session, st, root, **kw), session


def make_old(path):
    """Datei so aussehen lassen, als läge sie schon länger da (vor dem Index Eintrag)."""
    old = time.time() - 3600
    os.utime(path, (old, old))


# ---------------- Neue Dateien ----------------

def test_new_file_downloaded_and_indexed(st, root):
    actions, summary, session = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.NEW]
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    assert target.read_bytes() == b"%PDF " + url(1).encode()
    item = st.get(1)
    assert item.status == "ok"
    assert item.local_path == "01_SEMESTER/01_marketing/01_Vorlesung/Skript.pdf"
    assert item.sha256 == state.sha256_file(target)
    assert (item.ilias_mtime, item.size, item.url) == (MTIME, 1000, url(1))
    assert [i.ref_id for i in summary.new] == [1]
    assert st.last_run().new == 1


def test_second_run_does_nothing(st, root):
    run([file_item(1)], st, root)
    actions, summary, session = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.UNCHANGED]
    assert session.calls == []
    assert summary.new == [] and summary.changed == []


def test_existing_local_file_is_adopted_not_overwritten(st, root):
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"selbst geladen")
    make_old(target)
    actions, summary, session = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.ADOPT]
    assert session.calls == []
    assert target.read_bytes() == b"selbst geladen"
    item = st.get(1)
    assert item.status == "ok"
    assert item.sha256 == state.sha256_file(target)
    # Übernommene Dateien erscheinen nicht als "gerade geladen"
    assert item.downloaded_at < datetime.now() - timedelta(minutes=30)
    assert summary.adopted == 1 and summary.new == []


def test_error_is_recorded_and_retried(st, root):
    failing = FakeSession(fail={url(1)})
    _, summary, _ = run([file_item(1)], st, root, session=failing)
    assert len(summary.errors) == 1
    assert "Marketing" in summary.errors[0]
    assert st.get(1).status == "error"
    assert st.last_run().errors == 1
    actions, summary, _ = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.NEW]
    assert st.get(1).status == "ok"


# ---------------- In ILIAS geändert: nur melden ----------------

def test_changed_in_ilias_is_only_reported(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    before = target.read_bytes()
    new_mtime = datetime(2026, 10, 3, 9, 0)
    actions, summary, session = run([file_item(1, mtime=new_mtime, size=1200)], st, root)
    assert [a.kind for a in actions] == [Kind.CHANGED]
    assert session.calls == []
    assert target.read_bytes() == before
    assert [i.ref_id for i in summary.changed] == [1]
    item = st.get(1)
    assert item.status == "ilias_changed"
    assert (item.new_mtime, item.new_size) == (new_mtime, 1200)
    assert (item.ilias_mtime, item.size) == (MTIME, 1000)


def test_changed_reported_only_once(st, root):
    run([file_item(1)], st, root)
    changed = file_item(1, mtime=datetime(2026, 10, 3))
    _, first, _ = run([changed], st, root)
    _, second, _ = run([changed], st, root)
    assert len(first.changed) == 1
    assert second.changed == []
    assert st.get(1).status == "ilias_changed"


def test_size_change_alone_counts(st, root):
    run([file_item(1)], st, root)
    actions, _, _ = run([file_item(1, size=2000)], st, root)
    assert [a.kind for a in actions] == [Kind.CHANGED]


def test_unknown_values_do_not_count_as_change(st, root):
    run([file_item(1)], st, root)
    actions, _, _ = run([file_item(1, mtime=None, size=None)], st, root)
    assert [a.kind for a in actions] == [Kind.UNCHANGED]


def test_dismissed_change_stays_quiet(st, root):
    run([file_item(1)], st, root)
    changed = file_item(1, mtime=datetime(2026, 10, 3))
    run([changed], st, root)
    st.dismiss_changed(1)
    actions, summary, _ = run([changed], st, root)
    assert [a.kind for a in actions] == [Kind.UNCHANGED]
    assert summary.changed == []
    # Ändert sich die Datei erneut, wird wieder gemeldet
    _, summary, _ = run([file_item(1, mtime=datetime(2026, 10, 9))], st, root)
    assert len(summary.changed) == 1


def test_user_edited_then_changed_in_ilias(st, root):
    """Nutzer schreibt Notizen ins PDF, danach lädt der Dozent eine neue Version hoch."""
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.write_bytes(b"mit meinen Notizen")
    future = time.time() + 60
    os.utime(target, (future, future))
    actions, _, _ = run([file_item(1)], st, root)
    assert actions[0].user_modified
    assert st.get(1).status == "user_modified"

    actions, summary, session = run([file_item(1, mtime=datetime(2026, 10, 3))], st, root)
    assert [a.kind for a in actions] == [Kind.CHANGED]
    assert session.calls == []
    assert target.read_bytes() == b"mit meinen Notizen"
    assert len(summary.changed) == 1


# ---------------- Laden auf Wunsch des Nutzers ----------------

def test_download_changed_puts_new_version_next_to_file(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.write_bytes(b"mit meinen Notizen")
    new_mtime = datetime(2026, 10, 3, 9, 0)
    run([file_item(1, mtime=new_mtime, size=1200)], st, root)

    session = FakeSession({url(1): b"neue Version"})
    item = sync.download_changed(session, st, root, 1)
    new_file = target.with_name(f"Skript_ilias_{date.today():%Y-%m-%d}.pdf")
    assert new_file.read_bytes() == b"neue Version"
    assert target.read_bytes() == b"mit meinen Notizen"
    assert item.status == "updated"
    assert item.local_path.endswith(new_file.name)
    assert (item.ilias_mtime, item.size, item.new_mtime) == (new_mtime, 1200, None)
    # Beim nächsten Sync ist alles ruhig
    actions, summary, session = run([file_item(1, mtime=new_mtime, size=1200)], st, root)
    assert [a.kind for a in actions] == [Kind.UNCHANGED]
    assert session.calls == []


def test_download_changed_requires_changed_status(st, root):
    run([file_item(1)], st, root)
    with pytest.raises(sync.ItemNotAvailable):
        sync.download_changed(FakeSession(), st, root, 1)
    with pytest.raises(sync.ItemNotAvailable):
        sync.download_changed(FakeSession(), st, root, 99)


# ---------------- Vom Nutzer entfernt ----------------

def test_removed_by_user_is_not_downloaded_again(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.unlink()
    actions, summary, session = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.REMOVED]
    assert summary.removed == 1
    assert st.get(1).status == "user_removed"
    assert not target.exists()
    # Bleibt so, auch in späteren Läufen und wenn ILIAS die Datei ändert
    actions, summary, session = run([file_item(1, mtime=datetime(2026, 10, 3))], st, root)
    assert [a.kind for a in actions] == [Kind.REMOVED]
    assert summary.removed == 0 and session.calls == []


def test_moved_by_user_is_not_downloaded_again(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.rename(root / "woanders.pdf")
    actions, _, session = run([file_item(1)], st, root)
    assert [a.kind for a in actions] == [Kind.REMOVED]
    assert session.calls == []


def test_redownload(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.unlink()
    run([file_item(1)], st, root)
    item = sync.redownload(FakeSession({url(1): b"wieder da"}), st, root, 1)
    assert target.read_bytes() == b"wieder da"
    assert item.status == "ok"


def test_redownload_never_overwrites(st, root):
    run([file_item(1)], st, root)
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "Skript.pdf"
    target.unlink()
    run([file_item(1)], st, root)
    st.set_status(1, "user_removed")
    target.write_bytes(b"neu vom Nutzer")
    sync.redownload(FakeSession({url(1): b"aus ILIAS"}), st, root, 1)
    assert target.read_bytes() == b"neu vom Nutzer"
    assert target.with_name(f"Skript_ilias_{date.today():%Y-%m-%d}.pdf").read_bytes() == b"aus ILIAS"


# ---------------- Einstellungen, Abbruch, Fortschritt ----------------

def test_disabled_videos_are_skipped(st, root):
    cfg = Config(base_dir=None, courses=[CourseConfig(100, COURSE.name, "01_marketing", download_videos=False)])
    items = [file_item(1), file_item(2, name="Vorlesung 1.mp4")]
    actions, summary, session = run(items, st, root, cfg=cfg)
    assert [a.kind for a in actions] == [Kind.NEW, Kind.DISABLED]
    assert session.calls == [url(1)]
    assert st.get(2) is None


def test_non_files_are_ignored_for_now(st, root):
    link = file_item(1)
    link.obj.type = "webr"
    actions, _, _ = run([link], st, root)
    assert actions == []


def test_cancel_stops_downloads(st, root):
    cancel = threading.Event()
    cancel.set()
    _, summary, session = run([file_item(1), file_item(2, name="b.pdf")], st, root, cancel=cancel)
    assert summary.cancelled
    assert session.calls == []
    assert st.last_run().cancelled


def test_progress_reports_downloads(st, root):
    seen = []
    run([file_item(1), file_item(2, name="b.pdf")], st, root, progress=seen.append)
    assert seen[0].current == 0 and seen[0].total == 2
    assert seen[-1].current == 2
    assert all(p.phase == "Herunterladen" for p in seen)


def test_plan_writes_nothing(st, root):
    target = root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "vorhanden.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    sync.plan(mapping(file_item(1), file_item(2, name="vorhanden.pdf")), st, root, Config(base_dir=None))
    assert st.items() == []
    assert st.last_run() is None
    assert sorted(p.name for p in target.parent.iterdir()) == ["vorhanden.pdf"]
