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


def test_first_sync_refuses_non_empty_root_before_login(monkeypatch, tmp_path, capsys):
    root = tmp_path / "Uni"
    (root / "01_SEMESTER").mkdir(parents=True)
    monkeypatch.setattr(cli, "load_config_or_report", lambda: Config(base_dir=tmp_path, root_folder_name="Uni"))
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "daten")

    def no_login(cfg):
        raise AssertionError("kein Login, wenn der Ordner nicht passt")

    monkeypatch.setattr(cli, "open_session", no_login)
    for dry_run in (True, False):
        assert cli.cmd_sync(dry_run, None) == 1
        assert "leeren" in capsys.readouterr().out
