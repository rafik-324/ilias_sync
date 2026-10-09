"""Größe und SHA-256 einer Release Datei als JSON ausgeben (für den Eintrag auf der Webseite).

Aufruf: python packaging/release_info.py DATEI
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def release_info(path: Path) -> dict:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"file": path.name, "size": path.stat().st_size, "sha256": digest.hexdigest()}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not Path(argv[0]).is_file():
        print("Aufruf: python packaging/release_info.py DATEI", file=sys.stderr)
        return 1
    print(json.dumps(release_info(Path(argv[0])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
