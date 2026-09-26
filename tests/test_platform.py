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


@pytest.mark.parametrize("name, expected", [
    ("Ü1_MfN_Übungsskript_Kernlösung.pdf", "ue1_mfn_uebungsskript_kernloesung.pdf"),
    ("Linux: Folien und Übung", "linux_folien_und_uebung"),
    ("Vorlesungsskript (1).PDF", "vorlesungsskript_1.pdf"),
    ("Café Übersicht – Teil 2.pptx", "cafe_uebersicht_teil_2.pptx"),
    ("Große Übung", "grosse_uebung"),
    ("_Ohne_Semester", "_ohne_semester"),
    (".emacs", "emacs"),
    ("-rf", "rf"),
    ("CON.txt", "_con.txt"),
    ("???", "_"),
])
def test_linux_name(name, expected):
    assert pu.linux_name(name) == expected


def test_linux_name_only_allowed_characters():
    import re

    for name in ("Ä Ö Ü ä ö ü ß", "a\tb\nc", "日本語.pdf", "x/y\\z:*?\"<>|", "  spaces  .txt  "):
        assert re.fullmatch(r"[a-z0-9._-]+", pu.linux_name(name)), name


def test_new_file_goes_into_existing_folder_with_old_spelling(tmp_path):
    """Nur künftige Namen ändern sich: vorhandene Ordner mit alter Schreibweise werden weiter benutzt."""
    from ilias_sync import mapper
    from ilias_sync.api import Config
    from ilias_sync.crawler import Course, CrawlResult, IliasObject

    root = tmp_path
    (root / "01_SEMESTER/01_marketing/02_Übung/Zusatzmaterialien Case Study").mkdir(parents=True)
    (root / "_Ohne_Semester/License Information (Student University of Mannheim)").mkdir(parents=True)
    mk = Course("crs", 1, "Marketing [V] (HWS 2026)", "u1", "HWS 2026", "HWS 2026")
    lic = Course("crs", 2, "License Information (Student University of Mannheim)", "u2", None, "MSDNAA")
    objs = {
        1: [IliasObject("fold", 10, "Übung", "u10", path=[mk.name]),
            IliasObject("fold", 11, "Zusatzmaterialien Case Study", "u11", path=[mk.name, "Übung"]),
            IliasObject("file", 12, "Neuer Case Ü3.pdf", "u12", path=[mk.name, "Übung", "Zusatzmaterialien Case Study"])],
        2: [IliasObject("file", 20, "Neue Anleitung.pdf", "u20", path=[lic.name])],
    }
    result = mapper.Mapper(Config(base_dir=None), root).map_crawl(CrawlResult([mk, lic], objs))
    got = {it.obj.ref_id: "/".join(it.rel_path) for it in result.items}
    assert got[12] == "01_SEMESTER/01_marketing/02_Übung/Zusatzmaterialien Case Study/neuer_case_ue3.pdf"
    assert got[20] == "_Ohne_Semester/License Information (Student University of Mannheim)/neue_anleitung.pdf"
    assert all(not it.new_dirs for it in result.items)
