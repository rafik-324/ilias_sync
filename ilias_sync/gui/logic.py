"""
Qt freie Hilfslogik der GUI.

Alles hier ist ohne QApplication testbar: Zeitangaben, Benachrichtigungstexte,
Vorschau Baum, Ordnervorschläge, Validierung der Einstellungen.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ilias_sync.api import ActionKind, Config, DownloadedFile, PlannedAction

# ---------------- Zeitangaben ----------------

_WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def relative_time(when: datetime | None, now: datetime | None = None) -> str:
    """Menschlich lesbare Zeitangabe, z. B. "vor 5 Min.", "heute 14:03"."""
    if when is None:
        return "noch nie"
    now = now or datetime.now()
    delta = now - when
    if delta < timedelta(0):
        # Zukunft (z. B. nächster Sync)
        return future_time(when, now)
    if delta < timedelta(minutes=1):
        return "gerade eben"
    if delta < timedelta(hours=1):
        return f"vor {int(delta.total_seconds() // 60)} Min."
    if when.date() == now.date():
        return f"heute {when:%H:%M}"
    if when.date() == (now - timedelta(days=1)).date():
        return f"gestern {when:%H:%M}"
    if delta < timedelta(days=7):
        return f"{_WOCHENTAGE[when.weekday()]} {when:%H:%M}"
    return f"{when:%d.%m.%Y}"


def future_time(when: datetime | None, now: datetime | None = None) -> str:
    """Zeitangabe in der Zukunft, z. B. "in 12 Min.", "heute 15:00"."""
    if when is None:
        return "nicht geplant"
    now = now or datetime.now()
    delta = when - now
    if delta <= timedelta(minutes=1):
        return "gleich"
    if delta < timedelta(hours=1):
        return f"in {math.ceil(delta.total_seconds() / 60)} Min."
    if when.date() == now.date():
        return f"heute {when:%H:%M}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"morgen {when:%H:%M}"
    return f"{when:%d.%m.%Y %H:%M}"


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{size} B"


def course_display_name(ilias_name: str, pattern: str | None = None) -> str:
    """Kursname ohne Semesterzusatz, z. B. "Marketing (HWS 2026)" -> "Marketing"."""
    pattern = pattern or r"\((HWS|FSS) (\d{4})\)"
    try:
        name = re.sub(pattern, "", ilias_name)
    except re.error:
        name = ilias_name
    return " ".join(name.split()).strip() or ilias_name


# ---------------- Benachrichtigungen ----------------

def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def bundle_new_files(files: list[DownloadedFile]) -> str | None:
    """Gebündelter Text für neue Dateien, None wenn nichts neu ist."""
    if not files:
        return None
    per_course = Counter(course_display_name(f.course_name) for f in files)
    total = len(files)
    if len(per_course) == 1:
        course = next(iter(per_course))
        return f"{_plural(total, 'neue Datei', 'neue Dateien')} in {course}"
    return (
        f"{_plural(total, 'neue Datei', 'neue Dateien')} in "
        f"{_plural(len(per_course), 'Kurs', 'Kursen')}"
    )


def unassigned_course_text(names: list[str]) -> str | None:
    if not names:
        return None
    if len(names) == 1:
        return f"Neuer Kurs gefunden: {course_display_name(names[0])}. Bitte im Fenster zuordnen."
    return f"{len(names)} neue Kurse gefunden. Bitte im Fenster zuordnen."


# ---------------- Vorschau Baum ----------------

ACTION_LABELS: dict[ActionKind, str] = {
    ActionKind.NEW: "Neu",
    ActionKind.UPDATE: "Aktualisierung",
    ActionKind.CONFLICT: "Konflikt",
    ActionKind.SKIP_REMOVED: "Übersprungen (entfernt)",
    ActionKind.SKIP_IGNORED: "Übersprungen (ignoriert)",
    ActionKind.CREATE_DIR: "Neuer Ordner",
    ActionKind.UNASSIGNED: "Nicht zugeordnet",
}

ACTION_HINTS: dict[ActionKind, str] = {
    ActionKind.NEW: "Datei wird neu heruntergeladen",
    ActionKind.UPDATE: "In ILIAS geändert. Die alte Version wird zu *_alt_<Datum> umbenannt",
    ActionKind.CONFLICT: "Von dir lokal geändert. Neue Version kommt als *_ilias_<Datum> daneben",
    ActionKind.SKIP_REMOVED: "Von dir verschoben oder gelöscht, wird nicht erneut geladen",
    ActionKind.SKIP_IGNORED: "Liegt in einem ignorierten Ordner",
    ActionKind.CREATE_DIR: "Ordner wird angelegt",
    ActionKind.UNASSIGNED: "Kurs ist noch keinem Ordner zugeordnet, Ablage unter _Neue_Kurse",
}

SKIP_KINDS = {ActionKind.SKIP_REMOVED, ActionKind.SKIP_IGNORED}


@dataclass
class TreeNode:
    name: str
    action: PlannedAction | None = None
    children: dict[str, "TreeNode"] = field(default_factory=dict)

    def child(self, name: str) -> "TreeNode":
        if name not in self.children:
            self.children[name] = TreeNode(name)
        return self.children[name]

    def sorted_children(self) -> list["TreeNode"]:
        # Ordner vor Dateien, dann alphabetisch
        return sorted(
            self.children.values(),
            key=lambda n: (n.action is not None and not n.children, n.name.lower()),
        )

    def count_files(self) -> int:
        own = 1 if self.action is not None and self.action.kind != ActionKind.CREATE_DIR else 0
        return own + sum(c.count_files() for c in self.children.values())


def build_preview_tree(
    actions: list[PlannedAction], root: Path | None, only_changes: bool = False
) -> TreeNode:
    """Baut einen Baum nach lokalem Zielpfad, relativ zum Uni Ordner."""
    tree = TreeNode("")
    for action in actions:
        if only_changes and action.kind in SKIP_KINDS:
            continue
        path = action.local_path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        node = tree
        for part in path.parts:
            node = node.child(part)
        node.action = action
    return tree


@dataclass
class PreviewSummary:
    new: int = 0
    new_bytes: int = 0
    updates: int = 0
    conflicts: int = 0
    skipped: int = 0
    unassigned: int = 0
    new_dirs: int = 0

    def text(self) -> str:
        parts = []
        new = _plural(self.new, "neue Datei", "neue Dateien")
        if self.new_bytes:
            new += f" ({format_size(self.new_bytes)})"
        parts.append(new)
        if self.updates:
            parts.append(_plural(self.updates, "Aktualisierung", "Aktualisierungen"))
        if self.conflicts:
            parts.append(_plural(self.conflicts, "Konflikt", "Konflikte"))
        if self.unassigned:
            parts.append(f"{self.unassigned} nicht zugeordnet")
        if self.new_dirs:
            parts.append(_plural(self.new_dirs, "neuer Ordner", "neue Ordner"))
        if self.skipped:
            parts.append(f"{self.skipped} übersprungen")
        return ", ".join(parts)


def summarize(actions: list[PlannedAction]) -> PreviewSummary:
    s = PreviewSummary()
    for a in actions:
        if a.kind == ActionKind.NEW:
            s.new += 1
            s.new_bytes += a.size or 0
        elif a.kind == ActionKind.UPDATE:
            s.updates += 1
        elif a.kind == ActionKind.CONFLICT:
            s.conflicts += 1
        elif a.kind in SKIP_KINDS:
            s.skipped += 1
        elif a.kind == ActionKind.UNASSIGNED:
            s.unassigned += 1
        elif a.kind == ActionKind.CREATE_DIR:
            s.new_dirs += 1
    return s


# ---------------- Ordnernamen ----------------

_PREFIX = re.compile(r"^(\d+)_")


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def next_number(existing: list[str]) -> int:
    """Nächste freie Nummer nach den vorhandenen NN_ Präfixen."""
    numbers = [int(m.group(1)) for name in existing if (m := _PREFIX.match(name))]
    return max(numbers, default=0) + 1


def suggest_course_dir(ilias_name: str, existing: list[str], pattern: str | None = None) -> str:
    """Vorschlag "NN_kursname": nächste freie Nummer, klein, Leerzeichen zu _."""
    name = _nfc(course_display_name(ilias_name, pattern)).lower()
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", "_", name).strip("._")
    return f"{next_number(existing):02d}_{name or 'kurs'}"


# ---------------- Validierung ----------------

def validate_settings(cfg: Config) -> list[str]:
    """Prüft die Einstellungen, liefert verständliche Fehlertexte (leer = ok)."""
    errors: list[str] = []
    if cfg.base_dir is None or not str(cfg.base_dir).strip():
        errors.append("Bitte einen Basisordner wählen.")
    name = cfg.root_folder_name.strip()
    if not name:
        errors.append("Der Name des Uni Ordners darf nicht leer sein.")
    elif re.search(r'[<>:"/\\|?*]', name) or name.endswith((".", " ")):
        errors.append('Der Name des Uni Ordners enthält unzulässige Zeichen (<>:"/\\|?* oder Punkt am Ende).')
    if not 15 <= cfg.sync_interval_minutes <= 1440:
        errors.append("Das Intervall muss zwischen 15 und 1440 Minuten liegen.")
    if not 1 <= cfg.max_parallel <= 5:
        errors.append("Die Parallelität muss zwischen 1 und 5 liegen.")
    error = regex_error(cfg.semester_pattern)
    if error:
        errors.append(f"Semester Muster ungültig: {error}")
    return errors


def regex_error(pattern: str) -> str | None:
    """Fehlertext für ein ungültiges Regex Muster, sonst None."""
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return str(e)
    if compiled.groups < 2:
        return "Das Muster braucht zwei Gruppen: Semesterart und Jahr."
    return None


def relative_to_root(path: Path, root: Path | None) -> str:
    """Pfad relativ zum Uni Ordner für die Anzeige."""
    if root is not None:
        try:
            return str(path.relative_to(root).parent)
        except ValueError:
            pass
    return str(path.parent)


def base_dir_candidates(home: Path | None = None) -> list[Path]:
    """Übliche Orte, unter denen ein vorhandener Uni Ordner liegen kann."""
    home = home or Path.home()
    return [
        home / "OneDrive",
        home / "OneDrive" / "Dokumente",
        home / "OneDrive" / "Documents",
        home / "Documents",
        home / "Dokumente",
        home / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
        home,
    ]


def find_existing_base(root_folder_name: str, home: Path | None = None) -> Path | None:
    """Basisordner, unter dem der Uni Ordner schon existiert, sonst None."""
    target = _nfc(root_folder_name)
    for candidate in base_dir_candidates(home):
        try:
            if not candidate.is_dir():
                continue
            for child in candidate.iterdir():
                if child.is_dir() and _nfc(child.name) == target:
                    return candidate
        except OSError:
            continue
    return None


def contents_summary(flags: list[tuple[str, bool]]) -> str:
    """Kurzfassung der aktiven Inhaltstypen, z. B. "Alles" oder "Ohne Videos"."""
    off = [label for label, on in flags if not on]
    if not off:
        return "Alles"
    if len(off) == len(flags):
        return "Nichts"
    if len(off) <= 2:
        return "Ohne " + " und ".join(off)
    return f"{len(flags) - len(off)} von {len(flags)}"
