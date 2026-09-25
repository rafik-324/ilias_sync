"""Sync Tests: Planen und Ausführen mit Index, nie überschreiben, nie löschen."""

import threading
from datetime import date, datetime

import pytest
import requests

from ilias_sync import state, sync
from ilias_sync.api import ActionKind, Config, CourseConfig, ItemStatus
from ilias_sync.crawler import Course, IliasObject
from ilias_sync.mapper import CourseMapping, MappedItem, MappingResult

TODAY = date.today().strftime("%Y-%m-%d")
COURSE = Course("crs", 100, "Marketing [V] (HWS 2026)", "u100", "HWS 2026", "HWS 2026")


def response(body: bytes, content_type: str = "application/pdf") -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = body
    r._content_consumed = True
    r.headers["Content-Type"] = content_type
    return r


class FakeSession:
    """Liefert je URL einen Inhalt, zählt Abrufe."""

    def __init__(self, files: dict):
        self.files = files
        self.calls: list[str] = []

    def get(self, url, stream, timeout):
        self.calls.append(url)
        body = self.files[url]
        if isinstance(body, Exception):
            raise body
        return response(body)


def mapped(ref_id: int, name: str = "skript.pdf", size: int = 10, mtime=datetime(2026, 9, 1, 12, 0)) -> MappedItem:
    obj = IliasObject("file", ref_id, name, f"u{ref_id}", path=[COURSE.name], size=size, mtime=mtime)
    rel = ("01_SEMESTER", "01_marketing", "01_Vorlesung", name)
    return MappedItem(obj, COURSE, rel, "Vorlesung", False, {rel[:1], rel[:2], rel[:3]})


def mapping(*items: MappedItem) -> MappingResult:
    cm = CourseMapping(COURSE, "HWS 2026", "01_SEMESTER", "01_marketing", False, "config")
    return MappingResult(list(items), [cm], {"HWS 2026": "01_SEMESTER"})


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "Uni"
    st = state.State(tmp_path / "index.sqlite3")
    yield root, st
    st.close()


def run(m: MappingResult, st, root, session, cfg=None, cancel=None):
    jobs = sync.plan(m, st, root, cfg or Config(base_dir=None))
    result, _ = sync.execute(jobs, m, session, st, root, 2, cancel=cancel)
    return jobs, result


def target(root, name="skript.pdf"):
    return root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / name


def test_new_file_is_downloaded_and_indexed(env):
    root, st = env
    session = FakeSession({"u1": b"v1"})
    jobs, result = run(mapping(mapped(1)), st, root, session)
    assert [j.kind for j in jobs] == [ActionKind.NEW]
    assert target(root).read_bytes() == b"v1"
    entry = st.get(1)
    assert entry.status == ItemStatus.OK
    assert entry.local_path == "01_SEMESTER/01_marketing/01_Vorlesung/skript.pdf"
    assert entry.sha256 is not None and entry.size == 10
    assert result.run.new == 1 and result.run.errors == 0
    assert [f.name for f in result.new_files] == ["skript.pdf"]
    assert st.last_run().new == 1

    # Zweiter Lauf: nichts zu tun, kein Abruf
    jobs, result = run(mapping(mapped(1)), st, root, session)
    assert jobs == [] and session.calls == ["u1"]


def test_removed_by_user_is_never_downloaded_again(env):
    root, st = env
    session = FakeSession({"u1": b"v1"})
    run(mapping(mapped(1)), st, root, session)
    target(root).unlink()

    jobs, _ = run(mapping(mapped(1)), st, root, session)
    assert [(j.kind, j.mark_removed) for j in jobs] == [(ActionKind.SKIP_REMOVED, True)]
    assert st.get(1).status == ItemStatus.USER_REMOVED
    jobs, _ = run(mapping(mapped(1, size=99)), st, root, session)   # auch nicht, wenn ILIAS sie ändert
    assert [(j.kind, j.mark_removed) for j in jobs] == [(ActionKind.SKIP_REMOVED, False)]
    assert not target(root).exists() and session.calls == ["u1"]
    assert [i.ref_id for i in st.removed()] == [1]


