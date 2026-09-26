"""
Status Index: SQLite Datei im Datenverzeichnis.

Merkt sich pro ILIAS Objekt (ref_id), wohin es geladen wurde und in welchem
Zustand. Damit erkennt der Sync neue Dateien, in ILIAS geänderte Dateien, vom
Nutzer veränderte Dateien (Hash) und vom Nutzer verschobene oder gelöschte
Dateien (nicht erneut laden).

  * local_path ist relativ zum Uni Ordner und mit "/" getrennt, damit der Index
    unabhängig von Basispfad und Betriebssystem ist
  * ilias_mtime und size sind die Werte aus der ILIAS Liste (Größe gerundet wie
    angezeigt). Änderungen in ILIAS erkennt der Sync durch Vergleich mit dem
    nächsten Crawl, nicht mit der lokalen Datei
  * sha256 ist der Hash der Datei, wie sie geladen wurde
  * Ändert sich eine Datei in ILIAS, wird sie nicht automatisch geladen. Der Index
    vermerkt Status ilias_changed mit Datum und Größe der neuen Version
    (new_mtime, new_size), bis der Nutzer sie lädt oder die Änderung ignoriert

Alle Methoden sind threadsicher, Downloads laufen parallel.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .config import data_dir

SCHEMA_VERSION = 1
STATUSES = ("ok", "updated", "user_modified", "user_removed", "error", "ilias_changed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    ref_id        INTEGER PRIMARY KEY,
    course_ref_id INTEGER NOT NULL,
    type          TEXT    NOT NULL,
    ilias_path    TEXT    NOT NULL,
    local_path    TEXT    NOT NULL,
    ilias_mtime   TEXT,
    size          INTEGER,
    sha256        TEXT,
    downloaded_at TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    new_mtime     TEXT,
    new_size      INTEGER,
    detected_at   TEXT
);
CREATE INDEX IF NOT EXISTS items_course ON items (course_ref_id);
CREATE INDEX IF NOT EXISTS items_downloaded ON items (downloaded_at);
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    new         INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0,
    cancelled   INTEGER NOT NULL DEFAULT 0
);
"""


def state_file() -> Path:
    return data_dir() / "state.db"


def sha256_file(path: Path, chunk_size: int = 1 << 16) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def to_index_path(rel_path: tuple[str, ...] | list[str]) -> str:
    """Relativer Pfad (Teile) -> Schreibweise im Index."""
    return PurePosixPath(*rel_path).as_posix()


def from_index_path(root: Path, local_path: str) -> Path:
    """Schreibweise im Index -> absoluter Pfad unter dem Uni Ordner."""
    return root.joinpath(*PurePosixPath(local_path).parts)


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class Item:
    ref_id: int
    course_ref_id: int
    type: str
    ilias_path: str
    local_path: str              # relativ zum Uni Ordner, "/" getrennt
    ilias_mtime: datetime | None
    size: int | None
    sha256: str | None
    downloaded_at: datetime
    status: str
    new_mtime: datetime | None = None    # nur bei ilias_changed: neue Version in ILIAS
    new_size: int | None = None
    detected_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unbekannter Status: {self.status}")


@dataclass
class Run:
    id: int
    started_at: datetime
    finished_at: datetime | None
    new: int
    updated: int
    errors: int
    cancelled: bool


