"""Jedes Modul muss sich einzeln importieren lassen (fängt Importschleifen ab).

In den übrigen Tests wird api meist zuerst geladen, dann fällt eine Schleife nicht auf.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("module", [
    "ilias_sync.config", "ilias_sync.backend", "ilias_sync.api", "ilias_sync.state", "ilias_sync.sync",
    "ilias_sync.mapper", "ilias_sync.crawler", "ilias_sync.auth", "ilias_sync.platform_utils",
    "ilias_sync.__main__", "ilias_sync.gui.app",
])
def test_modul_einzeln_importierbar(module):
    result = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
