"""
ILIAS Kurse und Objekte erfassen.

Die Parser arbeiten auf HTML von ILIAS 9 der Uni Mannheim, entwickelt gegen
echte Seiten (Fixtures unter tests/fixtures/). Der Crawler liest nur, er lädt
keine Dateien herunter.

Merkmale der Seiten:
  * Dashboard: Panel "Meine Kurse und Gruppen", erkannt an den Aktionen von
    ilMembershipBlockGUI (sprachunabhängig, die Überschrift nur als Rückfall). Je Semester
    eine .il-item-group mit h3, je Kurs ein .il-std-item (Listenansicht) bzw. eine
    .il-card (Kachelansicht) mit Link goto.php/crs/<ref_id>. Aufgerufen werden Kurse
    danach immer über ihren Permalink aus der ref_id, nie über Dashboard Links
  * Container (Kurs, Gruppe, Ordner ...): je Objekt ein div.ilContainerListItemOuter
    mit data-list-item-id="lg_div_<ref_id>_pref_<parent>", Titel Link
    a.il_ContainerItemTitle und Eigenschaften span.il_ItemProperty (bei Dateien:
    Endung, Größe, Änderungsdatum). Der Typ steht meist in li#item_row_<typ>-<ref_id>,
    auf Seiten aus dem Seiten Editor aber nicht, dann über Link oder Icon
  * Übung (exc): Übersicht der Aufgaben, Ansicht "Alle" über einen Knopf mit
    cmd=showOverview&mode=all. Je Aufgabe ein Link auf ilAssignmentPresentationGUI mit
    ass_id, auf der Aufgabenseite Dateien der Arbeitsanweisung (cmd=downloadFile&file=...)
    und die Musterlösung (cmd=downloadGlobalFeedbackFile), der Dateiname steht in der
    Zeile davor (.control-label)
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from . import debug
from .auth import ILIAS, TIMEOUT, is_logged_in, safe_url, session_lock

DASHBOARD = ILIAS + "/ilias.php?baseClass=ilDashboardGUI"
# Eigene Seite "Meine Kurse und Gruppen", Rückfall, wenn der Block auf dem Dashboard fehlt
MEMBERSHIP_OVERVIEW = ILIAS + "/ilias.php?baseClass=ilMembershipOverviewGUI"
DEFAULT_SEMESTER_PATTERN = r"\((HWS|FSS) (\d{4})\)"
# Mindestabstand zwischen zwei Seitenabrufen in Sekunden, auch bei Parallelität (Server schonen)
DEFAULT_DELAY = 0.5
DEFAULT_PARALLEL = 3
MAX_PARALLEL = 5

# Aufgabe einer Übung. Kein ILIAS Objekttyp mit ref_id, nur im Crawler als Ordner geführt
ASSIGNMENT = "ass"

# Objekttypen, in die der Crawler hineingeht. Übungen und ihre Aufgaben gelten als Ordner,
# ihre Dateien als normale Dateien (IliasObject.exercise gesetzt)
CONTAINER_TYPES = {"crs", "grp", "fold", "sess", "exc", ASSIGNMENT}

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
    ASSIGNMENT: "Aufgabe",
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
# Aktionen des Blocks (Sortierung, Favoriten ...) zeigen auf diese GUI Klasse, in jeder Sprache
MEMBERSHIP_BLOCK_RE = re.compile(r"ilmembershipblockgui", re.IGNORECASE)
# Titel Links von Kursen: Listenansicht und Kachelansicht
COURSE_TITLE_SELECTOR = ".il-item-title a[href], .card-title a[href], .il-card .caption a[href]"

GOTO_RE = re.compile(r"goto\.php(?:/|\?target=)([a-z]+)[/_](\d+)")
# data-list-item-id="lg_div_<ref_id>_pref_<parent_ref_id>". Einträge in Blöcken der Kursseite
# (Objektblöcke, Seiten Editor, Gruppierung nach Typ) haben seit Oktober 2026 eine negative
# Block Nummer statt der Eltern ref_id, z. B. "lg_div_1787941_pref_-1"
LIST_ITEM_RE = re.compile(r"^lg_div_(\d+)_pref_(-?\d+)$")
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
    exercise: bool = False     # Datei aus einer Übung (Aufgabenstellung, Vorgaben, Musterlösung)

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


def permalink(obj_type: str, ref_id: int) -> str:
    """Stabiler Einstieg in ein Objekt, unabhängig von Dashboard Links und Navigation."""
    return f"{ILIAS}/goto.php?target={obj_type}_{ref_id}"


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

def my_courses_panel(soup: BeautifulSoup) -> Tag | None:
    """Panel "Meine Kurse und Gruppen". Zuerst über die Aktionen des Blocks (unabhängig von
    Sprache und Überschrift), dann über die Überschrift auf Deutsch oder Englisch."""
    for el in [*soup.find_all(attrs={"data-action": MEMBERSHIP_BLOCK_RE}),
               *soup.find_all("a", href=MEMBERSHIP_BLOCK_RE)]:
        if (panel := el.find_parent(class_="panel")) is not None:
            return panel
    for h2 in soup.find_all("h2"):
        if h2.get_text(strip=True).lower() in MY_COURSES_HEADINGS:
            return h2.find_parent(class_="panel")
    return None


def parse_dashboard(html: str, semester_pattern: str = DEFAULT_SEMESTER_PATTERN) -> list[Course]:
    """Kurse und Gruppen aus "Meine Kurse und Gruppen" lesen, je ref_id einmal."""
    soup = BeautifulSoup(html, "html.parser")
    panel = my_courses_panel(soup)
    if panel is None:
        log.warning("Dashboard: Bereich 'Meine Kurse und Gruppen' nicht gefunden")
        return []
    return parse_course_panel(panel, semester_pattern)


def parse_course_panel(panel: Tag, semester_pattern: str = DEFAULT_SEMESTER_PATTERN) -> list[Course]:
    courses = []
    seen: set[int] = set()
    for link in panel.select(COURSE_TITLE_SELECTOR):
        target = parse_goto(link["href"])
        if target is None or target[0] not in ("crs", "grp") or target[1] in seen:
            continue
        name = link.get_text(" ", strip=True)
        if not name:
            continue
        seen.add(target[1])
        group = link.find_parent(class_="il-item-group")
        heading = group.find("h3") if group else None
        courses.append(Course(
            type=target[0],
            ref_id=target[1],
            name=name,
            url=permalink(*target),
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
        item_id = row.get("data-list-item-id") or ""
        m = LIST_ITEM_RE.match(item_id)
        if m is None:
            # Nie still überspringen: sonst fehlen ganze Ordner, ohne dass es jemand merkt
            log.warning("Listeneintrag mit unbekannter Kennung übersprungen: %r", item_id)
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


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def assignment_ref_id(ass_id: int) -> int:
    """Aufgaben haben keine ref_id, sondern eine (in ILIAS eindeutige) ass_id. Negativ,
    damit sie nie mit einer echten ref_id zusammenfällt."""
    return -ass_id


def exercise_file_id(ass_id: int, kind: str, name: str) -> int:
    """Stabile Kennung einer Übungsdatei für den Index (dort Primärschlüssel statt ref_id).
    Negativ und 52 Bit groß, damit sie nie mit einer echten ref_id zusammenfällt."""
    digest = hashlib.sha1(f"{ass_id}/{kind}/{name}".encode()).hexdigest()
    return -int(digest[:13], 16)


def exercise_all_mode_url(html: str, base_url: str) -> str | None:
    """Adresse der Ansicht "Alle" einer Übung, None wenn sie schon aktiv ist oder fehlt.
    Ohne diese Ansicht zeigt ILIAS nur laufende Aufgaben."""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(["button", "a"]):
        target = el.get("data-action") or el.get("href") or ""
        if "cmd=showoverview" in target.lower() and _query(target).get("mode") == "all":
            if "engaged" in (el.get("class") or []) or el.get("aria-pressed") == "true":
                return None
            return urljoin(base_url, target)
    return None


def parse_exercise_overview(html: str, base_url: str) -> list[IliasObject]:
    """Aufgaben einer Übung, je ass_id einmal, als Ordner (Typ ASSIGNMENT)."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="il_center_col") or soup
    assignments = []
    seen: set[int] = set()
    for link in root.select(".il-item-title a[href]"):
        href = link["href"]
        params = _query(href)
        if "ilassignmentpresentationgui" not in href.lower() or not params.get("ass_id", "").isdigit():
            continue
        ass_id = int(params["ass_id"])
        name = link.get_text(" ", strip=True)
        if ass_id in seen or not name:
            continue
        seen.add(ass_id)
        assignments.append(IliasObject(type=ASSIGNMENT, ref_id=assignment_ref_id(ass_id), name=name,
                                       url=urljoin(base_url, href), type_label="Aufgabe"))
    if not assignments:
        log.warning("Übung ohne erkennbare Aufgaben: %s", safe_url(base_url))
    return assignments


