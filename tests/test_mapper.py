"""Mapper Tests mit der Beispielstruktur aus CLAUDE.md (in tmp_path nachgebaut)."""

from pathlib import Path

import pytest

from ilias_sync import mapper
from ilias_sync.api import Config, CourseConfig, CourseRule
from ilias_sync.crawler import Course, CrawlResult, IliasObject

STRUCTURE = [
    "01_SEMESTER/00_mathe_vorkurs/01_Vorlesung",
    "01_SEMESTER/00_mathe_vorkurs/02_Übung",
    "01_SEMESTER/01_marketing/01_Vorlesung",
    "01_SEMESTER/01_marketing/02_Übung",
    "01_SEMESTER/01_marketing/03_Tutorium",
    "01_SEMESTER/02_praktische_informatik_I/01_Vorlesung/01 Einführung",
    "01_SEMESTER/02_praktische_informatik_I/02_Übung",
    "01_SEMESTER/02_praktische_informatik_I/03_Tutorium",
    "01_SEMESTER/02_praktische_informatik_I/04_code/01_übung-MacBook Pro von Rafael",
    "01_SEMESTER/03_programmier_praktikum_1/01_Vorlesung",
    "01_SEMESTER/03_programmier_praktikum_1/02_Übung",
    "01_SEMESTER/03_programmier_praktikum_1/03_Tutorium/01_tutorium",
    "01_SEMESTER/03_programmier_praktikum_1/04_freiwillige_abgaben",
    "01_SEMESTER/03_programmier_praktikum_1/linux_data",
    "01_SEMESTER/04_wirtschaftsinformatik_1/01_vorlesung",
    "01_SEMESTER/04_wirtschaftsinformatik_1/02_tutorium",
    "01_SEMESTER/05_formale_grundlagen/01_Vorlesung",
    "01_SEMESTER/05_formale_grundlagen/02_Übung",
    "01_SEMESTER/05_formale_grundlagen/03_Übungsblätter",
    "02_Studium_vault",
    "code_projects",
]
IGNORE = ["04_code", "linux_data", "*-MacBook*", "* von *"]
PATTERN = Config(base_dir=None).semester_pattern


@pytest.fixture
def root(tmp_path) -> Path:
    for rel in STRUCTURE:
        (tmp_path / rel).mkdir(parents=True)
    return tmp_path


def make_mapper(root: Path, **cfg) -> mapper.Mapper:
    return mapper.Mapper(Config(base_dir=None, ignore_local_dirs=IGNORE, **cfg), root)


def crs(ref_id: int, name: str, kind: str = "crs", heading: str | None = "HWS 2026") -> Course:
    from ilias_sync.crawler import semester_from_name
    return Course(kind, ref_id, name, f"u{ref_id}", semester_from_name(name), heading)


def tree(course: Course, spec: list[tuple[str, str, int]]) -> list[IliasObject]:
    """spec: (Typ, ILIAS Pfad ab Kurs mit " / ", ref_id) in DFS Reihenfolge."""
    objs = []
    for obj_type, path, ref_id in spec:
        *parents, name = path.split(" / ")
        objs.append(IliasObject(obj_type, ref_id, name, f"u{ref_id}", path=[course.name, *parents]))
    return objs


def map_one(root: Path, course: Course, spec, **cfg) -> dict[str, str]:
    result = CrawlResult(courses=[course], objects={course.ref_id: tree(course, spec)})
    mapped = make_mapper(root, **cfg).map_crawl(result)
    return {it.obj.ilias_path.split(" / ", 1)[1]: "/".join(it.rel_path) for it in mapped.items}


# ---------------- Normalisierung und Schlüsselwörter ----------------

@pytest.mark.parametrize(
    "a, b",
    [
        ("02_Übung", "Übung"),
        ("02_uebung", "Übung"),
        ("01_vorlesung", "Vorlesung"),
        ("01 Einführung", "00 Einführung"),
        ("03_Übungsblätter", "Uebungsblaetter"),
    ],
)
def test_folder_key_equal(a, b):
    assert mapper.folder_key(a) == mapper.folder_key(b)