def test_moved_by_user_counts_as_removed(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    target(root).rename(root / "woanders.pdf")
    jobs, _ = run(mapping(mapped(1)), st, root, FakeSession({}))
    assert jobs[0].kind == ActionKind.SKIP_REMOVED


def test_changed_in_ilias_keeps_old_version(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    jobs, result = run(mapping(mapped(1, mtime=datetime(2026, 9, 20, 8, 0))), st, root, FakeSession({"u1": b"v2"}))
    assert [j.kind for j in jobs] == [ActionKind.UPDATE]
    assert target(root).read_bytes() == b"v2"
    assert target(root, f"skript_alt_{TODAY}.pdf").read_bytes() == b"v1"
    entry = st.get(1)
    assert entry.status == ItemStatus.UPDATED and entry.ilias_mtime == datetime(2026, 9, 20, 8, 0)
    assert result.run.updated == 1


def test_changed_in_ilias_and_by_user_is_conflict(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    target(root).write_bytes(b"meine Notizen")
    jobs, _ = run(mapping(mapped(1, size=20)), st, root, FakeSession({"u1": b"v2"}))
    assert [j.kind for j in jobs] == [ActionKind.CONFLICT]
    assert target(root).read_bytes() == b"meine Notizen"
    assert target(root, f"skript_ilias_{TODAY}.pdf").read_bytes() == b"v2"
    entry = st.get(1)
    assert entry.status == ItemStatus.USER_MODIFIED and entry.size == 20
    assert entry.local_path.endswith("/skript.pdf")
    # In ILIAS unverändert: kein weiterer Konflikt
    assert sync.plan(mapping(mapped(1, size=20)), st, root, Config(base_dir=None)) == []


def test_user_modified_without_ilias_change_is_left_alone(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    target(root).write_bytes(b"meine Notizen")
    assert sync.plan(mapping(mapped(1)), st, root, Config(base_dir=None)) == []


def test_unknown_identical_file_is_adopted(env):
    root, st = env
    target(root).parent.mkdir(parents=True)
    target(root).write_bytes(b"v1")
    jobs, result = run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    assert [j.kind for j in jobs] == [ActionKind.CONFLICT]
    assert sorted(p.name for p in target(root).parent.iterdir()) == ["skript.pdf"]
    assert st.get(1).status == ItemStatus.OK
    assert result.new_files == []


def test_unknown_different_file_is_kept(env):
    root, st = env
    target(root).parent.mkdir(parents=True)
    target(root).write_bytes(b"eigene Datei")
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    assert target(root).read_bytes() == b"eigene Datei"
    assert target(root, f"skript_ilias_{TODAY}.pdf").read_bytes() == b"v1"
    assert st.get(1).local_path.endswith(f"skript_ilias_{TODAY}.pdf")


def test_versioned_names_never_collide(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    target(root, f"skript_alt_{TODAY}.pdf").write_bytes(b"schon da")
    run(mapping(mapped(1, size=11)), st, root, FakeSession({"u1": b"v2"}))
    assert target(root, f"skript_alt_{TODAY}.pdf").read_bytes() == b"schon da"
    assert target(root, f"skript_alt_{TODAY}_2.pdf").read_bytes() == b"v1"
    assert target(root).read_bytes() == b"v2"


def test_error_is_retried_next_time(env):
    root, st = env
    _, result = run(mapping(mapped(1)), st, root, FakeSession({"u1": requests.ConnectionError()}))
    assert result.run.errors == 1 and result.errors
    assert st.get(1).status == ItemStatus.ERROR
    assert not list(target(root).parent.glob("*.part"))
    jobs, _ = run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    assert [j.kind for j in jobs] == [ActionKind.NEW]
    assert st.get(1).status == ItemStatus.OK


def test_failed_update_keeps_original(env):
    root, st = env
    run(mapping(mapped(1)), st, root, FakeSession({"u1": b"v1"}))
    run(mapping(mapped(1, size=11)), st, root, FakeSession({"u1": requests.Timeout()}))
    assert target(root).read_bytes() == b"v1"
    assert sorted(p.name for p in target(root).parent.iterdir()) == ["skript.pdf"]
    assert st.get(1).status == ItemStatus.OK   # nächster Lauf versucht das Update erneut


def test_cancel_before_start_downloads_nothing(env):
    root, st = env
    cancel = threading.Event()
    cancel.set()
    session = FakeSession({"u1": b"v1"})
    _, result = run(mapping(mapped(1)), st, root, session, cancel=cancel)
    assert session.calls == [] and result.run.cancelled
    assert st.get(1) is None


def test_video_switch(env):
    root, st = env
    cfg = Config(base_dir=None, courses=[CourseConfig(100, COURSE.name, "01_marketing", download_videos=False)])
    m = mapping(mapped(1), mapped(2, "vorlesung.mp4"))
    assert [j.item.obj.ref_id for j in sync.plan(m, st, root, cfg)] == [1]
    assert len(sync.plan(m, st, root, Config(base_dir=None))) == 2


def test_dry_run_writes_nothing(env):
    root, st = env
    m = mapping(mapped(1), mapped(2, "b.pdf"))
    actions = sync.planned_actions(sync.plan(m, st, root, Config(base_dir=None)), m, root)
    kinds = [a.kind for a in actions]
    assert kinds.count(ActionKind.NEW) == 2 and kinds.count(ActionKind.CREATE_DIR) == 3
    assert not root.exists() and st.is_empty()


def test_first_sync_needs_empty_root(env):
    root, st = env
    assert sync.check_first_sync(st, root) is None          # gibt es noch nicht
    root.mkdir()
    assert sync.check_first_sync(st, root) is None          # leer
    (root / "01_SEMESTER").mkdir()
    assert "leeren" in sync.check_first_sync(st, root)      # fremder Inhalt
    st.start_run(datetime.now())
    assert sync.check_first_sync(st, root) is None          # später: eigener Inhalt


def test_too_long_path_is_reported(env):
    root, st = env
    item = mapped(1, name="x" * 300 + ".pdf")
    item.rel_path = ("a" * 240, "b.pdf")
    problems = []
    assert sync.plan(mapping(item), st, root, Config(base_dir=None), problems) == []
    assert len(problems) == 1 and "Pfad zu lang" in problems[0]
