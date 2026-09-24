"""
ILIAS Kurse und Objekte erfassen.

Die Parser arbeiten auf HTML von ILIAS 9 der Uni Mannheim, entwickelt gegen
echte Seiten (Fixtures unter tests/fixtures/). Der Crawler liest nur, er lädt
keine Dateien herunter.

Merkmale der Seiten:
  * Dashboard: Panel "Meine Kurse und Gruppen", je Semester eine
    .il-item-group mit h3, je Kurs ein .il-std-item mit Link goto.php/crs/<ref_id>
  * Container (Kurs, Gruppe, Ordner ...): je Objekt ein div.ilContainerListItemOuter
    mit data-list-item-id="lg_div_<ref_id>_pref_<parent>", Titel Link
    a.il_ContainerItemTitle und Eigenschaften span.il_ItemProperty (bei Dateien:
    Endung, Größe, Änderungsdatum). Der Typ steht meist in li#item_row_<typ>-<ref_id>,
    auf Seiten aus dem Seiten Editor aber nicht, dann über Link oder Icon
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .auth import ILIAS, TIMEOUT

DASHBOARD = ILIAS + "/ilias.php?baseClass=ilDashboardGUI"
DEFAULT_SEMESTER_PATTERN = r"\((HWS|FSS) (\d{4})\)"
# Pause zwischen zwei Seitenabrufen in Sekunden (Server schonen)
DEFAULT_DELAY = 0.5

# Objekttypen, in die der Crawler hineingeht
CONTAINER_TYPES = {"crs", "grp", "fold", "sess"}

# Lesbare Namen für die Ausgabe
TYPE_NAMES = {
    "crs": "Kurs",
    "grp": "Gruppe",
    "fold": "Ordner",
    "sess": "Sitzung",
    "file": "Datei",
    "webr": "Weblink",
    "frm": "Forum",
    "exc": "Übung",
    "wiki": "Wiki",
    "tst": "Test",
    "svy": "Umfrage",
    "lm": "Lernmodul",
    "htlm": "HTML Lernmodul",
    "sahs": "SCORM Lernmodul",
    "copa": "Inhaltsseite",
    "mcst": "Mediacast",
    "xoct": "Opencast",
    "blog": "Blog",
    "glo": "Glossar",
    "itgr": "Objektblock",
}

MY_COURSES_HEADINGS = ("meine kurse und gruppen", "my courses and groups")

GOTO_RE = re.compile(r"goto\.php(?:/|\?target=)([a-z]+)[/_](\d+)")
# data-list-item-id="lg_div_<ref_id>_pref_<parent_ref_id>"
LIST_ITEM_RE = re.compile(r"^lg_div_(\d+)_pref_(\d+)$")
# li#item_row_<typ>-<ref_id>, auf Seiten aus dem Seiten Editor steht statt des Typs eine Block ID
ITEM_ROW_RE = re.compile(r"^item_row_([a-z]+)-(\d+)$")
ICON_RE = re.compile(r"icon_([a-z]+)\.svg")
# Links ohne goto.php, erkannt an der GUI Klasse
LINK_CLASS_TYPES = {
    "ilobjfilegui": "file",
    "illinkresourcehandlergui": "webr",
}
SIZE_RE = re.compile(r"^([\d.,]+)\s*(B|Bytes?|KB|MB|GB|TB)$", re.IGNORECASE)
DATE_RE = re.compile(r"^(\d{1,2})\.\s*([A-Za-zäÄ]{3,4})\.?\s+(\d{4}),\s*(\d{1,2}):(\d{2})$")
RELATIVE_DATE_RE = re.compile(r"^(Heute|Gestern|Morgen|Today|Yesterday|Tomorrow),\s*(\d{1,2}):(\d{2})$", re.I)
EXT_RE = re.compile(r"^[a-z0-9]{1,8}$", re.IGNORECASE)

MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12,
}
RELATIVE_DAYS = {"heute": 0, "today": 0, "gestern": -1, "yesterday": -1, "morgen": 1, "tomorrow": 1}
SIZE_UNITS = {"b": 1, "byte": 1, "bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}

log = logging.getLogger(__name__)


# ---------------- Datentypen ----------------

@dataclass
class Course:
    """Eintrag aus "Meine Kurse und Gruppen" (Kurs oder Gruppe)."""
    type: str                  # "crs" oder "grp"
    ref_id: int
    name: str
    url: str
    semester: str | None       # aus dem Namen, z. B. "HWS 2026"
    group_heading: str | None  # Überschrift auf dem Dashboard, z. B. "HWS 2026"


@dataclass
class IliasObject:
    """Ein Objekt in einem Container."""
    type: str                  # ILIAS Typ, z. B. "file", "fold", "webr"
    ref_id: int
    name: str                  # bei Dateien mit Endung
    url: str | None
    path: list[str] = field(default_factory=list)  # Namen der Container bis hierher
    size: int | None = None    # Bytes, aus der Anzeige gerundet
    mtime: datetime | None = None
    type_label: str | None = None  # Bezeichnung laut ILIAS (Icon), für unbekannte Typen

    @property
    def is_container(self) -> bool:
        return self.type in CONTAINER_TYPES

    @property
    def ilias_path(self) -> str:
        return " / ".join([*self.path, self.name])


# ---------------- Hilfsfunktionen ----------------

def semester_from_name(name: str, pattern: str = DEFAULT_SEMESTER_PATTERN) -> str | None:
    """"Marketing [V] (HWS 2026)" -> "HWS 2026"."""
    m = re.search(pattern, name)
    if not m:
        return None
    return " ".join(m.groups()) if m.groups() else m.group(0)


def parse_goto(url: str) -> tuple[str, int] | None:
    m = GOTO_RE.search(url)
    return (m.group(1), int(m.group(2))) if m else None


def parse_size(text: str) -> int | None:
    """"4.47 MB" -> Bytes. ILIAS zeigt gerundete Werte, das Ergebnis ist ungefähr."""
    m = SIZE_RE.match(text.strip())
    if not m:
        return None
    number, unit = m.groups()
    # Tausender und Dezimaltrenner vereinheitlichen: der letzte Trenner ist der Dezimaltrenner
    if "," in number and "." in number:
        number = number.replace(",", "") if number.rfind(".") > number.rfind(",") else \
            number.replace(".", "").replace(",", ".")
    else:
        number = number.replace(",", ".")
    try:
        return round(float(number) * SIZE_UNITS[unit.lower()])
    except ValueError:
        return None


def parse_date(text: str, today: date | None = None) -> datetime | None:
    """"7. Sep 2026, 13:28", "Heute, 09:15" oder "Gestern, 17:00" -> datetime."""
    text = text.strip()
    m = DATE_RE.match(text)
    if m:
        day, month, year, hour, minute = m.groups()
        month_no = MONTHS.get(month.lower()[:3])
        if month_no is None:
            return None
        try:
            return datetime(int(year), month_no, int(day), int(hour), int(minute))
        except ValueError:
            return None
    m = RELATIVE_DATE_RE.match(text)
    if m:
        word, hour, minute = m.groups()
        d = (today or date.today()) + timedelta(days=RELATIVE_DAYS[word.lower()])
        return datetime(d.year, d.month, d.day, int(hour), int(minute))
    return None


# ---------------- Parser ----------------

def _my_courses_panel(soup: BeautifulSoup) -> Tag | None:
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True).lower() in MY_COURSES_HEADINGS:
            return h2.find_parent(class_="panel")
    return None


def parse_dashboard(html: str, semester_pattern: str = DEFAULT_SEMESTER_PATTERN) -> list[Course]:
    """Kurse und Gruppen aus "Meine Kurse und Gruppen" lesen."""
    soup = BeautifulSoup(html, "html.parser")
    panel = _my_courses_panel(soup)
    if panel is None:
        log.warning("Dashboard: Bereich 'Meine Kurse und Gruppen' nicht gefunden")
        return []

    courses = []
    for item in panel.select(".il-std-item"):
        link = item.select_one(".il-item-title a[href]")
        if link is None:
            continue
        target = parse_goto(link["href"])
        if target is None or target[0] not in ("crs", "grp"):
            continue
        group = item.find_parent(class_="il-item-group")
        heading = group.find("h3") if group else None
        name = link.get_text(" ", strip=True)
        courses.append(Course(
            type=target[0],
            ref_id=target[1],
            name=name,
            url=link["href"],
            semester=semester_from_name(name, semester_pattern),
            group_heading=heading.get_text(strip=True) if heading else None,
        ))
    return courses


def _file_properties(row: Tag) -> tuple[str | None, int | None, datetime | None]:
    """Endung, Größe und Änderungsdatum aus den Eigenschaften einer Datei."""
    ext = size = mtime = None
    for prop in row.select(".il_ItemProperty"):
        text = prop.get_text(" ", strip=True)
        if size is None and (s := parse_size(text)) is not None:
            size = s
        elif mtime is None and (d := parse_date(text)) is not None:
            mtime = d
        elif ext is None and size is None and EXT_RE.match(text):
            ext = text.lower()
    return ext, size, mtime


def _object_type(row: Tag, link: Tag | None, icon: Tag | None) -> str:
    """Typ eines Listeneintrags aus mehreren Merkmalen bestimmen, das erste passende gewinnt."""
    li = row.find_parent("li")
    if li is not None and (m := ITEM_ROW_RE.match(li.get("id") or "")):
        return m.group(1)
    href = link.get("href", "") if link else ""
    if goto := parse_goto(href):
        return goto[0]
    if icon is not None and (m := ICON_RE.search(icon.get("src") or "")):
        return m.group(1)
    for gui_class, obj_type in LINK_CLASS_TYPES.items():
        if f"cmdclass={gui_class}" in href.lower() or f"baseclass={gui_class}" in href.lower():
            return obj_type
    return "unknown"


def parse_container(html: str, base_url: str = ILIAS) -> list[IliasObject]:
    """Objekte einer Container Seite (Kurs, Gruppe, Ordner, Sitzung) lesen, eine Ebene."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="il_center_col") or soup
    objects = []
    seen = set()
    for row in root.find_all(class_="ilContainerListItemOuter"):
        m = LIST_ITEM_RE.match(row.get("data-list-item-id") or "")
        if m is None:
            continue
        ref_id = int(m.group(1))
        if ref_id in seen:
            continue
        link = row.select_one("a.il_ContainerItemTitle")
        icon = row.select_one("img.ilListItemIcon")
        obj_type = _object_type(row, link, icon)
        title = row.select_one(".il_ContainerItemTitle")
        name = (link or title).get_text(" ", strip=True) if (link or title) else ""
        if not name:
            log.debug("Objekt ohne Titel übersprungen: %s %s", obj_type, ref_id)
            continue
        seen.add(ref_id)
        if obj_type == "unknown":
            log.warning("Unbekannter Objekttyp: %s (ref_id %s)", icon.get("alt") if icon else "?", ref_id)
        obj = IliasObject(
            type=obj_type,
            ref_id=ref_id,
            name=name,
            url=urljoin(base_url, link["href"]) if link and link.get("href") else None,
            type_label=icon.get("alt") if icon else None,
        )
        if obj_type == "file":
            ext, obj.size, obj.mtime = _file_properties(row)
            if ext and not name.lower().endswith("." + ext):
                obj.name = f"{name}.{ext}"
        objects.append(obj)
    return objects


