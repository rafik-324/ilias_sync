"""Parser Tests gegen gekürzte echte ILIAS Seiten (tests/fixtures/)."""

import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from ilias_sync import auth, crawler
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
        url="https://ilias.uni-mannheim.de/goto.php?target=crs_1746845",   # Permalink aus der ref_id
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


def test_dashboard_panel_found_without_heading_text():
    """Andere Oberflächensprache: das Panel wird an den Block Aktionen erkannt, nicht am Text."""
    html = fixture("dashboard.html").replace("Meine Kurse und Gruppen", "Mes cours et groupes")
    assert len(crawler.parse_dashboard(html)) == 11


def test_dashboard_tile_view_and_duplicates():
    html = """<div class="panel"><h2>Meine Kurse und Gruppen</h2>
      <div class="il-item-group"><h3>HWS 2026</h3><div class="il-deck">
        <div class="il-card thumbnail"><a href="goto.php/crs/7"><img alt=""></a>
          <div class="caption card-title"><a href="goto.php/crs/7">Marketing [V] (HWS 2026)</a></div></div>
        <div class="il-card thumbnail">
          <div class="caption card-title"><a href="goto.php?target=grp_8">Tutorium</a></div></div>
        <div class="il-card thumbnail">
          <div class="caption card-title"><a href="goto.php/crs/7">Marketing [V] (HWS 2026)</a></div></div>
      </div></div></div>"""
    courses = crawler.parse_dashboard(html)
    assert [(c.type, c.ref_id, c.url, c.group_heading) for c in courses] == [
        ("crs", 7, crawler.permalink("crs", 7), "HWS 2026"),
        ("grp", 8, crawler.permalink("grp", 8), "HWS 2026"),
    ]


class PageSession:
    """Antwortet je URL mit einer festen Seite, zählt gleichzeitige Abrufe."""

    def __init__(self, pages: dict[str, tuple[int, str]], delay: float = 0.0):
        self.pages = pages
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def get(self, url, timeout):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        status, html = self.pages.get(url, (200, LOGGED_IN))
        r = response(url, html)
        r.status_code = status
        return r


def test_fetch_courses_falls_back_to_membership_overview():
    panel = fixture("dashboard.html")
    s = PageSession({
        crawler.DASHBOARD: (200, LOGGED_IN),
        crawler.MEMBERSHIP_OVERVIEW: (200, panel + LOGGED_IN),
    })
    assert len(Crawler(s, delay=0).fetch_courses()) == 11


def test_fetch_courses_without_any_list_is_an_error():
    s = PageSession({crawler.DASHBOARD: (200, LOGGED_IN), crawler.MEMBERSHIP_OVERVIEW: (404, LOGGED_IN)})
    with pytest.raises(crawler.CourseListNotFound):
        Crawler(s, delay=0).fetch_courses()