# Downloads auf der Aufgabenseite: Arbeitsanweisung und Musterlösung, nie eigene Abgaben
EXERCISE_DOWNLOAD_CMDS = {"downloadfile": "anweisung", "downloadglobalfeedbackfile": "loesung"}


def parse_assignment(html: str, base_url: str) -> list[IliasObject]:
    """Dateien einer Aufgabe (Arbeitsanweisung, Vorgaben, Musterlösung) als Dateien."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="il_center_col") or soup
    files = []
    seen: set[int] = set()
    for link in root.find_all("a", href=True):
        href = link["href"]
        params = _query(href)
        kind = EXERCISE_DOWNLOAD_CMDS.get(params.get("cmd", "").lower())
        if kind is None or "ilexsubmissiongui" not in href.lower() or not params.get("ass_id", "").isdigit():
            continue
        # Dateiname aus der Zeile (links steht der Name, rechts der Link "Download")
        row = link.find_parent(class_="row")
        label = row.find(class_="control-label") if row else None
        name = (label.get_text(" ", strip=True) if label else "") or params.get("file", "")
        if not name:
            log.warning("Übungsdatei ohne Namen übersprungen: %s", safe_url(base_url))
            continue
        ref_id = exercise_file_id(int(params["ass_id"]), kind, name)
        if ref_id in seen:
            continue
        seen.add(ref_id)
        files.append(IliasObject(type="file", ref_id=ref_id, name=name, url=urljoin(base_url, href),
                                 type_label="Datei", exercise=True))
    return files


# ---------------- Crawler ----------------

class Throttle:
    """Verteilt Anfragen über alle Threads im Abstand von mindestens delay Sekunden."""

    def __init__(self, delay: float):
        self.delay = delay
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.delay
        if slot > now:
            time.sleep(slot - now)


class SessionExpired(Exception):
    """Session abgelaufen und erneuter Login nicht möglich. Der Lauf wird abgebrochen."""


class CrawlCancelled(Exception):
    """Lauf über das cancel Token abgebrochen."""


class CourseListNotFound(Exception):
    """Weder auf dem Dashboard noch auf der Seite "Meine Kurse und Gruppen" eine Kursliste.
    Lieber abbrechen als mit 0 Kursen still "nichts zu tun" melden."""


@dataclass
class CrawlResult:
    courses: list[Course]
    # Kurs ref_id -> alle Objekte darin, Container vor ihrem Inhalt, mit Pfad
    objects: dict[int, list[IliasObject]] = field(default_factory=dict)
    # Kurs ref_id -> Name des Kurses, in dessen Baum er schon enthalten ist (z. B. Gruppen)
    nested_in: dict[int, str] = field(default_factory=dict)
    # ILIAS Pfade, deren Seite nicht gelesen werden konnte. Deren Inhalt ist unbekannt,
    # nicht leer: ein Sync darf daraus nie schließen, dass Dateien entfernt wurden
    failed: list[str] = field(default_factory=list)
    pages_fetched: int = 0


class Crawler:
    """Liest Kurse und Objekte über eine eingeloggte Session. Schreibt nichts.

    Seiten werden parallel abgerufen (max_parallel Threads), zwischen zwei Abrufen
    liegen aber immer mindestens delay Sekunden, egal wie viele Threads laufen.
    Läuft die Session ab, wird einmal über relogin neu eingeloggt."""

    def __init__(
        self,
        session: requests.Session,
        delay: float = DEFAULT_DELAY,
        max_parallel: int = DEFAULT_PARALLEL,
        semester_pattern: str = DEFAULT_SEMESTER_PATTERN,
        relogin: Callable[[], None] | None = None,
        cancel: threading.Event | None = None,
        on_page: Callable[[int], None] | None = None,
    ):
        self.session = session
        self.delay = delay
        self.max_parallel = max(1, min(max_parallel, MAX_PARALLEL))
        self.semester_pattern = semester_pattern
        self.relogin = relogin
        self.cancel = cancel or threading.Event()
        self.on_page = on_page
        self.pages_fetched = 0
        self.throttle = Throttle(delay)
        self._lock = threading.Lock()
        self._relogin_lock = threading.Lock()
        self._session_generation = 0

    # ---------- einzelne Seiten ----------

    def _fetch(self, url: str) -> requests.Response:
        if self.cancel.is_set():
            raise CrawlCancelled()
        self.throttle.wait()
        # Requests auf einer Session nacheinander, die Threads parsen nur parallel
        with session_lock(self.session):
            r = self.session.get(url, timeout=TIMEOUT)
        with self._lock:
            self.pages_fetched += 1
            count = self.pages_fetched
        if self.on_page:
            self.on_page(count)
        r.raise_for_status()
        return r

    def _get(self, url: str) -> str:
        return self._get_response(url).text

    def _get_response(self, url: str) -> requests.Response:
        """Seite holen und sicherstellen, dass sie eingeloggt abgerufen wurde."""
        generation = self._session_generation
        r = self._fetch(url)
        if is_logged_in(r):
            return r

        log.info("Session abgelaufen (Antwort von %s), neuer Login", safe_url(r.url))
        if self.relogin is None:
            raise SessionExpired("ILIAS Session abgelaufen, bitte neu anmelden.")
        with self._relogin_lock:
            # Nur ein Thread loggt neu ein, die anderen nutzen danach dessen Session
            if self._session_generation == generation:
                try:
                    self.relogin()
                except Exception as e:
                    raise SessionExpired(f"ILIAS Session abgelaufen, neuer Login fehlgeschlagen: {e}") from None
                self._session_generation += 1
        r = self._fetch(url)
        if not is_logged_in(r):
            raise SessionExpired("ILIAS Session abgelaufen, auch nach neuem Login keine Anmeldung.")
        return r

    def fetch_courses(self) -> list[Course]:
        """Kursliste vom Dashboard, falls der Block dort fehlt von der Seite "Meine Kurse und Gruppen"."""
        for page in (DASHBOARD, MEMBERSHIP_OVERVIEW):
            try:
                r = self._get_response(page)
            except requests.HTTPError as e:
                if page == DASHBOARD:
                    raise
                # Rückfall Seite gibt es in dieser ILIAS Version nicht
                debug.log.warning("Rückfall %s nicht abrufbar: %s", debug.url(page), e.response.status_code
                                  if e.response is not None else type(e).__name__)
                continue
            debug.log.info("Kursliste: abgerufen %s, gelandet auf %s", debug.url(page), debug.url(r.url))
            panel = my_courses_panel(BeautifulSoup(r.text, "html.parser"))
            if panel is None:
                log.warning("Kursliste nicht gefunden auf %s", safe_url(r.url))
                debug.log.warning("Kein Bereich 'Meine Kurse und Gruppen' auf %s", debug.url(r.url))
                continue
            debug.html("Kursliste", str(panel))
            courses = parse_course_panel(panel, self.semester_pattern)
            log.info("%d Kurse und Gruppen gefunden", len(courses))
            debug.log.info("%d Kurse und Gruppen gefunden:", len(courses))
            for c in courses:
                debug.log.info("  ref_id %s [%s] %s (Semester: %s, Überschrift: %s)",
                               c.ref_id, c.type, c.name, c.semester, c.group_heading)
            return courses
        raise CourseListNotFound("Die Liste 'Meine Kurse und Gruppen' wurde in ILIAS nicht gefunden. "
                                 "Bitte später erneut versuchen, sonst den Debug Modus einschalten.")

    def list_container(self, url: str) -> list[IliasObject]:
        return parse_container(self._get(url), url)

    def list_exercise(self, url: str) -> list[IliasObject]:
        """Aufgaben einer Übung, in der Ansicht "Alle" (sonst fehlen beendete Aufgaben)."""
        r = self._get_response(url)
        if all_url := exercise_all_mode_url(r.text, r.url):
            r = self._get_response(all_url)
        return parse_exercise_overview(r.text, r.url)

    def list_assignment(self, url: str) -> list[IliasObject]:
        r = self._get_response(url)
        return parse_assignment(r.text, r.url)

    def list_children(self, obj_type: str, url: str) -> list[IliasObject]:
        if obj_type == "exc":
            return self.list_exercise(url)
        if obj_type == ASSIGNMENT:
            return self.list_assignment(url)
        return self.list_container(url)

    # ---------- ganzer Lauf ----------

    def crawl(self, courses: list[Course]) -> CrawlResult:
        """Alle Kurse rekursiv erfassen.

        Phase 1 ruft alle erreichbaren Container parallel ab, jeden nur einmal.
        Phase 2 baut daraus in fester Reihenfolge die Bäume je Kurs. Taucht ein
        Kurs (z. B. eine Gruppe) schon im Baum eines früheren Kurses auf, wird er
        dort geführt und in nested_in vermerkt."""
        children, failed = self._fetch_all(courses)
        result = CrawlResult(courses=courses, pages_fetched=self.pages_fetched)
        owner: dict[int, str] = {}  # ref_id -> Name des Kurses, der es zuerst enthält
        for course in courses:
            if course.ref_id in owner:
                result.nested_in[course.ref_id] = owner[course.ref_id]
                continue
            owner[course.ref_id] = course.name
            objs: list[IliasObject] = []
            failed_before = len(result.failed)
            self._assemble(course.ref_id, [course.name], children, failed, owner, objs, result.failed)
            result.objects[course.ref_id] = objs
            course_failed = result.failed[failed_before:]
            debug.log.info("Kurs %s (%s): %s, %d Objekte, davon %d Dateien%s", course.ref_id, course.name,
                           "FEHLER" if course.ref_id in failed else "ok", len(objs),
                           sum(o.type == "file" for o in objs),
                           f", nicht lesbar: {course_failed}" if course_failed else "")
        for ref_id, outer in result.nested_in.items():
            debug.log.info("Kurs %s: enthalten in %s", ref_id, outer)
        return result

    def _fetch_all(self, courses: list[Course]) -> tuple[dict[int, list[IliasObject]], set[int]]:
        children: dict[int, list[IliasObject]] = {}
        failed: set[int] = set()
        scheduled: set[int] = set()
        pending: dict[Future, int] = {}

        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="crawl") as pool:
            def schedule(obj_type: str, ref_id: int, url: str) -> None:
                if ref_id not in scheduled:
                    scheduled.add(ref_id)
                    pending[pool.submit(self.list_children, obj_type, url)] = ref_id

            for course in courses:
                schedule(course.type, course.ref_id, course.url)
            try:
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        ref_id = pending.pop(future)
                        try:
                            objs = future.result()
                        except (SessionExpired, CrawlCancelled):
                            raise
                        except Exception as e:
                            # Ein kaputter Kurs oder Ordner hält die anderen nicht auf
                            log.warning("Seite nicht lesbar (ref_id %s): %s", ref_id, type(e).__name__)
                            debug.log.error("Seite nicht lesbar (ref_id %s):\n%s", ref_id,
                                            "".join(traceback.format_exception(e)))
                            failed.add(ref_id)
                            continue
                        children[ref_id] = objs
                        for obj in objs:
                            if obj.is_container and obj.url:
                                schedule(obj.type, obj.ref_id, obj.url)
            except BaseException:
                # SessionExpired, CrawlCancelled oder Strg+C: offene Abrufe verwerfen
                self.cancel.set()
                for future in pending:
                    future.cancel()
                raise
        return children, failed

    def _assemble(
        self,
        ref_id: int,
        path: list[str],
        children: dict[int, list[IliasObject]],
        failed: set[int],
        owner: dict[int, str],
        out: list[IliasObject],
        failed_paths: list[str],
    ) -> None:
        if ref_id in failed:
            failed_paths.append(" / ".join(path))
            return
        for child in children.get(ref_id, []):
            if child.ref_id in owner:
                continue
            owner[child.ref_id] = path[0]
            obj = replace(child, path=list(path))
            out.append(obj)
            if obj.is_container and obj.url:
                self._assemble(obj.ref_id, [*path, obj.name], children, failed, owner, out, failed_paths)