# ---------------- Crawler ----------------

class Crawler:
    """Liest Kurse und Objekte über eine eingeloggte Session. Schreibt nichts."""

    def __init__(
        self,
        session: requests.Session,
        delay: float = DEFAULT_DELAY,
        semester_pattern: str = DEFAULT_SEMESTER_PATTERN,
        cancel: threading.Event | None = None,
        on_page: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.delay = delay
        self.semester_pattern = semester_pattern
        self.cancel = cancel or threading.Event()
        self.on_page = on_page
        self.pages_fetched = 0
        self._last_request = 0.0

    def _get(self, url: str) -> str:
        if self.cancel.is_set():
            raise InterruptedError("abgebrochen")
        wait = self._last_request + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        r = self.session.get(url, timeout=TIMEOUT)
        self._last_request = time.monotonic()
        self.pages_fetched += 1
        r.raise_for_status()
        return r.text

    def fetch_courses(self) -> list[Course]:
        return parse_dashboard(self._get(DASHBOARD), self.semester_pattern)

    def list_container(self, url: str) -> list[IliasObject]:
        if self.on_page:
            self.on_page(url)
        return parse_container(self._get(url), url)

    def walk(self, course: Course, visited: set[int] | None = None) -> Iterator[IliasObject]:
        """Alle Objekte eines Kurses rekursiv, Container vor ihrem Inhalt.

        visited verhindert doppelte Besuche, auch über Kurse hinweg (z. B. eine
        Gruppe, die im Kurs und auf dem Dashboard auftaucht)."""
        visited = visited if visited is not None else set()
        visited.add(course.ref_id)
        yield from self._walk(course.url, [course.name], visited)

    def _walk(self, url: str, path: list[str], visited: set[int]) -> Iterator[IliasObject]:
        try:
            children = self.list_container(url)
        except requests.RequestException as e:
            log.warning("Seite nicht lesbar (%s): %s", " / ".join(path), type(e).__name__)
            return
        for obj in children:
            if obj.ref_id in visited:
                continue
            visited.add(obj.ref_id)
            obj.path = list(path)
            yield obj
            if obj.is_container and obj.url:
                yield from self._walk(obj.url, [*path, obj.name], visited)