def test_requests_on_one_session_run_one_after_another():
    s = PageSession({}, delay=0.02)
    c = Crawler(s, delay=0, max_parallel=5)
    threads = [threading.Thread(target=c._get, args=(f"{crawler.ILIAS}/x{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.max_active == 1


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


def test_items_in_blocks_with_negative_parent_id():
    # Seit Oktober 2026: Einträge in Blöcken der Kursseite haben "_pref_-1" statt der Eltern ref_id.
    # Früher wurden sie still verworfen, ganze Ordner fehlten
    html = """<div id="il_center_col">
      <li id="item_row_fold-1787941"><div class="ilContainerListItemOuter" data-list-item-id="lg_div_1787941_pref_-1">
        <h3 class="il_ContainerItemTitle"><a class="il_ContainerItemTitle"
          href="https://ilias.uni-mannheim.de/goto.php/fold/1787941">Tutorien</a></h3></div></li>
      <li id="item_row_1803410-1802434"><div class="ilContainerListItemOuter" data-list-item-id="lg_div_1802434_pref_-2">
        <img class="ilListItemIcon" alt="Ordner" src="./assets/images/standard/icon_fold.svg">
        <h3 class="il_ContainerItemTitle"><a class="il_ContainerItemTitle"
          href="https://ilias.uni-mannheim.de/goto.php/fold/1802434">Große Übung</a></h3></div></li>
    </div>"""
    assert [(o.type, o.ref_id, o.name) for o in crawler.parse_container(html)] == [
        ("fold", 1787941, "Tutorien"),
        ("fold", 1802434, "Große Übung"),
    ]


def test_unknown_list_item_id_is_logged(caplog):
    html = """<div class="ilContainerListItemOuter" data-list-item-id="lg_div_x_pref_y">
      <h3 class="il_ContainerItemTitle"><a class="il_ContainerItemTitle" href="goto.php/fold/1">A</a></h3></div>"""
    assert crawler.parse_container(html) == []
    assert "unbekannter Kennung" in caplog.text


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

    def __init__(self, pages: dict[str, list[IliasObject]], fail: set[str] = frozenset(), **kw):
        super().__init__(session=None, delay=0, **kw)
        self.pages = pages
        self.fail = fail
        self.visited_urls = []

    def list_container(self, url):
        self.visited_urls.append(url)
        if url in self.fail:
            raise requests.ConnectionError("simuliert")
        return [IliasObject(o.type, o.ref_id, o.name, o.url) for o in self.pages.get(url, [])]


def course(ref_id: int, name: str, url: str, kind: str = "crs") -> Course:
    return Course(kind, ref_id, name, url, None, None)


def test_crawl_recurses_and_builds_paths_in_order():
    pages = {
        "k": [IliasObject("fold", 2, "Vorlesung", "f2"), IliasObject("webr", 3, "Link", "w3")],
        "f2": [IliasObject("fold", 4, "Woche 1", "f4"), IliasObject("file", 5, "a.pdf", "d5")],
        "f4": [IliasObject("file", 6, "b.pdf", "d6")],
    }
    result = FakeCrawler(pages, max_parallel=3).crawl([course(1, "Kurs", "k")])
    assert [o.ilias_path for o in result.objects[1]] == [
        "Kurs / Vorlesung",
        "Kurs / Vorlesung / Woche 1",
        "Kurs / Vorlesung / Woche 1 / b.pdf",
        "Kurs / Vorlesung / a.pdf",
        "Kurs / Link",
    ]
    assert result.failed == []


def test_crawl_fetches_each_container_once_and_handles_cycles():
    pages = {
        "k": [IliasObject("grp", 2, "Gruppe", "g2"), IliasObject("frm", 3, "Forum", "fr3")],
        "g2": [IliasObject("fold", 4, "Ordner", "f4")],
        "f4": [IliasObject("grp", 2, "Gruppe", "g2")],  # Zyklus
    }
    c = FakeCrawler(pages)
    result = c.crawl([course(1, "Kurs", "k"), course(2, "Gruppe", "g2", "grp")])
    assert [o.ref_id for o in result.objects[1]] == [2, 4, 3]
    # Forum ist kein Container, Gruppe wird trotz zwei Fundstellen nur einmal abgerufen
    assert sorted(c.visited_urls) == ["f4", "g2", "k"]
    # Die Gruppe steht auch auf dem Dashboard, ist aber schon im Kurs enthalten
    assert result.nested_in == {2: "Kurs"}
    assert 2 not in result.objects


def test_crawl_group_first_on_dashboard_keeps_its_own_tree():
    pages = {"g2": [IliasObject("file", 5, "a.pdf", "d5")], "k": [IliasObject("grp", 2, "Gruppe", "g2")]}
    result = FakeCrawler(pages).crawl([course(2, "Gruppe", "g2", "grp"), course(1, "Kurs", "k")])
    assert [o.name for o in result.objects[2]] == ["a.pdf"]
    assert result.objects[1] == []


def test_crawl_records_unreadable_pages_instead_of_treating_them_as_empty():
    pages = {
        "k": [IliasObject("fold", 2, "Kaputt", "f2"), IliasObject("fold", 3, "Gut", "f3")],
        "f3": [IliasObject("file", 5, "a.pdf", "d5")],
    }
    result = FakeCrawler(pages, fail={"f2"}).crawl([course(1, "Kurs", "k")])
    assert result.failed == ["Kurs / Kaputt"]
    assert [o.name for o in result.objects[1]] == ["Kaputt", "Gut", "a.pdf"]


def test_unexpected_error_in_one_course_does_not_stop_others():
    class Broken(FakeCrawler):
        def list_container(self, url):
            if url == "k1":
                raise AttributeError("Parser kaputt")
            return super().list_container(url)

    pages = {"k2": [IliasObject("file", 5, "a.pdf", "d5")]}
    result = Broken(pages).crawl([course(1, "Kaputt", "k1"), course(2, "Gut", "k2")])
    assert result.failed == ["Kaputt"]
    assert [o.name for o in result.objects[2]] == ["a.pdf"]


def test_crawl_is_parallel():
    # 6 Ordner, jeder Abruf dauert 0,2 s: nacheinander 1,2 s, mit 3 Threads etwa 0,4 s
    class Slow(FakeCrawler):
        def list_container(self, url):
            time.sleep(0.2)
            return super().list_container(url)

    pages = {"k": [IliasObject("fold", i, f"O{i}", f"f{i}") for i in range(2, 8)]}
    started = time.monotonic()
    Slow(pages, max_parallel=3).crawl([course(1, "Kurs", "k")])
    assert time.monotonic() - started < 0.9


def test_max_parallel_is_capped():
    assert FakeCrawler({}, max_parallel=50).max_parallel == crawler.MAX_PARALLEL
    assert FakeCrawler({}, max_parallel=0).max_parallel == 1


# ---------------- Abstand und Session ----------------

LOGGED_IN = '<html><a href="logout.php?lang=de">Abmelden</a></html>'
LOGIN_PAGE = '<html><a href="login.php?cmd=force_login">Anmelden</a></html>'


def response(url: str, html: str) -> requests.Response:
    r = requests.Response()
    r.url = url
    r.status_code = 200
    r._content = html.encode("utf-8")
    r.encoding = "utf-8"
    return r


class ScriptedSession:
    """Antwortet der Reihe nach mit vorgegebenen Seiten, merkt sich die Zeitpunkte."""

    def __init__(self, pages: list[tuple[str, str]]):
        self.pages = list(pages)
        self.times = []
        self.lock = threading.Lock()

    def get(self, url, timeout):
        with self.lock:
            self.times.append(time.monotonic())
            final_url, html = self.pages.pop(0) if self.pages else (url, LOGGED_IN)
        return response(final_url, html)


def test_throttle_spaces_requests_across_threads():
    s = ScriptedSession([])
    c = Crawler(s, delay=0.1, max_parallel=5)
    threads = [threading.Thread(target=c._get, args=(f"{crawler.ILIAS}/x{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    times = sorted(s.times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) == 5
    assert min(gaps) >= 0.09


def test_expired_session_triggers_one_relogin_and_retry():
    url = crawler.ILIAS + "/goto.php/fold/1"
    s = ScriptedSession([(crawler.ILIAS + "/login.php", LOGIN_PAGE), (url, LOGGED_IN)])
    calls = []
    c = Crawler(s, delay=0, relogin=lambda: calls.append(1))
    assert c._get(url) == LOGGED_IN
    assert calls == [1]
    assert c.pages_fetched == 2


def test_expired_session_without_relogin_aborts():
    s = ScriptedSession([(crawler.ILIAS + "/login.php", LOGIN_PAGE)])
    with pytest.raises(crawler.SessionExpired):
        Crawler(s, delay=0)._get(crawler.ILIAS + "/goto.php/fold/1")


def test_relogin_failure_aborts_with_message_without_url():
    s = ScriptedSession([(crawler.ILIAS + "/login.php?sessid=GEHEIM", LOGIN_PAGE)])

    def fail():
        raise auth.InvalidCredentials("Uni-ID oder Passwort falsch.")

    with pytest.raises(crawler.SessionExpired, match="Passwort falsch") as exc:
        Crawler(s, delay=0, relogin=fail)._get(crawler.ILIAS + "/goto.php/fold/1")
    assert "GEHEIM" not in str(exc.value)


def test_still_logged_out_after_relogin_aborts():
    login = (crawler.ILIAS + "/login.php", LOGIN_PAGE)
    s = ScriptedSession([login, login])
    with pytest.raises(crawler.SessionExpired, match="auch nach neuem Login"):
        Crawler(s, delay=0, relogin=lambda: None)._get(crawler.ILIAS + "/goto.php/fold/1")


def test_session_expiry_aborts_whole_crawl():
    s = ScriptedSession([(crawler.ILIAS + "/login.php", LOGIN_PAGE)])
    with pytest.raises(crawler.SessionExpired):
        Crawler(s, delay=0).crawl([course(1, "Kurs", crawler.ILIAS + "/goto.php/crs/1")])


def test_parallel_expiry_logs_in_only_once():
    # Drei Threads bekommen gleichzeitig die Login Seite, nur einer loggt neu ein
    login = (crawler.ILIAS + "/login.php", LOGIN_PAGE)
    s = ScriptedSession([login, login, login])
    calls = []
    barrier = threading.Barrier(3)

    class Synced(Crawler):
        def _fetch(self, url):
            r = super()._fetch(url)
            if len(calls) == 0 and not auth.is_logged_in(r):
                barrier.wait(timeout=2)
            return r

    c = Synced(s, delay=0, relogin=lambda: (time.sleep(0.05), calls.append(1)))
    threads = [threading.Thread(target=c._get, args=(f"{crawler.ILIAS}/goto.php/fold/{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]


# ---------------- Übungen ----------------

EXC_BASE = "https://ilias.uni-mannheim.de/ilias.php"
OVERVIEW_URL = ("ilias.php?baseClass=ilexercisehandlergui&cmdNode=cz:nw&cmdClass=ilObjExerciseGUI"
                "&cmd=showOverview&ref_id=7&mode={mode}&from_overview=1")
ASS_URL = ("ilias.php?baseClass=ilexercisehandlergui&cmdNode=cz:nw:5a&cmdClass=ilAssignmentPresentationGUI"
           "&ref_id=7&mode=all&from_overview=1&ass_id={ass_id}")


def _mode_buttons(engaged: str) -> str:
    return "".join(
        f'<button class="btn btn-default{" engaged" if mode == engaged else ""}" '
        f'aria-pressed="{"true" if mode == engaged else "false"}" data-action="{OVERVIEW_URL.format(mode=mode)}">'
        f"{label}</button>"
        for mode, label in (("ongoing", "Laufende"), ("past", "Vergangene"), ("all", "Alle"))
    )


def test_exercise_all_mode_url():
    url = crawler.exercise_all_mode_url(_mode_buttons("ongoing"), EXC_BASE)
    assert url == "https://ilias.uni-mannheim.de/" + OVERVIEW_URL.format(mode="all")
    assert crawler.exercise_all_mode_url(_mode_buttons("all"), EXC_BASE) is None
    assert crawler.exercise_all_mode_url("<p>keine Knöpfe</p>", EXC_BASE) is None


def _assignment_item(ass_id: int, title: str) -> str:
    href = ASS_URL.format(ass_id=ass_id)
    return f"""<li class="il-std-item-container"><div class="il-item il-std-item">
      <h4 class="il-item-title"><a href="{href}">{title}</a></h4>
      <ul class="dropdown-menu"><li><a href="{href}">Arbeitsanweisung anzeigen</a></li>
      <li><a href="ilias.php?cmdClass=ilExSubmissionFileGUI&cmd=submissionScreen&ass_id={ass_id}">
        Bereits abgegebene Dateien</a></li></ul></div></li>"""


def test_parse_exercise_overview():
    html = f'<div id="il_center_col">{_mode_buttons("all")}<ul>' \
           f'{_assignment_item(26131, "P09")}{_assignment_item(26122, "P01")}{_assignment_item(26122, "P01")}</ul></div>'
    objs = crawler.parse_exercise_overview(html, EXC_BASE)
    assert [(o.type, o.ref_id, o.name) for o in objs] == [
        (crawler.ASSIGNMENT, -26131, "P09"),
        (crawler.ASSIGNMENT, -26122, "P01"),
    ]
    assert objs[0].is_container
    assert objs[0].url == "https://ilias.uni-mannheim.de/" + ASS_URL.format(ass_id=26131)


def _download_row(label: str, cmd: str, extra: str = "") -> str:
    href = (f"ilias.php?baseClass=ilexercisehandlergui&cmdNode=cz:nw:5a:cp&cmdClass=ilexsubmissiongui"
            f"&cmd={cmd}&ref_id=7&ass_id=26122&mode=all{extra}")
    return f"""<div class="row"><div class="control-label col-sm-4"><p>{label}</p></div>
      <div class="col-sm-8"><p><a href="{href}">Download</a></p></div></div>"""


def test_parse_assignment_files_and_solution_but_not_own_submissions():
    html = f"""<div id="il_center_col">
      {_download_row("P1.pdf", "downloadFile", "&file=P1.pdf")}
      {_download_row("vorgaben-P1.zip", "downloadFile", "&file=vorgaben-P1.zip")}
      <a href="ilias.php?cmdClass=ilExSubmissionFileGUI&cmd=submissionScreen&ass_id=26122">Bereits abgegebene</a>
      <a href="ilias.php?cmdClass=ilExSubmissionFileGUI&cmd=download&ass_id=26122&delivered=5">Aufgabe_1_1.java</a>
      {_download_row("lsgen-P1.zip", "downloadGlobalFeedbackFile")}
    </div>"""
    files = crawler.parse_assignment(html, EXC_BASE)
    assert [f.name for f in files] == ["P1.pdf", "vorgaben-P1.zip", "lsgen-P1.zip"]
    assert all(f.type == "file" and f.exercise and f.ref_id < 0 for f in files)
    assert files[2].url.startswith("https://ilias.uni-mannheim.de/ilias.php?")
    # Kennungen stabil über Läufe und eindeutig
    assert [f.ref_id for f in crawler.parse_assignment(html, EXC_BASE)] == [f.ref_id for f in files]
    assert len({f.ref_id for f in files}) == 3


def test_crawl_descends_into_exercises():
    class ExcCrawler(FakeCrawler):
        def list_exercise(self, url):
            self.visited_urls.append(url)
            return [IliasObject(crawler.ASSIGNMENT, -11, "P01", "a11")]

        def list_assignment(self, url):
            self.visited_urls.append(url)
            return [IliasObject("file", -99, "p1.pdf", "d99", exercise=True)]

    pages = {"k": [IliasObject("fold", 2, "Tutorien", "f2")], "f2": [IliasObject("exc", 3, "Projektblätter", "e3")]}
    result = ExcCrawler(pages).crawl([course(1, "Kurs", "k")])
    assert [o.ilias_path for o in result.objects[1]] == [
        "Kurs / Tutorien",
        "Kurs / Tutorien / Projektblätter",
        "Kurs / Tutorien / Projektblätter / P01",
        "Kurs / Tutorien / Projektblätter / P01 / p1.pdf",
    ]
    assert result.objects[1][-1].exercise
