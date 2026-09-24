"""Parser Tests gegen gekürzte echte ILIAS Seiten (tests/fixtures/)."""

from datetime import date, datetime
from pathlib import Path

import pytest

from ilias_sync import crawler
from ilias_sync.crawler import Course, Crawler, IliasObject

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------- Dashboard ----------------

def test_dashboard_courses():
    courses = crawler.parse_dashboard(fixture("dashboard.html"))
    names = [c.name for c in courses]
    assert len(courses) == 11
    assert names[0] == "License Information (Student University of Mannheim)"
    assert "Praktische Informatik I [Ü] [1. PG] (HWS 2026)" in names

    marketing = next(c for c in courses if c.ref_id == 1746845)
    assert marketing == Course(
        type="crs",
        ref_id=1746845,
        name="Marketing [V] (HWS 2026)",
        url="https://ilias.uni-mannheim.de/goto.php/crs/1746845",
        semester="HWS 2026",
        group_heading="HWS 2026",
    )


def test_dashboard_groups_and_semester():
    courses = {c.ref_id: c for c in crawler.parse_dashboard(fixture("dashboard.html"))}
    group = courses[1746847]
    assert group.type == "grp"
    assert group.name == "Marketing [2. PG]"
    # Gruppen haben kein Semester im Namen, die Dashboard Überschrift hilft
    assert group.semester is None
    assert group.group_heading == "HWS 2026"
    assert courses[342184].semester is None
    assert courses[342184].group_heading == "MSDNAA"


def test_dashboard_without_panel_returns_empty():
    assert crawler.parse_dashboard("<html><h2>Neuigkeiten</h2></html>") == []


# ---------------- Container ----------------

def test_course_page_types():
    objs = crawler.parse_container(fixture("course_pi1.html"))
    summary = [(o.type, o.ref_id, o.name) for o in objs]
    assert ("fold", 1792450, "Vorlesung") in summary
    assert ("frm", 1792448, "Forum") in summary
    assert ("webr", 1809629, "Java lernen") in summary
    assert ("xtra", 1792445, "Q&A") in summary
    vorlesung = next(o for o in objs if o.ref_id == 1792450)
    assert vorlesung.url == "https://ilias.uni-mannheim.de/goto.php/fold/1792450"
    assert vorlesung.is_container


def test_course_page_plugins_and_group_without_access():
    objs = {o.ref_id: o for o in crawler.parse_container(fixture("course_marketing.html"))}
    quiz = [o for o in objs.values() if o.type == "xuiz"]
    assert len(quiz) == 10
    assert quiz[0].type_label == "MobileQuiz"
    # Gruppe ohne Zugriff: Titel ja, Link nein
    no_access = [o for o in objs.values() if o.type == "grp" and o.url is None]
    assert [o.name for o in no_access] == ["Marketing [1. PG]"]
    assert objs[1746847].url == "https://ilias.uni-mannheim.de/goto.php/grp/1746847"


def test_folder_of_folders():
    objs = crawler.parse_container(fixture("folder_of_folders.html"))
    assert len(objs) == 14
    assert all(o.type == "fold" for o in objs)
    assert objs[0].name == "00 Einführung"
    assert objs[0].ref_id == 1792513


def test_course_page_from_page_editor():
    # Hier steht in li#item_row_ eine Block ID statt des Typs
    objs = crawler.parse_container(fixture("course_page_editor.html"))
    assert [(o.type, o.ref_id, o.name) for o in objs] == [
        ("fold", 1802434, "Große Übung"),
        ("fold", 1802393, "Tutorien"),
    ]


def _row(ref_id: int, href: str, icon: str = "", li_id: str = "item_row_123-1") -> str:
    return f"""
    <li id="{li_id}"><div class="ilContainerListItemOuter" data-list-item-id="lg_div_{ref_id}_pref_9">
      <img class="ilListItemIcon" alt="X" src="{icon}">
      <h3 class="il_ContainerItemTitle"><a class="il_ContainerItemTitle" href="{href}">Objekt {ref_id}</a></h3>
    </div></li>"""


@pytest.mark.parametrize(
    "row, expected",
    [
        (_row(1, "goto.php/fold/1", li_id="item_row_file-1"), "file"),
        (_row(2, "https://ilias.uni-mannheim.de/goto.php/exc/2"), "exc"),
        (_row(3, "#", icon="./assets/images/standard/icon_wiki.svg"), "wiki"),
        (_row(4, "ilias.php?baseClass=ilrepositorygui&cmdClass=ilObjFileGUI&cmd=sendfile&ref_id=4"), "file"),
        (_row(5, "ilias.php?baseClass=ilLinkResourceHandlerGUI&ref_id=5&cmd=calldirectlink"), "webr"),
        (_row(6, "ilias.php?baseClass=ilObjPluginDispatchGUI&ref_id=6"), "unknown"),
    ],
)
def test_type_detection_fallbacks(row, expected):
    (obj,) = crawler.parse_container(f"<ul>{row}</ul>")
    assert obj.type == expected


