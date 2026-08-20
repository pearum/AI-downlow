"""Collection engine: scan, paginate, select, queue, resume, retry, report.

Runs all platform I/O on background threads (the GUI never blocks) and emits
events on the shared bus so the Collections page can update live. Download
execution is delegated to the existing DownloadManager queue so collection
logic never hard-codes a platform.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core.bus import EventBus
from ..core.errors import AppError, UnsupportedPlatformError
from ..core.models import ContentType, ItemStatus, Platform
from ..download.manager import DownloadManager
from ..download.queue import QueueItem
from ..providers.base import DownloadOptions
from ..providers.registry import ProviderRegistry
from ..utils.filenames import build_filename, sanitize_filename
from .base import (
    CollectionInfo,
    CollectionItem,
    CollectionItemStatus,
    CollectionMode,
    CollectionProvider,
    CollectionProviderRegistry,
)
from .providers import build_collection_registry
from .store import CollectionStore

log = logging.getLogger(__name__)


@dataclass
class CollectionRun:
    """Live progress of one collection download run."""

    url: str
    collection_id: int
    total: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    output_dir: str = ""
    finished: bool = False
    run_id: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "collection_id": self.collection_id,
            "total": self.total,
            "completed": self.completed,
            "skipped": self.skipped,
            "failed": self.failed,
            "output_dir": self.output_dir,
            "finished": self.finished,
        }


class CollectionEngine:
    """Coordinates collection analysis and bulk downloads."""

    def __init__(
        self,
        registry: ProviderRegistry,
        bus: EventBus,
        manager: DownloadManager,
        settings_manager,
        store: Optional[CollectionStore] = None,
        collections: Optional[CollectionProviderRegistry] = None,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.manager = manager
        self.settings = settings_manager
        self.store = store or CollectionStore()
        self.collections = collections or build_collection_registry()

        self._lock = threading.RLock()
        self._scans: dict[str, threading.Thread] = {}
        self._current: dict[str, CollectionInfo] = {}
        self._full_items: dict[str, list[CollectionItem]] = {}
        self._selected: dict[str, set[str]] = {}
        self._runs: dict[str, CollectionRun] = {}
        self._run_lock = threading.Lock()

        bus.connect("download_completed", self._on_download_completed)
        bus.connect("download_failed", self._on_download_failed)

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    def scan(self, url: str) -> None:
        with self._lock:
            existing = self._scans.get(url)
            if existing and existing.is_alive():
                return
        thread = threading.Thread(
            target=self._run_scan,
            args=(url,),
            name=f"collection-scan-{abs(hash(url)) % 10000}",
            daemon=True,
        )
        with self._lock:
            self._scans[url] = thread
        thread.start()

    def is_scanning(self, url: str) -> bool:
        with self._lock:
            existing = self._scans.get(url)
        return bool(existing and existing.is_alive())

    def cancel_scan(self, url: str) -> None:
        # Extraction is a single blocking yt-dlp call; we cannot abort it
        # midway, but we detach it so results for a stale URL are ignored.
        with self._lock:
            self._scans.pop(url, None)

    def _run_scan(self, url: str) -> None:
        self.bus.emit("collection_scan_started", url)
        self.bus.emit("collection_scan_progress", url, 0, 0, 0.0)
        try:
            provider = self.collections.detect(url)
            if provider is None:
                raise UnsupportedPlatformError(
                    "No collection provider supports this URL."
                )
            info = provider.analyze_collection(url)
            with self._lock:
                self._current[url] = info
                self._full_items[url] = list(info.items)
                self._selected[url] = {i.item_id for i in info.items}
            collection_id = self.store.upsert_collection(info)
            self.store.save_items(collection_id, info.items)
            self.bus.emit("collection_scan_progress", url, info.total_items, info.total_items, 100.0)
            self.bus.emit("collection_scan_ready", url, info)
        except Exception as exc:  # noqa: BLE001
            log.exception("Collection scan failed for %s", url)
            message = (
                exc.to_user_string()
                if isinstance(exc, AppError)
                else str(exc)
            )
            self.bus.emit("collection_scan_failed", url, message)
        finally:
            with self._lock:
                self._scans.pop(url, None)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def load_more(self, url: str) -> Optional[CollectionInfo]:
        provider = self.collections.detect(url)
        if provider is None:
            return None
        with self._lock:
            info = self._current.get(url)
        if info is None:
            return None
        next_info = provider.load_more(info)
        with self._lock:
            known = self._full_items.setdefault(url, list(info.items))
            known.extend(next_info.items)
            selection = self._selected.setdefault(url, {i.item_id for i in known})
            for item in next_info.items:
                selection.add(item.item_id)
            # surface accumulated state as the "current" info
            merged = CollectionInfo(
                url=info.url,
                platform=info.platform,
                collection_type=info.collection_type,
                mode=info.mode,
                name=info.name,
                account_username=info.account_username,
                account_display_name=info.account_display_name,
                description=info.description,
                total_items=info.total_items,
                accessible_items=info.accessible_items,
                items=next_info.items,
                has_more=next_info.has_more,
                next_cursor=next_info.next_cursor,
                notice=info.notice,
                series=info.series,
            )
            self._current[url] = merged
        self.bus.emit("collection_items_ready", url, next_info)
        return next_info

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def run_for(self, url: str) -> Optional[CollectionRun]:
        with self._run_lock:
            return self._runs.get(url)

    def selected_ids(self, url: str) -> set[str]:
        with self._lock:
            return set(self._selected.get(url, set()))

    def set_selected(self, url: str, item_ids: set[str]) -> None:
        with self._lock:
            self._selected[url] = set(item_ids)

    def select_all(self, url: str) -> None:
        with self._lock:
            known = self._full_items.get(url, [])
            self._selected[url] = {i.item_id for i in known}

    def select_none(self, url: str) -> None:
        with self._lock:
            self._selected[url] = set()

    def invert_selection(self, url: str) -> None:
        with self._lock:
            known = self._full_items.get(url, [])
            selected = self._selected.get(url, set())
            all_ids = {i.item_id for i in known}
            self._selected[url] = all_ids - selected

    def discovered_items(self, url: str) -> list[CollectionItem]:
        with self._lock:
            return list(self._full_items.get(url, []))

    def current_info(self, url: str) -> Optional[CollectionInfo]:
        with self._lock:
            return self._current.get(url)

    # ------------------------------------------------------------------
    # Queue creation
    # ------------------------------------------------------------------
    def build_queue_items(self, url: str, item_ids: set[str]) -> list[QueueItem]:
        provider = self.collections.detect(url)
        if provider is None:
            return []
        with self._lock:
            known = self._full_items.get(url, [])
        chosen = [item for item in known if item.item_id in item_ids]
        items: list[QueueItem] = []
        for ci in chosen:
            media = provider.get_item_metadata(ci)
            ext = self.settings.settings.video.default_format.lower()
            filename = self._build_filename(ci, ext)
            output_dir = str(self._output_dir_for(ci))
            options = DownloadOptions(
                url=media.url,
                quality=self.settings.settings.video.default_quality,
                output_format=self.settings.settings.video.default_format,
                output_dir=output_dir,
                filename=filename,
                embed_metadata=self.settings.settings.video.embed_metadata,
                skip_existing=self.settings.settings.general.skip_existing,
            )
            items.append(
                QueueItem(
                    media=media,
                    options=options,
                    collection_url=url,
                    collection_item_id=ci.item_id,
                )
            )
        return items

    def _build_filename(self, item: CollectionItem, ext: str) -> str:
        date_str = item.upload_date.strftime("%Y-%m-%d") if item.upload_date else None
        return build_filename(
            "{title}",
            title=item.title,
            creator=item.account_display_name or item.account_username,
            index=item.index,
            date_str=date_str,
            ext=ext,
        )

    def _output_dir_for(self, item: Any) -> Path:
        base = Path(self.settings.ensure_download_folder())
        if isinstance(item, QueueItem):
            platform = item.media.platform
            account = item.account or "Unknown"
            series = item.series
        else:
            platform = item.platform
            account = item.account_username or "Unknown"
            series = item.series_name
        parts = [base, platform.display_name]
        parts.append(sanitize_filename(account))
        if series:
            parts.append(sanitize_filename(series))
        return Path(*parts)

    # ------------------------------------------------------------------
    # Start downloads
    # ------------------------------------------------------------------
    def add_to_queue(self, url: str, item_ids: Optional[set[str]] = None) -> int:
        if item_ids is None:
            item_ids = self.selected_ids(url)
        items = self.build_queue_items(url, item_ids)
        if not items:
            return 0
        self._track_run(url, items)
        self.manager.enqueue_many(items)
        self.manager.start()
        return len(items)

    def _track_run(self, url: str, items: list[QueueItem]) -> None:
        col = self.store.get_collection(url)
        collection_id = int(col["id"]) if col else 0
        for qi in items:
            self.store.update_item_status(
                collection_id, qi.collection_item_id, "queued"
            )
        run = CollectionRun(
            url=url,
            collection_id=collection_id,
            total=len(items),
            output_dir=str(self._output_dir_for(items[0])),
        )
        run.run_id = self.store.start_run(collection_id, len(items))
        with self._run_lock:
            self._runs[url] = run
        self.bus.emit("collection_progress", url, run.snapshot())

    # ------------------------------------------------------------------
    # Resume / retry
    # ------------------------------------------------------------------
    def _restore_from_store(self, url: str) -> Optional[CollectionInfo]:
        """Reconstruct scan state (items, selection) from persisted rows."""
        col = self.store.get_collection(url)
        if col is None:
            return None
        collection_id = int(col["id"])
        stored = self.store.get_items(collection_id)
        if not stored:
            return None
        platform = Platform(col["platform"])
        info = CollectionInfo(
            url=url,
            platform=platform,
            collection_type=ContentType(col["collection_type"]),
            mode=CollectionMode(col["mode"]),
            name=col["name"],
            account_username=col["account_username"],
            account_display_name=col["account_display_name"],
            description=col["description"],
            total_items=len(stored),
            accessible_items=len(stored),
        )
        items = [self._row_to_item(row, platform) for row in stored]
        info.items = items
        with self._lock:
            self._current[url] = info
            self._full_items[url] = items
            self._selected[url] = {i.item_id for i in items}
        return info

    def resume(self, url: str) -> Optional[CollectionInfo]:
        """Re-queue unfinished items of a previously scanned collection."""
        info = self._restore_from_store(url)
        if info is None:
            return None

        unfinished = [
            i
            for i in info.items
            if i.status
            in (
                CollectionItemStatus.READY,
                CollectionItemStatus.QUEUED,
                CollectionItemStatus.DOWNLOADING,
                CollectionItemStatus.FAILED,
            )
        ]
        queue_items = self.build_queue_items(
            url, {i.item_id for i in unfinished}
        )
        if queue_items:
            self._track_run(url, queue_items)
            self.manager.enqueue_many(queue_items)
            self.manager.start()
        return info

    def retry_failed(self, url: str) -> int:
        info = self._restore_from_store(url)
        if info is None:
            return 0
        failed_ids = {
            i.item_id
            for i in info.items
            if i.status == CollectionItemStatus.FAILED
        }
        if not failed_ids:
            return 0
        items = self.build_queue_items(url, failed_ids)
        if not items:
            return 0
        self._track_run(url, items)
        self.manager.enqueue_many(items)
        self.manager.start()
        return len(items)

    # ------------------------------------------------------------------
    # Event handlers (bus -> run tracking + store)
    # ------------------------------------------------------------------
    def _on_download_completed(self, uid: str, path: str, size: int) -> None:
        item = self.manager.queue.get(uid)
        if item is None or not item.collection_url:
            return
        url = item.collection_url
        col = self.store.get_collection(url)
        collection_id = int(col["id"]) if col else 0
        if item.status == ItemStatus.SKIPPED:
            status = CollectionItemStatus.SKIPPED
        else:
            status = CollectionItemStatus.COMPLETED
        self.store.update_item_status(
            collection_id, item.collection_item_id, status.value, output_path=path
        )
        with self._run_lock:
            run = self._runs.get(url)
            if run is None:
                return
            if status == CollectionItemStatus.SKIPPED:
                run.skipped += 1
            else:
                run.completed += 1
            self._maybe_finish_run(run)

    def _on_download_failed(self, uid: str, message: str, detail: str) -> None:
        item = self.manager.queue.get(uid)
        if item is None or not item.collection_url:
            return
        url = item.collection_url
        col = self.store.get_collection(url)
        collection_id = int(col["id"]) if col else 0
        self.store.update_item_status(
            collection_id,
            item.collection_item_id,
            CollectionItemStatus.FAILED.value,
            error_message=message,
        )
        with self._run_lock:
            run = self._runs.get(url)
            if run is None:
                return
            run.failed += 1
            self._maybe_finish_run(run)

    def _maybe_finish_run(self, run: CollectionRun) -> None:
        done = run.completed + run.skipped + run.failed
        if run.finished or done >= run.total:
            run.finished = True
            self.store.finish_run(
                run.run_id, run.completed, run.skipped, run.failed
            )
            self.bus.emit("collection_progress", run.url, run.snapshot())
            self.bus.emit("collection_finished", run.url, run.snapshot())

    # ------------------------------------------------------------------
    def _row_to_item(
        self, row: dict[str, Any], platform: Platform
    ) -> CollectionItem:
        from ..core.models import parse_upload_date

        return CollectionItem(
            item_id=row["item_id"],
            url=row["url"],
            title=row["title"],
            platform=platform,
            index=row.get("index_no"),
            account_username=row.get("account_username"),
            series_name=row.get("series_name"),
            collection_name=row.get("collection_name"),
            duration=row.get("duration"),
            thumbnail=row.get("thumbnail"),
            upload_date=parse_upload_date(row.get("upload_date")),
            status=CollectionItemStatus(row.get("status") or "ready"),
            output_path=row.get("output_path") or "",
            error_message=row.get("error_message") or "",
        )