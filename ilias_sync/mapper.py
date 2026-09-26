"""
ILIAS Pfad -> lokaler Zielpfad.

Regeln laut CLAUDE.md Abschnitt "Mapping":
  Semester  aus Kursname (oder Dashboard Überschrift) -> Semesterordner laut Config,
            sonst chronologisch den vorhandenen NN_SEMESTER Ordnern zugeordnet
  Kurs      Config Zuordnung per ref_id, sonst Fuzzy Match gegen vorhandene
            Kursordner. Mehrere ILIAS Kurse ([V], [Ü], [T]) landen im selben Ordner.
            Ohne Treffer: Semesterordner/_Neue_Kurse/<ILIAS Kursname>/
  Kategorie Config Regel, sonst Schlüsselwort im ersten ILIAS Ordner (Sammelordner
            wie "Dateien" übersprungen), sonst Kürzel des Kurses, sonst Gruppenname
  Ordner    vorhandene lokale Ordner werden wiederverwendet (Vergleich ohne
            Nummernpräfix, ohne Groß/Kleinschreibung, Umlaute normalisiert)
  Videos    in einen Unterordner NN_Videos des Ordners, in dem sie sonst lägen,
            außer ein übernommener ILIAS Ordner auf dem Weg ist schon ein Video Ordner

Der Mapper liest das lokale Dateisystem nur (Ordnernamen), er legt nichts an.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .api import Config, CourseConfig
from .crawler import Course, CrawlResult, IliasObject
from .platform_utils import linux_name, nfc, short_dir_name

log = logging.getLogger(__name__)

# Neue Namen folgen dem Linux Standard (platform_utils.linux_name), vorhandene Ordner
# werden per Normalisierung wiedergefunden, auch wenn sie anders geschrieben sind
NEW_COURSES_DIR = "_neue_kurse"
VIDEO_DIR = "videos"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv"}
# Ordnernamen, die schon Video Ordner sind (Wortteil, nach fold)
VIDEO_FOLDER_WORDS = ("video", "aufzeichnung", "recording", "screencast")
NO_SEMESTER_DIR = "_ohne_semester"

# Kategorien in Tabellenreihenfolge: (Name, Schlüsselwörter)
CATEGORIES: list[tuple[str, list[str]]] = [
    ("Vorlesung", ["vorlesung", "lecture", "slides", "folien", "skript"]),
    ("Übung", ["übung", "uebung", "exercise", "übungsgruppe"]),
    ("Tutorium", ["tutorium", "tutorien", "tutorial", "tut"]),
    ("Übungsblätter", ["blatt", "sheet", "übungsblatt", "aufgabenblatt"]),
    ("Abgaben", ["abgabe", "submission", "hausaufgabe"]),
]
# Lokaler Ordner, der eine Kategorie darstellt (nach Normalisierung)
CATEGORY_LOCAL_NAMES = {
    "Vorlesung": ["vorlesung", "vorlesungen"],
    "Übung": ["übung", "übungen"],
    "Tutorium": ["tutorium", "tutorien"],
    "Übungsblätter": ["übungsblätter", "übungsblatt"],
    "Abgaben": ["abgaben", "abgabe"],
}
# Reihenfolge der Nummern neuer Kategorieordner. Übungsblätter direkt nach Übung,
# weil sie ohne eigenen lokalen Ordner in den Übungsordner fallen
CATEGORY_NUMBER_ORDER = ["Vorlesung", "Übung", "Übungsblätter", "Tutorium", "Abgaben"]
COURSE_SUFFIX_CATEGORIES ={"v": "Vorlesung", "ü": "Übung", "ue": "Übung", "t": "Tutorium"}
# Allgemeine Sammelordner, zählen bei der Kategorieerkennung nicht als Ebene
TRANSPARENT_FOLDERS = {"dateien", "files", "materialien", "material", "unterlagen"}
# Füllwörter, die einen Ordner nicht von seiner Kategorie unterscheiden
FILLER_WORDS = {"und", "der", "die", "das", "zur", "zum", "fur", "alle", "grosse", "kleine"}
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
NUMBER_PREFIX_RE = re.compile(r"^\d{1,3}[a-z]?[\s_.\-]+", re.IGNORECASE)
SEMESTER_DIR_RE = re.compile(r"^(\d+)_.*semester", re.IGNORECASE)
SEMESTER_WORD_RE = re.compile(r"\b(HWS|FSS)\s+(\d{4})\b")


# ---------------- Normalisierung ----------------

def fold(text: str) -> str:
    """Kleinschreibung, Umlaute und Ersatzschreibweise gleichsetzen (ü = ue = u)."""
    text = nfc(text).lower().replace("ß", "ss")
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ae", "a"), ("oe", "o"), ("ue", "u")):
        text = text.replace(a, b)
    return text


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def strip_number_prefix(name: str) -> str:
    return NUMBER_PREFIX_RE.sub("", name)


def folder_key(name: str) -> str:
    """Vergleichsschlüssel für Ordnernamen: "02_Übung" == "übung" == "2 Uebung".

    Bleibt nach Entfernen der Nummer zu wenig übrig (z. B. "2. PG"), zählt die Nummer mit,
    sonst würden "1. PG" und "2. PG" als gleich gelten."""
    stripped = " ".join(words(strip_number_prefix(name)))
    return stripped if len(stripped) >= 4 else " ".join(words(name))


def loose_folder_key(name: str) -> frozenset[str]:
    """Zweiter Vergleich, wenn folder_key nichts findet: Wörter ohne Reihenfolge,
    Zahlen ohne führende Nullen. So passt "Tutorium 01" zu "01_tutorium"."""
    return frozenset(str(int(w)) if w.isdigit() else w for w in words(name))


def number_prefix(name: str) -> int | None:
    m = re.match(r"^(\d{1,3})_", name)
    return int(m.group(1)) if m else None


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_SUFFIXES


def is_video_folder(name: str) -> bool:
    return any(v in w for w in words(name) for v in VIDEO_FOLDER_WORDS)


def is_ignored(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(nfc(name), nfc(p)) for p in patterns)


# ---------------- Kategorie ----------------

def _keyword_matches(word: str, keyword: str) -> bool:
    # Kurze Schlüsselwörter nur am Wortanfang ("tut" nicht in "institut")
    return word.startswith(keyword) if len(keyword) < 5 else keyword in word


def keyword_category(name: str, preferred: str | None = None) -> str | None:
    """Kategorie aus einem ILIAS Ordner oder Gruppennamen, None wenn kein Schlüsselwort passt.

    Passen mehrere Kategorien, fällt eine weg, deren Schlüsselwort nur Teil eines
    längeren Treffers ist ("übung" in "übungsblatt"). Unter den übrigen entscheidet
    das Kurskürzel, sonst die erste Zeile der Tabelle."""
    ws = words(name)
    matched: dict[str, str] = {}  # Kategorie -> längstes passendes Schlüsselwort
    for category, keywords in CATEGORIES:
        hits = [fold(k) for k in keywords for w in ws if _keyword_matches(w, fold(k))]
        if hits:
            matched[category] = max(hits, key=len)
    candidates = [
        category for category, kw in matched.items()
        if not any(kw != other and kw in other for other in matched.values())
    ]
    if not candidates:
        return None
    if preferred in candidates:
        return preferred
    return candidates[0]


def is_pure_category_name(name: str) -> bool:
    """True, wenn der Ordner nur die Kategorie selbst benennt ("Vorlesung", "Tutorien",
    "Übungsblätter", "Vorlesungsfolien"). Dann wird er zum Kategorieordner. Sonst
    ("Übung 01", "Linux: Folien und Übung") bleibt er als Unterordner erhalten."""
    ws = [w for w in words(name) if w not in FILLER_WORDS]
    all_keywords = [fold(k) for _, keywords in CATEGORIES for k in keywords]
    return bool(ws) and all(any(_keyword_matches(w, k) for k in all_keywords) for w in ws)


def course_suffix_category(course_name: str) -> str | None:
    """"Praktische Informatik I [Ü] [1. PG]" -> "Übung"."""
    for tag in re.findall(r"\[([^\]]+)\]", course_name):
        category = COURSE_SUFFIX_CATEGORIES.get(nfc(tag).strip().lower())
        if category:
            return category
    return None


# ---------------- Kurs Zuordnung ----------------

def _course_words(text: str) -> list[str]:
    return [ROMAN.get(w, w) for w in words(text)]


def ilias_course_words(name: str, semester_pattern: str) -> list[str]:
    """Kursname ohne Semester, ohne [Kürzel] und ohne Zusätze wie "Große Übung" oder "- Tut"."""
    name = re.sub(semester_pattern, " ", name)
    name = re.sub(r"\[[^\]]*\]", " ", name)
    name = re.sub(r"(,\s*)?\b(gro(ß|ss)e|kleine)\s+(Übung|Uebung)\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s-\s*Tut\b.*$", " ", name, flags=re.IGNORECASE)
    return _course_words(name)


def course_match_score(local_dir: str, ilias_words: list[str]) -> float:
    """Anteil der Wörter des lokalen Ordners, die im ILIAS Namen vorkommen (0.0 bis 1.0)."""
    local_words = _course_words(strip_number_prefix(local_dir))
    if not local_words or not ilias_words:
        return 0.0
    compact = "".join(ilias_words)
    found = [w for w in local_words if w in compact]
    return len(found) / len(local_words)


def best_course_dir(ilias_name: str, local_dirs: list[str], semester_pattern: str) -> tuple[str | None, float]:
    """Bester vorhandener Kursordner. Nur ein vollständiger Treffer gilt (alle Wörter
    des Ordners im ILIAS Namen), bei mehreren der mit der längsten Übereinstimmung."""
    ilias_words = ilias_course_words(ilias_name, semester_pattern)
    scored = []
    for d in local_dirs:
        score = course_match_score(d, ilias_words)
        if score >= 1.0:
            coverage = len("".join(_course_words(strip_number_prefix(d))))
            scored.append((coverage, d))
    if not scored:
        return None, 0.0
    return max(scored)[1], 1.0


def course_dir_name(ilias_name: str, semester_pattern: str) -> str:
    """Ordnername ohne Nummer für einen neuen Kurs: "Praktische Informatik I [V] (HWS 2026)"
    -> "praktische_informatik_i". Gleiche Kürzungen wie beim Fuzzy Match."""
    name = re.sub(semester_pattern, " ", ilias_name)
    name = re.sub(r"\[[^\]]*\]", " ", name)
    name = re.sub(r"(,\s*)?\b(gro(ß|ss)e|kleine)\s+(Übung|Uebung)\b", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s-\s*Tut\b.*$", " ", name, flags=re.IGNORECASE)
    # Kurz halten: Untertitel nach ":" und Modulkürzel wie "IS 201" am Anfang weglassen
    name = name.split(":", 1)[0]
    name = re.sub(r"^\s*[A-Z]{2,5}\s?\d{2,4}[a-z]?\s+", " ", name)
    name = short_dir_name(linux_name(name).strip("._-"))
    return name or "kurs"


# ---------------- Semester ----------------

def semester_sort_key(semester: str) -> tuple[int, int]:
    """"FSS 2027" nach "HWS 2026": FSS ist das Frühjahr, HWS der Herbst."""
    m = SEMESTER_WORD_RE.search(semester)
    if not m:
        return (9999, 9)
    return (int(m.group(2)), 0 if m.group(1).upper() == "FSS" else 1)


def course_semester(course: Course) -> str | None:
    if course.semester:
        return course.semester
    # Gruppen haben kein Semester im Namen, die Dashboard Überschrift schon
    if course.group_heading and (m := SEMESTER_WORD_RE.search(course.group_heading)):
        return f"{m.group(1)} {m.group(2)}"
    return None


def assign_semesters(semesters: list[str], configured: dict[str, str], existing_dirs: list[str]) -> dict[str, str]:
    """Semester -> Semesterordner. Config zuerst, die übrigen chronologisch auf die
    vorhandenen NN_SEMESTER Ordner verteilt, danach neue Nummern."""
    result = dict(configured)
    used = set(configured.values())
    free = sorted((d for d in existing_dirs if d not in used), key=lambda d: number_prefix(d) or 0)
    numbers = [number_prefix(d) or 0 for d in [*existing_dirs, *used]]
    next_no = max(numbers, default=0) + 1
    for semester in sorted(set(semesters) - set(configured), key=semester_sort_key):
        if free:
            result[semester] = free.pop(0)
        else:
            result[semester] = f"{next_no:02d}_semester"
            next_no += 1
    return result


# ---------------- Lokales Dateisystem (nur lesen) ----------------

class LocalTree:
    """Liest Ordnernamen unter dem echten Uni Ordner, mit Cache. Schreibt nie."""

    def __init__(self, root: Path | None, ignore: list[str]):
        self.root = root
        self.ignore = ignore
        self._cache: dict[tuple[str, ...], list[str]] = {}

    def subdirs(self, rel: tuple[str, ...], include_ignored: bool = False) -> list[str]:
        if rel not in self._cache:
            names: list[str] = []
            if self.root is not None:
                path = self.root.joinpath(*rel)
                try:
                    with os.scandir(path) as it:
                        names = sorted(nfc(e.name) for e in it if e.is_dir() and not e.name.startswith("."))
                except OSError:
                    names = []
            self._cache[rel] = names
        names = self._cache[rel]
        return names if include_ignored else [n for n in names if not is_ignored(n, self.ignore)]


# ---------------- Ergebnis ----------------

@dataclass
class MappedItem:
    obj: IliasObject
    course: Course
    rel_path: tuple[str, ...]        # relativ zum Uni Ordner, inklusive Dateiname
    category: str | None
    course_dir_exists: bool
    new_dirs: set[tuple[str, ...]] = field(default_factory=set)  # davon neu anzulegende Ordner


@dataclass
class CourseMapping:
    course: Course
    semester: str | None
    semester_dir: str
    course_dir: str
    exists: bool                     # vorhandener Kursordner gefunden
    source: str                      # "config", "fuzzy", "neu", "ohne Semester"


@dataclass
class MappingResult:
    items: list[MappedItem]
    courses: list[CourseMapping]
    semesters: dict[str, str]


# ---------------- Mapper ----------------

class Mapper:
    def __init__(self, cfg: Config, local_root: Path | None):
        self.cfg = cfg
        self.tree = LocalTree(local_root, cfg.ignore_local_dirs)
        self.config_courses: dict[int, CourseConfig] = {c.ref_id: c for c in cfg.courses}
        # Neu geplante Ordner je Elternordner, damit Nummern nicht doppelt vergeben werden
        self._planned: dict[tuple[str, ...], list[str]] = {}
        self._resolved: dict[tuple[tuple[str, ...], str], str] = {}

    # ---------- Ordner auflösen ----------

    def _children(self, parent: tuple[str, ...]) -> list[str]:
        return [*self.tree.subdirs(parent), *self._planned.get(parent, [])]

    def _exists(self, parent: tuple[str, ...], name: str) -> bool:
        return name in self.tree.subdirs(parent)

    def _plan(self, parent: tuple[str, ...], name: str) -> str:
        self._planned.setdefault(parent, [])
        if name not in self._planned[parent]:
            self._planned[parent].append(name)
        return name

    def resolve_folder(self, parent: tuple[str, ...], ilias_name: str) -> str:
        """Unterordner 1:1 aus ILIAS, aber vorhandenen lokalen Ordner wiederverwenden."""
        cache_key = (parent, ilias_name)
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        key = folder_key(ilias_name)
        short = short_dir_name(linux_name(ilias_name))
        local = self.tree.subdirs(parent)
        # Vorhandenen Ordner nutzen: ungekürzt (alter Stand) oder schon gekürzt angelegt
        existing = [d for d in local if folder_key(d) == key or d == short]
        if not existing:
            loose = loose_folder_key(ilias_name)
            existing = [d for d in local if loose_folder_key(d) == loose]
        if existing:
            name = existing[0]
        else:
            name = short
            if is_ignored(name, self.cfg.ignore_local_dirs):
                # Nie in einen ignorierten Ordner schreiben
                name = f"{name}_ilias"
            self._plan(parent, name)
        self._resolved[cache_key] = name
        return name

    def resolve_category(self, course_rel: tuple[str, ...], category: str) -> str:
        cache_key = (course_rel, "\0" + category)
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        wanted = [fold(n) for n in CATEGORY_LOCAL_NAMES[category]]
        local = self.tree.subdirs(course_rel)
        exact = [d for d in local if " ".join(words(strip_number_prefix(d))) in wanted]
        partial = [d for d in local if any(w in wanted for w in words(strip_number_prefix(d)))]
        if category == "Übungsblätter" and not (exact or partial):
            # Übungsblätter nur, wenn es lokal schon einen Ordner dafür gibt
            name = self.resolve_category(course_rel, "Übung")
        elif exact or partial:
            name = (exact or partial)[0]
        else:
            siblings = self._children(course_rel)
            all_local = self.tree.subdirs(course_rel, include_ignored=True)
            numbers = [n for n in (number_prefix(d) for d in [*all_local, *siblings]) if n is not None]
            name = self._plan(course_rel, f"{max(numbers, default=0) + 1:02d}_{linux_name(category)}")
        self._resolved[cache_key] = name
        return name

    def resolve_video_dir(self, parent: tuple[str, ...]) -> str:
        """Unterordner für Videos: vorhandenen Video Ordner nutzen, sonst NN_Videos mit
        der nächsten freien Nummer unter den Geschwistern."""
        cache_key = (parent, "\0videos")
        if cache_key in self._resolved:
            return self._resolved[cache_key]
        existing = [d for d in self.tree.subdirs(parent) if " ".join(words(strip_number_prefix(d))) in ("video", "videos")]
        if existing:
            name = existing[0]
        else:
            siblings = [*self.tree.subdirs(parent, include_ignored=True), *self._planned.get(parent, [])]
            numbers = [n for n in (number_prefix(d) for d in siblings) if n is not None]
            name = self._plan(parent, f"{max(numbers, default=0) + 1:02d}_{VIDEO_DIR}")
        self._resolved[cache_key] = name
        return name

    # ---------- Kurse ----------

    def map_course(self, course: Course, semester_dirs: dict[str, str]) -> CourseMapping:
        semester = course_semester(course)
        if semester is None:
            # Kurse ohne Semester (z. B. Lizenzkurs): ILIAS Struktur 1:1 unter _ohne_semester.
            # Mit Config Eintrag gilt dessen Ordner, damit die Schalter pro Kurs greifen
            top = self.resolve_folder((), NO_SEMESTER_DIR)
            configured = self.config_courses.get(course.ref_id)
            if configured and configured.local_dir:
                return CourseMapping(course, None, top, configured.local_dir,
                                     self._exists((top,), configured.local_dir), "ohne Semester")
            return CourseMapping(course, None, top, self.resolve_folder((top,), course.name), False, "ohne Semester")
        semester_dir = semester_dirs[semester]
        configured = self.config_courses.get(course.ref_id)
        if configured and configured.local_dir:
            exists = self._exists((semester_dir,), configured.local_dir)
            return CourseMapping(course, semester, semester_dir, configured.local_dir, exists, "config")
        local_dirs = self.tree.subdirs((semester_dir,))
        match, _ = best_course_dir(course.name, [d for d in local_dirs if not d.startswith("_")],
                                   self.cfg.semester_pattern)
        if match:
            return CourseMapping(course, semester, semester_dir, match, True, "fuzzy")
        new_dir = self.resolve_folder((semester_dir,), NEW_COURSES_DIR)
        course_dir = self.resolve_folder((semester_dir, new_dir), course.name)
        return CourseMapping(course, semester, semester_dir, f"{new_dir}/{course_dir}", False, "neu")

    def semester_dirs(self, result: CrawlResult) -> dict[str, str]:
        found = {s for c in result.courses if (s := course_semester(c))}
        existing = [d for d in self.tree.subdirs(()) if SEMESTER_DIR_RE.match(d)]
        return assign_semesters(sorted(found), dict(self.cfg.semesters), existing)

    def suggest_courses(self, result: CrawlResult) -> list[CourseConfig]:
        """Neue Kursordner NN_kursname für Kurse ohne Config Eintrag und ohne passenden
        vorhandenen Ordner. Kurse derselben Veranstaltung ([V], [Ü], [T], Tut Gruppe)
        bekommen denselben Ordner. Nummern fortlaufend nach vorhandenen Ordnern, in der
        Reihenfolge des Dashboards. Schreibt nichts, der Aufrufer speichert in der Config."""
        semester_dirs = self.semester_dirs(result)
        courses = [c for c in result.courses if c.ref_id not in result.nested_in]
        mappings = [self.map_course(c, semester_dirs) for c in courses]
        # Belegte Namen je Semesterordner: vorhandene Ordner und Kurse aus der Config
        used: dict[str, list[str]] = {}
        for cm in mappings:
            names = used.setdefault(cm.semester_dir, list(self.tree.subdirs((cm.semester_dir,), include_ignored=True)))
            if cm.source == "config":
                names.append(cm.course_dir)

        planned: dict[tuple[str, tuple[str, ...]], str] = {}   # (Semesterordner, Kurswörter) -> Ordner
        suggestions: list[CourseConfig] = []
        for course, cm in zip(courses, mappings):
            if cm.source == "ohne Semester" and course.ref_id not in self.config_courses:
                # Eigener Config Eintrag, damit der Kurs in der GUI erscheint und abschaltbar ist
                suggestions.append(CourseConfig(ref_id=course.ref_id, ilias_name=course.name, local_dir=cm.course_dir))
                continue
            if cm.source != "neu":
                continue
            key = (cm.semester_dir, tuple(ilias_course_words(course.name, self.cfg.semester_pattern)))
            if key not in planned:
                names = used[cm.semester_dir]
                numbers = [n for n in (number_prefix(d) for d in names) if n is not None]
                name = f"{max(numbers, default=0) + 1:02d}_{course_dir_name(course.name, self.cfg.semester_pattern)}"
                names.append(name)
                planned[key] = name
            suggestions.append(CourseConfig(ref_id=course.ref_id, ilias_name=course.name, local_dir=planned[key]))
        return suggestions

    # ---------- Objekte ----------

    def map_crawl(self, result: CrawlResult) -> MappingResult:
        semester_dirs = self.semester_dirs(result)

        # 1. Alle Objekte mit ihren ILIAS Ordnern sammeln
        mappings: list[CourseMapping] = []
        work: list[tuple[IliasObject, Course, CourseMapping, tuple[str, ...], list[IliasObject]]] = []
        for course in result.courses:
            if course.ref_id in result.nested_in:
                continue
            cm = self.map_course(course, semester_dirs)
            mappings.append(cm)
            course_rel = (cm.semester_dir, *cm.course_dir.split("/"))
            stack: list[IliasObject] = []
            for obj in result.objects.get(course.ref_id, []):
                depth = len(obj.path) - 1  # path[0] ist der Kurs
                stack = stack[:depth]
                if obj.is_container:
                    stack.append(obj)
                    continue
                work.append((obj, course, cm, course_rel, list(stack)))

        # 2. Neue Kategorieordner in fester Reihenfolge nummerieren (01_Vorlesung, 02_Übung,
        #    03_Tutorium ...), nicht in der Reihenfolge, in der ILIAS die Dateien liefert.
        #    Mehrere ILIAS Kurse im selben Kursordner zählen zusammen. Nur Dateien zählen.
        needed: dict[tuple[str, ...], set[str]] = {}
        for obj, course, cm, course_rel, ancestors in work:
            if obj.type != "file" or cm.source in ("neu", "ohne Semester"):
                continue
            if self._rule_target(course, obj, ancestors):
                continue
            category, _ = self._category_and_rest(course, ancestors)
            if category:
                needed.setdefault(course_rel, set()).add(category)
        for course_rel, categories in needed.items():
            for category in sorted(categories, key=CATEGORY_NUMBER_ORDER.index):
                self.resolve_category(course_rel, category)

        # 3. Zielpfade
        items = [self._map_object(obj, course, cm, course_rel, ancestors)
                 for obj, course, cm, course_rel, ancestors in work]
        return MappingResult(items=items, courses=mappings, semesters=semester_dirs)

    def _category_and_rest(self, course: Course, ancestors: list[IliasObject]) -> tuple[str | None, list[str]]:
        """Kategorie bestimmen und die verbleibenden ILIAS Ordner zurückgeben."""
        suffix = course_suffix_category(course.name)
        remaining = list(ancestors)

        def drop_transparent() -> None:
            while remaining and remaining[0].type == "fold" and folder_key(remaining[0].name) in TRANSPARENT_FOLDERS:
                remaining.pop(0)

        drop_transparent()
        category = None
        if remaining:
            first = remaining[0]
            category = keyword_category(first.name, preferred=suffix)
            if category is not None and (first.type == "grp" or is_pure_category_name(first.name)):
                remaining.pop(0)
                drop_transparent()
        if category is None:
            category = suffix
        return category, [o.name for o in remaining]

    def _map_object(self, obj: IliasObject, course: Course, cm: CourseMapping,
                    course_rel: tuple[str, ...], ancestors: list[IliasObject]) -> MappedItem:
        new_dirs: set[tuple[str, ...]] = set()

        def note(parent: tuple[str, ...], name: str) -> tuple[str, ...]:
            rel = (*parent, name)
            if not self._exists(parent, name):
                new_dirs.add(rel)
            return rel

        rel: tuple[str, ...] = ()
        for part in course_rel:
            rel = note(rel, part)

        if cm.source in ("neu", "ohne Semester"):
            # Nicht zugeordnete Kurse: ILIAS Struktur 1:1, keine Kategorien
            category, rest = None, [o.name for o in ancestors]
        else:
            rule_target = self._rule_target(course, obj, ancestors)
            if rule_target:
                category, rest = rule_target, [o.name for o in ancestors]
                rel = note(rel, rule_target)
            else:
                category, rest = self._category_and_rest(course, ancestors)
                if category:
                    rel = note(rel, self.resolve_category(rel, category))
        for name in rest:
            rel = note(rel, self.resolve_folder(rel, name))
        if obj.type == "file" and is_video_name(obj.name) and not any(is_video_folder(n) for n in rest):
            rel = note(rel, self.resolve_video_dir(rel))
        return MappedItem(obj, course, (*rel, linux_name(obj.name)), category, cm.exists, new_dirs)

    def _rule_target(self, course: Course, obj: IliasObject, ancestors: list[IliasObject]) -> str | None:
        """Config Regeln des Kurses, erste passende gewinnt."""
        configured = self.config_courses.get(course.ref_id)
        if not configured:
            return None
        for rule in configured.rules:
            if rule.match_file and fnmatch.fnmatch(fold(obj.name), fold(rule.match_file)):
                return rule.target
            if rule.match_folder and any(fnmatch.fnmatch(fold(a.name), fold(rule.match_folder)) for a in ancestors):
                return rule.target
        return None