class State:
    """Zugriff auf den Status Index."""

    def __init__(self, path: Path | None = None):
        self.path = path or state_file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Status Index ist neuer als dieses Programm (Version {version}): {self.path}")
            self._db.executescript(SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- Objekte ----------

    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        return Item(
            ref_id=row["ref_id"],
            course_ref_id=row["course_ref_id"],
            type=row["type"],
            ilias_path=row["ilias_path"],
            local_path=row["local_path"],
            ilias_mtime=_dt(row["ilias_mtime"]),
            size=row["size"],
            sha256=row["sha256"],
            downloaded_at=_dt(row["downloaded_at"]),
            status=row["status"],
            new_mtime=_dt(row["new_mtime"]),
            new_size=row["new_size"],
            detected_at=_dt(row["detected_at"]),
        )

    def get(self, ref_id: int) -> Item | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM items WHERE ref_id = ?", (ref_id,)).fetchone()
        return self._item(row) if row else None

    def put(self, item: Item) -> None:
        """Objekt anlegen oder vollständig ersetzen."""
        values = (
            item.ref_id, item.course_ref_id, item.type, item.ilias_path, item.local_path,
            item.ilias_mtime.isoformat() if item.ilias_mtime else None, item.size, item.sha256,
            item.downloaded_at.isoformat(), item.status,
            item.new_mtime.isoformat() if item.new_mtime else None, item.new_size,
            item.detected_at.isoformat() if item.detected_at else None,
        )
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)

    def set_status(self, ref_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"Unbekannter Status: {status}")
        with self._lock, self._db:
            self._db.execute("UPDATE items SET status = ? WHERE ref_id = ?", (status, ref_id))

    def mark_changed(self, ref_id: int, new_mtime: datetime | None, new_size: int | None,
                     detected_at: datetime | None = None) -> bool:
        """Neue ILIAS Version vermerken. True, wenn die Änderung neu ist (für die Benachrichtigung)."""
        detected_at = detected_at or datetime.now()
        new = (new_mtime.isoformat() if new_mtime else None, new_size)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT status, new_mtime, new_size FROM items WHERE ref_id = ?", (ref_id,)
            ).fetchone()
            if row is None:
                raise KeyError(ref_id)
            if row["status"] == "ilias_changed" and (row["new_mtime"], row["new_size"]) == new:
                return False
            self._db.execute(
                "UPDATE items SET status = 'ilias_changed', new_mtime = ?, new_size = ?, detected_at = ? "
                "WHERE ref_id = ?",
                (*new, detected_at.isoformat(), ref_id),
            )
        return True

    def dismiss_changed(self, ref_id: int) -> None:
        """Änderung ignorieren: neue ILIAS Werte gelten als bekannt, lokale Datei bleibt wie sie ist."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE items SET ilias_mtime = new_mtime, size = new_size, status = 'ok', "
                "new_mtime = NULL, new_size = NULL, detected_at = NULL "
                "WHERE ref_id = ? AND status = 'ilias_changed'",
                (ref_id,),
            )

    def items(self, status: str | None = None, course_ref_id: int | None = None) -> list[Item]:
        sql, args = "SELECT * FROM items WHERE 1", []
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if course_ref_id is not None:
            sql += " AND course_ref_id = ?"
            args.append(course_ref_id)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY ref_id", args).fetchall()
        return [self._item(r) for r in rows]

    def recent(self, limit: int = 50) -> list[Item]:
        """Zuletzt geladene Objekte, neueste zuerst."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM items WHERE status IN ('ok', 'updated') ORDER BY downloaded_at DESC, ref_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._item(r) for r in rows]

    # ---------- Sync Läufe ----------

    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        return Run(
            id=row["id"],
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            new=row["new"],
            updated=row["updated"],
            errors=row["errors"],
            cancelled=bool(row["cancelled"]),
        )

    def start_run(self, started_at: datetime | None = None) -> int:
        started_at = started_at or datetime.now()
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (started_at.isoformat(),))
        return cur.lastrowid

    def finish_run(self, run_id: int, new: int, updated: int, errors: int,
                   cancelled: bool = False, finished_at: datetime | None = None) -> None:
        finished_at = finished_at or datetime.now()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE sync_runs SET finished_at = ?, new = ?, updated = ?, errors = ?, cancelled = ? WHERE id = ?",
                (finished_at.isoformat(), new, updated, errors, int(cancelled), run_id),
            )

    def last_run(self) -> Run | None:
        """Letzter abgeschlossener Lauf (auch abgebrochen), None wenn es noch keinen gab."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self._run(row) if row else None
