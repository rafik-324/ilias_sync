from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ilias_sync.api import ActionKind, Config, DownloadedFile, ItemStatus, PlannedAction
from ilias_sync.gui import logic
from ilias_sync.gui.scheduler_gui import START_DELAY, next_due

NOW = datetime(2026, 9, 24, 15, 0)


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=20), "gerade eben"),
        (timedelta(minutes=5), "vor 5 Min."),
        (timedelta(hours=2), "heute 13:00"),
        (timedelta(days=1), "gestern 15:00"),
        (timedelta(days=3), "Mo 15:00"),
        (timedelta(days=30), "25.08.2026"),
    ],
)
def test_relative_time(delta, expected):
    assert logic.relative_time(NOW - delta, NOW) == expected


def test_relative_time_none():
    assert logic.relative_time(None) == "noch nie"


@pytest.mark.parametrize(
    "delta, expected",
    [
        (timedelta(seconds=30), "gleich"),
        (timedelta(minutes=12), "in 12 Min."),
        (timedelta(hours=2), "heute 17:00"),
        (timedelta(hours=10), "morgen 01:00"),
    ],
)
def test_future_time(delta, expected):
    assert logic.future_time(NOW + delta, NOW) == expected


@pytest.mark.parametrize(
    "size, expected",
    [(None, ""), (512, "512 B"), (2048, "2,0 KB"), (5_800_000, "5,5 MB"), (3 * 1024**3, "3,0 GB")],
)
def test_format_size(size, expected):
    assert logic.format_size(size) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Marketing (HWS 2026)", "Marketing"),
        ("Formale Grundlagen (FSS 2027)", "Formale Grundlagen"),
        ("Ohne Semester", "Ohne Semester"),
    ],
)
def test_course_display_name(name, expected):
    assert logic.course_display_name(name) == expected


def _file(course: str) -> DownloadedFile:
    return DownloadedFile(1, course, "a.pdf", Path("a.pdf"), NOW, ItemStatus.OK)


def test_bundle_new_files():
    assert logic.bundle_new_files([]) is None
    assert logic.bundle_new_files([_file("Marketing (HWS 2026)")]) == "1 neue Datei in Marketing"
    five = [_file("Marketing (HWS 2026)")] * 5
    assert logic.bundle_new_files(five) == "5 neue Dateien in Marketing"
    mixed = five + [_file("Formale Grundlagen (HWS 2026)")] * 7
    assert logic.bundle_new_files(mixed) == "12 neue Dateien in 2 Kursen"


def test_unassigned_course_text():
    assert logic.unassigned_course_text([]) is None
    assert logic.unassigned_course_text(["Statistik I (FSS 2026)"]) == (
        "Neuer Kurs gefunden: Statistik I. Bitte im Fenster zuordnen."
    )
    assert logic.unassigned_course_text(["A", "B"]) == "2 neue Kurse gefunden. Bitte im Fenster zuordnen."


def _action(kind: ActionKind, rel: str, size: int | None = 100) -> PlannedAction:
    root = Path("/uni")
    return PlannedAction(kind, 1, "Kurs", "Kurs / x", root / rel, size)


def test_build_preview_tree_relative_and_filter():
    actions = [
        _action(ActionKind.NEW, "01_SEMESTER/01_marketing/01_Vorlesung/a.pdf"),
        _action(ActionKind.NEW, "01_SEMESTER/01_marketing/01_Vorlesung/b.pdf"),
        _action(ActionKind.SKIP_REMOVED, "01_SEMESTER/01_marketing/02_Übung/c.pdf"),
    ]
    tree = logic.build_preview_tree(actions, Path("/uni"))
    course = tree.children["01_SEMESTER"].children["01_marketing"]
    assert set(course.children) == {"01_Vorlesung", "02_Übung"}
    assert tree.count_files() == 3

    only = logic.build_preview_tree(actions, Path("/uni"), only_changes=True)
    course = only.children["01_SEMESTER"].children["01_marketing"]
    assert set(course.children) == {"01_Vorlesung"}


def test_tree_sorts_folders_first():
    actions = [
        _action(ActionKind.NEW, "K/a.pdf"),
        _action(ActionKind.NEW, "K/Z_Ordner/b.pdf"),
    ]
    tree = logic.build_preview_tree(actions, Path("/uni"))
    names = [n.name for n in tree.children["K"].sorted_children()]
    assert names == ["Z_Ordner", "a.pdf"]


