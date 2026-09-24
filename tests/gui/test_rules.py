"""Regeln aus CLAUDE.md, die sich statisch prüfen lassen."""

import ast
import re
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[2] / "ilias_sync" / "gui"

# Backend Module, die die GUI nicht direkt importieren darf
VERBOTEN = {"auth", "crawler", "objects", "mapper", "downloader", "state", "config", "scheduler", "platform_utils"}


def _modules():
    return sorted(GUI_DIR.glob("*.py"))


def test_keine_gedankenstriche_in_strings():
    funde = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.search("[–—]", node.value):
                    funde.append(f"{path.name}:{node.lineno}: {node.value[:60]!r}")
    assert not funde, "Gedankenstriche in Texten:\n" + "\n".join(funde)


def test_gui_importiert_nur_api():
    funde = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            for name in names:
                parts = name.split(".")
                if parts[0] == "ilias_sync" and len(parts) > 1 and parts[1] in VERBOTEN:
                    funde.append(f"{path.name}:{node.lineno}: {name}")
    assert not funde, "Direkte Backend Imports:\n" + "\n".join(funde)


def test_api_ohne_qt():
    api = (GUI_DIR.parent / "api.py").read_text(encoding="utf-8")
    assert "PySide6" not in api