def test_folder_key_keeps_short_numbers_apart():
    assert mapper.folder_key("1. PG") != mapper.folder_key("2. PG")


@pytest.mark.parametrize(
    "a, b, equal",
    [
        ("Tutorium 01", "01_tutorium", True),
        ("Tutorium_1", "01_Tutorium", True),
        ("Übung 2", "02_uebung", True),
        ("Tutorium 01", "02_tutorium", False),
        ("1. PG", "2. PG", False),
    ],
)
def test_loose_folder_key(a, b, equal):
    assert (mapper.loose_folder_key(a) == mapper.loose_folder_key(b)) is equal


def test_swapped_number_finds_existing_folder(root):
    c = crs(1, "Programmierpraktikum I, Große Übung [Ü] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Tutorien", 2),
        ("fold", "Tutorien / Tutorium 01", 3),
        ("file", "Tutorien / Tutorium 01 / a.pdf", 4),
        ("fold", "Tutorien / Tutorium 02", 5),
        ("file", "Tutorien / Tutorium 02 / b.pdf", 6),
    ])
    assert paths == {
        "Tutorien / Tutorium 01 / a.pdf": "01_SEMESTER/03_programmier_praktikum_1/03_Tutorium/01_tutorium/a.pdf",
        # 02_tutorium gibt es im Test Aufbau nicht: neuer Ordner mit ILIAS Namen
        "Tutorien / Tutorium 02 / b.pdf": "01_SEMESTER/03_programmier_praktikum_1/03_Tutorium/tutorium_02/b.pdf",
    }


@pytest.mark.parametrize(
    "name, suffix, expected",
    [
        ("Vorlesung", None, "Vorlesung"),
        ("Vorlesungsfolien", None, "Vorlesung"),
        ("Lecture Slides", None, "Vorlesung"),
        ("Übung", None, "Übung"),
        ("Uebung 01", None, "Übung"),
        ("Tutorien", None, "Tutorium"),
        ("Tut1-PG3", None, "Tutorium"),
        ("Institut", None, None),
        ("Übungsblätter", None, "Übungsblätter"),
        ("Aufgabenblatt 3", None, "Übungsblätter"),
        ("Abgaben", None, "Abgaben"),
        ("Linux: Folien und Übung", "Übung", "Übung"),
        ("Studenteninitiativen", None, None),
        ("Praktische Informatik I - Tut [1. PG]", None, "Tutorium"),
    ],
)
def test_keyword_category(name, suffix, expected):
    assert mapper.keyword_category(name, preferred=suffix) == expected


@pytest.mark.parametrize(
    "name, pure",
    [("Vorlesung", True), ("Tutorien", True), ("Übungsblätter", True), ("Vorlesungsfolien", True),
     ("Große Übung", True), ("Übung 01", False), ("Tutorium_1", False), ("Linux: Folien und Übung", False)],
)
def test_is_pure_category_name(name, pure):
    assert mapper.is_pure_category_name(name) is pure


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Praktische Informatik I [V] [1. PG] (HWS 2026)", "Vorlesung"),
        ("Praktische Informatik I [Ü] [1. PG] (HWS 2026)", "Übung"),
        ("Praktische Informatik I [T] (HWS 2026)", "Tutorium"),
        ("Marketing [2. PG]", None),
    ],
)
def test_course_suffix_category(name, expected):
    assert mapper.course_suffix_category(name) == expected


# ---------------- Kurs Zuordnung ----------------

LOCAL_COURSES = ["00_mathe_vorkurs", "01_marketing", "02_praktische_informatik_I",
                 "03_programmier_praktikum_1", "04_wirtschaftsinformatik_1", "05_formale_grundlagen"]


