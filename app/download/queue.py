"""Thread-safe download queue and queue item model."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..core.models import ItemStatus, MediaItem
from ..providers.base import DownloadOptions
from .result import DownloadResult


@dataclass
class QueueItem:
    """A single unit of work in the download queue."""

    media: MediaItem
    options: DownloadOptions
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: ItemStatus = ItemStatus.PENDING
    percent: float = 0.0
    downloaded: int = 0
    total: int = 0
    speed: Optional[float] = None
    eta: Optional[float] = None
    error: str = ""
    error_type: str = ""
    error_stage: str = ""
    error_detail: str = ""
    output_path: str = ""
    file_size: int = 0
    result: Optional[DownloadResult] = None
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    cancelled: bool = field(default=False, repr=False)

    # Collection context (empty for standalone downloads).
    collection_url: str = ""
    collection_item_id: str = ""

    @property
    def title(self) -> str:
        return self.media.title

    @property
    def platform(self) -> str:
        return self.media.platform.display_name

    @property
    def collection_name(self) -> str:
        extra = self.media.extra.get("collection") or {}
        return extra.get("collection_name") or ""

    @property
    def account(self) -> str:
        extra = self.media.extra.get("collection") or {}
        return extra.get("account_username") or ""

    @property
    def account_display(self) -> str:
        extra = self.media.extra.get("collection") or {}
        return extra.get("account_display_name") or ""

    @property
    def series(self) -> str:
        extra = self.media.extra.get("collection") or {}
        return extra.get("series_name") or ""


class DownloadQueue:
    """Thread-safe list of QueueItem with stable ordering."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: list[QueueItem] = []

    def add(self, item: QueueItem) -> None:
        with self._lock:
            self._items.append(item)

    def add_all(self, items: list[QueueItem]) -> None:
        with self._lock:
            self._items.extend(items)

    def get(self, uid: str) -> QueueItem | None:
        with self._lock:
            for item in self._items:
                if item.uid == uid:
                    return item
        return None

    def all(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    def remove(self, uid: str) -> Optional[QueueItem]:
        with self._lock:
            for idx, item in enumerate(self._items):
                if item.uid == uid:
                    return self._items.pop(idx)
        return None

    def clear_completed(self) -> list[QueueItem]:
        """Remove all completed/cancelled/skipped items and return them."""
        removed: list[QueueItem] = []
        with self._lock:
            kept: list[QueueItem] = []
            for item in self._items:
                if item.status in (
                    ItemStatus.COMPLETED,
                    ItemStatus.CANCELLED,
                    ItemStatus.SKIPPED,
                ):
                    removed.append(item)
                else:
                    kept.append(item)
            self._items = kept
        return removed

    def next_pending(self) -> Optional[QueueItem]:
        """Return the oldest pending item (or None), leaving it in the queue."""
        with self._lock:
            for item in self._items:
                if item.status in (ItemStatus.PENDING, ItemStatus.QUEUED):
                    return item
        return None

    def retry_failed(self) -> list[QueueItem]:
        """Reset all failed items to pending. Returns the list."""
        with self._lock:
            for item in self._items:
                if item.status == ItemStatus.FAILED:
                    item.status = ItemStatus.PENDING
                    item.percent = 0.0
                    item.error = ""
                    item.attempts = 0
                    item.cancelled = False
        return self.all()

    def count(self) -> int:
        with self._lock:
            return len(self._items)