def test_summarize_text():
    actions = [
        _action(ActionKind.NEW, "a", 1024 * 1024),
        _action(ActionKind.NEW, "b", 1024 * 1024),
        _action(ActionKind.UPDATE, "c"),
        _action(ActionKind.CONFLICT, "d"),
        _action(ActionKind.SKIP_IGNORED, "e"),
    ]
    assert logic.summarize(actions).text() == (
        "2 neue Dateien (2,0 MB), 1 Aktualisierung, 1 Konflikt, 1 übersprungen"
    )


@pytest.mark.parametrize(
    "name, existing, expected",
    [
        ("Marketing (HWS 2026)", [], "01_marketing"),
        ("Statistik I (FSS 2026)", ["00_mathe_vorkurs", "05_formale_grundlagen"], "06_statistik_i"),
        ("Recht: Grundlagen? (HWS 2026)", ["01_x", "notes"], "02_recht_grundlagen"),
        ("Wirtschaftsinformatik 1 (HWS 2026)", ["09_a", "10_b"], "11_wirtschaftsinformatik_1"),
    ],
)
def test_suggest_course_dir(name, existing, expected):
    assert logic.suggest_course_dir(name, existing) == expected


def test_suggest_course_dir_nfc():
    nfd = "Übung (HWS 2026)"  # Ü in NFD wie auf macOS
    assert logic.suggest_course_dir(nfd, []) == "01_übung"


def _cfg(**kw) -> Config:
    base = dict(base_dir=Path("/uni"))
    base.update(kw)
    return Config(**base)


def test_validate_settings_ok():
    assert logic.validate_settings(_cfg()) == []


@pytest.mark.parametrize(
    "kw, fragment",
    [
        ({"base_dir": None}, "Basisordner"),
        ({"root_folder_name": ""}, "darf nicht leer"),
        ({"root_folder_name": "Uni:Mannheim"}, "unzulässige Zeichen"),
        ({"root_folder_name": "Uni."}, "unzulässige Zeichen"),
        ({"sync_interval_minutes": 5}, "Intervall"),
        ({"max_parallel": 9}, "Parallelität"),
        ({"semester_pattern": "(HWS"}, "Semester Muster"),
        ({"semester_pattern": r"HWS \d{4}"}, "zwei Gruppen"),
    ],
)
def test_validate_settings_errors(kw, fragment):
    errors = logic.validate_settings(_cfg(**kw))
    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Marketing (HWS 2026)", "HWS 2026"),
        ("Statistik I [V] (FSS 2027)", "FSS 2027"),
        ("MSDNAA Lizenzkurs", None),
    ],
)
def test_semester_of(name, expected):
    assert logic.semester_of(name, r"\((HWS|FSS) (\d{4})\)") == expected


def test_semester_rows_ergaenzt_fehlende():
    from ilias_sync.api import CourseConfig

    cfg = Config(base_dir=Path("x"), semesters={"HWS 2026": "01_SEMESTER"}, courses=[
        CourseConfig(1, "Marketing (HWS 2026)", "01_marketing"),
        CourseConfig(2, "Statistik I (FSS 2027)", None),
    ])
    assert logic.semester_rows(cfg) == [("HWS 2026", "01_SEMESTER"), ("FSS 2027", "")]


def test_next_due():
    start = NOW
    assert next_due(None, 60, start) == start + START_DELAY
    assert next_due(NOW, 60, start) == NOW + timedelta(minutes=60)
    # nach dem Ruhezustand: überfälliger Termin liegt in der Vergangenheit, also sofort fällig
    old = NOW - timedelta(hours=5)
    assert next_due(old, 60, NOW - timedelta(hours=6)) < NOW


@pytest.mark.parametrize(
    "off, expected",
    [
        ([], "Alles"),
        (["Videos"], "Ohne Videos"),
        (["Videos", "Foren"], "Ohne Videos und Foren"),
        (["Videos", "Foren", "Weblinks"], "3 von 6"),
        (["Dateien", "Videos", "Übungen", "Weblinks", "Foren", "Sonstiges"], "Nichts"),
    ],
)
def test_contents_summary(off, expected):
    labels = ["Dateien", "Videos", "Übungen", "Weblinks", "Foren", "Sonstiges"]
    assert logic.contents_summary([(label, label not in off) for label in labels]) == expected