@pytest.mark.parametrize(
    "ilias, expected",
    [
        ("Marketing [V] (HWS 2026)", "01_marketing"),
        ("Praktische Informatik I [V] [1. PG] (HWS 2026)", "02_praktische_informatik_I"),
        ("Praktische Informatik I [Ü] [1. PG] (HWS 2026)", "02_praktische_informatik_I"),
        ("Praktische Informatik I [T] (HWS 2026)", "02_praktische_informatik_I"),
        ("Programmierpraktikum I, Große Übung [Ü] (HWS 2026)", "03_programmier_praktikum_1"),
        ("Programmierpraktikum I [V] (HWS 2026)", "03_programmier_praktikum_1"),
        ("IS 201 Wirtschaftsinformatik I: Einführung und Grundlagen [V] (HWS 2026)", "04_wirtschaftsinformatik_1"),
        ("Formale Grundlagen der Informatik [V] (HWS 2026)", "05_formale_grundlagen"),
        ("Statistik I (HWS 2026)", None),
    ],
)
def test_best_course_dir(ilias, expected):
    assert mapper.best_course_dir(ilias, LOCAL_COURSES, PATTERN)[0] == expected


def test_assign_semesters_chronologically():
    assert mapper.assign_semesters(["FSS 2027", "HWS 2026"], {}, ["01_SEMESTER"]) == {
        "HWS 2026": "01_SEMESTER",
        "FSS 2027": "02_semester",
    }
    assert mapper.assign_semesters(["FSS 2027"], {"HWS 2026": "01_SEMESTER"}, ["01_SEMESTER"]) == {
        "HWS 2026": "01_SEMESTER",
        "FSS 2027": "02_semester",
    }


# ---------------- Ganze Pfade ----------------

def test_lecture_course_with_dateien_folder(root):
    c = crs(1, "Marketing [V] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Dateien", 2),
        ("fold", "Dateien / Tutorium", 3),
        ("file", "Dateien / Tutorium / Skript.pdf", 4),
        ("fold", "Dateien / Übung", 5),
        ("fold", "Dateien / Übung / Zusatzmaterialien Case Study", 6),
        ("file", "Dateien / Übung / Zusatzmaterialien Case Study / Case.pdf", 7),
        ("file", "Dateien / Übung / Ü1.pdf", 8),
        ("fold", "Dateien / Vorlesung", 9),
        ("file", "Dateien / Vorlesung / Folien.pdf", 10),
    ])
    assert paths == {
        "Dateien / Tutorium / Skript.pdf": "01_SEMESTER/01_marketing/03_Tutorium/skript.pdf",
        "Dateien / Übung / Zusatzmaterialien Case Study / Case.pdf":
            "01_SEMESTER/01_marketing/02_Übung/zusatzmaterialien_case_study/case.pdf",
        "Dateien / Übung / Ü1.pdf": "01_SEMESTER/01_marketing/02_Übung/ue1.pdf",
        "Dateien / Vorlesung / Folien.pdf": "01_SEMESTER/01_marketing/01_Vorlesung/folien.pdf",
    }


def test_exercise_course_without_keyword_uses_suffix(root):
    c = crs(1, "Praktische Informatik I [Ü] [1. PG] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Dateien", 2),
        ("fold", "Dateien / W01", 3),
        ("file", "Dateien / W01 / Blatt.pdf", 4),
    ])
    assert paths == {"Dateien / W01 / Blatt.pdf": "01_SEMESTER/02_praktische_informatik_I/02_Übung/w01/blatt.pdf"}


def test_tutorial_inside_exercise_course_goes_to_tutorium(root):
    c = crs(1, "Programmierpraktikum I, Große Übung [Ü] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Große Übung", 2),
        ("fold", "Große Übung / Übung 01", 3),
        ("file", "Große Übung / Übung 01 / Uebung_1.pdf", 4),
        ("fold", "Tutorien", 5),
        ("fold", "Tutorien / Tutorium 01", 6),
        ("file", "Tutorien / Tutorium 01 / Tut.pdf", 7),
    ])
    assert paths == {
        "Große Übung / Übung 01 / Uebung_1.pdf": "01_SEMESTER/03_programmier_praktikum_1/02_Übung/uebung_01/uebung_1.pdf",
        "Tutorien / Tutorium 01 / Tut.pdf": "01_SEMESTER/03_programmier_praktikum_1/03_Tutorium/01_tutorium/tut.pdf",
    }


