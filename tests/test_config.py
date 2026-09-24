"""Tests für config.py: Laden, Speichern, Validierung."""

from pathlib import Path

import pytest

from ilias_sync import config
from ilias_sync.api import Config, ConfigError, CourseConfig, CourseRule

EXAMPLE = r"""
[general]
base_dir = "~/Documents/Uni"
root_folder_name = "01_Universität_Mannheim"
sync_interval_minutes = 60
max_parallel = 3
semester_pattern = '\((HWS|FSS) (\d{4})\)'
autostart = false
sync_paused = false
username = "nutzer1"

[semesters]
"HWS 2026" = "01_SEMESTER"

[[courses]]
ref_id = 1234567
ilias_name = "Marketing (HWS 2026)"
local_dir = "01_marketing"
download_videos = false

  [[courses.rules]]
  match_folder = "Lecture Slides"
  target = "01_Vorlesung"

  [[courses.rules]]
  match_file = "*blatt*.pdf"
  target = "03_Übungsblätter"

[[courses]]
ref_id = 7654321
ilias_name = "Formale Grundlagen (HWS 2026)"

[ignore]
local_dirs = ["04_code", "linux_data", "*-MacBook*", "* von *"]
"""


@pytest.fixture
def cfg_path(tmp_path) -> Path:
    return tmp_path / "config.toml"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_gives_defaults(cfg_path):
    assert not config.config_exists(cfg_path)
    cfg = config.load_config(cfg_path)
    assert cfg == Config(base_dir=None)
    assert cfg.username is None


def test_load_example(cfg_path):
    cfg = config.load_config(write(cfg_path, EXAMPLE))
    assert cfg.base_dir == Path("~/Documents/Uni").expanduser()
    assert cfg.username == "nutzer1"
    assert cfg.semesters == {"HWS 2026": "01_SEMESTER"}
    assert cfg.ignore_local_dirs == ["04_code", "linux_data", "*-MacBook*", "* von *"]

    marketing, formale = cfg.courses
    assert marketing.ref_id == 1234567
    assert marketing.local_dir == "01_marketing"
    assert marketing.download_videos is False
    assert marketing.download_forums is True
    assert marketing.rules == [
        CourseRule(target="01_Vorlesung", match_folder="Lecture Slides"),
        CourseRule(target="03_Übungsblätter", match_file="*blatt*.pdf"),
    ]
    assert formale.local_dir is None


def test_roundtrip(cfg_path):
    original = config.load_config(write(cfg_path, EXAMPLE))
    config.save_config(original, cfg_path)
    assert config.load_config(cfg_path) == original
    assert not cfg_path.with_name("config.toml.part").exists()


def test_save_creates_directory_and_omits_none(tmp_path):
    path = tmp_path / "neu" / "config.toml"
    config.save_config(Config(base_dir=None, username="nutzer1"), path)
    text = path.read_text(encoding="utf-8")
    assert 'username = "nutzer1"' in text
    assert "base_dir" not in text
    assert config.load_config(path).username == "nutzer1"


def test_no_password_field_written(cfg_path):
    config.save_config(Config(base_dir=None, username="nutzer1"), cfg_path)
    assert "password" not in cfg_path.read_text(encoding="utf-8").lower()


def test_syntax_error_names_line(cfg_path):
    write(cfg_path, "[general]\nmax_parallel = = 3\n")
    with pytest.raises(ConfigError, match="line 2"):
        config.load_config(cfg_path)


@pytest.mark.parametrize(
    "toml, message",
    [
        ("[general]\nmax_parallel = \"drei\"", "general.max_parallel: Ganzzahl erwartet"),
        ("[general]\nmax_parallel = true", "general.max_parallel: Ganzzahl erwartet"),
        ("[general]\nmax_parallel = 9", "general.max_parallel: muss zwischen 1 und 5"),
        ("[general]\nsync_interval_minutes = 1", "sync_interval_minutes: muss zwischen 15"),
        ("[general]\nsemester_pattern = '(HWS'", "semester_pattern: kein gültiger"),
        ("[general]\nroot_folder_name = \"a/b\"", "root_folder_name: nur ein Ordnername"),
        ("[general]\nusername = \"a b\"", "username: Uni-ID ohne Leerzeichen"),
        ("[[courses]]\nilias_name = \"X\"", r"courses\[0\].ref_id fehlt"),
        (
            "[[courses]]\nref_id = 1\nilias_name = \"A\"\n[[courses]]\nref_id = 1\nilias_name = \"B\"",
            "ref_id 1 kommt mehrfach vor",
        ),
        (
            "[[courses]]\nref_id = 1\nilias_name = \"A\"\nlocal_dir = \"../weg\"",
            "local_dir: nur ein Ordnername",
        ),
        (
            "[[courses]]\nref_id = 1\nilias_name = \"A\"\n[[courses.rules]]\ntarget = \"X\"",
            "genau eins von match_folder oder match_file",
        ),
        ("[ignore]\nlocal_dirs = [1, 2]", "ignore.local_dirs: Liste aus Texten"),
        ("[semesters]\n\"HWS 2026\" = 1", "semesters.HWS 2026: Text erwartet"),
    ],
)
def test_validation_errors(cfg_path, toml, message):
    with pytest.raises(ConfigError, match=message):
        config.load_config(write(cfg_path, toml))


def test_unknown_keys_are_warned_not_fatal(cfg_path, caplog):
    cfg = config.load_config(write(cfg_path, "[general]\nfoo = 1\n"))
    assert cfg == Config(base_dir=None)
    assert "general.foo" in caplog.text


def test_save_validates_before_writing(cfg_path):
    bad = Config(base_dir=None, max_parallel=0)
    with pytest.raises(ConfigError):
        config.save_config(bad, cfg_path)
    assert not cfg_path.exists()


def test_course_without_rules_roundtrip(cfg_path):
    cfg = Config(base_dir=Path("/tmp/uni"), courses=[CourseConfig(ref_id=5, ilias_name="X", local_dir=None)])
    config.save_config(cfg, cfg_path)
    assert config.load_config(cfg_path) == cfg
