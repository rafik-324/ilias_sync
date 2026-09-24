"""Downloader Tests: atomares Schreiben, nie überschreiben, Session Ablauf."""

from pathlib import Path

import requests

from ilias_sync import downloader
from ilias_sync.platform_utils import fit_path, sanitize_name


def response(body: bytes, content_type: str = "application/pdf", status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body
    r._content_consumed = True
    r.headers["Content-Type"] = content_type
    r.url = "https://ilias.uni-mannheim.de/x"
    return r


class FakeSession:
    def __init__(self, *responses, on_get=None):
        self.responses = list(responses)
        self.calls = 0
        self.on_get = on_get

    def get(self, url, stream, timeout):
        self.calls += 1
        if self.on_get:
            self.on_get()
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_downloads_atomically(tmp_path):
    target = tmp_path / "a" / "b" / "datei.pdf"
    result = downloader.download_file(FakeSession(response(b"%PDF-1.7 Inhalt")), "u", target)
    assert result.status == "neu"
    assert result.size == 15
    assert target.read_bytes() == b"%PDF-1.7 Inhalt"
    assert not downloader.part_path(target).exists()


def test_never_overwrites_existing_file(tmp_path):
    target = tmp_path / "datei.pdf"
    target.write_bytes(b"vom Nutzer")
    session = FakeSession(response(b"neu aus ILIAS"))
    result = downloader.download_file(session, "u", target)
    assert result.status == "vorhanden"
    assert target.read_bytes() == b"vom Nutzer"
    assert session.calls == 0


def test_file_created_during_download_is_kept(tmp_path):
    target = tmp_path / "datei.pdf"
    session = FakeSession(response(b"aus ILIAS"), on_get=lambda: target.write_bytes(b"vom Nutzer"))
    result = downloader.download_file(session, "u", target)
    assert result.status == "vorhanden"
    assert target.read_bytes() == b"vom Nutzer"
    assert not downloader.part_path(target).exists()


def test_html_instead_of_file_triggers_relogin(tmp_path):
    target = tmp_path / "datei.pdf"
    calls = []
    session = FakeSession(response(b"<html>Login</html>", "text/html; charset=UTF-8"), response(b"%PDF"))
    result = downloader.download_file(session, "u", target, relogin=lambda: calls.append(1))
    assert result.status == "neu"
    assert calls == [1]
    assert target.read_bytes() == b"%PDF"


def test_html_twice_is_an_error_and_leaves_nothing(tmp_path):
    target = tmp_path / "datei.pdf"
    html = response(b"<html>Login</html>", "text/html")
    session = FakeSession(html, response(b"<html>Login</html>", "text/html"))
    result = downloader.download_file(session, "u", target, relogin=lambda: None)
    assert result.status == "fehler"
    assert "Webseite statt der Datei" in result.error
    assert list(tmp_path.iterdir()) == []


def test_html_file_itself_is_allowed(tmp_path):
    target = tmp_path / "seite.html"
    result = downloader.download_file(FakeSession(response(b"<html></html>", "text/html")), "u", target)
    assert result.status == "neu"


def test_network_error_removes_part_file(tmp_path):
    target = tmp_path / "datei.pdf"
    result = downloader.download_file(FakeSession(requests.ConnectionError("weg")), "u", target)
    assert result.status == "fehler"
    assert result.error == "ConnectionError"
    assert not downloader.part_path(target).exists()
    assert not target.exists()


def test_http_error(tmp_path):
    result = downloader.download_file(FakeSession(response(b"", status=404)), "u", tmp_path / "x.pdf")
    assert result.status == "fehler"
    assert result.error == "HTTPError"


# ---------------- Dateinamen ----------------

def test_sanitize_name():
    assert sanitize_name("07 OOP: Einführung") == "07 OOP_ Einführung"
    assert sanitize_name('a<b>c"d|e?f*g') == "a_b_c_d_e_f_g"
    assert sanitize_name("Ordner. ") == "Ordner"
    assert sanitize_name("CON") == "_CON"
    assert sanitize_name("nul.txt") == "_nul.txt"
    assert sanitize_name("  ") == "_"
    # NFD (macOS) wird zu NFC
    assert sanitize_name("U\u0308bung") == "\u00dcbung"


def test_fit_path_shortens_stem_keeps_suffix():
    long = Path("C:/x") / ("a" * 300 + ".pdf")
    fitted = fit_path(long, limit=100)
    assert len(str(fitted)) <= 100
    assert fitted.suffix == ".pdf"
    assert fit_path(Path("C:/x/kurz.pdf"), limit=100) == Path("C:/x/kurz.pdf")