def test_existing_lowercase_category_and_subfolder_reused(root):
    c = crs(1, "IS 201 Wirtschaftsinformatik I: Einführung und Grundlagen [V] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Tutorium", 2),
        ("file", "Tutorium / T1.pdf", 3),
        ("file", "V1.pdf", 4),
    ])
    assert paths == {
        "Tutorium / T1.pdf": "01_SEMESTER/04_wirtschaftsinformatik_1/02_tutorium/t1.pdf",
        "V1.pdf": "01_SEMESTER/04_wirtschaftsinformatik_1/01_vorlesung/v1.pdf",
    }


def test_existing_deeper_folder_found_despite_other_number(root):
    c = crs(1, "Praktische Informatik I [V] [1. PG] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Vorlesung", 2),
        ("fold", "Vorlesung / 00 Einführung", 3),
        ("file", "Vorlesung / 00 Einführung / E.pdf", 4),
        ("fold", "Vorlesung / 07 Objektorientierte Programmierung: Einführung", 5),
        ("file", "Vorlesung / 07 Objektorientierte Programmierung: Einführung / O.pdf", 6),
    ])
    assert paths["Vorlesung / 00 Einführung / E.pdf"] == \
        "01_SEMESTER/02_praktische_informatik_I/01_Vorlesung/01 Einführung/e.pdf"
    # Doppelpunkt ist unter Windows verboten
    assert paths["Vorlesung / 07 Objektorientierte Programmierung: Einführung / O.pdf"] == \
        "01_SEMESTER/02_praktische_informatik_I/01_Vorlesung/07_objektorientierte_programmierung_79f4/o.pdf"


def test_uebungsblaetter_folder_used_when_present(root):
    c = crs(1, "Formale Grundlagen der Informatik [V] (HWS 2026)")
    paths = map_one(root, c, [
        ("fold", "Übungsblätter", 2),
        ("file", "Übungsblätter / ub01.pdf", 3),
        ("fold", "Tutorien", 4),
        ("file", "Tutorien / t1.pdf", 5),
    ])
    assert paths["Übungsblätter / ub01.pdf"] == "01_SEMESTER/05_formale_grundlagen/03_Übungsblätter/ub01.pdf"
    # Tutorium gibt es lokal noch nicht: nächste freie Nummer, Schreibweise wie die Mehrheit
    assert paths["Tutorien / t1.pdf"] == "01_SEMESTER/05_formale_grundlagen/04_tutorium/t1.pdf"


def test_uebungsblaetter_falls_back_to_uebung(root):
    c = crs(1, "Marketing [V] (HWS 2026)")
    paths = map_one(root, c, [("fold", "Übungsblätter", 2), ("file", "Übungsblätter / b.pdf", 3)])
    assert paths["Übungsblätter / b.pdf"] == "01_SEMESTER/01_marketing/02_Übung/b.pdf"


def test_new_category_number_skips_ignored_folder_numbers(root):
    c = crs(1, "Praktische Informatik I [V] (HWS 2026)")
    paths = map_one(root, c, [("fold", "Abgaben", 2), ("file", "Abgaben / a.zip", 3)])
    # 04_code ist ignoriert, belegt aber die Nummer 04
    assert paths["Abgaben / a.zip"] == "01_SEMESTER/02_praktische_informatik_I/05_abgaben/a.zip"


def test_existing_partial_category_name(root):
    c = crs(1, "Programmierpraktikum I [V] (HWS 2026)")
    paths = map_one(root, c, [("fold", "Abgaben", 2), ("file", "Abgaben / a.zip", 3)])
    assert paths["Abgaben / a.zip"] == "01_SEMESTER/03_programmier_praktikum_1/04_freiwillige_abgaben/a.zip"


