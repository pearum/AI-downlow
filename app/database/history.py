"""SQLite-backed download history."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.logging_setup import app_data_dir

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    creator TEXT,
    file_path TEXT,
    format TEXT,
    resolution TEXT,
    date_downloaded TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    error_type TEXT,
    error_stage TEXT,
    error_detail TEXT,
    file_size INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_date ON history (date_downloaded DESC);
CREATE INDEX IF NOT EXISTS idx_history_status ON history (status);
"""

_EXTRA_COLUMNS = {
    "error_type": "TEXT",
    "error_stage": "TEXT",
    "error_detail": "TEXT",
    "file_size": "INTEGER DEFAULT 0",
}


@dataclass
class HistoryEntry:
    url: str
    platform: str
    title: str
    creator: str | None
    file_path: str | None
    format: str
    resolution: str
    status: str
    error_message: str | None = None
    error_type: str | None = None
    error_stage: str | None = None
    error_detail: str | None = None
    file_size: int = 0
    date_downloaded: str = ""
    id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "HistoryEntry":
        def get(key: str) -> object:
            try:
                return row[key]
            except (KeyError, IndexError):
                return None

        return cls(
            id=row["id"],
            url=row["url"],
            platform=row["platform"],
            title=row["title"],
            creator=row["creator"],
            file_path=row["file_path"],
            format=row["format"],
            resolution=row["resolution"],
            date_downloaded=row["date_downloaded"],
            status=row["status"],
            error_message=row["error_message"],
            error_type=get("error_type"),
            error_stage=get("error_stage"),
            error_detail=get("error_detail"),
            file_size=int(get("file_size") or 0),
        )


class HistoryDatabase:
    """Thread-safe wrapper around the history SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / "history.db")
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

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the original schema (idempotent)."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
        for column, col_type in _EXTRA_COLUMNS.items():
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE history ADD COLUMN {column} {col_type}"
                )

    def add(self, entry: HistoryEntry) -> int:
        entry.date_downloaded = entry.date_downloaded or datetime.now().isoformat(
            timespec="seconds"
        )
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO history
                        (url, platform, title, creator, file_path, format,
                         resolution, date_downloaded, status, error_message,
                         error_type, error_stage, error_detail, file_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.url,
                        entry.platform,
                        entry.title,
                        entry.creator,
                        entry.file_path,
                        entry.format,
                        entry.resolution,
                        entry.date_downloaded,
                        entry.status,
                        entry.error_message,
                        entry.error_type,
                        entry.error_stage,
                        entry.error_detail,
                        int(entry.file_size or 0),
                    ),
                )
                return int(cur.lastrowid)

    def list(self, limit: int = 500, status: str | None = None) -> list[HistoryEntry]:
        query = "SELECT * FROM history"
        params: list = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY date_downloaded DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        return [HistoryEntry.from_row(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()
                return int(row["n"]) if row else 0

    def clear(self) -> int:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute("DELETE FROM history")
                return cur.rowcount

    def search(self, term: str, limit: int = 100) -> list[HistoryEntry]:
        like = f"%{term}%"
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM history
                    WHERE title LIKE ? OR url LIKE ? OR creator LIKE ?
                    ORDER BY date_downloaded DESC LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
        return [HistoryEntry.from_row(r) for r in rows]