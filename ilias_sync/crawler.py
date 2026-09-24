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
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .auth import ILIAS, TIMEOUT, is_logged_in, safe_url

DASHBOARD = ILIAS + "/ilias.php?baseClass=ilDashboardGUI"
DEFAULT_SEMESTER_PATTERN = r"\((HWS|FSS) (\d{4})\)"
# Mindestabstand zwischen zwei Seitenabrufen in Sekunden, auch bei Parallelität (Server schonen)
DEFAULT_DELAY = 0.5
DEFAULT_PARALLEL = 3
MAX_PARALLEL = 5

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
        r = self.session.get(url, timeout=TIMEOUT)
        with self._lock:
            self.pages_fetched += 1
            count = self.pages_fetched
        if self.on_page:
            self.on_page(count)
        r.raise_for_status()
        return r

    def _get(self, url: str) -> str:
        """Seite holen und sicherstellen, dass sie eingeloggt abgerufen wurde."""
        generation = self._session_generation
        r = self._fetch(url)
        if is_logged_in(r):
            return r.text

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
        return r.text

    def fetch_courses(self) -> list[Course]:
        return parse_dashboard(self._get(DASHBOARD), self.semester_pattern)

    def list_container(self, url: str) -> list[IliasObject]:
        return parse_container(self._get(url), url)

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
            self._assemble(course.ref_id, [course.name], children, failed, owner, objs, result.failed)
            result.objects[course.ref_id] = objs
        return result

    def _fetch_all(self, courses: list[Course]) -> tuple[dict[int, list[IliasObject]], set[int]]:
        children: dict[int, list[IliasObject]] = {}
        failed: set[int] = set()
        scheduled: set[int] = set()
        pending: dict[Future, int] = {}

        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="crawl") as pool:
            def schedule(ref_id: int, url: str) -> None:
                if ref_id not in scheduled:
                    scheduled.add(ref_id)
                    pending[pool.submit(self.list_container, url)] = ref_id

            for course in courses:
                schedule(course.ref_id, course.url)
            try:
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        ref_id = pending.pop(future)
                        try:
                            objs = future.result()
                        except requests.RequestException as e:
                            log.warning("Seite nicht lesbar (ref_id %s): %s", ref_id, type(e).__name__)
                            failed.add(ref_id)
                            continue
                        children[ref_id] = objs
                        for obj in objs:
                            if obj.is_container and obj.url:
                                schedule(obj.ref_id, obj.url)
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
