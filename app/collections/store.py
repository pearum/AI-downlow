"""SQLite persistence for collection scans and per-item status.

Used for:
- resuming a collection download after the app restarts (only unfinished
  items are re-queued; validated files are never re-downloaded)
- tracking per-item status (queued / completed / skipped / failed / ...)
- recording completion-run statistics for the collection report.

Stores metadata only — never passwords, cookies or session tokens.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core.logging_setup import app_data_dir
from .base import CollectionInfo, CollectionItem, CollectionItemStatus

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    collection_type TEXT NOT NULL,
    mode TEXT NOT NULL,
    name TEXT NOT NULL,
    account_username TEXT,
    account_display_name TEXT,
    description TEXT,
    total_items INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    index_no INTEGER,
    account_username TEXT,
    account_display_name TEXT,
    series_name TEXT,
    collection_name TEXT,
    duration REAL,
    thumbnail TEXT,
    upload_date TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    output_path TEXT,
    error_message TEXT,
    UNIQUE(collection_id, item_id)
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    total INTEGER DEFAULT 0,
    completed INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0
);
"""


class CollectionStore:
    """Thread-safe SQLite store for collection scans and resume state."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "collections.db")
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
                self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (best-effort)."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(collection_items)")
        }
        if "account_display_name" not in columns:
            conn.execute(
                "ALTER TABLE collection_items "
                "ADD COLUMN account_display_name TEXT"
            )

    # ------------------------------------------------------------------
    def upsert_collection(self, info: CollectionInfo) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM collections WHERE url = ?", (info.url,)
                ).fetchone()
                if row is not None:
                    conn.execute(
                        """
                        UPDATE collections SET platform=?, collection_type=?,
                            mode=?, name=?, account_username=?,
                            account_display_name=?, description=?,
                            total_items=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            info.platform.value,
                            info.collection_type.value,
                            info.mode.value,
                            info.name,
                            info.account_username,
                            info.account_display_name,
                            info.description,
                            info.total_items,
                            now,
                            row["id"],
                        ),
                    )
                    return int(row["id"])
                cur = conn.execute(
                    """
                    INSERT INTO collections
                        (url, platform, collection_type, mode, name,
                         account_username, account_display_name, description,
                         total_items, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        info.url,
                        info.platform.value,
                        info.collection_type.value,
                        info.mode.value,
                        info.name,
                        info.account_username,
                        info.account_display_name,
                        info.description,
                        info.total_items,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

    def save_items(
        self, collection_id: int, items: list[CollectionItem]
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM collection_items WHERE collection_id = ?",
                    (collection_id,),
                )
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO collection_items
                        (collection_id, item_id, url, title, index_no,
                         account_username, account_display_name, series_name,
                         collection_name, duration, thumbnail, upload_date,
                         status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._item_row(collection_id, item) for item in items],
                )

    def add_items(
        self, collection_id: int, items: list[CollectionItem]
    ) -> None:
        """Insert newly discovered items without touching existing rows.

        Unlike ``save_items`` this never deletes or resets previously saved
        statuses, so paginated discovery and resume state coexist safely.
        """
        if not items:
            return
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO collection_items
                        (collection_id, item_id, url, title, index_no,
                         account_username, account_display_name, series_name,
                         collection_name, duration, thumbnail, upload_date,
                         status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._item_row(collection_id, item) for item in items],
                )

    @staticmethod
    def _item_row(collection_id: int, item: CollectionItem) -> tuple[Any, ...]:
        return (
            collection_id,
            item.item_id,
            item.url,
            item.title,
            item.index,
            item.account_username,
            item.account_display_name,
            item.series_name,
            item.collection_name,
            item.duration,
            item.thumbnail,
            item.upload_date.isoformat() if item.upload_date else None,
            item.status.value,
        )

    # ------------------------------------------------------------------
    def get_collection(self, url: str) -> Optional[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM collections WHERE url = ?", (url,)
                ).fetchone()
        return dict(row) if row else None

    def list_collections(self) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM collections ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def get_items(self, collection_id: int) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM collection_items WHERE collection_id = ? "
                    "ORDER BY index_no ASC, id ASC",
                    (collection_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_items_by_status(
        self, collection_id: int, statuses: list[str]
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM collection_items WHERE collection_id = ? "
                    f"AND status IN ({placeholders}) ORDER BY index_no ASC, id ASC",
                    [collection_id, *statuses],
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    def update_item_status(
        self,
        collection_id: int,
        item_id: str,
        status: str,
        output_path: str = "",
        error_message: str = "",
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE collection_items
                    SET status=?, output_path=?, error_message=?
                    WHERE collection_id=? AND item_id=?
                    """,
                    (status, output_path, error_message, collection_id, item_id),
                )

    def stats(self, url: str) -> dict[str, int]:
        with self._lock:
            with self._connect() as conn:
                col = conn.execute(
                    "SELECT id FROM collections WHERE url = ?", (url,)
                ).fetchone()
                if col is None:
                    return {"total": 0, "completed": 0, "skipped": 0, "failed": 0}
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM collection_items "
                    "WHERE collection_id = ? GROUP BY status",
                    (col["id"],),
                ).fetchall()
        counts: dict[str, int] = {
            "total": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
        }
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
            counts["total"] += int(row["n"])
        return counts

    # ------------------------------------------------------------------
    def start_run(self, collection_id: int, total: int) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO collection_runs (collection_id, started_at, total)
                    VALUES (?, ?, ?)
                    """,
                    (collection_id, now, total),
                )
                return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, completed: int, skipped: int, failed: int
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE collection_runs
                    SET finished_at=?, completed=?, skipped=?, failed=?
                    WHERE id=?
                    """,
                    (now, completed, skipped, failed, run_id),
                )

    def update_run_total(self, run_id: int, total: int) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE collection_runs SET total=? WHERE id=?",
                    (total, run_id),
                )

    def is_finished(self, url: str) -> bool:
        """True when the most recent run for the collection finished."""
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT r.finished_at FROM collection_runs r
                    JOIN collections c ON c.id = r.collection_id
                    WHERE c.url = ? ORDER BY r.id DESC LIMIT 1
                    """,
                    (url,),
                ).fetchone()
        return bool(row and row["finished_at"])

    def clear_items(self, collection_id: int) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM collection_items WHERE collection_id = ?",
                    (collection_id,),
                )