def test_never_maps_into_ignored_folder(root):
    # Ignore Muster gelten auf jeder Ebene: ein Ordner namens linux_data würde nie beschrieben
    c = crs(1, "Programmierpraktikum I [V] (HWS 2026)")
    paths = map_one(root, c, [("fold", "linux_data", 2), ("file", "linux_data / x.txt", 3)])
    assert paths["linux_data / x.txt"] == \
        "01_SEMESTER/03_programmier_praktikum_1/01_Vorlesung/linux_data_ilias/x.txt"
    c2 = crs(2, "Programmierpraktikum I (HWS 2026)")
    paths = map_one(root, c2, [("fold", "linux_data", 2), ("file", "linux_data / x.txt", 3)])
    assert paths["linux_data / x.txt"] == "01_SEMESTER/03_programmier_praktikum_1/linux_data_ilias/x.txt"


def test_group_with_tut_keyword(root):
    c = crs(1, "Praktische Informatik I [T] (HWS 2026)")
    paths = map_one(root, c, [
        ("grp", "Praktische Informatik I - Tut [1. PG]", 2),
        ("fold", "Praktische Informatik I - Tut [1. PG] / Dateien", 3),
        ("file", "Praktische Informatik I - Tut [1. PG] / Dateien / t.pdf", 4),
    ])
    assert paths == {
        "Praktische Informatik I - Tut [1. PG] / Dateien / t.pdf":
            "01_SEMESTER/02_praktische_informatik_I/03_Tutorium/t.pdf",
    }


def test_unknown_course_goes_to_new_courses(root):
    c = crs(1, "Statistik I (HWS 2026)")
    paths = map_one(root, c, [("fold", "Vorlesung", 2), ("file", "Vorlesung / s.pdf", 3)])
    assert paths == {"Vorlesung / s.pdf": "01_SEMESTER/_neue_kurse/statistik_i_hws_2026/vorlesung/s.pdf"}


def test_course_without_semester(root):
    c = crs(1, "License Information", heading="MSDNAA")
    paths = map_one(root, c, [("file", "Matlab.pdf", 2)])
    assert paths == {"Matlab.pdf": "_ohne_semester/license_information/matlab.pdf"}


def test_config_course_and_rules_win(root):
    c = crs(1, "Marketing [V] (HWS 2026)")
    cfg = {
        "semesters": {"HWS 2026": "01_SEMESTER"},
        "courses": [CourseConfig(ref_id=1, ilias_name=c.name, local_dir="01_marketing",
                                 rules=[CourseRule(target="99_Spezial", match_file="*blatt*.pdf")])],
    }
    paths = map_one(root, c, [
        ("fold", "Vorlesung", 2),
        ("file", "Vorlesung / aufgabenblatt1.pdf", 3),
        ("file", "Vorlesung / folie.pdf", 4),
    ], **cfg)
    assert paths == {
        "Vorlesung / aufgabenblatt1.pdf": "01_SEMESTER/01_marketing/99_Spezial/vorlesung/aufgabenblatt1.pdf",
        "Vorlesung / folie.pdf": "01_SEMESTER/01_marketing/01_Vorlesung/folie.pdf",
    }


def test_new_dirs_are_reported(root):
    c = crs(1, "Praktische Informatik I [Ü] (HWS 2026)")
    result = CrawlResult(courses=[c], objects={1: tree(c, [("fold", "W01", 2), ("file", "W01 / a.pdf", 3)])})
    (item,) = make_mapper(root).map_crawl(result).items
    assert item.new_dirs == {("01_SEMESTER", "02_praktische_informatik_I", "02_Übung", "w01")}


def test_mapper_does_not_write(root):
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    c = crs(1, "Statistik I (HWS 2026)")
    map_one(root, c, [("fold", "Neu", 2), ("file", "Neu / a.pdf", 3)])
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before


