"""
Plattformdetails: Dateinamen, Unicode, Pfadlängen.

Alle Namen werden in NFC geschrieben und verglichen (macOS liefert NFD).
Die Regeln für Dateinamen sind die strengeren von Windows, damit ein Ordner
auf beiden Systemen gleich aussieht.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import unicodedata
from pathlib import Path

FORBIDDEN_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
# Windows Grenze ist 260, etwas Luft für .part Endung und Versionszusätze
MAX_PATH_LENGTH = 250


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def sanitize_name(name: str, replacement: str = "_") -> str:
    """Datei oder Ordnernamen für Windows und macOS gültig machen."""
    name = nfc(name)
    name = "".join(replacement if c in FORBIDDEN_CHARS or ord(c) < 32 else c for c in name)
    # Leerzeichen und Punkte am Ende sind unter Windows nicht erlaubt
    name = name.strip().rstrip(". ")
    if not name:
        return replacement
    if name.split(".")[0].upper() in RESERVED_NAMES:
        name = replacement + name
    return name


def fit_path(path: Path, limit: int = MAX_PATH_LENGTH) -> Path:
    """Dateinamen kürzen (Endung bleibt), bis der ganze Pfad unter limit Zeichen liegt."""
    excess = len(str(path)) - limit
    if excess <= 0:
        return path
    stem, suffix = path.stem, path.suffix
    if excess >= len(stem):
        raise ValueError(f"Pfad zu lang, auch gekürzt nicht unter {limit} Zeichen: {path.parent}")
    return path.with_name(stem[: len(stem) - excess].rstrip(". ") + suffix)


# ---------------- Autostart ----------------
# macOS: LaunchAgent in ~/Library/LaunchAgents, Windows: Registry HKCU\...\Run.
# Beide Einträge gehören dem Tool selbst, Abschalten entfernt nur diesen Eintrag.

AUTOSTART_LABEL = "de.uni-mannheim.ilias-sync"
AUTOSTART_NAME = "ILIAS Sync"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command() -> list[str]:
    """Programmaufruf für den Autostart: gebaute App direkt, sonst Python mit dem Paket."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "ilias_sync"]


def launch_agent_file() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AUTOSTART_LABEL}.plist"


def autostart_enabled() -> bool:
    if sys.platform == "darwin":
        return launch_agent_file().exists()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except OSError:
            return False
    return False


def set_autostart(enabled: bool) -> None:
    """Autostart an oder aus. Wirft OSError, wenn das System es nicht zulässt."""
    if sys.platform == "darwin":
        path = launch_agent_file()
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            plist = {
                "Label": AUTOSTART_LABEL,
                "ProgramArguments": autostart_command(),
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            }
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(plistlib.dumps(plist))
            os.replace(tmp, path)
        else:
            path.unlink(missing_ok=True)
    elif sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                command = subprocess.list2cmdline(autostart_command())
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    else:
        raise OSError("Autostart wird auf diesem System nicht unterstützt.")
