"""Collection / account / series downloader tests.

Offline: downloads use the MockProvider (real files are written to tmp dirs),
URL detection and series parsing use the real providers' pure string logic.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.collections.base import (
    COLLECTION_PAGE_SIZE,
    CollectionInfo,
    CollectionItem,
    CollectionItemStatus,
    CollectionMode,
    CollectionProviderRegistry,
)
from app.collections.engine import CollectionEngine
from app.collections.providers import (
    FacebookCollectionProvider,
    TikTokCollectionProvider,
    YouTubeCollectionProvider,
)
from app.collections.store import CollectionStore
from app.config.settings import SettingsManager
from app.core.bus import EventBus
from app.core.models import ContentType, ItemStatus, Platform
from app.download.manager import DownloadManager
from app.providers.registry import ProviderRegistry
from app.utils.filenames import build_filename

from tests.mocks.collections import MockCollectionProvider
from tests.mocks.providers import MockProvider


@pytest.fixture
def env(tmp_path):
    bus = EventBus()
    registry = ProviderRegistry()
    registry._providers["tiktok"] = MockProvider()
    sm = SettingsManager(tmp_path / "cfg.json")
    sm.update(
        general={
            "download_folder": str(tmp_path / "downloads"),
            "skip_existing": True,
        }
    )
    manager = DownloadManager(registry, bus, concurrency=2)
    store = CollectionStore(tmp_path / "collections.db")
    col_provider = MockCollectionProvider(total=5)
    engine = CollectionEngine(
        registry,
        bus,
        manager,
        sm,
        store=store,
        collections=CollectionProviderRegistry([col_provider]),
    )
    manager.start()
    return SimpleNamespace(
        bus=bus,
        registry=registry,
        settings=sm,
        manager=manager,
        store=store,
        provider=col_provider,
        engine=engine,
        downloads=tmp_path / "downloads",
    )


def wait_status(engine, url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        stats = engine.store.stats(url)
        done = stats["completed"] + stats["skipped"] + stats["failed"]
        if done >= stats["total"]:
            return stats
        time.sleep(0.05)
    return engine.store.stats(url)


# ----------------------------------------------------------------------
# URL detection (pure string logic, no network)
# ----------------------------------------------------------------------
class TestTikTokUrlDetection:
    def test_account_url_detection(self):
        provider = TikTokCollectionProvider()
        url = "https://www.tiktok.com/@scout2015"
        assert provider.supports_collection(url)
        assert provider._account_from_url(url) == "scout2015"
        assert provider._detect_mode(url, {}) == CollectionMode.ACCOUNT

    def test_video_url_detection(self):
        provider = TikTokCollectionProvider()
        url = "https://www.tiktok.com/@user/video/1234567890123456789"
        assert provider._detect_mode(url, {}) == CollectionMode.SINGLE

    def test_series_url_detection(self):
        provider = TikTokCollectionProvider()
        url = "https://www.tiktok.com/@chef/collection/the-hidden-god-of-cookery-7371330159376370462"
        assert provider._detect_mode(url, {}) == CollectionMode.SERIES
        assert provider._collection_type(url, CollectionMode.SERIES).value == "collection"

    def test_series_name_from_slug(self):
        provider = TikTokCollectionProvider()
        url = "https://www.tiktok.com/@chef/collection/the-hidden-god-of-cookery-7371330159376370462"
        name = provider._collection_name(url, {}, CollectionMode.SERIES)
        assert name == "the-hidden-god-of-cookery"

    def test_short_link_detected_as_single(self):
        provider = TikTokCollectionProvider()
        url = "https://vt.tiktok.com/ZSrVrunN/"
        assert provider.supports_collection(url)
        assert provider._detect_mode(url, {}) == CollectionMode.SINGLE


class TestOtherUrlDetection:
    def test_youtube_channel(self):
        provider = YouTubeCollectionProvider()
        url = "https://www.youtube.com/@mkbhd"
        assert provider.supports_collection(url)
        assert provider._account_from_url(url) == "mkbhd"
        assert provider._detect_mode(url, {}) == CollectionMode.ACCOUNT

    def test_youtube_playlist(self):
        provider = YouTubeCollectionProvider()
        url = "https://www.youtube.com/playlist?list=PL123"
        assert provider._detect_mode(url, {}) == CollectionMode.PLAYLIST

    def test_youtube_video(self):
        provider = YouTubeCollectionProvider()
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert provider._detect_mode(url, {}) == CollectionMode.SINGLE

    def test_facebook_page(self):
        provider = FacebookCollectionProvider()
        url = "https://www.facebook.com/NatGeo"
        assert provider.supports_collection(url)
        assert provider._detect_mode(url, {}) == CollectionMode.ACCOUNT

    def test_facebook_video(self):
        provider = FacebookCollectionProvider()
        url = "https://www.facebook.com/watch/?v=10158839510148036"
        assert provider._detect_mode(url, {}) == CollectionMode.SINGLE


# ----------------------------------------------------------------------
# Scanning + progress events
# ----------------------------------------------------------------------
class TestScanning:
    def test_scan_emits_events(self, env):
        events = []
        env.bus.connect("collection_scan_started", lambda url: events.append(("started", url)))
        env.bus.connect("collection_scan_progress", lambda url, f, p, pc: events.append(("progress", pc)))
        env.bus.connect("collection_scan_ready", lambda url, info: events.append(("ready", info.total_items)))
        env.engine.scan("https://mock.example/account")
        deadline = time.time() + 10
        while "ready" not in [e[0] for e in events] and time.time() < deadline:
            time.sleep(0.05)
        kinds = [e[0] for e in events]
        assert "started" in kinds
        assert "progress" in kinds
        assert "ready" in kinds
        ready = [e for e in events if e[0] == "ready"][0]
        assert ready[1] == 5

    def test_scan_failure_emits_failed(self, env):
        env.provider.fail_scan = True
        events = []
        env.bus.connect("collection_scan_failed", lambda url, msg: events.append(msg))
        env.engine.scan("https://mock.example/account")
        deadline = time.time() + 10
        while not events and time.time() < deadline:
            time.sleep(0.05)
        assert events and "Mock scan failure" in events[0]

    def test_scan_persists_collection(self, env):
        env.engine.scan("https://mock.example/account")
        deadline = time.time() + 10
        while not env.engine.discovered_items("https://mock.example/account") and time.time() < deadline:
            time.sleep(0.05)
        col = env.store.get_collection("https://mock.example/account")
        assert col is not None
        assert env.store.stats("https://mock.example/account")["total"] == 5

    def test_duplicate_scan_no_duplicates(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while len(env.engine.discovered_items(url)) < 5 and time.time() < deadline:
            time.sleep(0.05)
        env.engine.scan(url)
        deadline = time.time() + 10
        while env.provider.scan_calls < 2 and time.time() < deadline:
            time.sleep(0.05)
        items = env.store.get_items(int(env.store.get_collection(url)["id"]))
        assert len(items) == 5
        assert len({r["item_id"] for r in items}) == 5


# ----------------------------------------------------------------------
# Pagination
# ----------------------------------------------------------------------
class TestPagination:
    @pytest.fixture
    def paged(self, tmp_path):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        sm = SettingsManager(tmp_path / "cfg.json")
        sm.update(general={"download_folder": str(tmp_path / "downloads")})
        manager = DownloadManager(registry, bus, concurrency=1)
        store = CollectionStore(tmp_path / "c.db")
        provider = MockCollectionProvider(total=7, page_size=2)
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([provider]),
        )
        manager.start()
        return engine, provider

    def test_pagination_steps(self, paged):
        engine, provider = paged
        url = "https://mock.example/big"
        info = provider.analyze_collection(url)
        assert len(info.items) == 2
        assert info.has_more is True
        page2 = provider.load_more(info)
        assert len(page2.items) == 2
        page3 = provider.load_more(page2)
        assert len(page3.items) == 2
        page4 = provider.load_more(page3)
        assert len(page4.items) == 1
        assert page4.has_more is False

    def test_engine_load_more_accumulates(self, paged):
        engine, provider = paged
        url = "https://mock.example/big"
        events = []
        engine.bus.connect("collection_scan_ready", lambda u, info: events.append(info))
        engine.bus.connect("collection_items_ready", lambda u, info: events.append(info))
        engine.scan(url)
        deadline = time.time() + 10
        while not events and time.time() < deadline:
            time.sleep(0.05)
        engine.load_more(url)
        engine.load_more(url)
        engine.load_more(url)
        discovered = engine.discovered_items(url)
        assert len(discovered) == 7

    def test_select_all_covers_full_discovery(self, paged):
        engine, provider = paged
        url = "https://mock.example/big"
        engine.scan(url)
        deadline = time.time() + 10
        while not engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        engine.load_more(url)
        engine.load_more(url)
        engine.load_more(url)
        engine.select_all(url)
        assert len(engine.selected_ids(url)) == 7
        engine.select_none(url)
        assert len(engine.selected_ids(url)) == 0
        engine.invert_selection(url)
        assert len(engine.selected_ids(url)) == 7


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------
class TestSelection:
    def test_default_selection(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        assert len(env.engine.selected_ids(url)) == 5

    def test_invert_and_clear(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        env.engine.set_selected(url, {"v0001"})
        assert env.engine.selected_ids(url) == {"v0001"}
        env.engine.invert_selection(url)
        assert "v0001" not in env.engine.selected_ids(url)
        assert len(env.engine.selected_ids(url)) == 4
        env.engine.select_none(url)
        assert env.engine.selected_ids(url) == set()


# ----------------------------------------------------------------------
# Folder naming / filenames
# ----------------------------------------------------------------------
class TestFolderNaming:
    def test_account_folder(self, env):
        item = CollectionItem(
            item_id="v1",
            url="https://mock.example/watch?v=v1",
            title="Video 001",
            platform=Platform.TIKTOK,
            account_username="@mockuser",
            index=1,
        )
        out = env.engine._output_dir_for(item)
        assert out == env.downloads / "TikTok" / "@mockuser"

    def test_series_folder(self, env):
        item = CollectionItem(
            item_id="v1",
            url="https://mock.example/watch?v=v1",
            title="Episode 01",
            platform=Platform.TIKTOK,
            account_username="@mockuser",
            series_name="The Hidden God of Cookery",
            index=1,
        )
        out = env.engine._output_dir_for(item)
        assert out == env.downloads / "TikTok" / "@mockuser" / "The Hidden God of Cookery"

    def test_series_folder_sanitized(self, env):
        item = CollectionItem(
            item_id="v1",
            url="u",
            title="t",
            platform=Platform.TIKTOK,
            account_username="@mockuser",
            series_name='Bad:Name/With"Chars',
            index=1,
        )
        out = env.engine._output_dir_for(item)
        leaf = str(out).replace("\\", "/").split("/")[-1]
        assert ":" not in leaf and "/" not in leaf and '"' not in leaf

    def test_build_filename_indexed(self, env):
        item = env.provider._make_items("https://mock.example/account")[0]
        name = env.engine._build_filename(item, "mp4")
        assert name.startswith("001 - ")
        assert name.endswith(".mp4")
        assert env.engine._output_dir_for(item).joinpath(name)  # constructible


# ----------------------------------------------------------------------
# Queue creation
# ----------------------------------------------------------------------
class TestQueueCreation:
    def test_build_queue_items(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        items = env.engine.build_queue_items(url, env.engine.selected_ids(url))
        assert len(items) == 5
        for qi in items:
            assert qi.collection_url == url
            assert qi.collection_item_id
            assert "TikTok" in qi.options.output_dir
            assert "@mockuser" in qi.options.output_dir

    def test_add_to_queue_enqueues(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        count = env.engine.add_to_queue(url)
        assert count == 5
        assert env.manager.queue.count() == 5


# ----------------------------------------------------------------------
# End-to-end: scan -> select -> queue -> download -> validate -> folders
# ----------------------------------------------------------------------
class TestEndToEndDownload:
    def test_full_account_download(self, env):
        url = "https://mock.example/account"
        finished = []
        env.bus.connect("collection_finished", lambda u, snap: finished.append(snap))
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        env.engine.add_to_queue(url)
        stats = wait_status(env.engine, url)
        assert stats["completed"] == 5
        assert stats["failed"] == 0
        folder = env.downloads / "TikTok" / "@mockuser"
        files = sorted(p.name for p in folder.glob("*.mp4"))
        assert len(files) == 5
        assert all((folder / f).stat().st_size > 0 for f in files)
        deadline = time.time() + 10
        while not finished and time.time() < deadline:
            time.sleep(0.05)
        assert finished and finished[0]["total"] == 5
        assert finished[0]["completed"] == 5

    def test_series_download_folders(self, tmp_path):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        sm = SettingsManager(tmp_path / "cfg.json")
        sm.update(general={"download_folder": str(tmp_path / "downloads")})
        manager = DownloadManager(registry, bus, concurrency=2)
        store = CollectionStore(tmp_path / "c.db")
        provider = MockCollectionProvider(total=3, series="The Hidden God of Cookery")
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([provider]),
        )
        manager.start()
        url = "https://mock.example/series"
        engine.scan(url)
        deadline = time.time() + 10
        while not engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        engine.add_to_queue(url)
        stats = wait_status(engine, url)
        assert stats["completed"] == 3
        folder = tmp_path / "downloads" / "TikTok" / "@mockuser" / "The Hidden God of Cookery"
        files = sorted(p.name for p in folder.glob("*.mp4"))
        assert len(files) == 3
        assert files[0].startswith("001 - ")
        assert files[1].startswith("002 - ")
        assert files[2].startswith("003 - ")


# ----------------------------------------------------------------------
# Duplicate protection / skip existing
# ----------------------------------------------------------------------
class TestSkipExisting:
    def _seed_files(self, env, valid=True):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        items = env.engine.discovered_items(url)
        folder = env.downloads / "TikTok" / "@mockuser"
        folder.mkdir(parents=True, exist_ok=True)
        for item in items:
            name = env.engine._build_filename(item, "mp4")
            path = folder / name
            path.write_bytes(b"x" * 100 if valid else b"")
        return url

    def test_valid_existing_files_are_skipped(self, env):
        url = self._seed_files(env, valid=True)
        env.engine.add_to_queue(url)
        stats = wait_status(env.engine, url)
        assert stats["skipped"] == 5
        assert stats["completed"] == 0

    def test_zero_byte_existing_file_is_redownloaded(self, env):
        url = self._seed_files(env, valid=False)
        env.engine.add_to_queue(url)
        stats = wait_status(env.engine, url)
        assert stats["completed"] == 5
        folder = env.downloads / "TikTok" / "@mockuser"
        for item in env.engine.discovered_items(url):
            path = folder / env.engine._build_filename(item, "mp4")
            assert path.stat().st_size > 0


# ----------------------------------------------------------------------
# Resume
# ----------------------------------------------------------------------
class TestResume:
    def _completed_setup(self, tmp_path, completed=2):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        sm = SettingsManager(tmp_path / "cfg.json")
        sm.update(general={"download_folder": str(tmp_path / "downloads")})
        manager = DownloadManager(registry, bus, concurrency=2)
        store = CollectionStore(tmp_path / "c.db")
        provider = MockCollectionProvider(total=5)
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([provider]),
        )
        manager.start()
        url = "https://mock.example/account"
        engine.scan(url)
        deadline = time.time() + 10
        while store.stats(url)["total"] < 5 and time.time() < deadline:
            time.sleep(0.05)
        col_id = int(store.get_collection(url)["id"])
        rows = store.get_items(col_id)
        for row in rows[:completed]:
            store.update_item_status(col_id, row["item_id"], "completed", output_path="C:/fake/done.mp4")
        return store, url, sm

    def test_resume_requeues_only_unfinished(self, tmp_path):
        store, url, sm = self._completed_setup(tmp_path, completed=2)
        # simulate app restart: fresh engine bound to the same store
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        manager = DownloadManager(registry, bus, concurrency=2)
        manager.start()
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([MockCollectionProvider(total=5)]),
        )
        info = engine.resume(url)
        assert info is not None
        assert info.total_items == 5
        assert manager.queue.count() == 3  # only the 3 unfinished
        # completed items must not be re-downloaded
        ids = {qi.collection_item_id for qi in manager.queue.all()}
        assert ids == {"v0003", "v0004", "v0005"}

    def test_resume_returns_none_for_unknown(self, tmp_path):
        bus = EventBus()
        registry = ProviderRegistry()
        manager = DownloadManager(registry, bus)
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            SettingsManager(tmp_path / "cfg.json"),
            store=CollectionStore(tmp_path / "c.db"),
            collections=CollectionProviderRegistry([MockCollectionProvider()]),
        )
        assert engine.resume("https://mock.example/nope") is None


# ----------------------------------------------------------------------
# Retry failed
# ----------------------------------------------------------------------
class TestRetryFailed:
    def _failed_setup(self, tmp_path):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        sm = SettingsManager(tmp_path / "cfg.json")
        sm.update(general={"download_folder": str(tmp_path / "downloads")})
        manager = DownloadManager(registry, bus, concurrency=2)
        store = CollectionStore(tmp_path / "c.db")
        provider = MockCollectionProvider(total=5)
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([provider]),
        )
        manager.start()
        url = "https://mock.example/account"
        engine.scan(url)
        deadline = time.time() + 10
        while store.stats(url)["total"] < 5 and time.time() < deadline:
            time.sleep(0.05)
        col_id = int(store.get_collection(url)["id"])
        rows = store.get_items(col_id)
        for row in rows[:2]:
            store.update_item_status(col_id, row["item_id"], "failed", error_message="boom")
        return store, url, sm

    def test_retry_failed_only(self, tmp_path):
        store, url, sm = self._failed_setup(tmp_path)
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        manager = DownloadManager(registry, bus, concurrency=2)
        manager.start()
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([MockCollectionProvider(total=5)]),
        )
        count = engine.retry_failed(url)
        assert count == 2
        assert manager.queue.count() == 2


# ----------------------------------------------------------------------
# Completion report
# ----------------------------------------------------------------------
class TestCompletionReport:
    def test_report_counts(self, env):
        url = "https://mock.example/account"
        env.engine.scan(url)
        deadline = time.time() + 10
        while not env.engine.discovered_items(url) and time.time() < deadline:
            time.sleep(0.05)
        report = []
        env.bus.connect("collection_finished", lambda u, snap: report.append(snap))
        env.engine.add_to_queue(url)
        wait_status(env.engine, url)
        deadline = time.time() + 10
        while not report and time.time() < deadline:
            time.sleep(0.05)
        assert report
        snap = report[0]
        assert snap["total"] == 5
        assert snap["completed"] == 5
        assert snap["skipped"] == 0
        assert snap["failed"] == 0
        assert snap["output_dir"] and Path(snap["output_dir"]).is_dir()


# ----------------------------------------------------------------------
# Windows filename sanitization for collection filenames
# ----------------------------------------------------------------------
class TestSanitization:
    def test_build_filename_sanitizes(self):
        name = build_filename(
            "{title}",
            title='Bad:Title/with"chars*',
            creator="creator",
            index=3,
            date_str=None,
            ext="mp4",
        )
        for ch in '<>:"/\\|?*':
            assert ch not in name

    def test_series_name_sanitized_in_path(self, env):
        item = CollectionItem(
            item_id="v1",
            url="u",
            title="t",
            platform=Platform.TIKTOK,
            account_username="@mockuser",
            series_name='A:B/C\\D*E?F"G<H>I|J',
            index=1,
        )
        out = env.engine._output_dir_for(item)
        tail = out.name
        for ch in '<>:"/\\|?*':
            assert ch not in tail


# ----------------------------------------------------------------------
# Store durability
# ----------------------------------------------------------------------
class TestStore:
    def test_upsert_is_idempotent(self, tmp_path):
        store = CollectionStore(tmp_path / "c.db")
        info = CollectionInfo(
            url="https://mock.example/account",
            platform=Platform.TIKTOK,
            collection_type=ContentType.PROFILE,
            mode=CollectionMode.ACCOUNT,
            name="account",
            total_items=5,
        )
        first = store.upsert_collection(info)
        second = store.upsert_collection(info)
        assert first == second

    def test_stats_counts(self, tmp_path):
        store = CollectionStore(tmp_path / "c.db")
        info = CollectionInfo(
            url="https://mock.example/a",
            platform=Platform.TIKTOK,
            collection_type=ContentType.PROFILE,
            mode=CollectionMode.ACCOUNT,
            name="a",
            total_items=2,
        )
        col_id = store.upsert_collection(info)
        store.save_items(
            col_id,
            [
                CollectionItem(
                    item_id="x",
                    url="u1",
                    title="X",
                    platform=Platform.TIKTOK,
                    index=1,
                ),
                CollectionItem(
                    item_id="y",
                    url="u2",
                    title="Y",
                    platform=Platform.TIKTOK,
                    index=2,
                ),
            ],
        )
        store.update_item_status(col_id, "x", "completed")
        store.update_item_status(col_id, "y", "failed")
        stats = store.stats("https://mock.example/a")
        assert stats["total"] == 2
        assert stats["completed"] == 1
        assert stats["failed"] == 1