# ---------------- Vorschläge für neue Kursordner (erster Sync in leeren Ordner) ----------------

def test_course_dir_name():
    assert mapper.course_dir_name("Praktische Informatik I [V] [1. PG] (HWS 2026)", PATTERN) == "praktische_informatik_i"
    assert mapper.course_dir_name("Programmierpraktikum I, Große Übung [Ü] (HWS 2026)", PATTERN) \
        == "programmierpraktikum_i"
    assert mapper.course_dir_name("IS 201 Wirtschaftsinformatik I: Einführung und Grundlagen [V] (HWS 2026)",
                                  PATTERN) == "wirtschaftsinformatik_i"
    assert mapper.course_dir_name("CS 560 Datenbanken I [V] (FSS 2027)", PATTERN) == "datenbanken_i"
    assert mapper.course_dir_name("Mathematik I für Wirtschaftsinformatiker [V] (HWS 2026)", PATTERN) \
        == "mathematik_i_fuer_5f62"
    assert mapper.course_dir_name("[V] (HWS 2026)", PATTERN) == "kurs"


def test_suggest_courses_in_empty_root_groups_lectures(tmp_path):
    courses = [
        crs(1, "Marketing [V] (HWS 2026)"),
        crs(2, "Praktische Informatik I [V] [1. PG] (HWS 2026)"),
        crs(3, "Praktische Informatik I [Ü] [1. PG] (HWS 2026)"),
        crs(4, "Praktische Informatik I - Tut [1. PG]", kind="grp"),
        crs(5, "License Information (Student University of Mannheim)", heading="Sonstige"),
        crs(6, "Programmierpraktikum I [V] (HWS 2026)"),
    ]
    m = mapper.Mapper(Config(base_dir=None), tmp_path / "leer")
    got = {c.ref_id: c.local_dir for c in m.suggest_courses(CrawlResult(courses=courses, nested_in={4: "PI1"}))}
    assert got == {
        1: "01_marketing",
        2: "02_praktische_informatik_i",
        3: "02_praktische_informatik_i",
        # Kurs ohne Semester bekommt auch einen Eintrag (Ordner unter _ohne_semester, ohne Nummer)
        5: "license_information_student_2051",
        6: "03_programmierpraktikum_i",
    }


def test_suggest_courses_skips_configured_and_existing(root):
    courses = [crs(1, "Marketing [V] (HWS 2026)"), crs(2, "Neues Fach [V] (HWS 2026)"),
               crs(3, "Anderes Fach [V] (HWS 2026)")]
    m = make_mapper(root, semesters={"HWS 2026": "01_SEMESTER"},
                    courses=[CourseConfig(3, "Anderes Fach [V] (HWS 2026)", "07_anderes")])
    got = [(c.ref_id, c.local_dir) for c in m.suggest_courses(CrawlResult(courses=courses))]
    assert got == [(2, "08_neues_fach")]


# ---------------- Videos in eigenen Unterordner ----------------

PI1_V = "Praktische Informatik I [V] [1. PG] (HWS 2026)"


def test_videos_go_to_subfolder_next_to_pdfs(tmp_path):
    course = crs(1, PI1_V)
    got = map_one(tmp_path, course, [
        ("fold", "01 Vom Problem zum Programm", 10),
        ("file", "01 Vom Problem zum Programm / 01_01_VomProblemZumProgramm.pdf", 11),
        ("file", "01 Vom Problem zum Programm / 01 01 VomProblemZumProgramm.mp4", 12),
        ("file", "01 Vom Problem zum Programm / 01 02 Algorithmen.MP4", 13),
        ("file", "Einführung.mp4", 14),
    ], courses=[CourseConfig(1, PI1_V, "02_praktische_informatik_i")], semesters={"HWS 2026": "01_SEMESTER"})
    base = "01_SEMESTER/02_praktische_informatik_i/01_vorlesung"
    assert got == {
        "01 Vom Problem zum Programm / 01_01_VomProblemZumProgramm.pdf":
            f"{base}/01_vom_problem_zum_programm/01_01_vomproblemzumprogramm.pdf",
        "01 Vom Problem zum Programm / 01 01 VomProblemZumProgramm.mp4":
            f"{base}/01_vom_problem_zum_programm/01_videos/01_01_vomproblemzumprogramm.mp4",
        "01 Vom Problem zum Programm / 01 02 Algorithmen.MP4":
            f"{base}/01_vom_problem_zum_programm/01_videos/01_02_algorithmen.mp4",
        # 01_vom_problem_zum_programm belegt die 01, der Video Ordner bekommt die nächste Nummer
        "Einführung.mp4": f"{base}/02_videos/einfuehrung.mp4",
    }


