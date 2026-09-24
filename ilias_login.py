#!/usr/bin/env python3
"""
ILIAS Uni Mannheim Login (SAML ueber Shibboleth IdP).

Ablauf:
  1. ILIAS saml.php leitet zum IdP (idp.uni-mannheim.de)
  2. IdP Loginformular mit Uni-ID und Passwort
  3. IdP liefert Formular mit SAMLResponse
  4. POST an ILIAS /module.php/saml/sp/saml2-acs.php/default-sp
  5. ILIAS setzt Session Cookies

Zugangsdaten: Umgebungsvariablen ILIAS_USER / ILIAS_PASSWORD
oder System-Schluesselbund (keyring) oder interaktive Eingabe.
"""

import getpass
import os
import sys
from http.cookiejar import LWPCookieJar
from pathlib import Path
from urllib.parse import urljoin, urlparse

import keyring
import requests
from bs4 import BeautifulSoup

ILIAS = "https://ilias.uni-mannheim.de"
ILIAS_HOST = urlparse(ILIAS).hostname
SAML_START = ILIAS + "/saml.php?baseClass=ilstartupgui&cmd=doSamlAuthentication&saml_idp_id=1"
DASHBOARD = ILIAS + "/ilias.php?baseClass=ilDashboardGUI"

KEYRING_SERVICE = "ilias-sync"
DATA_DIR = Path.home() / ".ilias-sync"
COOKIE_FILE = DATA_DIR / "cookies.txt"
TIMEOUT = 30
USER_AGENT = "ilias-sync/0.1 (private use)"


class LoginError(Exception):
    pass


# ---------------- Session und Cookies ----------------

def make_session() -> requests.Session:
    DATA_DIR.mkdir(mode=0o700, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    jar = LWPCookieJar(str(COOKIE_FILE))
    if COOKIE_FILE.exists():
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
        except Exception:
            pass
    s.cookies = jar
    return s


def save_session(s: requests.Session) -> None:
    s.cookies.save(ignore_discard=True)
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass


def is_logged_in(resp: requests.Response) -> bool:
    host = urlparse(resp.url).hostname
    if host != ILIAS_HOST or "login.php" in resp.url:
        return False
    return "logout" in resp.text.lower()


# ---------------- Zugangsdaten ----------------

def get_credentials() -> tuple[str, str]:
    user = os.environ.get("ILIAS_USER") or input("Uni-ID: ").strip()
    pw = os.environ.get("ILIAS_PASSWORD") or keyring.get_password(KEYRING_SERVICE, user)
    if not pw:
        pw = getpass.getpass("Passwort: ")
        if input("Passwort im System-Schluesselbund speichern? [j/N] ").strip().lower() == "j":
            keyring.set_password(KEYRING_SERVICE, user, pw)
    return user, pw


def forget_password(user: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, user)
    except keyring.errors.PasswordDeleteError:
        pass


# ---------------- Formular Parsing ----------------

def parse_form(resp: requests.Response):
    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form")
    if form is None:
        return None

    data = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype == "submit":
            continue
        if itype in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        data[name] = inp.get("value", "")

    buttons = []
    for b in form.find_all(["button", "input"]):
        name = b.get("name")
        if not name:
            continue
        if b.name == "button" or (b.get("type") or "").lower() == "submit":
            buttons.append(name)

    return {
        "form": form,
        "data": data,
        "buttons": buttons,
        "action": urljoin(resp.url, form.get("action") or resp.url),
        "method": (form.get("method") or "get").lower(),
    }


# ---------------- Login ----------------

def login(s: requests.Session, user: str, pw: str) -> requests.Response:
    r = s.get(SAML_START, timeout=TIMEOUT)
    credentials_sent = False

    for _ in range(8):
        host = urlparse(r.url).hostname

        if host == ILIAS_HOST:
            if is_logged_in(r):
                return r
            # Auf ILIAS aber nicht eingeloggt: nur SAML Formulare weiterreichen
            f = parse_form(r)
            if f is None or "SAMLResponse" not in f["data"]:
                break
        else:
            f = parse_form(r)
            if f is None:
                break

        data = f["data"]
        form = f["form"]
        pw_input = form.find("input", {"type": "password"})

        if pw_input:
            if credentials_sent:
                raise LoginError("Uni-ID oder Passwort falsch (IdP zeigt erneut das Loginformular).")
            user_input = form.find("input", {"name": "j_username"}) or form.find(
                "input", {"type": ["text", "email"]}
            )
            if user_input is None:
                raise LoginError("Benutzerfeld im Loginformular nicht gefunden.")
            data[user_input["name"]] = user
            data[pw_input["name"]] = pw
            credentials_sent = True

        # Shibboleth Buttons (Login, LocalStorage Check, Einwilligung)
        if "_eventId_proceed" in f["buttons"]:
            data["_eventId_proceed"] = ""

        if f["method"] == "post":
            r = s.post(f["action"], data=data, timeout=TIMEOUT)
        else:
            r = s.get(f["action"], params=data, timeout=TIMEOUT)

    r = s.get(DASHBOARD, timeout=TIMEOUT)
    if is_logged_in(r):
        return r
    raise LoginError(f"Login fehlgeschlagen, zuletzt auf: {r.url}")


def get_session() -> requests.Session:
    """Gibt eine eingeloggte Session zurueck, nutzt gespeicherte Cookies wenn moeglich."""
    s = make_session()
    r = s.get(DASHBOARD, timeout=TIMEOUT)
    if is_logged_in(r):
        return s

    user, pw = get_credentials()
    try:
        login(s, user, pw)
    except LoginError:
        if "falsch" in str(sys.exc_info()[1]):
            forget_password(user)
        raise
    save_session(s)
    return s


# ---------------- Test ----------------

if __name__ == "__main__":
    try:
        session = get_session()
    except LoginError as e:
        print("FEHLER:", e)
        sys.exit(1)

    r = session.get(DASHBOARD, timeout=TIMEOUT)
    title = BeautifulSoup(r.text, "html.parser").title
    print("Login OK")
    print("Seite:", title.get_text(strip=True) if title else r.url)
    print("Cookies gespeichert in:", COOKIE_FILE)
