"""Einstieg für den CLI Modus: python -m ilias_sync --login"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import auth, config, crawler
from .api import Config, ConfigError

log = logging.getLogger(config.APP_NAME)


def setup_logging(verbose: bool = False) -> Path:
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ilias-sync.log"

    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    # urllib3 loggt auf DEBUG vollständige URLs, die Session Daten enthalten können
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return log_file


def console_credentials(cfg: Config) -> auth.Credentials:
    """Uni-ID aus der Config, Passwort aus dem Schlüsselbund. Nur Fehlendes abfragen."""
    user = cfg.username or input("Uni-ID: ").strip()
    password = auth.load_password(user)
    if password:
        return auth.Credentials(user, password)
    if cfg.username:
        print("Uni-ID:", user)
    password = getpass.getpass("Passwort: ")
    remember = input("Passwort im Schlüsselbund speichern? [j/N] ").strip().lower() == "j"
    return auth.Credentials(user, password, remember)


def remember_username(cfg: Config, user: str | None) -> None:
    """Uni-ID nach erfolgreichem Login in der Config merken."""
    if user and user != cfg.username:
        cfg.username = user
        config.save_config(cfg)
        print("Uni-ID gespeichert in:", config.config_file())


def load_config_or_report() -> Config | None:
    try:
        return config.load_config()
    except ConfigError as e:
        print("FEHLER in der Config:", e)
        return None


def open_session(cfg: Config) -> requests.Session | None:
    """Eingeloggte Session holen, bei Bedarf Zugangsdaten abfragen. None bei Fehler."""
    used = {}

    def credentials() -> auth.Credentials:
        creds = console_credentials(cfg)
        used["user"] = creds.user
        return creds

    try:
        s = auth.get_session(credentials)
    except auth.LoginError as e:
        print("FEHLER:", e)
        return None
    except requests.RequestException as e:
        print("FEHLER: Keine Verbindung zu ILIAS:", type(e).__name__)
        return None
    remember_username(cfg, used.get("user"))
    return s


def cmd_login() -> int:
    cfg = load_config_or_report()
    if cfg is None:
        return 1
    s = open_session(cfg)
    if s is None:
        return 1
    try:
        r = s.get(auth.DASHBOARD, timeout=auth.TIMEOUT)
    except requests.RequestException as e:
        print("FEHLER: Keine Verbindung zu ILIAS:", type(e).__name__)
        return 1
    title = BeautifulSoup(r.text, "html.parser").title
    print("Login OK")
    print("Seite:", title.get_text(strip=True) if title else auth.safe_url(r.url))
    print("Cookies gespeichert in:", auth.cookie_file())
    return 0


def cmd_logout() -> int:
    cfg = load_config_or_report()
    if cfg is None:
        return 1
    user = cfg.username or input("Uni-ID: ").strip()
    auth.logout(user)
    print("Abgemeldet: Passwort und Cookies gelöscht. Die Uni-ID bleibt in der Config.")
    return 0


# ---------------- Testlauf: nur lesen ----------------

def format_size(size: int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def format_object(obj: crawler.IliasObject) -> str:
    label = crawler.TYPE_NAMES.get(obj.type) or obj.type_label or obj.type
    line = f"[{label}] {obj.name}"
    if obj.type == "file":
        details = [format_size(obj.size), obj.mtime.strftime("%d.%m.%Y %H:%M") if obj.mtime else ""]
        line += "   " + "   ".join(d for d in details if d)
    elif obj.is_container and obj.url is None:
        line += "   (kein Zugriff)"
    return line


def cmd_crawl(course_ids: list[int], output: Path | None) -> int:
    cfg = load_config_or_report()
    if cfg is None:
        return 1
    s = open_session(cfg)
    if s is None:
        return 1

    out = open(output, "w", encoding="utf-8") if output else None

    def emit(line: str = "") -> None:
        print(line)
        if out:
            out.write(line + "\n")

    c = crawler.Crawler(s, semester_pattern=cfg.semester_pattern)
    started = time.monotonic()
    counts: Counter[str] = Counter()
    total_size = 0
    try:
        courses = c.fetch_courses()
        if course_ids:
            courses = [k for k in courses if k.ref_id in course_ids]
        emit("ILIAS Testlauf: nur lesen, es wird nichts heruntergeladen oder geschrieben")
        emit(f"{len(courses)} Kurse und Gruppen gefunden")

        visited: set[int] = set()
        for course in courses:
            emit()
            kind = "Kurs" if course.type == "crs" else "Gruppe"
            semester = course.semester or f"unbekannt (Überschrift: {course.group_heading})"
            emit(f"[{kind}] {course.name}   Semester: {semester}   ref_id {course.ref_id}")
            if course.ref_id in visited:
                emit("    (bereits in einem anderen Kurs erfasst)")
                continue
            counts[kind] += 1
            for obj in c.walk(course, visited):
                emit("    " * len(obj.path) + format_object(obj))
                counts[crawler.TYPE_NAMES.get(obj.type) or obj.type_label or obj.type] += 1
                total_size += obj.size or 0
    except KeyboardInterrupt:
        emit("\nAbgebrochen.")
    except requests.RequestException as e:
        emit(f"FEHLER: Verbindung zu ILIAS unterbrochen: {type(e).__name__}")
        return 1
    finally:
        emit()
        emit("Zusammenfassung")
        for label, n in counts.most_common():
            emit(f"  {label}: {n}")
        emit(f"  Gesamtgröße der Dateien: {format_size(total_size)}")
        emit(f"  Seiten abgerufen: {c.pages_fetched} in {time.monotonic() - started:.0f} s")
        if out:
            out.close()
            print("Ausgabe gespeichert in:", output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ilias_sync", description="ILIAS Sync (Uni Mannheim)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="Anmeldung testen, Session speichern")
    group.add_argument("--logout", action="store_true", help="Passwort und Cookies löschen")
    group.add_argument("--crawl", action="store_true",
                       help="Testlauf: alle Kurse, Ordner und Dateien auflisten, nichts herunterladen")
    parser.add_argument("--course", type=int, action="append", default=[], metavar="REF_ID",
                        help="nur diesen Kurs crawlen (mehrfach möglich)")
    parser.add_argument("--output", type=Path, metavar="DATEI", help="Ausgabe zusätzlich in Datei speichern")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    args = parser.parse_args(argv)

    log_file = setup_logging(args.verbose)
    log.debug("Log Datei: %s", log_file)

    if args.login:
        return cmd_login()
    if args.crawl:
        return cmd_crawl(args.course, args.output)
    return cmd_logout()


if __name__ == "__main__":
    sys.exit(main())
