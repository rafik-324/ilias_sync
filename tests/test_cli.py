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