def test_video_folder_from_ilias_gets_no_extra_subfolder(tmp_path):
    course = crs(1, PI1_V)
    got = map_one(tmp_path, course, [
        ("fold", "Aufzeichnungen 2026", 10),
        ("file", "Aufzeichnungen 2026 / woche1.mp4", 11),
        ("fold", "Vorlesungsaufzeichnungen", 20),
        ("file", "Vorlesungsaufzeichnungen / woche2.mp4", 21),
    ], courses=[CourseConfig(1, PI1_V, "02_praktische_informatik_i")], semesters={"HWS 2026": "01_SEMESTER"})
    base = "01_SEMESTER/02_praktische_informatik_i/01_vorlesung"
    assert got["Aufzeichnungen 2026 / woche1.mp4"] == f"{base}/aufzeichnungen_2026/woche1.mp4"
    # Reiner Kategoriename verschwindet im Pfad, dann doch eigener Video Ordner
    assert got["Vorlesungsaufzeichnungen / woche2.mp4"] == f"{base}/01_videos/woche2.mp4"


def test_existing_local_video_folder_is_reused(tmp_path):
    (tmp_path / "01_SEMESTER/02_pi/01_Vorlesung/03_videos").mkdir(parents=True)
    (tmp_path / "01_SEMESTER/02_pi/01_Vorlesung/04_Folien").mkdir(parents=True)
    course = crs(1, PI1_V)
    got = map_one(tmp_path, course, [("file", "a.mp4", 11)],
                  courses=[CourseConfig(1, PI1_V, "02_pi")], semesters={"HWS 2026": "01_SEMESTER"})
    assert got["a.mp4"] == "01_SEMESTER/02_pi/01_Vorlesung/03_videos/a.mp4"
    (tmp_path / "01_SEMESTER/02_pi/01_Vorlesung/03_videos").rmdir()
    got = map_one(tmp_path, course, [("file", "a.mp4", 11)],
                  courses=[CourseConfig(1, PI1_V, "02_pi")], semesters={"HWS 2026": "01_SEMESTER"})
    assert got["a.mp4"] == "01_SEMESTER/02_pi/01_Vorlesung/05_videos/a.mp4"


def test_new_category_folders_in_fixed_order(tmp_path):
    """ILIAS liefert Tutorium zuerst, trotzdem 01_Vorlesung, 02_Übung, 03_Tutorium."""
    mk = crs(1, "Marketing [V] (HWS 2026)")
    got = map_one(tmp_path, mk, [
        ("fold", "Tutorium", 10),
        ("file", "Tutorium / t.pdf", 11),
        ("fold", "Übung", 20),
        ("file", "Übung / u.pdf", 21),
        ("file", "v.pdf", 30),
    ], courses=[CourseConfig(1, mk.name, "01_marketing")], semesters={"HWS 2026": "01_SEMESTER"})
    assert got == {
        "Tutorium / t.pdf": "01_SEMESTER/01_marketing/03_tutorium/t.pdf",
        "Übung / u.pdf": "01_SEMESTER/01_marketing/02_uebung/u.pdf",
        "v.pdf": "01_SEMESTER/01_marketing/01_vorlesung/v.pdf",
    }


