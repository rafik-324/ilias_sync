"""Tests für die Zugangsdaten Logik im CLI (__main__.py)."""

import pytest

from ilias_sync import __main__ as cli
from ilias_sync import auth, config
from ilias_sync.api import Config


@pytest.fixture
def no_input(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError("es darf nichts abgefragt werden")

    monkeypatch.setattr("builtins.input", fail)
    monkeypatch.setattr(cli.getpass, "getpass", fail)


def test_everything_stored_asks_nothing(monkeypatch, no_input):
    monkeypatch.setattr(auth, "load_password", lambda u: "geheim" if u == "nutzer1" else None)
    creds = cli.console_credentials(Config(base_dir=None, username="nutzer1"))
    assert (creds.user, creds.password, creds.remember) == ("nutzer1", "geheim", False)


def test_stored_user_only_asks_password(monkeypatch):
    monkeypatch.setattr(auth, "load_password", lambda u: None)
    answers = iter(["j"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "neu")
    creds = cli.console_credentials(Config(base_dir=None, username="nutzer1"))
    assert (creds.user, creds.password, creds.remember) == ("nutzer1", "neu", True)


def test_no_user_asks_user(monkeypatch):
    monkeypatch.setattr(auth, "load_password", lambda u: "geheim")
    monkeypatch.setattr("builtins.input", lambda prompt="": " nutzer2 ")
    creds = cli.console_credentials(Config(base_dir=None))
    assert creds.user == "nutzer2"


def test_remember_username_saves_once(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "config_file", lambda: path)
    cfg = Config(base_dir=None)

    cli.remember_username(cfg, "nutzer1")
    assert config.load_config(path).username == "nutzer1"

    mtime = path.stat().st_mtime_ns
    cli.remember_username(cfg, "nutzer1")
    assert path.stat().st_mtime_ns == mtime


def test_remember_username_without_login_does_nothing(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "config_file", lambda: path)
    cli.remember_username(Config(base_dir=None), None)
    assert not path.exists()


def test_test_target_must_not_be_inside_semester(tmp_path):
    root = tmp_path / "01_Universität_Mannheim"
    (root / "01_SEMESTER" / "01_marketing").mkdir(parents=True)
    (root / "code_projects").mkdir()
    assert cli.check_test_target(root / "_ILIAS_Sync_Test", root) is None
    assert cli.check_test_target(root, root) is not None
    assert cli.check_test_target(root / "01_SEMESTER", root) is not None
    assert "01_SEMESTER" in cli.check_test_target(root / "01_SEMESTER" / "01_marketing" / "x", root)


def test_format_plan_summary_and_details(tmp_path):
    from datetime import datetime

    from ilias_sync import sync
    from ilias_sync.crawler import Course, IliasObject
    from ilias_sync.mapper import MappedItem

    course = Course("crs", 1, "Marketing [V] (HWS 2026)", "u", "HWS 2026", "HWS 2026")

    def action(kind, name, size=None, mtime=None, new_dirs=None, modified=False):
        obj = IliasObject("file", 1, name, "u", [course.name], size, mtime)
        rel = ("01_SEMESTER", "01_marketing", name)
        item = MappedItem(obj, course, rel, None, True, new_dirs or set())
        return sync.FileAction(kind, item, tmp_path.joinpath(*rel), user_modified=modified)

    actions = [
        action(sync.Kind.NEW, "neu.pdf", size=2048, new_dirs={("01_SEMESTER",)}),
        action(sync.Kind.CHANGED, "geaendert.pdf", mtime=datetime(2026, 10, 3, 9, 5), modified=True),
        action(sync.Kind.UNCHANGED, "alt.pdf"),
    ]
    lines = cli.format_plan(actions, tmp_path)
    text = "\n".join(lines)
    assert "Neu laden: 1 (2.0 KB)" in text
    assert "In ILIAS geändert (wird nur gemeldet, nicht geladen): 1" in text
    assert "Unverändert: 1" in text
    assert "Davon von dir bearbeitet (bleiben unangetastet): 1" in text
    assert "  01_SEMESTER/01_marketing/neu.pdf   2.0 KB   (neuer Ordner)" in lines
    assert "  01_SEMESTER/01_marketing/geaendert.pdf   neu in ILIAS: 03.10.2026 09:05" in lines
    assert "alt.pdf" not in text
    assert cli.format_plan(actions, tmp_path, details=False) == lines[: len(cli.format_plan(actions, tmp_path, False))]
