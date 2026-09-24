"""Einstieg für den CLI Modus: python -m ilias_sync --login"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import auth, config
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


def cmd_login() -> int:
    try:
        cfg = config.load_config()
    except ConfigError as e:
        print("FEHLER in der Config:", e)
        return 1

    used = {}

    def credentials() -> auth.Credentials:
        creds = console_credentials(cfg)
        used["user"] = creds.user
        return creds

    try:
        s = auth.get_session(credentials)
        r = s.get(auth.DASHBOARD, timeout=auth.TIMEOUT)
    except auth.LoginError as e:
        print("FEHLER:", e)
        return 1
    except requests.RequestException as e:
        print("FEHLER: Keine Verbindung zu ILIAS:", type(e).__name__)
        return 1

    remember_username(cfg, used.get("user"))
    title = BeautifulSoup(r.text, "html.parser").title
    print("Login OK")
    print("Seite:", title.get_text(strip=True) if title else auth.safe_url(r.url))
    print("Cookies gespeichert in:", auth.cookie_file())
    return 0


def cmd_logout() -> int:
    try:
        cfg = config.load_config()
    except ConfigError as e:
        print("FEHLER in der Config:", e)
        return 1
    user = cfg.username or input("Uni-ID: ").strip()
    auth.logout(user)
    print("Abgemeldet: Passwort und Cookies gelöscht. Die Uni-ID bleibt in der Config.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ilias_sync", description="ILIAS Sync (Uni Mannheim)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="Anmeldung testen, Session speichern")
    group.add_argument("--logout", action="store_true", help="Passwort und Cookies löschen")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Ausgabe")
    args = parser.parse_args(argv)

    log_file = setup_logging(args.verbose)
    log.debug("Log Datei: %s", log_file)

    if args.login:
        return cmd_login()
    return cmd_logout()


if __name__ == "__main__":
    sys.exit(main())
