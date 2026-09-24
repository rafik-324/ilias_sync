"""
Plattformdetails: Dateinamen, Unicode, Pfadlängen.

Alle Namen werden in NFC geschrieben und verglichen (macOS liefert NFD).
Die Regeln für Dateinamen sind die strengeren von Windows, damit ein Ordner
auf beiden Systemen gleich aussieht.
"""

from __future__ import annotations

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