def test_category_order_across_courses_in_same_folder(tmp_path):
    t = crs(1, "Praktische Informatik I [T] (HWS 2026)")
    v = crs(2, PI1_V)
    result = CrawlResult(courses=[t, v], objects={1: tree(t, [("file", "t.pdf", 11)]),
                                                  2: tree(v, [("file", "v.pdf", 21)])})
    cfg = dict(courses=[CourseConfig(1, t.name, "02_pi"), CourseConfig(2, v.name, "02_pi")],
               semesters={"HWS 2026": "01_SEMESTER"})
    got = sorted("/".join(it.rel_path) for it in make_mapper(tmp_path, **cfg).map_crawl(result).items)
    assert got == ["01_SEMESTER/02_pi/01_vorlesung/v.pdf", "01_SEMESTER/02_pi/02_tutorium/t.pdf"]


def test_sheets_fall_back_to_exercise_before_tutorial(tmp_path):
    fg = crs(1, "Formale Grundlagen der Informatik [V] (HWS 2026)")
    got = map_one(tmp_path, fg, [
        ("fold", "Tutorium", 10),
        ("file", "Tutorium / t.pdf", 11),
        ("fold", "Übungsblätter", 20),
        ("file", "Übungsblätter / ub01.pdf", 21),
    ], courses=[CourseConfig(1, fg.name, "02_fg")], semesters={"HWS 2026": "01_SEMESTER"})
    assert got == {
        "Tutorium / t.pdf": "01_SEMESTER/02_fg/02_tutorium/t.pdf",
        "Übungsblätter / ub01.pdf": "01_SEMESTER/02_fg/01_uebung/ub01.pdf",
    }


LICENSE = "License Information (Student University of Mannheim)"


def test_course_without_semester_gets_config_entry_for_existing_folder(tmp_path):
    """Lizenzkurs: vorhandener Ordner mit alter Schreibweise wird Config Eintrag, nichts Neues angelegt."""
    (tmp_path / "_Ohne_Semester" / LICENSE / "Anleitung").mkdir(parents=True)
    c = crs(342184, LICENSE, heading="MSDNAA")
    result = CrawlResult([c], {c.ref_id: tree(c, [("fold", "Anleitung", 1), ("file", "Anleitung / Matlab.pdf", 2)])})
    m = mapper.Mapper(Config(base_dir=None), tmp_path)
    [suggestion] = m.suggest_courses(result)
    assert (suggestion.ref_id, suggestion.local_dir) == (342184, LICENSE)

    cfg = Config(base_dir=None, courses=[suggestion])
    [item] = mapper.Mapper(cfg, tmp_path).map_crawl(result).items
    assert item.rel_path == ("_Ohne_Semester", LICENSE, "Anleitung", "matlab.pdf")
    assert not item.new_dirs


def test_course_without_semester_new_folder_follows_linux_rule(tmp_path):
    c = crs(342184, LICENSE, heading="MSDNAA")
    result = CrawlResult([c], {c.ref_id: tree(c, [("file", "Matlab.pdf", 2)])})
    [suggestion] = mapper.Mapper(Config(base_dir=None), tmp_path).suggest_courses(result)
    assert suggestion.local_dir == "license_information_student_2051"


def test_course_without_semester_can_be_switched_off(tmp_path):
    from ilias_sync import state, sync

    c = crs(342184, LICENSE, heading="MSDNAA")
    result = CrawlResult([c], {c.ref_id: tree(c, [("file", "Matlab.pdf", 2)])})
    course = CourseConfig(342184, LICENSE, "license_information", download_files=False)
    cfg = Config(base_dir=None, courses=[course])
    mapping = mapper.Mapper(cfg, tmp_path).map_crawl(result)
    with state.State(tmp_path / "index.sqlite3") as st:
        assert sync.plan(mapping, st, tmp_path, cfg) == []
        course.download_files = True
        assert [j.target.name for j in sync.plan(mapping, st, tmp_path, cfg)] == ["matlab.pdf"]
