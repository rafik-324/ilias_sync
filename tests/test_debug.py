"""Debug Modus: nur mit Schalter aktiv, keine Geheimnisse in URLs."""

import logging

import pytest

from ilias_sync import debug


@pytest.fixture
def debug_on(tmp_path, monkeypatch):
    monkeypatch.setattr(debug, "_forced", False)
    monkeypatch.setattr(debug, "_handler", None)
    monkeypatch.setenv(debug.DEBUG_ENV, "1")
    path = debug.setup(tmp_path)
    yield path
    debug.log.removeHandler(debug._handler)
    debug._handler.close()


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(debug.DEBUG_ENV, raising=False)
    monkeypatch.setattr(debug, "_forced", False)
    assert not debug.enabled()
    assert debug.setup(tmp_path) is None
    assert not (tmp_path / debug.DEBUG_FILE_NAME).exists()


def test_enable_switches_on(monkeypatch):
    monkeypatch.delenv(debug.DEBUG_ENV, raising=False)
    monkeypatch.setattr(debug, "_forced", False)
    debug.enable()
    assert debug.enabled()


def test_url_keeps_only_harmless_parameters():
    text = debug.url("https://ilias.uni-mannheim.de/ilias.php?baseClass=ilDashboardGUI&SAMLResponse=geheim&token=x")
    assert text == "https://ilias.uni-mannheim.de/ilias.php?baseClass=ilDashboardGUI (+2 Parameter entfernt)"
    assert "geheim" not in text


def test_debug_file_gets_html_and_paths(debug_on, tmp_path):
    debug.html("Kursliste", "<div>Marketing</div>")
    debug.describe_paths(tmp_path / "config.toml", tmp_path, tmp_path / "cookies.txt", None, None)
    for h in debug.log.handlers:
        h.flush()
    content = debug_on.read_text(encoding="utf-8")
    assert "<div>Marketing</div>" in content
    assert "cookies.txt (fehlt)" in content


def test_debug_does_not_reach_normal_log(debug_on):
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    root = logging.getLogger()
    handler = Collect()
    root.addHandler(handler)
    try:
        debug.log.info("nur im Debug Log")
    finally:
        root.removeHandler(handler)
    assert "nur im Debug Log" not in seen
    assert "nur im Debug Log" in debug_on.read_text(encoding="utf-8")
