"""Tests für auth.py mit simuliertem IdP, ohne echte Requests."""

import pytest
import requests

from ilias_sync import auth

IDP_SSO = "https://idp.uni-mannheim.de/idp/profile/SAML2/Redirect/SSO"
ACS = "https://ilias.uni-mannheim.de/module.php/saml/sp/saml2-acs.php/default-sp"

LOGIN_FORM = """
<html><body>
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2" method="post">
  <input type="hidden" name="csrf_token" value="CSRF123">
  <input type="text" name="j_username" value="">
  <input type="password" name="j_password" value="">
  <input type="checkbox" name="donotcache" value="1">
  <button type="submit" name="_eventId_proceed">Anmelden</button>
</form>
</body></html>
"""

LOCALSTORAGE_FORM = """
<html><body>
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
  <input type="hidden" name="csrf_token" value="CSRF000">
  <input type="hidden" name="shib_idp_ls_supported" value="">
  <input name="_eventId_proceed" type="hidden" value="">
</form>
</body></html>
"""

SAML_FORM = f"""
<html><body onload="document.forms[0].submit()">
<form action="{ACS}" method="post">
  <input type="hidden" name="RelayState" value="RELAY">
  <input type="hidden" name="SAMLResponse" value="SAMLDATA">
  <input type="submit" value="Weiter">
</form>
</body></html>
"""

DASHBOARD_HTML = """
<html><head><title>Dashboard</title></head><body>
<a href="logout.php?lang=de">Abmelden</a>
</body></html>
"""

ILIAS_LOGIN_HTML = """
<html><body><a href="login.php?cmd=force_login">Anmelden</a></body></html>
"""


def page(url: str, html: str, history: list[str] = ()) -> requests.Response:
    r = requests.Response()
    r.url = url
    r.status_code = 200
    r._content = html.encode("utf-8")
    r.encoding = "utf-8"
    r.history = [page(u, "") for u in history]
    return r


