"""Autostart Tests: LaunchAgent (macOS) und Registry (Windows), ohne das echte System anzufassen."""

import plistlib
import sys
import types

import pytest

from ilias_sync import platform_utils as pu


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(pu.sys, "platform", "darwin")
    monkeypatch.setattr(pu.Path, "home", lambda: tmp_path)
    return tmp_path


def test_mac_autostart_on_and_off(mac):
    assert not pu.autostart_enabled()
    pu.set_autostart(True)
    path = mac / "Library" / "LaunchAgents" / "de.uni-mannheim.ilias-sync.plist"
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == "de.uni-mannheim.ilias-sync"
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"] == [sys.executable, "-m", "ilias_sync"]
    assert pu.autostart_enabled()
    pu.set_autostart(True)   # zweimal an ist kein Fehler
    pu.set_autostart(False)
    assert not path.exists() and not pu.autostart_enabled()
    pu.set_autostart(False)  # zweimal aus auch nicht


def test_frozen_app_starts_itself(monkeypatch):
    monkeypatch.setattr(pu.sys, "frozen", True, raising=False)
    assert pu.autostart_command() == [sys.executable]


class FakeWinreg(types.SimpleNamespace):
    """Nachbildung der benötigten winreg Funktionen, Werte in einem dict."""

    def __init__(self):
        super().__init__(HKEY_CURRENT_USER="HKCU", KEY_SET_VALUE=2, REG_SZ=1, values={})

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802
        assert (root, path) == ("HKCU", pu.WINDOWS_RUN_KEY)
        return self._Key()

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, typ, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_windows_autostart_on_and_off(monkeypatch):
    reg = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(pu.sys, "platform", "win32")
    monkeypatch.setattr(pu, "autostart_command", lambda: [r"C:\Program Files\ILIAS Sync\ILIAS Sync.exe"])
    assert not pu.autostart_enabled()
    pu.set_autostart(True)
    assert reg.values["ILIAS Sync"] == r'"C:\Program Files\ILIAS Sync\ILIAS Sync.exe"'
    assert pu.autostart_enabled()
    pu.set_autostart(False)
    assert not pu.autostart_enabled()
    pu.set_autostart(False)


def test_other_systems(monkeypatch):
    monkeypatch.setattr(pu.sys, "platform", "linux")
    assert not pu.autostart_enabled()
    with pytest.raises(OSError):
        pu.set_autostart(True)
