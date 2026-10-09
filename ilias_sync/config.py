"""
Config laden, validieren und speichern.

Die Config ist eine TOML Datei im Config Verzeichnis von platformdirs,
also außerhalb des Projektordners. Sie enthält nie ein Passwort, nur die
Uni-ID (`username`). Die Datentypen kommen aus dem Vertrag in api.py.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import platformdirs
import tomli_w

# Nur Datentypen, api.py importiert auf Modulebene kein Backend Modul
from .api import Config, ConfigError, CourseConfig, CourseRule

APP_NAME = "ilias-sync"

INTERVAL_RANGE = (15, 1440)
PARALLEL_RANGE = (1, 5)
DOWNLOAD_FLAGS = (
    "download_files",
    "download_videos",
    "download_exercises",
    "download_weblinks",
    "download_forums",
    "download_other",
)

log = logging.getLogger(__name__)


# ---------------- Pfade ----------------

# Getrennte Umgebung für Tests: lenkt Config, Daten (Index, Cookies) und Log in einen
# eigenen Ordner um (<Ordner>/config, /data, /logs). Der Schlüsselbund Eintrag bleibt derselbe.
HOME_ENV = "ILIAS_SYNC_HOME"


def home_override() -> Path | None:
    value = os.environ.get(HOME_ENV, "").strip()
    return Path(value).expanduser() if value else None


def config_dir() -> Path:
    if home := home_override():
        return home / "config"
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    if home := home_override():
        return home / "data"
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def log_dir() -> Path:
    if home := home_override():
        return home / "logs"
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def config_file() -> Path:
    return config_dir() / "config.toml"


# ---------------- Lesen ----------------

def _get(table: dict, key: str, typ: type, where: str, default: Any = None) -> Any:
    if key not in table:
        return default
    value = table[key]
    # bool ist in Python ein int, soll hier aber nicht als Zahl durchgehen
    if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
        names = {str: "Text", int: "Ganzzahl", bool: "true oder false", list: "Liste", dict: "Tabelle"}
        raise ConfigError(f"{where}.{key}: {names.get(typ, typ.__name__)} erwartet")
    return value


def _require(table: dict, key: str, typ: type, where: str) -> Any:
    if key not in table:
        raise ConfigError(f"{where}.{key} fehlt")
    return _get(table, key, typ, where)


def _warn_unknown(table: dict, known: set[str], where: str) -> None:
    for key in table.keys() - known:
        log.warning("Unbekannter Config Eintrag wird ignoriert: %s.%s", where, key)


def _str_list(table: dict, key: str, where: str) -> list[str]:
    values = _get(table, key, list, where, [])
    if not all(isinstance(v, str) for v in values):
        raise ConfigError(f"{where}.{key}: Liste aus Texten erwartet")
    return list(values)


def _parse_rule(raw: Any, where: str) -> CourseRule:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: Tabelle erwartet")
    _warn_unknown(raw, {"target", "match_folder", "match_file"}, where)
    return CourseRule(
        target=_require(raw, "target", str, where),
        match_folder=_get(raw, "match_folder", str, where),
        match_file=_get(raw, "match_file", str, where),
    )


def _parse_course(raw: Any, where: str) -> CourseConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: Tabelle erwartet")
    _warn_unknown(raw, {"ref_id", "ilias_name", "local_dir", "rules", *DOWNLOAD_FLAGS}, where)
    course = CourseConfig(
        ref_id=_require(raw, "ref_id", int, where),
        ilias_name=_require(raw, "ilias_name", str, where),
        local_dir=_get(raw, "local_dir", str, where),
    )
    for flag in DOWNLOAD_FLAGS:
        setattr(course, flag, _get(raw, flag, bool, where, True))
    rules = _get(raw, "rules", list, where, [])
    course.rules = [_parse_rule(r, f"{where}.rules[{i}]") for i, r in enumerate(rules)]
    return course


def from_dict(raw: dict) -> Config:
    """TOML Inhalt in Config umwandeln. Wirft ConfigError mit Feldnamen."""
    _warn_unknown(raw, {"general", "semesters", "courses", "ignore"}, "config")
    defaults = Config(base_dir=None)

    general = _get(raw, "general", dict, "config", {})
    _warn_unknown(
        general,
        {
            "base_dir", "root_folder_name", "sync_interval_minutes", "max_parallel",
            "semester_pattern", "autostart", "sync_paused", "username", "check_updates",
        },
        "general",
    )
    base_dir = _get(general, "base_dir", str, "general")

    semesters = _get(raw, "semesters", dict, "config", {})
    for name, folder in semesters.items():
        if not isinstance(folder, str):
            raise ConfigError(f"semesters.{name}: Text erwartet")

    courses = _get(raw, "courses", list, "config", [])
    ignore = _get(raw, "ignore", dict, "config", {})
    _warn_unknown(ignore, {"local_dirs"}, "ignore")

    return Config(
        base_dir=Path(base_dir).expanduser() if base_dir else None,
        root_folder_name=_get(general, "root_folder_name", str, "general", defaults.root_folder_name),
        sync_interval_minutes=_get(
            general, "sync_interval_minutes", int, "general", defaults.sync_interval_minutes
        ),
        max_parallel=_get(general, "max_parallel", int, "general", defaults.max_parallel),
        semester_pattern=_get(general, "semester_pattern", str, "general", defaults.semester_pattern),
        semesters=dict(semesters),
        courses=[_parse_course(c, f"courses[{i}]") for i, c in enumerate(courses)],
        ignore_local_dirs=_str_list(ignore, "local_dirs", "ignore"),
        autostart=_get(general, "autostart", bool, "general", defaults.autostart),
        sync_paused=_get(general, "sync_paused", bool, "general", defaults.sync_paused),
        username=_get(general, "username", str, "general"),
        check_updates=_get(general, "check_updates", bool, "general", defaults.check_updates),
    )


# ---------------- Prüfen ----------------

def _check_folder_name(name: str, where: str) -> None:
    if not name.strip():
        raise ConfigError(f"{where}: darf nicht leer sein")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise ConfigError(f"{where}: nur ein Ordnername erlaubt, kein Pfad ({name})")


def validate(cfg: Config) -> None:
    """Wirft ConfigError mit verständlichem Text, wenn etwas nicht stimmt."""
    lo, hi = INTERVAL_RANGE
    if not lo <= cfg.sync_interval_minutes <= hi:
        raise ConfigError(f"general.sync_interval_minutes: muss zwischen {lo} und {hi} liegen")
    lo, hi = PARALLEL_RANGE
    if not lo <= cfg.max_parallel <= hi:
        raise ConfigError(f"general.max_parallel: muss zwischen {lo} und {hi} liegen")
    try:
        re.compile(cfg.semester_pattern)
    except re.error as e:
        raise ConfigError(f"general.semester_pattern: kein gültiger regulärer Ausdruck ({e})") from None
    if cfg.root_folder_name:  # leer erlaubt: dann ist der Basisordner selbst der Uni Ordner
        _check_folder_name(cfg.root_folder_name, "general.root_folder_name")
    if cfg.username is not None and (not cfg.username or cfg.username != cfg.username.strip()
                                     or " " in cfg.username):
        raise ConfigError("general.username: Uni-ID ohne Leerzeichen erwartet")

    for name, folder in cfg.semesters.items():
        _check_folder_name(folder, f"semesters.{name}")

    seen = set()
    for i, course in enumerate(cfg.courses):
        where = f"courses[{i}] ({course.ilias_name})"
        if course.ref_id in seen:
            raise ConfigError(f"{where}: ref_id {course.ref_id} kommt mehrfach vor")
        seen.add(course.ref_id)
        if course.local_dir is not None:
            _check_folder_name(course.local_dir, f"{where}.local_dir")
        for j, rule in enumerate(course.rules):
            rwhere = f"{where}.rules[{j}]"
            if (rule.match_folder is None) == (rule.match_file is None):
                raise ConfigError(f"{rwhere}: genau eins von match_folder oder match_file angeben")
            if not rule.target.strip():
                raise ConfigError(f"{rwhere}.target: darf nicht leer sein")


# ---------------- Schreiben ----------------

def to_dict(cfg: Config) -> dict:
    """Config in TOML Struktur umwandeln. None Werte werden weggelassen."""
    general = {
        "base_dir": str(cfg.base_dir) if cfg.base_dir else None,
        "root_folder_name": cfg.root_folder_name,
        "sync_interval_minutes": cfg.sync_interval_minutes,
        "max_parallel": cfg.max_parallel,
        "semester_pattern": cfg.semester_pattern,
        "autostart": cfg.autostart,
        "sync_paused": cfg.sync_paused,
        "username": cfg.username,
        "check_updates": cfg.check_updates,
    }
    courses = []
    for c in cfg.courses:
        course = {"ref_id": c.ref_id, "ilias_name": c.ilias_name, "local_dir": c.local_dir}
        course.update({flag: getattr(c, flag) for flag in DOWNLOAD_FLAGS})
        if c.rules:
            course["rules"] = [
                {k: v for k, v in (("match_folder", r.match_folder), ("match_file", r.match_file),
                                   ("target", r.target)) if v is not None}
                for r in c.rules
            ]
        courses.append({k: v for k, v in course.items() if v is not None})

    data = {
        "general": {k: v for k, v in general.items() if v is not None},
        "semesters": dict(cfg.semesters),
        "courses": courses,
        "ignore": {"local_dirs": list(cfg.ignore_local_dirs)},
    }
    if not courses:
        del data["courses"]
    return data


# ---------------- Öffentliche Funktionen ----------------

def config_exists(path: Path | None = None) -> bool:
    return (path or config_file()).exists()


def load_config(path: Path | None = None) -> Config:
    """Config laden. Fehlt die Datei, gibt es Standardwerte."""
    path = path or config_file()
    if not path.exists():
        return Config(base_dir=None)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Config Datei fehlerhaft: {e}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"Config Datei nicht lesbar: {type(e).__name__}") from None
    cfg = from_dict(raw)
    validate(cfg)
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Prüfen und atomar schreiben (erst temporäre Datei, dann umbenennen)."""
    validate(cfg)
    path = path or config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(tomli_w.dumps(to_dict(cfg)), encoding="utf-8")
    os.replace(tmp, path)
    log.info("Config gespeichert")
