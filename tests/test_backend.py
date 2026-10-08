"""Tests der echten Fassade (backend.Api) ohne Netz und ohne Schlüsselbund."""

import threading
from datetime import date, datetime

import pytest
import requests

from ilias_sync import auth, backend, config, crawler
from ilias_sync.api import (
    Api, Cancelled, Config, ConfigError, CourseConfig, IliasCourse, InvalidCredentialsError, ItemStatus,
    NetworkError, SessionState,
)
from ilias_sync.crawler import Course, CrawlResult, IliasObject

COURSE = Course("crs", 100, "Marketing [V] (HWS 2026)", "https://ilias/crs/100", "HWS 2026", "HWS 2026")
TODAY = f"{date.today():%Y-%m-%d}"


def response(body: bytes) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = body
    r._content_consumed = True
    r.headers["Content-Type"] = "application/pdf"
    return r


class FakeSession:
    def __init__(self, files=None):
        self.files = files or {}
        self.calls = []

    def get(self, url, stream=False, timeout=None):
        self.calls.append(url)
        return response(self.files.get(url, b"%PDF"))


def crawl_result(size=10, mtime=datetime(2026, 9, 1, 12, 0)) -> CrawlResult:
    folder = IliasObject("fold", 200, "Vorlesung", "https://ilias/fold/200", path=[COURSE.name])
    file = IliasObject("file", 300, "skript.pdf", "u300", path=[COURSE.name, "Vorlesung"], size=size, mtime=mtime)
    return CrawlResult(courses=[COURSE], objects={COURSE.ref_id: [folder, file]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Api mit eigenen Pfaden, Schlüsselbund als dict, ILIAS als Attrappe."""
    passwords: dict[str, str] = {}
    monkeypatch.setattr(auth, "load_password", lambda u: passwords.get(u))
    monkeypatch.setattr(auth, "store_password", lambda u, p: passwords.__setitem__(u, p))
    monkeypatch.setattr(auth, "forget_password", lambda u: passwords.pop(u, None))
    session = FakeSession()
    monkeypatch.setattr(auth, "get_session", lambda creds, path=None: (creds(), session)[1])
    monkeypatch.setattr(auth, "check_session", lambda s: True)
    api = Api(config_path=tmp_path / "config.toml", data_dir=tmp_path / "data", cookie_path=tmp_path / "cookies.txt")
    monkeypatch.setattr(api, "_crawl", lambda cfg, s, progress, cancel: crawl_result())
    base = tmp_path / "Uni_neu"
    return api, base, passwords, session


def setup_account(api, base, passwords):
    cfg = Config(base_dir=base, username="nutzer1")
    config.save_config(cfg, api._config_path)
    passwords["nutzer1"] = "geheim"


def noop(_progress):
    pass


# ---------------- Fehler ----------------

@pytest.mark.parametrize("error, expected", [
    (requests.ConnectionError("https://ilias?PHPSESSID=abc"), NetworkError),
    (requests.Timeout(), NetworkError),
    (auth.InvalidCredentials("Uni-ID oder Passwort falsch."), InvalidCredentialsError),
    (crawler.CrawlCancelled(), Cancelled),
])
def test_errors_are_translated_without_secrets(error, expected):
    with pytest.raises(expected) as info:
        with backend.translate_errors():
            raise error
    assert "PHPSESSID" not in str(info.value)


# ---------------- Konto ----------------

def test_login_stores_password_and_username(env, monkeypatch):
    api, base, passwords, _ = env
    monkeypatch.setattr(auth, "login", lambda s, u, p: None)
    monkeypatch.setattr(auth, "save_cookies", lambda s: None)
    api.login(" nutzer1 ", "geheim", remember=True)
    assert passwords == {"nutzer1": "geheim"}
    assert api.stored_username() == "nutzer1"
    assert api.has_stored_password()


def test_login_without_remember_forgets_password(env, monkeypatch):
    api, base, passwords, _ = env
    passwords["nutzer1"] = "alt"
    monkeypatch.setattr(auth, "login", lambda s, u, p: None)
    monkeypatch.setattr(auth, "save_cookies", lambda s: None)
    api.login("nutzer1", "geheim", remember=False)
    assert passwords == {}


def test_wrong_password_clears_keyring(env, monkeypatch):
    api, base, passwords, _ = env
    passwords["nutzer1"] = "alt"

    def reject(s, u, p):
        raise auth.InvalidCredentials("Uni-ID oder Passwort falsch.")

    monkeypatch.setattr(auth, "login", reject)
    with pytest.raises(InvalidCredentialsError):
        api.login("nutzer1", "falsch")
    assert passwords == {}


def test_login_offline(env, monkeypatch):
    api, *_ = env

    def offline(s, u, p):
        raise requests.ConnectionError()

    monkeypatch.setattr(auth, "login", offline)
    with pytest.raises(NetworkError):
        api.login("nutzer1", "geheim")


def test_check_session_states(env, monkeypatch):
    api, base, passwords, _ = env
    assert api.check_session() == SessionState.LOGIN_REQUIRED   # kein Passwort gespeichert
    setup_account(api, base, passwords)
    assert api.check_session() == SessionState.OK

    def offline(creds, path=None):
        raise requests.ConnectionError()

    api._session = None
    monkeypatch.setattr(auth, "get_session", offline)
    assert api.check_session() == SessionState.OFFLINE


def test_logout(env, monkeypatch):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    api._cookie_path.write_text("cookies")
    api.logout()
    assert passwords == {}
    assert not api._cookie_path.exists()
    assert api.stored_username() == "nutzer1"


# ---------------- Config und Basispfad ----------------

def test_config_exists_needs_base_dir(env):
    api, base, passwords, _ = env
    assert not api.config_exists()
    config.save_config(Config(base_dir=None, username="nutzer1"), api._config_path)
    assert not api.config_exists()
    setup_account(api, base, passwords)
    assert api.config_exists()


def test_check_base_dir(env):
    api, base, *_ = env
    check = api.check_base_dir(base)
    assert not check.exists and check.message and check.is_empty
    base.mkdir()
    check = api.check_base_dir(base)
    # ohne root_folder_name ist der gewählte Ordner selbst der Uni Ordner
    assert check.exists and check.writable and check.has_root_folder and check.is_empty
    root = base
    (root / ".DS_Store").write_bytes(b"")
    assert api.check_base_dir(base).is_empty
    (root / "01_SEMESTER").mkdir()
    (root / "notizen").mkdir()
    check = api.check_base_dir(base)
    assert check.has_root_folder and not check.is_empty
    assert check.existing_semesters == ["01_SEMESTER"]
    assert "leeren" in check.message


def test_check_base_dir_mit_unterordner(env):
    api, base, *_ = env
    config.save_config(Config(base_dir=None, root_folder_name="01_Universität_Mannheim"), api._config_path)
    base.mkdir()
    check = api.check_base_dir(base)
    assert not check.has_root_folder and check.is_empty
    (base / "01_Universität_Mannheim" / "01_SEMESTER").mkdir(parents=True)
    check = api.check_base_dir(base)
    assert check.has_root_folder and not check.is_empty
    assert "01_Universität_Mannheim" in check.message
    api.create_base_structure(base)
    assert api.root_dir() == base / "01_Universität_Mannheim"


def test_create_base_structure(env):
    api, base, *_ = env
    api.create_base_structure(base)
    assert base.is_dir()
    assert api.load_config().base_dir == base
    assert api.root_dir() == base


def test_suggest_mapping_existing_and_new(env):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    (base / "01_SEMESTER" / "01_marketing").mkdir(parents=True)
    courses = [
        IliasCourse(100, "Marketing [V] (HWS 2026)", "HWS 2026", "u"),
        IliasCourse(101, "Statistik I (HWS 2026)", "HWS 2026", "u"),
    ]
    marketing, statistik = api.suggest_mapping(courses)
    assert (marketing.semester_dir, marketing.local_dir, marketing.exists) == ("01_SEMESTER", "01_marketing", True)
    assert marketing.score > 0.5
    assert (statistik.local_dir, statistik.exists, statistik.score) == ("02_statistik_i", False, 0.0)


def test_local_course_dirs(env):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    cfg = api.load_config()
    cfg.ignore_local_dirs = ["*-MacBook*"]
    api.save_config(cfg)
    semester = base / "01_SEMESTER"
    for d in ("01_marketing", "02_pi", "_Neue_Kurse", "01_marketing-MacBook Pro"):
        (semester / d).mkdir(parents=True)
    assert api.local_course_dirs("01_SEMESTER") == ["01_marketing", "02_pi"]
    assert api.local_course_dirs("02_SEMESTER") == []


# ---------------- Sync ----------------

def test_sync_downloads_and_reports(env, monkeypatch):
    api, base, passwords, session = env
    setup_account(api, base, passwords)
    session.files["u300"] = b"v1"
    result = api.sync(noop, threading.Event())
    assert [f.name for f in result.new_files] == ["skript.pdf"]
    path = result.new_files[0].local_path
    assert path.read_bytes() == b"v1"
    assert path == base / "01_semester" / "01_marketing" / "01_vorlesung" / "skript.pdf"
    # Neuer Kursordner steht jetzt in der Config
    assert [c.ref_id for c in api.load_config().courses] == [100]
    assert api.last_run().new == 1
    assert [f.name for f in api.recent_files()] == ["skript.pdf"]

    # In ILIAS geändert: nur melden
    monkeypatch.setattr(api, "_crawl", lambda cfg, s, progress, cancel: crawl_result(size=20))
    result = api.sync(noop, threading.Event())
    assert [f.name for f in result.changed_files] == ["skript.pdf"]
    assert path.read_bytes() == b"v1"
    assert [f.ref_id for f in api.changed_items()] == [300]

    session.files["u300"] = b"v2"
    loaded = api.download_changed(300)
    assert loaded.local_path.name == f"skript_ilias_{TODAY}.pdf"
    assert loaded.status == ItemStatus.UPDATED
    assert api.changed_items() == []
    assert path.read_bytes() == b"v1"


def test_dismiss_changed(env, monkeypatch):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    api.sync(noop, threading.Event())
    monkeypatch.setattr(api, "_crawl", lambda cfg, s, progress, cancel: crawl_result(size=20))
    api.sync(noop, threading.Event())
    api.dismiss_changed(300)
    assert api.changed_items() == []
    assert api.sync(noop, threading.Event()).changed_files == []


def test_removed_and_redownload(env):
    api, base, passwords, session = env
    setup_account(api, base, passwords)
    path = api.sync(noop, threading.Event()).new_files[0].local_path
    path.unlink()
    api.sync(noop, threading.Event())
    assert [f.ref_id for f in api.removed_items()] == [300]
    api.redownload(300)
    assert path.exists()
    assert api.removed_items() == []


def snapshot(path):
    return sorted((str(p.relative_to(path)), p.stat().st_mtime_ns, p.is_dir()) for p in path.rglob("*"))


def test_first_sync_into_existing_uni_folder_writes_nothing(env, monkeypatch):
    """Lage auf dem Mac: base_dir zeigt auf den echten Uni Ordner mit eigenen Dateien, Index leer.
    Der Sync muss abbrechen, bevor er ILIAS fragt oder irgendetwas schreibt."""
    api, base, passwords, session = env
    setup_account(api, base, passwords)
    root = base
    (root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung").mkdir(parents=True)
    (root / "01_SEMESTER" / "01_marketing" / "01_Vorlesung" / "01_Marketing_Vorlesungsskript.pdf").write_bytes(b"x")
    (root / "code_projects").mkdir()
    # Eine Vorschau vorher (legt nur den leeren Index im Datenverzeichnis an) ändert daran nichts
    api.dry_run(noop, threading.Event())
    before = snapshot(base)
    config_before = api._config_path.read_bytes()

    def no_login(*a, **kw):
        raise AssertionError("darf ILIAS nicht fragen")

    monkeypatch.setattr(api, "_logged_in_session", no_login)
    with pytest.raises(ConfigError, match="leeren oder neuen Uni Ordner"):
        api.sync(noop, threading.Event())
    assert snapshot(base) == before
    assert api._config_path.read_bytes() == config_before
    assert session.calls == []
    assert api.last_run() is None


@pytest.mark.parametrize("error", [requests.ConnectionError("x"), requests.Timeout(), requests.exceptions.SSLError()])
def test_network_errors_are_readable(error):
    with pytest.raises(NetworkError) as info:
        with backend.translate_errors():
            raise error
    text = str(info.value)
    assert "Error" not in text and "ILIAS" in text


def test_dry_run_writes_nothing(env):
    api, base, passwords, session = env
    setup_account(api, base, passwords)
    actions = api.dry_run(noop, threading.Event())
    assert any(a.local_path.name == "skript.pdf" for a in actions)
    assert not base.exists()
    assert session.calls == []
    assert api.load_config().courses == []
    assert api.last_run() is None


def test_cancel_during_crawl(env, monkeypatch):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)

    def cancelled(cfg, s, progress, cancel):
        raise crawler.CrawlCancelled()

    monkeypatch.setattr(api, "_crawl", cancelled)
    with pytest.raises(Cancelled):
        api.sync(noop, threading.Event())


def test_sync_without_password_needs_login(env):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    passwords.clear()
    with pytest.raises(InvalidCredentialsError):
        api.sync(noop, threading.Event())


def test_status_without_config(env):
    api, *_ = env
    assert api.last_run() is None
    assert api.recent_files() == [] and api.removed_items() == [] and api.changed_items() == []
    assert api.root_dir() is None
    with pytest.raises(ConfigError):
        api.dry_run(noop, threading.Event())


def test_courses_keep_config_settings(env):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    cfg = api.load_config()
    cfg.courses = [CourseConfig(100, COURSE.name, "03_marketing", download_files=False)]
    api.save_config(cfg)
    result = api.sync(noop, threading.Event())
    assert result.new_files == []
    assert api.load_config().courses[0].local_dir == "03_marketing"


def test_fetch_courses_uses_dashboard_heading_for_groups(env, monkeypatch):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    group = Course("grp", 101, "Marketing [2. PG]", "u", None, "HWS 2026")
    monkeypatch.setattr(crawler.Crawler, "fetch_courses", lambda self: [COURSE, group])
    courses = api.fetch_courses(noop, threading.Event())
    assert [c.semester for c in courses] == ["HWS 2026", "HWS 2026"]


def test_sync_result_is_logged(env, caplog, monkeypatch):
    api, base, passwords, _ = env
    setup_account(api, base, passwords)
    with caplog.at_level("INFO", logger="ilias_sync.backend"):
        api.sync(noop, threading.Event())
    assert any("Sync fertig: 1 neu, 0 in ILIAS geändert, 0 Fehler, Dauer" in r.message for r in caplog.records)

    def cancelled(cfg, s, progress, cancel):
        raise crawler.CrawlCancelled()

    monkeypatch.setattr(api, "_crawl", cancelled)
    caplog.clear()
    with caplog.at_level("INFO", logger="ilias_sync.backend"), pytest.raises(Cancelled):
        api.sync(noop, threading.Event())
    assert any("Sync abgebrochen nach" in r.message for r in caplog.records)
