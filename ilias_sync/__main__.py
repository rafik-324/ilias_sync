"""Einstieg für den CLI Modus: python -m ilias_sync --login"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import auth, config, crawler, downloader, mapper
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


def progress_line(text: str) -> None:
    if sys.stderr.isatty():
        print(f"\r  {text}", end="", file=sys.stderr, flush=True)


def run_crawl(cfg: Config, s: requests.Session, course_ids: list[int]) -> crawler.CrawlResult | None:
    """Alle (oder die gewählten) Kurse crawlen. None bei Abbruch oder Fehler."""
    c = crawler.Crawler(
        s,
        max_parallel=cfg.max_parallel,
        semester_pattern=cfg.semester_pattern,
        relogin=lambda: auth.relogin(s, lambda: console_credentials(cfg)),
        on_page=lambda n: progress_line(f"Seiten gelesen: {n}"),
    )
    print(f"{cfg.max_parallel} parallele Abrufe, Strg+C bricht ab")
    try:
        courses = c.fetch_courses()
        if course_ids:
            courses = [k for k in courses if k.ref_id in course_ids]
        result = c.crawl(courses)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return None
    except crawler.SessionExpired as e:
        print("\nFEHLER:", e)
        return None
    except requests.RequestException as e:
        print(f"\nFEHLER: Verbindung zu ILIAS unterbrochen: {type(e).__name__}")
        return None
    if sys.stderr.isatty():
        print(file=sys.stderr)
    return result


def cmd_crawl(course_ids: list[int], output: Path | None) -> int:
    cfg = load_config_or_report()
    if cfg is None:
        return 1
    s = open_session(cfg)
    if s is None:
        return 1

    started = time.monotonic()
    print("ILIAS Testlauf: nur lesen, es wird nichts heruntergeladen oder geschrieben")
    result = run_crawl(cfg, s, course_ids)
    if result is None:
        return 1

    lines = format_crawl(result)
    lines.append(f"  Seiten abgerufen: {result.pages_fetched} in {time.monotonic() - started:.0f} s")
    print("\n".join(lines))
    if output:
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Ausgabe gespeichert in:", output)
    return 0 if not result.failed else 2


def format_crawl(result: crawler.CrawlResult) -> list[str]:
    """Baum und Zusammenfassung eines Crawls als Textzeilen."""
    lines = [f"{len(result.courses)} Kurse und Gruppen gefunden"]
    counts: Counter[str] = Counter()
    total_size = 0
    for course in result.courses:
        kind = "Kurs" if course.type == "crs" else "Gruppe"
        semester = course.semester or f"unbekannt (Überschrift: {course.group_heading})"
        lines += ["", f"[{kind}] {course.name}   Semester: {semester}   ref_id {course.ref_id}"]
        if course.ref_id in result.nested_in:
            lines.append(f"    (bereits enthalten in: {result.nested_in[course.ref_id]})")
            continue
        counts[kind] += 1
        for obj in result.objects.get(course.ref_id, []):
            lines.append("    " * len(obj.path) + format_object(obj))
            counts[crawler.TYPE_NAMES.get(obj.type) or obj.type_label or obj.type] += 1
            total_size += obj.size or 0

    lines += ["", "Zusammenfassung"]
    lines += [f"  {label}: {n}" for label, n in counts.most_common()]
    lines.append(f"  Gesamtgröße der Dateien: {format_size(total_size)}")
    if result.failed:
        lines.append(f"  NICHT GELESEN ({len(result.failed)} Seiten, Inhalt unbekannt):")
        lines += [f"    {path}" for path in result.failed]
    return lines


# ---------------- Test Sync: echte Downloads in einen Testordner ----------------

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv"}


def is_video(item: mapper.MappedItem) -> bool:
    return Path(item.obj.name).suffix.lower() in VIDEO_SUFFIXES


def check_test_target(target: Path, real_root: Path) -> str | None:
    """Fehlertext, wenn der Testordner in echten Semester Ordnern liegen würde."""
    target, real_root = target.resolve(), real_root.resolve()
    if target == real_root:
        return "Der Testordner darf nicht der Uni Ordner selbst sein."
    for semester in (d for d in real_root.iterdir() if d.is_dir() and mapper.SEMESTER_DIR_RE.match(d.name)):
        if target == semester.resolve() or semester.resolve() in target.parents:
            return f"Der Testordner liegt in {semester.name}, dort wird bis zur Fertigstellung nichts geschrieben."
    return None


def format_preview(mapping: mapper.MappingResult, skipped_videos: set[int]) -> list[str]:
    """Vorschau als Baum: wohin jede Datei käme, welche Ordner neu wären."""
    lines = ["Zuordnung der ILIAS Kurse", ""]
    for cm in mapping.courses:
        state = "vorhanden" if cm.exists else "NEU"
        lines.append(f"  {cm.course.name}")
        lines.append(f"      -> {cm.semester_dir}/{cm.course_dir}   ({state}, {cm.source})")
    lines += ["", "Semester: " + ", ".join(f"{k} -> {v}" for k, v in mapping.semesters.items()), ""]

    new_dirs = set().union(*(it.new_dirs for it in mapping.items)) if mapping.items else set()
    tree: dict = {}
    for it in mapping.items:
        if it.obj.type != "file":
            continue
        node = tree
        for part in it.rel_path[:-1]:
            node = node.setdefault(part, {})
        note = "   (Video, im Test übersprungen)" if it.obj.ref_id in skipped_videos else ""
        node.setdefault("\0files", []).append(it.rel_path[-1] + note)

    lines += ["Einsortierung (NEU = Ordner wird angelegt, sonst vorhanden)", ""]

    def walk(node: dict, rel: tuple[str, ...], depth: int) -> None:
        for name in sorted(k for k in node if k != "\0files"):
            path = (*rel, name)
            lines.append("    " * depth + f"{name}/" + ("   NEU" if path in new_dirs else ""))
            walk(node[name], path, depth + 1)
        for f in sorted(node.get("\0files", [])):
            lines.append("    " * depth + f)

    walk(tree, (), 0)
    others = Counter(crawler.TYPE_NAMES.get(it.obj.type) or it.obj.type_label or it.obj.type
                     for it in mapping.items if it.obj.type != "file")
    if others:
        lines += ["", "Noch nicht behandelt (kommt mit Meilenstein 5): "
                  + ", ".join(f"{n} {label}" for label, n in others.most_common())]
    return lines


def cmd_test_sync(target: Path, with_videos: bool) -> int:
    cfg = load_config_or_report()
    if cfg is None:
        return 1
    if cfg.base_dir is None:
        print("FEHLER: In der Config fehlt general.base_dir (Basispfad).")
        return 1
    real_root = cfg.base_dir / cfg.root_folder_name
    if not real_root.is_dir():
        print("FEHLER: Uni Ordner nicht gefunden:", real_root)
        return 1
    if problem := check_test_target(target, real_root):
        print("FEHLER:", problem)
        return 1
    s = open_session(cfg)
    if s is None:
        return 1

    print("ILIAS Test Sync: echte Uni Ordner werden nur gelesen, geschrieben wird nur in:")
    print("  ", target)
    started = time.monotonic()
    result = run_crawl(cfg, s, [])
    if result is None:
        return 1

    mapping = mapper.Mapper(cfg, real_root).map_crawl(result)
    files = [it for it in mapping.items if it.obj.type == "file" and it.obj.url]
    skipped = {it.obj.ref_id for it in files if is_video(it) and not with_videos}
    todo = [it for it in files if it.obj.ref_id not in skipped]

    target.mkdir(parents=True, exist_ok=True)
    preview = target / f"{date.today():%Y-%m-%d}_Vorschau_Einsortierung.txt"
    preview.write_text("\n".join(format_preview(mapping, skipped)) + "\n", encoding="utf-8")
    print("Vorschau gespeichert:", preview)

    total = sum(it.obj.size or 0 for it in todo)
    print(f"Lade {len(todo)} Dateien (etwa {format_size(total)}), {len(skipped)} Videos übersprungen")
    throttle = crawler.Throttle(crawler.DEFAULT_DELAY)
    relogin_lock = threading.Lock()

    def relogin() -> None:
        with relogin_lock:
            auth.relogin(s, lambda: console_credentials(cfg))

    results: list[downloader.DownloadResult] = []
    try:
        with ThreadPoolExecutor(max_workers=cfg.max_parallel) as pool:
            futures = [
                pool.submit(downloader.download_file, s, it.obj.url, target.joinpath(*it.rel_path), throttle, relogin)
                for it in todo
            ]
            for future in as_completed(futures):
                results.append(future.result())
                progress_line(f"Dateien: {len(results)}/{len(todo)}")
    except KeyboardInterrupt:
        print("\nAbgebrochen. Bereits geladene Dateien bleiben, .part Dateien werden entfernt.")
        return 1
    if sys.stderr.isatty():
        print(file=sys.stderr)

    counts = Counter(r.status for r in results)
    errors = [r for r in results if r.status == "fehler"]
    summary = [
        f"Test Sync {date.today():%d.%m.%Y}: {counts['neu']} neu geladen, "
        f"{counts['vorhanden']} schon vorhanden, {len(errors)} Fehler, {len(skipped)} Videos übersprungen",
        f"Geladen: {format_size(sum(r.size for r in results))} in {time.monotonic() - started:.0f} s",
    ]
    summary += [f"  FEHLER {r.path.relative_to(target)}: {r.error}" for r in errors]
    if result.failed:
        summary.append(f"Nicht gelesene ILIAS Seiten: {', '.join(result.failed)}")
    print("\n".join(summary))
    with open(preview, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(summary) + "\n")
    return 0 if not errors else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ilias_sync", description="ILIAS Sync (Uni Mannheim)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="Anmeldung testen, Session speichern")
    group.add_argument("--logout", action="store_true", help="Passwort und Cookies löschen")
    group.add_argument("--crawl", action="store_true",
                       help="Testlauf: alle Kurse, Ordner und Dateien auflisten, nichts herunterladen")
    group.add_argument("--test-sync", type=Path, metavar="ZIELORDNER",
                       help="Test: Dateien einsortiert in ZIELORDNER laden, echte Uni Ordner nur lesen")
    parser.add_argument("--videos", action="store_true", help="beim Test Sync auch Videos laden")
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
    if args.test_sync:
        return cmd_test_sync(args.test_sync, args.videos)
    return cmd_logout()


if __name__ == "__main__":
    sys.exit(main())
