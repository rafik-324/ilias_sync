"""Startdatei für PyInstaller: ruft ilias_sync.__main__.main() als Paket auf.

__main__.py selbst nutzt relative Importe (from . import ...), die beim direkten
Ausführen als Skript nicht funktionieren.
"""

import sys

from ilias_sync.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