def test_file_properties():
    (f,) = crawler.parse_container(fixture("folder_with_file.html"))
    assert f.type == "file"
    assert f.ref_id == 1798786
    assert f.name == "HWS2026-pi1-Einführung.pdf"
    assert f.size == round(4.47 * 1024**2)
    assert f.mtime == datetime(2026, 9, 7, 13, 28)
    assert "cmd=sendfile" in f.url and "ref_id=1798786" in f.url


# ---------------- Hilfsfunktionen ----------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("4.47 MB", round(4.47 * 1024**2)),
        ("4,47 MB", round(4.47 * 1024**2)),
        ("1,024.5 KB", round(1024.5 * 1024)),
        ("1.024,5 KB", round(1024.5 * 1024)),
        ("512 Bytes", 512),
        ("290 B", 290),
        ("2 GB", 2 * 1024**3),
        ("pdf", None),
        ("Version: 2", None),
    ],
)
def test_parse_size(text, expected):
    assert crawler.parse_size(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("7. Sep 2026, 13:28", datetime(2026, 9, 7, 13, 28)),
        ("1. Mär 2027, 08:05", datetime(2027, 3, 1, 8, 5)),
        ("24. Okt 2026, 00:00", datetime(2026, 10, 24, 0, 0)),
        ("3. Dec 2026, 10:00", datetime(2026, 12, 3, 10, 0)),
        ("Heute, 09:15", datetime(2026, 9, 24, 9, 15)),
        ("Gestern, 17:00", datetime(2026, 9, 23, 17, 0)),
        ("Verfügbarkeit: 1. Sep 2026, 00:01 - 31. Jan 2027, 23:59", None),
        ("31. Feb 2026, 10:00", None),
        ("pdf", None),
    ],
)
def test_parse_date(text, expected):
    assert crawler.parse_date(text, today=date(2026, 9, 24)) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Marketing [V] (HWS 2026)", "HWS 2026"),
        ("Statistik I (FSS 2027)", "FSS 2027"),
        ("Marketing [2. PG]", None),
    ],
)
def test_semester_from_name(name, expected):
    assert crawler.semester_from_name(name) == expected


def test_parse_goto():
    assert crawler.parse_goto("https://ilias.uni-mannheim.de/goto.php/crs/1746845") == ("crs", 1746845)
    assert crawler.parse_goto("https://ilias.uni-mannheim.de/goto.php?target=fold_42") == ("fold", 42)
    assert crawler.parse_goto("ilias.php?baseClass=ilDashboardGUI") is None


# ---------------- Crawler Rekursion ----------------

class FakeCrawler(Crawler):
    """Liefert Container Inhalte aus einem Dict statt aus dem Netz."""

    def __init__(self, pages: dict[str, list[IliasObject]]):
        super().__init__(session=None, delay=0)
        self.pages = pages
        self.visited_urls = []

    def list_container(self, url):
        self.visited_urls.append(url)
        return [IliasObject(o.type, o.ref_id, o.name, o.url) for o in self.pages.get(url, [])]


def test_walk_recurses_and_builds_paths():
    pages = {
        "k": [IliasObject("fold", 2, "Vorlesung", "f2"), IliasObject("webr", 3, "Link", "w3")],
        "f2": [IliasObject("fold", 4, "Woche 1", "f4"), IliasObject("file", 5, "a.pdf", "d5")],
        "f4": [IliasObject("file", 6, "b.pdf", "d6")],
    }
    course = Course("crs", 1, "Kurs", "k", None, None)
    objs = list(FakeCrawler(pages).walk(course))
    assert [o.ilias_path for o in objs] == [
        "Kurs / Vorlesung",
        "Kurs / Vorlesung / Woche 1",
        "Kurs / Vorlesung / Woche 1 / b.pdf",
        "Kurs / Vorlesung / a.pdf",
        "Kurs / Link",
    ]


def test_walk_skips_already_visited_and_does_not_enter_non_containers():
    pages = {
        "k": [IliasObject("grp", 2, "Gruppe", "g2"), IliasObject("frm", 3, "Forum", "fr3")],
        "g2": [IliasObject("fold", 4, "Ordner", "f4")],
        "f4": [IliasObject("grp", 2, "Gruppe", "g2")],  # Zyklus
    }
    c = FakeCrawler(pages)
    visited: set[int] = set()
    objs = list(c.walk(Course("crs", 1, "Kurs", "k", None, None), visited))
    assert [o.ref_id for o in objs] == [2, 4, 3]
    assert c.visited_urls == ["k", "g2", "f4"]
    # Gruppe ist schon besucht, eigener Durchlauf liefert nichts Neues außer sich selbst
    again = list(FakeCrawler(pages).walk(Course("grp", 2, "Gruppe", "g2", None, None), visited))
    assert again == []
