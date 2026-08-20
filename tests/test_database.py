"""Database (history + accounts) tests using temp directories."""

import tempfile
from pathlib import Path

from app.database.accounts import AccountsStore
from app.database.history import HistoryDatabase, HistoryEntry


class TestHistoryDatabase:
    def _db(self):
        return HistoryDatabase(Path(tempfile.mkdtemp()) / "test.db")

    def test_add_and_count(self):
        db = self._db()
        entry = HistoryEntry(
            url="https://youtube.com/watch?v=abc",
            platform="YouTube",
            title="My Video",
            creator="Creator",
            file_path="C:/tmp/video.mp4",
            format="MP4",
            resolution="1080p",
            status="completed",
        )
        entry_id = db.add(entry)
        assert entry_id > 0
        assert db.count() == 1

    def test_list_orders_by_date(self):
        db = self._db()
        first = HistoryEntry(
            url="u1", platform="YouTube", title="Old", creator=None,
            file_path=None, format="MP4", resolution="720p", status="completed",
            date_downloaded="2026-01-01T00:00:00",
        )
        second = HistoryEntry(
            url="u2", platform="TikTok", title="New", creator=None,
            file_path=None, format="MP4", resolution="1080p", status="completed",
            date_downloaded="2026-08-01T00:00:00",
        )
        db.add(first)
        db.add(second)
        rows = db.list()
        assert [r.title for r in rows] == ["New", "Old"]

    def test_list_filter_by_status(self):
        db = self._db()
        ok = HistoryEntry(
            url="u1", platform="YouTube", title="Ok", creator=None,
            file_path=None, format="MP4", resolution="720p", status="completed",
        )
        bad = HistoryEntry(
            url="u2", platform="TikTok", title="Bad", creator=None,
            file_path=None, format="MP4", resolution="720p", status="failed",
            error_message="boom",
        )
        db.add(ok)
        db.add(bad)
        rows = db.list(status="failed")
        assert len(rows) == 1 and rows[0].title == "Bad"
        assert rows[0].error_message == "boom"

    def test_search(self):
        db = self._db()
        db.add(HistoryEntry(
            url="https://youtube.com/x", platform="YouTube", title="Super Video",
            creator="Cool Creator", file_path=None, format="MP4",
            resolution="720p", status="completed",
        ))
        db.add(HistoryEntry(
            url="https://tiktok.com/y", platform="TikTok", title="Other",
            creator=None, file_path=None, format="MP4",
            resolution="720p", status="completed",
        ))
        rows = db.search("super")
        assert len(rows) == 1 and rows[0].title == "Super Video"

    def test_clear(self):
        db = self._db()
        db.add(HistoryEntry(
            url="u1", platform="YouTube", title="T", creator=None,
            file_path=None, format="MP4", resolution="720p", status="completed",
        ))
        assert db.clear() == 1
        assert db.count() == 0


class TestAccountsStore:
    def _store(self):
        return AccountsStore(Path(tempfile.mkdtemp()) / "accounts.json")

    def test_initial_state(self):
        store = self._store()
        assert store.is_connected("youtube") is False

    def test_connect_and_disconnect(self):
        store = self._store()
        store.set_connected("youtube", display_name="My Channel", scopes=["read"])
        assert store.is_connected("youtube") is True
        store.disconnect("youtube")
        assert store.is_connected("youtube") is False

    def test_persists_between_instances(self):
        path = Path(tempfile.mkdtemp()) / "accounts.json"
        store1 = AccountsStore(path)
        store1.set_connected("tiktok", display_name="Creator")
        store2 = AccountsStore(path)
        assert store2.is_connected("tiktok") is True
        assert store2.get("tiktok").display_name == "Creator"

    def test_all(self):
        store = self._store()
        store.set_connected("youtube")
        assert len(store.all()) == 1
