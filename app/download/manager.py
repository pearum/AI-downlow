"""Download manager: concurrency-limited execution of the queue.

The scheduler is event-driven: workers call back into the manager when they
finish, which wakes the scheduler so it can immediately start the next item.
No busy-polling of worker threads is used.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..core.bus import EventBus
from ..core.models import ItemStatus
from ..providers.registry import ProviderRegistry
from .queue import DownloadQueue, QueueItem
from .worker import DownloadWorker

log = logging.getLogger(__name__)

_SCHEDULER_POLL = 0.25


class DownloadManager:
    """Runs up to `concurrency` workers at once, auto-consuming the queue."""

    def __init__(
        self,
        registry: ProviderRegistry,
        bus: EventBus,
        *,
        concurrency: int = 3,
        ffmpeg_path: str = "",
        retry_count: int = 0,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.queue = DownloadQueue()
        self.concurrency = max(1, int(concurrency))
        self.ffmpeg_path = ffmpeg_path
        self.retry_count = max(0, int(retry_count))
        self._lock = threading.Lock()
        self._workers: dict[str, DownloadWorker] = {}
        self._paused = threading.Event()
        self._paused.set()
        self._wake = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._wake.set()
        self._scheduler_thread = threading.Thread(
            target=self._schedule_loop, name="download-scheduler", daemon=True
        )
        self._scheduler_thread.start()

    def stop(self) -> None:
        self._running = False
        self._wake.set()
        self._cancel_all()

    def set_concurrency(self, value: int) -> None:
        self.concurrency = max(1, int(value))
        self._wake.set()

    def set_retry_count(self, value: int) -> None:
        self.retry_count = max(0, int(value))

    # ------------------------------------------------------------------
    def enqueue(self, item: QueueItem) -> None:
        self.queue.add(item)
        log.info("QUEUE: item queued %r (uid=%s)", item.title, item.uid)
        self.bus.emit("queue_changed", self.queue.all())
        self.bus.emit("download_status", item.uid, ItemStatus.QUEUED)
        self._wake.set()

    def enqueue_many(self, items: list[QueueItem]) -> None:
        if not items:
            return
        self.queue.add_all(items)
        log.info("QUEUE: %d item(s) queued", len(items))
        self.bus.emit("queue_changed", self.queue.all())
        for item in items:
            self.bus.emit("download_status", item.uid, ItemStatus.QUEUED)
        self._wake.set()

    # ------------------------------------------------------------------
    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()
        self._wake.set()

    @property
    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def cancel(self, uid: str) -> None:
        item = self.queue.get(uid)
        if item is None:
            return
        item.cancelled = True
        if item.status not in (ItemStatus.COMPLETED, ItemStatus.FAILED, ItemStatus.SKIPPED):
            item.status = ItemStatus.CANCELLED
        self.bus.emit("download_status", uid, ItemStatus.CANCELLED)
        self.bus.emit("queue_changed", self.queue.all())
        self._wake.set()

    def retry(self, uid: str) -> None:
        item = self.queue.get(uid)
        if item is None:
            return
        item.status = ItemStatus.PENDING
        item.percent = 0.0
        item.downloaded = 0
        item.total = 0
        item.speed = None
        item.eta = None
        item.error = ""
        item.error_type = ""
        item.error_stage = ""
        item.error_detail = ""
        item.output_path = ""
        item.file_size = 0
        item.result = None
        item.attempts = 0
        item.cancelled = False
        self.bus.emit("download_status", uid, ItemStatus.QUEUED)
        self.bus.emit("queue_changed", self.queue.all())
        self._wake.set()

    def retry_all_failed(self) -> None:
        self.queue.retry_failed()
        self.bus.emit("queue_changed", self.queue.all())
        self._wake.set()

    def clear_completed(self) -> None:
        self.queue.clear_completed()
        self.bus.emit("queue_changed", self.queue.all())

    def _cancel_all(self) -> None:
        for item in self.queue.all():
            if item.status in (
                ItemStatus.PENDING,
                ItemStatus.QUEUED,
                ItemStatus.DOWNLOADING,
                ItemStatus.PROCESSING,
                ItemStatus.VALIDATING,
            ):
                item.cancelled = True
                if item.status != ItemStatus.DOWNLOADING:
                    item.status = ItemStatus.CANCELLED

    # ------------------------------------------------------------------
    def _schedule_loop(self) -> None:
        while self._running:
            self._paused.wait()
            self._dispatch_available()
            # Sleep until a worker finishes / an item is enqueued / resume.
            self._wake.wait(timeout=_SCHEDULER_POLL)
            self._wake.clear()

    def _dispatch_available(self) -> None:
        with self._lock:
            active = {uid: w for uid, w in self._workers.items() if w.is_alive()}
            self._workers = active
            free_slots = self.concurrency - len(active)
        if free_slots <= 0:
            return
        for _ in range(free_slots):
            with self._lock:
                if self._running is False:
                    return
                item = self.queue.next_pending()
                if item is None:
                    return
                if item.uid in self._workers or item.status not in (
                    ItemStatus.PENDING,
                    ItemStatus.QUEUED,
                ):
                    continue
                worker = DownloadWorker(
                    item,
                    self.registry,
                    self.bus,
                    ffmpeg_path=self.ffmpeg_path,
                    retry_count=self.retry_count,
                    on_finished=self._on_worker_finished,
                )
                self._workers[item.uid] = worker
            log.info("QUEUE: starting item %r (uid=%s)", item.title, item.uid)
            worker.start()

    def _on_worker_finished(self, uid: str) -> None:
        log.info("QUEUE: worker finished for uid=%s — waking scheduler", uid)
        self._wake.set()

    def active_count(self) -> int:
        with self._lock:
            return len([w for w in self._workers.values() if w.is_alive()])

    def shutdown(self) -> None:
        self.stop()