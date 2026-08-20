"""Queue management, retry logic and worker tests."""

import time

import pytest

from app.core.bus import EventBus
from app.core.models import ItemStatus, MediaItem, Platform
from app.download.manager import DownloadManager
from app.download.queue import DownloadQueue, QueueItem
from app.providers.base import DownloadOptions
from app.providers.registry import ProviderRegistry
from app.core.errors import NetworkError

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
            output_dir=str(__import__("tempfile").mkdtemp()),
            filename=f"{item.item_id}.mp4",
        ),
    )


class TestDownloadQueue:
    def test_add_and_get(self):
        q = DownloadQueue()
        qi = make_queue_item()
        q.add(qi)
        assert q.get(qi.uid) is qi
        assert q.count() == 1

    def test_next_pending(self):
        q = DownloadQueue()
        first = make_queue_item()
        q.add(first)
        second = make_queue_item(make_item("2", "Video B"))
        q.add(second)
        assert q.next_pending() is first
        assert q.count() == 2

    def test_next_pending_skips_active(self):
        q = DownloadQueue()
        first = make_queue_item()
        first.status = ItemStatus.DOWNLOADING
        second = make_queue_item(make_item("2", "Video B"))
        q.add(first)
        q.add(second)
        assert q.next_pending() is second

    def test_remove(self):
        q = DownloadQueue()
        qi = make_queue_item()
        q.add(qi)
        assert q.remove(qi.uid) is qi
        assert q.count() == 0

    def test_clear_completed(self):
        q = DownloadQueue()
        done = make_queue_item(make_item("1", "Done"))
        done.status = ItemStatus.COMPLETED
        pending = make_queue_item(make_item("2", "Pending"))
        q.add(done)
        q.add(pending)
        removed = q.clear_completed()
        assert len(removed) == 1 and removed[0] is done
        assert q.count() == 1

    def test_retry_failed(self):
        q = DownloadQueue()
        failed = make_queue_item(make_item("1", "Failed"))
        failed.status = ItemStatus.FAILED
        failed.error = "boom"
        q.add(failed)
        q.retry_failed()
        assert failed.status == ItemStatus.PENDING
        assert failed.error == ""
        assert failed.attempts == 0


class TestManager:
    def _manager(self, concurrency=2):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["youtube"] = MockProvider(sleep=0.1)
        return DownloadManager(registry, bus, concurrency=concurrency)

    def test_enqueue_and_complete(self):
        m = self._manager()
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while (
            qi.status != ItemStatus.COMPLETED and time.time() < deadline
        ):
            time.sleep(0.05)
        assert qi.status == ItemStatus.COMPLETED
        assert qi.output_path
        m.shutdown()

    def test_enqueue_and_fail(self):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["youtube"] = MockProvider(fail_download=True)
        m = DownloadManager(registry, bus)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.FAILED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.FAILED
        assert "mock download failure" in qi.error.lower()
        m.shutdown()

    def test_retry_after_failure(self):
        bus = EventBus()
        registry = ProviderRegistry()
        provider = MockProvider(fail_download=True)
        registry._providers["youtube"] = provider
        m = DownloadManager(registry, bus)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.FAILED and time.time() < deadline:
            time.sleep(0.05)
        provider.fail_download = False
        m.retry(qi.uid)
        deadline = time.time() + 10
        while qi.status != ItemStatus.COMPLETED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.COMPLETED
        m.shutdown()

    def test_cancel_pending(self):
        m = self._manager()
        qi = make_queue_item()
        m.enqueue(qi)
        m.cancel(qi.uid)
        assert qi.status == ItemStatus.CANCELLED
        m.shutdown()

    def test_retry_all_failed(self):
        m = self._manager()
        qi = make_queue_item()
        qi.status = ItemStatus.FAILED
        m.enqueue(qi)
        m.retry_all_failed()
        assert qi.status == ItemStatus.PENDING

    def test_concurrency_limited(self):
        bus = EventBus()
        registry = ProviderRegistry()
        provider = MockProvider(sleep=0.5)
        registry._providers["youtube"] = provider
        m = DownloadManager(registry, bus, concurrency=2)
        items = [make_queue_item(make_item(str(i), f"Video {i}")) for i in range(6)]
        m.enqueue_many(items)
        m.start()
        time.sleep(1.0)
        active = m.active_count()
        assert active <= 2
        deadline = time.time() + 20
        while any(i.status not in (ItemStatus.COMPLETED,) for i in items) and time.time() < deadline:
            time.sleep(0.1)
        assert all(i.status == ItemStatus.COMPLETED for i in items)
        m.shutdown()


class TestAutoRetry:
    def test_auto_retry_recovers(self):
        bus = EventBus()
        registry = ProviderRegistry()
        provider = MockProvider(fail_first=1)
        registry._providers["youtube"] = provider
        m = DownloadManager(registry, bus, retry_count=3)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 15
        while qi.status != ItemStatus.COMPLETED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.COMPLETED
        assert len(provider.download_calls) == 2
        m.shutdown()

    def test_auto_retry_gives_up(self):
        bus = EventBus()
        registry = ProviderRegistry()
        provider = MockProvider(fail_download=True)
        registry._providers["youtube"] = provider
        m = DownloadManager(registry, bus, retry_count=2)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 20
        while qi.status != ItemStatus.FAILED and time.time() < deadline:
            time.sleep(0.05)
        assert qi.status == ItemStatus.FAILED
        assert len(provider.download_calls) == 3  # initial + 2 retries
        m.shutdown()


class TestEvents:
    def test_progress_and_status_emitted(self):
        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["youtube"] = MockProvider()
        m = DownloadManager(registry, bus)
        events = {"progress": 0, "completed": 0}

        def on_progress(*a):
            events["progress"] += 1

        def on_completed(uid, path, size):
            events["completed"] += 1
            assert size > 0

        bus.connect("download_progress", on_progress)
        bus.connect("download_completed", on_completed)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        deadline = time.time() + 10
        while qi.status != ItemStatus.COMPLETED and time.time() < deadline:
            time.sleep(0.05)
        assert events["progress"] > 0
        assert events["completed"] == 1
        m.shutdown()
