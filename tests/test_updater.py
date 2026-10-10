"""Tests für App Updates (updater.py, Backend Hüllen, Config). Keine Live Requests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path, PurePosixPath

import pytest
import requests

from ilias_sync import backend, config, updater
from ilias_sync.api import Cancelled, Config, IliasSyncError, NetworkError, UpdateInfo

PAYLOAD = b"installer-inhalt " * 5000
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


class FakeResponse:
    def __init__(self, status=200, data=None, body=b"", url="https://example.org/datei"):
        self.status_code = status
        self._data = data
        self._body = body
        self.url = url

    def json(self):
        if self._data is None:
            raise ValueError("kein JSON")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def iter_content(self, size):
        for i in range(0, len(self._body), 1000):
            yield self._body[i:i + 1000]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def server(monkeypatch):
    """Ersetzt _get. Manifest und Datei frei einstellbar, Aufrufe werden mitgeschrieben."""
    state = {"manifest": None, "status": 200, "body": PAYLOAD, "calls": [], "error": None}

    def fake_get(url, **kw):
        state["calls"].append((url, kw))
        if state["error"]:
            raise state["error"]
        if url == updater.UPDATE_URL:
            return FakeResponse(state["status"], state["manifest"])
        return FakeResponse(200, body=state["body"], url=url)

    monkeypatch.setattr(updater, "_get", fake_get)
    monkeypatch.setattr(updater, "UPDATE_URL", "https://update.example.org/api/update")
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    return state


def manifest(version="0.2.0", **extra):
    data = {"version": version, "notes": "Neu: Updates", "size": len(PAYLOAD), "sha256": PAYLOAD_SHA,
            "published_at": "2026-10-10T12:00:00Z"}
    data.update(extra)
    return data


# ---------------- Versionen ----------------

@pytest.mark.parametrize("remote, local, newer", [
    ("0.10.0", "0.9.1", True),
    ("0.2.0", "0.1.0", True),
    ("1.0", "0.99.99", True),
    ("0.1.0", "0.1.0", False),
    ("0.1", "0.1.0", False),
    ("0.1.0", "0.2.0", False),
    ("0.1.10", "0.1.9", True),
])
def test_is_newer(remote, local, newer):
    assert updater.is_newer(remote, local) is newer


@pytest.mark.parametrize("bad", ["", "v1.0", "1.0-beta", "1..0", "1.0.0.0.0", "../1"])
def test_parse_version_rejects(bad):
    with pytest.raises(ValueError):
        updater.parse_version(bad)


def test_installer_names():
    assert updater.installer_name("0.2.0", "mac") == "ILIAS-Sync-0.2.0.dmg"
    assert updater.installer_name("0.2.0", "win") == "ILIAS-Sync-0.2.0-windows.zip"


# ---------------- Prüfen ----------------

def test_check_newer(server):
    server["manifest"] = manifest("0.2.0")
    info = updater.check("0.1.0")
    assert info.version == "0.2.0" and info.notes == "Neu: Updates" and info.size == len(PAYLOAD)
    assert info.published_at.year == 2026
    url, kw = server["calls"][0]
    assert kw["params"] == {"platform": "mac", "version": "0.1.0"}
    assert kw["headers"]["X-App-Key"] == updater.APP_KEY


def test_check_current(server):
    server["manifest"] = manifest("0.1.0", published_at=None)
    assert updater.check("0.1.0") is None
    # ältere Version auf dem Server wird angeboten (Zurückrollen eines fehlerhaften Release)
    server["manifest"] = manifest("0.0.9")
    info = updater.check("0.1.0")
    assert info is not None and info.version == "0.0.9"


@pytest.mark.parametrize("status", [403, 404])
def test_check_403_404_means_no_update(server, status):
    server["status"] = status
    assert updater.check("0.1.0") is None


def test_check_windows_platform(server, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    server["manifest"] = manifest("0.2.0")
    updater.check("0.1.0")
    assert server["calls"][0][1]["params"]["platform"] == "win"


def test_check_other_platform_asks_nothing(server, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert updater.check("0.1.0") is None
    assert server["calls"] == []


def test_check_invalid_answer(server):
    server["manifest"] = {"version": "neueste"}
    with pytest.raises(NetworkError):
        updater.check("0.1.0")


def test_check_network_error_via_backend(server):
    server["error"] = requests.ConnectionError("https://geheim.example.org/?token=abc")
    with pytest.raises(NetworkError) as e:
        backend.Api().check_for_update()
    assert "token" not in str(e.value)


def test_check_server_error_via_backend(server):
    server["status"] = 500
    with pytest.raises(NetworkError):
        backend.Api().check_for_update()


def test_http_only_for_localhost(server, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_URL", "http://update.example.org/api/update")
    with pytest.raises(IliasSyncError):
        updater.check("0.1.0")
    monkeypatch.setattr(updater, "UPDATE_URL", "http://127.0.0.1:8123/api/update")
    server["manifest"] = manifest("0.2.0")
    assert updater.check("0.1.0").version == "0.2.0"


# ---------------- Laden ----------------

def info():
    return UpdateInfo(version="0.2.0", notes="", size=len(PAYLOAD), published_at=None)


def test_download_ok(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/signiert?token=x")
    seen = []
    path = updater.download(info(), seen.append, threading.Event(), tmp_path)
    assert path == tmp_path / "ILIAS-Sync-0.2.0.dmg"
    assert path.read_bytes() == PAYLOAD
    assert not list(tmp_path.glob("*.part"))
    assert seen[-1].phase == "Update laden" and seen[-1].current == len(PAYLOAD) == seen[-1].total
    # Manifest mit download=1, die Datei ohne App Schlüssel
    assert server["calls"][0][1]["params"]["download"] == "1"
    file_url, file_kw = server["calls"][1]
    assert "X-App-Key" not in file_kw.get("headers", {})


def test_download_windows_name(server, tmp_path, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    server["manifest"] = manifest(url="https://storage.example.org/x")
    path = updater.download(info(), lambda p: None, threading.Event(), tmp_path)
    assert path.name == "ILIAS-Sync-0.2.0-windows.zip"


def test_download_wrong_checksum_deletes(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/x", sha256="0" * 64)
    with pytest.raises(IliasSyncError, match="Prüfsumme"):
        updater.download(info(), lambda p: None, threading.Event(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_larger_than_announced(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/x", size=100)
    with pytest.raises(IliasSyncError):
        updater.download(info(), lambda p: None, threading.Event(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_cancel(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/x")
    cancel = threading.Event()

    def progress(p):
        if p.current > 10_000:
            cancel.set()

    with pytest.raises(Cancelled):
        updater.download(info(), progress, cancel, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_rejects_http_link(server, tmp_path):
    server["manifest"] = manifest(url="http://storage.example.org/x")
    with pytest.raises(IliasSyncError):
        updater.download(info(), lambda p: None, threading.Event(), tmp_path)
    assert len(server["calls"]) == 1


def test_download_missing_checksum(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/x", sha256=None)
    with pytest.raises(NetworkError):
        updater.download(info(), lambda p: None, threading.Event(), tmp_path)


def test_download_via_backend_uses_data_dir(server, tmp_path):
    server["manifest"] = manifest(url="https://storage.example.org/x")
    api = backend.Api(data_dir=tmp_path)
    path = api.download_update(info(), lambda p: None, threading.Event())
    assert path == tmp_path / "updates" / "ILIAS-Sync-0.2.0.dmg"


def test_updates_dir_follows_home(monkeypatch, tmp_path):
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    assert updater.updates_dir() == tmp_path / "data" / "updates"


# ---------------- Selbst aktualisieren ----------------

def test_can_self_update_from_source():
    assert not getattr(sys, "frozen", False)
    assert updater.can_self_update() is False
    assert backend.Api().can_self_update() is False


def test_install_refused_from_source(tmp_path):
    with pytest.raises(IliasSyncError):
        backend.Api().install_update(tmp_path / "x.dmg")


def test_app_location_mac(monkeypatch, tmp_path):
    exe = tmp_path / "Programme" / "ILIAS Sync.app" / "Contents" / "MacOS" / "ILIAS Sync"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(exe))
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    assert updater.app_location() == (tmp_path / "Programme" / "ILIAS Sync.app").resolve()
    assert updater.can_self_update() is True


def test_app_location_win(monkeypatch, tmp_path):
    exe = tmp_path / "ILIAS Sync" / "ILIAS Sync.exe"
    exe.parent.mkdir()
    exe.touch()
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(exe))
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert updater.app_location() == exe.parent.resolve()


def test_app_version():
    from ilias_sync import __version__
    assert backend.Api().app_version() == __version__


# ---------------- Hilfsskripte (nur erzeugen, nie ausführen) ----------------

ODD = "Prog ramme/Ünï 'quote' $HOME `x`"


def test_mac_script_quotes_paths(tmp_path):
    # PurePosixPath: macOS Pfade, auch wenn der Test unter Windows läuft
    app = PurePosixPath("/Users/max/" + ODD + "/ILIAS Sync.app")
    dmg = PurePosixPath("/Users/max/Library/Application Support/ilias-sync/updates/ILIAS-Sync-0.2.0.dmg")
    text = updater.mac_script(4242, app, dmg, PurePosixPath("/tmp/a b/log.txt"))
    assert "PID=4242" in text
    assert "APP='/Users/max/Prog ramme/Ünï '\"'\"'quote'\"'\"' $HOME `x`/ILIAS Sync.app'" in text
    assert "DMG='/Users/max/Library/Application Support/ilias-sync/updates/ILIAS-Sync-0.2.0.dmg'" in text
    assert "hdiutil attach -nobrowse -readonly" in text and "ditto" in text and ".Trash" in text
    assert "xattr -dr com.apple.quarantine" in text and 'open "$APP"' in text
    # Nur Syntax prüfen, nichts ausführen
    script = tmp_path / "s.sh"
    script.write_text(text, encoding="utf-8")
    # Unter Windows wäre "bash" meist WSL, das Windows Pfade nicht versteht
    if sys.platform != "win32" and shutil.which("bash"):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_win_script_quotes_paths():
    app = Path("C:/Users/Max Müller/" + ODD + "/ILIAS Sync")
    text = updater.win_script(4242, app, Path("C:/Daten/ILIAS-Sync-0.2.0-windows.zip"),
                              Path("C:/Daten/log.txt"), "ILIAS Sync.exe")
    a = str(app).replace("'", "''")
    assert "$AppPid = 4242" in text
    assert f"$App = '{a}'" in text
    assert f"$Neu = '{a}.neu'" in text and f"$Alt = '{a}.alt'" in text
    assert "Expand-Archive -LiteralPath $Zip" in text
    assert "Remove-Item -LiteralPath $Alt" in text
    # Uninstaller des Inno Setup Installers bleibt erhalten
    assert "-Filter 'unins*'" in text and "Copy-Item -Destination $Src" in text
    # Gelöscht werden nur $Neu und $Alt
    for line in text.splitlines():
        if "Remove-Item" in line:
            assert "-LiteralPath $Neu" in line or "-LiteralPath $Alt" in line


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Prozess Flags")
def test_win_helper_flags(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: calls.append(kw["creationflags"]))
    updater._start_win_helper(["powershell.exe"], tmp_path)
    flags = calls[0]
    # DETACHED_PROCESS lässt powershell.exe sofort sterben (im Test auf Windows gesehen)
    assert not flags & subprocess.DETACHED_PROCESS
    assert flags & subprocess.CREATE_NO_WINDOW and flags & subprocess.CREATE_BREAKAWAY_FROM_JOB


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Prozess Flags")
def test_win_helper_without_breakaway(monkeypatch, tmp_path):
    calls = []

    def popen(args, **kw):
        calls.append(kw["creationflags"])
        if kw["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(13, "Zugriff verweigert")

    monkeypatch.setattr(updater.subprocess, "Popen", popen)
    updater._start_win_helper(["powershell.exe"], tmp_path)
    assert len(calls) == 2 and not calls[1] & subprocess.CREATE_BREAKAWAY_FROM_JOB


# ---------------- Config ----------------

def test_config_check_updates_roundtrip(tmp_path):
    path = tmp_path / "config.toml"
    cfg = Config(base_dir=tmp_path, check_updates=False)
    config.save_config(cfg, path)
    assert "check_updates = false" in path.read_text(encoding="utf-8")
    assert config.load_config(path).check_updates is False


def test_config_without_check_updates(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\nbase_dir = "/tmp/uni"\n', encoding="utf-8")
    assert config.load_config(path).check_updates is True


def test_config_check_updates_must_be_bool(tmp_path):
    from ilias_sync.api import ConfigError
    path = tmp_path / "config.toml"
    path.write_text('[general]\ncheck_updates = "ja"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="check_updates"):
        config.load_config(path)


# ---------------- CLI und Release Hilfen ----------------

def test_cli_check_update(server, capsys, monkeypatch, tmp_path):
    from ilias_sync import __main__ as cli
    monkeypatch.setenv(config.HOME_ENV, str(tmp_path))
    server["manifest"] = manifest("99.0.0")
    assert cli.main(["--check-update"]) == 0
    out = capsys.readouterr().out
    assert "Update verfügbar: 99.0.0" in out and "Neu: Updates" in out
    # gleiche Version wie installiert: aktuell
    from ilias_sync import __version__
    server["manifest"] = manifest(__version__)
    assert cli.main(["--check-update"]) == 0
    assert "aktuell (" in capsys.readouterr().out
    # ältere Version: wird angeboten (Zurückrollen)
    server["manifest"] = manifest("0.0.1")
    assert cli.main(["--check-update"]) == 0
    assert "Update verfügbar: 0.0.1" in capsys.readouterr().out


def _load(name):
    path = Path(__file__).resolve().parent.parent / "packaging" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_win_zip(tmp_path):
    app = tmp_path / "dist" / "ILIAS Sync"
    (app / "_internal").mkdir(parents=True)
    (app / "ILIAS Sync.exe").write_bytes(b"exe")
    (app / "_internal" / "lib.dll").write_bytes(b"dll")
    target = _load("make_win_zip").make_zip(app, tmp_path / "out", "0.2.0")
    assert target.name == "ILIAS-Sync-0.2.0-windows.zip"
    with zipfile.ZipFile(target) as zf:
        assert sorted(zf.namelist()) == ["ILIAS Sync/ILIAS Sync.exe", "ILIAS Sync/_internal/lib.dll"]
    info = _load("release_info").release_info(target)
    assert info["size"] == target.stat().st_size
    assert info["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    json.dumps(info)
