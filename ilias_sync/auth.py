"""
Anmeldung an ILIAS der Uni Mannheim (SAML über Shibboleth IdP).

Ablauf:
  1. ILIAS saml.php leitet zum IdP (idp.uni-mannheim.de)
  2. IdP Loginformular mit Uni-ID und Passwort, dazu Zwischenseiten
     wie LocalStorage Check oder Einwilligung
  3. IdP liefert Auto-Submit Formular mit SAMLResponse
  4. POST an ILIAS /module.php/saml/sp/saml2-acs.php/default-sp
  5. ILIAS setzt Session Cookies

Das Passwort liegt nur im Schlüsselbund und taucht nie in Logs oder
Fehlermeldungen auf. Dieses Modul fragt selbst nichts ab: Zugangsdaten
kommen über einen Callback, damit CLI und GUI denselben Code nutzen.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from http.cookiejar import LWPCookieJar
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import keyring
import platformdirs
import requests
from bs4 import BeautifulSoup
from keyring.errors import PasswordDeleteError

from . import APP_NAME, __version__

ILIAS = "https://ilias.uni-mannheim.de"
ILIAS_HOST = urlparse(ILIAS).hostname
SAML_START = ILIAS + "/saml.php?baseClass=ilstartupgui&cmd=doSamlAuthentication&saml_idp_id=1"
DASHBOARD = ILIAS + "/ilias.php?baseClass=ilDashboardGUI"

KEYRING_SERVICE = APP_NAME
TIMEOUT = 30
USER_AGENT = f"ilias-sync/{__version__} (private use)"
# Höchstzahl an Formularen (IdP Zwischenseiten) pro Login
MAX_FORM_STEPS = 8

log = logging.getLogger(__name__)


class LoginError(Exception):
    """Login fehlgeschlagen. Die Meldung ist für Nutzer gedacht."""


class InvalidCredentials(LoginError):
    """Der IdP hat Uni-ID oder Passwort abgelehnt."""


@dataclass
class Credentials:
    user: str
    password: str
    # Nach erfolgreichem Login im Schlüsselbund speichern
    remember: bool = False

    def __repr__(self) -> str:
        return f"Credentials(user={self.user!r}, password=***, remember={self.remember})"


CredentialsProvider = Callable[[], Credentials]


# ---------------- Pfade ----------------

def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def cookie_file() -> Path:
    return data_dir() / "cookies.txt"


def safe_url(url: str) -> str:
    """URL ohne Query und Fragment, damit keine Session Daten in Meldungen landen."""
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}{p.path}"


# ---------------- Session und Cookies ----------------

def make_session(path: Path | None = None) -> requests.Session:
    path = path or cookie_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    jar = LWPCookieJar(str(path))
    if path.exists():
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
        except (OSError, ValueError) as e:
            log.warning("Gespeicherte Cookies nicht lesbar, neuer Login nötig: %s", type(e).__name__)
    s.cookies = jar
    return s


def save_cookies(s: requests.Session) -> None:
    # LWPCookieJar legt neue Dateien mit Rechten 600 an, chmod gilt für ältere Dateien
    s.cookies.save(ignore_discard=True)
    try:
        os.chmod(s.cookies.filename, 0o600)
    except OSError:
        pass


def clear_cookies(path: Path | None = None) -> None:
    path = path or cookie_file()
    path.unlink(missing_ok=True)


# ---------------- Schlüsselbund ----------------

def load_password(user: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, user)


def store_password(user: str, password: str) -> None:
    keyring.set_password(KEYRING_SERVICE, user, password)


def forget_password(user: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, user)
    except PasswordDeleteError:
        pass


def logout(user: str) -> None:
    """Abmelden: Passwort aus dem Schlüsselbund und Cookies löschen."""
    forget_password(user)
    clear_cookies()


# ---------------- Prüfungen ----------------

def _require_https(resp: requests.Response) -> None:
    for r in [*resp.history, resp]:
        if urlparse(r.url).scheme != "https":
            raise LoginError(f"Unsichere Verbindung abgelehnt: {safe_url(r.url)}")


def is_logged_in(resp: requests.Response) -> bool:
    """Eingeloggt, wenn auf ILIAS, nicht auf login.php und ein Logout Link existiert."""
    if urlparse(resp.url).hostname != ILIAS_HOST or "login.php" in resp.url:
        return False
    soup = BeautifulSoup(resp.text, "html.parser")
    return any("logout" in a["href"].lower() for a in soup.find_all("a", href=True))


def check_session(s: requests.Session) -> bool:
    r = s.get(DASHBOARD, timeout=TIMEOUT)
    _require_https(r)
    return is_logged_in(r)


# ---------------- Formular Parsing ----------------

@dataclass
class Form:
    action: str
    method: str
    data: dict[str, str]
    buttons: list[str]
    password_field: str | None
    user_field: str | None


def parse_form(html: str, base_url: str) -> Form | None:
    """Erstes Formular der Seite mit allen vorbelegten Feldern auslesen."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None:
        return None

    data = {}
    buttons = []
    password_field = None
    user_field = None
    for el in form.find_all(["input", "button"]):
        name = el.get("name")
        if not name:
            continue
        etype = (el.get("type") or ("submit" if el.name == "button" else "text")).lower()
        if etype == "submit":
            buttons.append(name)
            continue
        if el.name == "button":
            continue
        if etype in ("checkbox", "radio") and not el.has_attr("checked"):
            continue
        if etype == "password" and password_field is None:
            password_field = name
        if etype in ("text", "email") and user_field is None:
            user_field = name
        data[name] = el.get("value", "")

    if "j_username" in data:
        user_field = "j_username"

    return Form(
        action=urljoin(base_url, form.get("action") or base_url),
        method=(form.get("method") or "get").lower(),
        data=data,
        buttons=buttons,
        password_field=password_field,
        user_field=user_field,
    )


