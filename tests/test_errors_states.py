"""Structured error info, download state transitions and history error details."""

import tempfile
import time
from pathlib import Path

import pytest

from app.core.bus import EventBus
from app.core.errors import (
    AppError,
    NetworkError,
    build_error_info,
)
from app.core.models import ItemStatus, MediaItem, Platform
from app.database.history import HistoryDatabase, HistoryEntry
from app.download.manager import DownloadManager
from app.download.queue import QueueItem
from app.providers.base import DownloadOptions
from app.providers.registry import ProviderRegistry

from tests.mocks.providers import MockProvider


def make_item(item_id="1", title="Video A") -> MediaItem:
    return MediaItem(
        item_id=item_id,
        title=title,
        url=f"https://mock.example/watch?v={item_id}",
        platform=Platform.YOUTUBE,
        creator="Mock Creator",
    )


def make_queue_item(item: MediaItem | None = None) -> QueueItem:
    item = item or make_item()
    return QueueItem(
        media=item,
        options=DownloadOptions(
            url=item.url,
            output_dir=tempfile.mkdtemp(),
            filename=f"{item.item_id}.mp4",
        ),
    )


class TestBuildErrorInfo:
    def test_app_error(self):
        err = NetworkError(detail="raw technical text", stage="Downloading")
        info = build_error_info(err)
        assert info["error_type"] == "NetworkError"
        assert info["message"] == "A network error occurred while communicating with the platform."
        assert info["detail"] == "raw technical text"
        assert info["stage"] == "Downloading"

    def test_raw_exception(self):
        info = build_error_info(ValueError("boom"), stage="Extracting media")
        assert info["error_type"] == "ValueError"
        assert info["message"] == "boom"
        assert info["stage"] == "Extracting media"
        assert "ValueError" in info["detail"]

    def test_app_error_default_stage(self):
        info = build_error_info(AppError(message="x"), stage="DefaultStage")
        assert info["stage"] == "DefaultStage"


class TestStateTransitions:
    def _manager(self, **mock_kwargs):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["youtube"] = MockProvider(**mock_kwargs)
        return DownloadManager(registry, bus)

    def test_status_path_downloading_processing_completed(self):
        m = self._manager()
        qi = make_queue_item()
        seen: list[ItemStatus] = []

        def on_status(uid, status):
            seen.append(ItemStatus(status))

        m.bus.connect("download_status", on_status)
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.COMPLETED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.COMPLETED
        assert ItemStatus.DOWNLOADING in seen
        assert ItemStatus.PROCESSING in seen
        assert ItemStatus.COMPLETED in seen
        # completed must never flip to failed afterwards
        assert qi.status == ItemStatus.COMPLETED
        m.shutdown()

    def test_failure_clears_errors_on_retry(self):
        m = self._manager(fail_download=True)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.FAILED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.error and qi.error_type and qi.error_detail
        assert "mock download failure" in qi.error_detail.lower()
        assert "mock download failure" in qi.error.lower()
        m.shutdown()

    def test_cancel_during_download_stays_cancelled(self):
        m = self._manager(sleep=0.5)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        time.sleep(0.3)
        m.cancel(qi.uid)
        deadline = time.time() + 5
        while qi.status == ItemStatus.DOWNLOADING and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.CANCELLED
        m.shutdown()

    def test_failed_event_carries_detail(self):
        bus = EventBus()
        events: list[tuple] = []
        bus.connect("download_failed", lambda uid, msg, detail: events.append((uid, msg, detail)))
        registry = ProviderRegistry()
        registry._providers["youtube"] = MockProvider(fail_download=True)
        m = DownloadManager(registry, bus)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.FAILED and time.time() < deadline:
            time.sleep(0.05)
        assert events and events[-1][0] == qi.uid
        assert "mock download failure" in events[-1][2].lower()
        m.shutdown()


class TestHistoryErrorDetails:
    def _db(self):
        return HistoryDatabase(Path(tempfile.mkdtemp()) / "h.db")

    def test_structured_columns_roundtrip(self):
        db = self._db()
        db.add(
            HistoryEntry(
                url="https://youtube.com/watch?v=abc",
                platform="YouTube",
                title="Broken",
                creator=None,
                file_path=None,
                format="MP4",
                resolution="1080p",
                status="failed",
                error_message="FFmpeg is required...",
                error_type="FFmpegNotFoundError",
                error_stage="Downloading",
                error_detail="ERROR: ... ffmpeg is not installed",
            )
        )
        rows = db.list(status="failed")
        assert len(rows) == 1
        row = rows[0]
        assert row.error_type == "FFmpegNotFoundError"
        assert row.error_stage == "Downloading"
        assert "ffmpeg is not installed" in row.error_detail
        assert row.status == "failed"

    def test_legacy_db_migrated(self):
        path = Path(tempfile.mkdtemp()) / "legacy.db"
        import sqlite3

        conn = sqlite3.connect(str(path))
        conn.execute(
            """
            CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL, platform TEXT NOT NULL, title TEXT NOT NULL,
                creator TEXT, file_path TEXT, format TEXT, resolution TEXT,
                date_downloaded TEXT NOT NULL, status TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO history (url, platform, title, creator, file_path, format,"
            " resolution, date_downloaded, status, error_message) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("u", "YouTube", "Old", None, None, "MP4", "720p",
             "2026-01-01T00:00:00", "failed", "old error"),
        )
        conn.commit()
        conn.close()

        db = HistoryDatabase(path)
        rows = db.list()
        assert len(rows) == 1
        assert rows[0].error_message == "old error"
        assert rows[0].error_detail is None

        # new columns now exist and are writable
        db.add(
            HistoryEntry(
                url="u2", platform="TikTok", title="New", creator=None,
                file_path=None, format="MP4", resolution="720p",
                status="failed", error_message="msg", error_detail="detail",
                error_type="NetworkError", error_stage="Downloading",
            )
        )
        rows = db.list()
        assert any(r.title == "New" and r.error_detail == "detail" for r in rows)

    def test_invalid_url_translation(self):
        from app.core.errors import UnsupportedPlatformError
        from app.providers.registry import ProviderRegistry

        reg = ProviderRegistry()
        with pytest.raises(UnsupportedPlatformError):
            reg.require("https://example.com/not-supported")