class FakeSession:
    """Liefert Antworten der Reihe nach und merkt sich alle Requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw.get("data") or kw.get("params")))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)


# ---------------- parse_form ----------------

def test_parse_form_login():
    f = auth.parse_form(LOGIN_FORM, IDP_SSO + "?execution=e1s1")
    assert f.action == IDP_SSO + "?execution=e1s2"
    assert f.method == "post"
    assert f.data == {"csrf_token": "CSRF123", "j_username": "", "j_password": ""}
    assert f.buttons == ["_eventId_proceed"]
    assert f.password_field == "j_password"
    assert f.user_field == "j_username"


def test_parse_form_user_field_fallback():
    html = '<form><input type="email" name="mail"><input type="password" name="pw"></form>'
    f = auth.parse_form(html, IDP_SSO)
    assert f.user_field == "mail"
    assert f.method == "get"
    assert f.action == IDP_SSO


def test_parse_form_saml():
    f = auth.parse_form(SAML_FORM, IDP_SSO)
    assert f.action == ACS
    assert f.data == {"RelayState": "RELAY", "SAMLResponse": "SAMLDATA"}
    assert f.password_field is None


def test_parse_form_none():
    assert auth.parse_form("<html><p>kein Formular</p></html>", IDP_SSO) is None


# ---------------- is_logged_in ----------------

@pytest.mark.parametrize(
    "url, html, expected",
    [
        (auth.DASHBOARD, DASHBOARD_HTML, True),
        (auth.ILIAS + "/login.php?target=", DASHBOARD_HTML, False),
        (auth.DASHBOARD, ILIAS_LOGIN_HTML, False),
        (IDP_SSO, DASHBOARD_HTML, False),
        (auth.DASHBOARD, "<p>Bitte logout durchführen</p>", False),
    ],
)
def test_is_logged_in(url, html, expected):
    assert auth.is_logged_in(page(url, html)) is expected


# ---------------- login ----------------

def test_login_full_flow():
    s = FakeSession([
        page(IDP_SSO + "?execution=e1s1", LOCALSTORAGE_FORM, history=[auth.SAML_START]),
        page(IDP_SSO + "?execution=e1s2", LOGIN_FORM),
        page(IDP_SSO + "?execution=e1s3", SAML_FORM),
        page(auth.DASHBOARD, DASHBOARD_HTML, history=[ACS]),
    ])
    r = auth.login(s, "nutzer", "geheim")
    assert r.url == auth.DASHBOARD

    methods = [(m, u) for m, u, _ in s.calls]
    assert methods == [
        ("GET", auth.SAML_START),
        ("POST", IDP_SSO + "?execution=e1s1"),
        ("POST", IDP_SSO + "?execution=e1s2"),
        ("POST", ACS),
    ]
    login_data = s.calls[2][2]
    assert login_data == {
        "csrf_token": "CSRF123",
        "j_username": "nutzer",
        "j_password": "geheim",
        "_eventId_proceed": "",
    }
    assert s.calls[3][2] == {"RelayState": "RELAY", "SAMLResponse": "SAMLDATA"}


def test_login_wrong_password():
    s = FakeSession([
        page(IDP_SSO + "?execution=e1s1", LOGIN_FORM),
        page(IDP_SSO + "?execution=e1s2", LOGIN_FORM),
    ])
    with pytest.raises(auth.InvalidCredentials) as exc:
        auth.login(s, "nutzer", "falschespw")
    assert "falschespw" not in str(exc.value)


def test_login_rejects_http():
    s = FakeSession([page("http://idp.uni-mannheim.de/idp/x", LOGIN_FORM)])
    with pytest.raises(auth.LoginError, match="Unsichere Verbindung"):
        auth.login(s, "nutzer", "geheim")
    assert len(s.calls) == 1


def test_login_failure_message_hides_query():
    s = FakeSession([
        page(auth.ILIAS + "/login.php?sessid=GEHEIM", ILIAS_LOGIN_HTML),
        page(auth.ILIAS + "/login.php?sessid=GEHEIM", ILIAS_LOGIN_HTML),
    ])
    with pytest.raises(auth.LoginError) as exc:
        auth.login(s, "nutzer", "geheim")
    assert "GEHEIM" not in str(exc.value)
    assert "login.php" in str(exc.value)


# ---------------- get_session ----------------

@pytest.fixture
def fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(auth, "load_password", lambda u: store.get(u))
    monkeypatch.setattr(auth, "store_password", lambda u, p: store.__setitem__(u, p))
    monkeypatch.setattr(auth, "forget_password", lambda u: store.pop(u, None))
    return store


def test_get_session_reuses_cookies(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "check_session", lambda s: True)

    def no_credentials():
        raise AssertionError("Zugangsdaten dürfen nicht abgefragt werden")

    auth.get_session(no_credentials, tmp_path / "cookies.txt")


def test_get_session_stores_password_after_success(monkeypatch, tmp_path, fake_keyring):
    monkeypatch.setattr(auth, "check_session", lambda s: False)
    monkeypatch.setattr(auth, "login", lambda s, u, p: None)
    cookies = tmp_path / "cookies.txt"
    auth.get_session(lambda: auth.Credentials("nutzer", "geheim", remember=True), cookies)
    assert fake_keyring == {"nutzer": "geheim"}
    assert cookies.exists()


def test_get_session_wrong_password_clears_keyring(monkeypatch, tmp_path, fake_keyring):
    fake_keyring["nutzer"] = "alt"
    monkeypatch.setattr(auth, "check_session", lambda s: False)

    def reject(s, u, p):
        raise auth.InvalidCredentials("Uni-ID oder Passwort falsch.")

    monkeypatch.setattr(auth, "login", reject)
    with pytest.raises(auth.InvalidCredentials):
        auth.get_session(lambda: auth.Credentials("nutzer", "alt", remember=True), tmp_path / "c.txt")
    assert fake_keyring == {}


def test_credentials_repr_hides_password():
    assert "geheim" not in repr(auth.Credentials("nutzer", "geheim"))