# ---------------- Login ----------------

def login(s: requests.Session, user: str, password: str) -> requests.Response:
    """SAML Login durchlaufen. Wirft InvalidCredentials oder LoginError."""
    log.info("Login gestartet")
    r = s.get(SAML_START, timeout=TIMEOUT)
    credentials_sent = False

    for _ in range(MAX_FORM_STEPS):
        _require_https(r)
        on_ilias = urlparse(r.url).hostname == ILIAS_HOST
        if on_ilias and is_logged_in(r):
            log.info("Login erfolgreich")
            return r

        f = parse_form(r.text, r.url)
        if f is None:
            break
        # Auf ILIAS nur SAML Formulare weiterreichen, keine anderen Formulare absenden
        if on_ilias and "SAMLResponse" not in f.data:
            break

        if f.password_field:
            if credentials_sent:
                raise InvalidCredentials("Uni-ID oder Passwort falsch.")
            if f.user_field is None:
                raise LoginError("Benutzerfeld im Loginformular nicht gefunden.")
            f.data[f.user_field] = user
            f.data[f.password_field] = password
            credentials_sent = True
            log.debug("Zugangsdaten an IdP gesendet")
        else:
            log.debug("Formular weitergereicht: %s", safe_url(f.action))

        # Shibboleth Buttons (Login, LocalStorage Check, Einwilligung)
        if "_eventId_proceed" in f.buttons:
            f.data["_eventId_proceed"] = ""

        if f.method == "post":
            r = s.post(f.action, data=f.data, timeout=TIMEOUT)
        else:
            r = s.get(f.action, params=f.data, timeout=TIMEOUT)

    if check_session(s):
        log.info("Login erfolgreich")
        return r
    raise LoginError(f"Login fehlgeschlagen, zuletzt auf: {safe_url(r.url)}")


def get_session(credentials: CredentialsProvider, cookie_path: Path | None = None) -> requests.Session:
    """Eingeloggte Session liefern. Gespeicherte Cookies werden wiederverwendet,
    Zugangsdaten nur abgefragt, wenn ein neuer Login nötig ist."""
    s = make_session(cookie_path)
    if check_session(s):
        log.info("Gespeicherte Session gültig")
        return s

    log.info("Keine gültige Session, neuer Login")
    creds = credentials()
    try:
        login(s, creds.user, creds.password)
    except InvalidCredentials:
        forget_password(creds.user)
        raise
    if creds.remember:
        store_password(creds.user, creds.password)
    save_cookies(s)
    return s
