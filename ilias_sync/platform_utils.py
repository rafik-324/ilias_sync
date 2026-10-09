"""
Plattformdetails: Dateinamen, Unicode, Pfadlängen.

Alle Namen werden in NFC geschrieben und verglichen (macOS liefert NFD).
Die Regeln für Dateinamen sind die strengeren von Windows, damit ein Ordner
auf beiden Systemen gleich aussieht.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
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


# Umlaute vor dem Entfernen von Akzenten ersetzen, sonst würde aus "ü" nur "u"
TRANSLITERATION = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def linux_name(name: str) -> str:
    """Name nach Linux Standard (Entscheidung des Nutzers 2026-09-26): nur Kleinbuchstaben,
    Ziffern, "_", "-" und ".". Leerzeichen und andere Zeichen werden zu "_", Umlaute zu
    ae/oe/ue/ss. "Ü1_MfN_Übungsskript Kernlösung.PDF" -> "ue1_mfn_uebungsskript_kernloesung.pdf".
    Ein führender "_" bleibt (eigene Ordner wie _ohne_semester), ein führender Punkt oder
    Bindestrich nicht (versteckte Dateien, Verwechslung mit Optionen)."""
    name = nfc(name).lower()
    for a, b in TRANSLITERATION:
        name = name.replace(a, b)
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z0-9._-]+", "_", name)
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"[_-]+\.", ".", name)        # "skript_(1).pdf" -> "skript_1.pdf"
    name = re.sub(r"\.[_-]+", ".", name)
    name = name.lstrip(".-").rstrip("._-")
    return sanitize_name(name) if name else "_"


# Neu angelegte Ordner höchstens so lang, damit auch tiefe ILIAS Strukturen unter einem
# langen Basisordner (z. B. in OneDrive) unter MAX_PATH_LENGTH bleiben
MAX_DIR_NAME = 40


def short_dir_name(name: str, limit: int = MAX_DIR_NAME) -> str:
    """Zu langen neuen Ordnernamen kürzen, stabil: am Wortende abschneiden und einen kurzen
    Hash des vollen Namens anhängen. Gleicher Name ergibt immer dasselbe Ergebnis, zwei
    verschiedene lange Namen mit gleichem Anfang kollidieren nicht.
    "09a_objektorientierte_programmierung_fortgeschrittene_konzepte" -> "09a_objektorientierte_programmierung_1c2d"."""
    if len(name) <= limit:
        return name
    tag = hashlib.sha256(nfc(name).encode("utf-8")).hexdigest()[:4]
    room = limit - len(tag) - 1
    cut = name[:room]
    # Endet der Schnitt genau vor einem Trenner, bleibt das letzte Wort ganz
    if name[room] not in "_- ":
        boundary = max(cut.rfind("_"), cut.rfind("-"), cut.rfind(" "))
        if boundary >= room // 2:
            cut = cut[:boundary]
    return f"{cut.rstrip('._- ')}_{tag}"


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
    """Programmaufruf für den Autostart: gebaute App direkt, sonst Python mit dem Paket.
    --autostart: nur Menüleiste bzw. Tray, ohne Fenster (ein Start per Doppelklick öffnet das Fenster)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--autostart"]
    return [sys.executable, "-m", "ilias_sync", "--autostart"]


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
                # nach einem Absturz neu starten, damit das Symbol in der Menüleiste bleibt.
                # "Beenden" im Menü (Exit Code 0) bleibt beendet
                "KeepAlive": {"SuccessfulExit": False},
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
