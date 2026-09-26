"""
Status Index (SQLite): was wurde wann wohin geladen.

Eine Datenbank pro Uni Ordner (Name aus dem Hash des Pfads), damit ein neuer
Basisordner mit leerem Index startet und alte Einträge nie auf den neuen Ordner
wirken. Lokale Pfade stehen relativ zum Uni Ordner mit "/" getrennt, so übersteht
der Index auch ein Verschieben des ganzen Ordners.

Tabelle items:     ref_id, course_ref_id, course_name, type, ilias_path, local_path,
                   ilias_mtime, size, sha256, downloaded_at, status
Tabelle sync_runs: started_at, finished_at, new, updated, errors, cancelled
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .api import ItemStatus, SyncRun
from .platform_utils import nfc

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    ref_id        INTEGER PRIMARY KEY,
    course_ref_id INTEGER NOT NULL,
    course_name   TEXT NOT NULL,
    type          TEXT NOT NULL,
    ilias_path    TEXT NOT NULL,
    local_path    TEXT NOT NULL,
    ilias_mtime   TEXT,
    size          INTEGER,
    sha256        TEXT,
    downloaded_at TEXT,
    status        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_downloaded ON items (downloaded_at);
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    new         INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0,
    cancelled   INTEGER NOT NULL DEFAULT 0
);
"""

ITEM_COLUMNS = ("ref_id", "course_ref_id", "course_name", "type", "ilias_path", "local_path",
                "ilias_mtime", "size", "sha256", "downloaded_at", "status")


@dataclass
class Item:
    ref_id: int
    course_ref_id: int
    course_name: str
    type: str
    ilias_path: str                   # "Kurs / Ordner / Datei.pdf"
    local_path: str                   # relativ zum Uni Ordner, "/" getrennt
    ilias_mtime: datetime | None
    size: int | None                  # Größe laut ILIAS Anzeige (gerundet)
    sha256: str | None                # Hash der Datei, wie sie aus ILIAS kam
    downloaded_at: datetime | None
    status: ItemStatus


def rel_to_str(rel: tuple[str, ...]) -> str:
    return "/".join(nfc(p) for p in rel)


def str_to_path(root: Path, local_path: str) -> Path:
    return root.joinpath(*PurePosixPath(local_path).parts)


def db_file(data_dir: Path, root: Path) -> Path:
    """Eigene Datenbank pro Uni Ordner."""
    key = nfc(str(root.expanduser().resolve())).casefold().encode("utf-8")
    return data_dir / f"index_{hashlib.sha256(key).hexdigest()[:16]}.sqlite3"


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


class State:
    """Zugriff auf den Index. Thread sicher über eine Sperre, jede Änderung sofort gespeichert."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- items ----------

    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        return Item(
            ref_id=row["ref_id"], course_ref_id=row["course_ref_id"], course_name=row["course_name"],
            type=row["type"], ilias_path=row["ilias_path"], local_path=row["local_path"],
            ilias_mtime=_dt(row["ilias_mtime"]), size=row["size"], sha256=row["sha256"],
            downloaded_at=_dt(row["downloaded_at"]), status=ItemStatus(row["status"]),
        )

    def _query(self, sql: str, params: tuple = ()) -> list[Item]:
        with self._lock:
            return [self._item(r) for r in self._db.execute(sql, params).fetchall()]

    def get(self, ref_id: int) -> Item | None:
        found = self._query("SELECT * FROM items WHERE ref_id = ?", (ref_id,))
        return found[0] if found else None

    def all_items(self) -> dict[int, Item]:
        return {it.ref_id: it for it in self._query("SELECT * FROM items")}

    def put(self, item: Item) -> None:
        values = (
            item.ref_id, item.course_ref_id, item.course_name, item.type, item.ilias_path, item.local_path,
            _iso(item.ilias_mtime), item.size, item.sha256, _iso(item.downloaded_at), item.status.value,
        )
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO items ({', '.join(ITEM_COLUMNS)}) "
                f"VALUES ({', '.join('?' * len(ITEM_COLUMNS))})",
                values,
            )

    def set_status(self, ref_id: int, status: ItemStatus) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE items SET status = ? WHERE ref_id = ?", (status.value, ref_id))

    def recent(self, limit: int = 50) -> list[Item]:
        return self._query(
            "SELECT * FROM items WHERE downloaded_at IS NOT NULL AND status IN (?, ?) "
            "ORDER BY downloaded_at DESC, ref_id LIMIT ?",
            (ItemStatus.OK.value, ItemStatus.UPDATED.value, limit),
        )

    def removed(self) -> list[Item]:
        return self._query("SELECT * FROM items WHERE status = ? ORDER BY local_path",
                           (ItemStatus.USER_REMOVED.value,))

    def is_empty(self) -> bool:
        with self._lock:
            items = self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            runs = self._db.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
        return items == 0 and runs == 0

    # ---------- sync_runs ----------

    def start_run(self, started_at: datetime) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (_iso(started_at),))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, run: SyncRun) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE sync_runs SET finished_at = ?, new = ?, updated = ?, errors = ?, cancelled = ? WHERE id = ?",
                (_iso(run.finished_at), run.new, run.updated, run.errors, int(run.cancelled), run_id),
            )

    def last_run(self) -> SyncRun | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return SyncRun(
            started_at=_dt(row["started_at"]), finished_at=_dt(row["finished_at"]), new=row["new"],
            updated=row["updated"], errors=row["errors"], cancelled=bool(row["cancelled"]),
        )
