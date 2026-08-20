"""Download worker: executes a single queue item on a background thread.

A download is NEVER marked Completed on progress alone. The worker only
reports success after the final output file has been located and verified
(exists, regular file, size > 0, readable).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..core.bus import EventBus
from ..core.errors import (
    AppError,
    DownloadBlockedError,
    NetworkError,
    RateLimitError,
    build_error_info,
)
from ..core.models import ItemStatus
from ..providers.base import BaseProvider
from ..providers.registry import ProviderRegistry
from .queue import QueueItem
from .result import DownloadResult
from .validate import (
    NO_VALID_OUTPUT_MESSAGE,
    cleanup_bad_output,
    expected_targets,
    locate_final_output,
    validate_output_file,
)

log = logging.getLogger(__name__)

_RETRYABLE_TYPES = (NetworkError, RateLimitError, DownloadBlockedError)
_RETRY_BACKOFF = 1.0

#: Extensions that can be produced by FFmpeg merging / audio extraction.
_ALLOWED_EXTENSIONS = ["mp4", "webm", "mkv", "m4a", "mp3", "flac", "wav", "mov", "ts"]


class DownloadWorker(threading.Thread):
    """Runs one QueueItem to completion or failure (with auto-retry)."""

    def __init__(
        self,
        item: QueueItem,
        registry: ProviderRegistry,
        bus: EventBus,
        *,
        ffmpeg_path: str = "",
        retry_count: int = 0,
        on_finished: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(name=f"dl-worker-{item.uid}", daemon=True)
        self.item = item
        self.registry = registry
        self.bus = bus
        self.ffmpeg_path = ffmpeg_path
        self.retry_count = max(0, int(retry_count))
        self.on_finished = on_finished

    # ------------------------------------------------------------------
    def _progress(self, data: dict) -> None:
        item = self.item
        item.downloaded = data.get("downloaded", 0) or 0
        item.total = data.get("total", 0) or 0
        item.speed = data.get("speed")
        item.eta = data.get("eta")
        if item.total and item.total > 0:
            item.percent = min(100.0, item.downloaded / item.total * 100)
        phase = data.get("status", "downloading")
        if phase == "finished":
            phase = "processing"
        if phase == "processing" and item.status != ItemStatus.PROCESSING:
            item.status = ItemStatus.PROCESSING
            self.bus.emit("download_status", item.uid, ItemStatus.PROCESSING)
        self.bus.emit(
            "download_progress",
            item.uid,
            item.percent,
            item.speed,
            item.eta,
            item.downloaded,
            item.total,
            phase,
        )

    # ------------------------------------------------------------------
    def _set_failed(self, exc: BaseException) -> DownloadResult:
        item = self.item
        info = build_error_info(exc, stage="Downloading")
        item.status = ItemStatus.FAILED
        item.percent = 0.0
        item.error = info["message"]
        item.error_type = info["error_type"]
        item.error_stage = info["stage"]
        item.error_detail = info["detail"]
        result = DownloadResult(
            success=False,
            status="failed",
            error=item.error,
            technical_error=item.error_detail,
            error_type=item.error_type,
            error_stage=item.error_stage,
            duration=item.media.duration,
        )
        item.result = result
        log.error(
            "WORKER: download failed for %r (uid=%s) stage=%s type=%s: %s\nDetail: %s",
            item.title,
            item.uid,
            item.error_stage,
            item.error_type,
            item.error,
            item.error_detail,
        )
        log.exception("WORKER: download failed for %s (%s)", item.title, item.uid)
        return result

    def _is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, _RETRYABLE_TYPES):
            return True
        if isinstance(exc, AppError):
            return False
        text = str(exc).lower()
        return "timeout" in text or "connection" in text or "403" in text or "429" in text

    # ------------------------------------------------------------------
    def _existing_valid_file(self) -> Optional[str]:
        """Return a pre-existing valid output path to skip, or None."""
        item = self.item
        if not item.options.skip_existing:
            return None
        for target in expected_targets(item.options.output_dir, item.options.filename):
            ok, _, _ = validate_output_file(target)
            if ok:
                log.info(
                    "WORKER: skipping %r — existing valid file %s",
                    item.title,
                    target,
                )
                return target
        return None

    def _resolve_final_output(self, raw_path: str) -> tuple[Optional[str], int]:
        """Locate and verify the actual final output file."""
        item = self.item

        ok, _, size = validate_output_file(raw_path)
        if ok:
            log.info(
                "WORKER: output path = %s, output size = %d", raw_path, size
            )
            return raw_path, size

        for target in expected_targets(item.options.output_dir, item.options.filename):
            ok, _, size = validate_output_file(target)
            if ok:
                log.info(
                    "WORKER: output path = %s, output size = %d", target, size
                )
                return target, size

        stem = Path(item.options.filename).stem
        located = locate_final_output(
            item.options.output_dir, stem, _ALLOWED_EXTENSIONS
        )
        if located:
            ok, _, size = validate_output_file(located)
            if ok:
                log.info(
                    "WORKER: output path = %s, output size = %d", located, size
                )
                return located, size

        log.warning(
            "WORKER: validation failed for %r. raw=%r candidates=%r",
            item.title,
            raw_path,
            expected_targets(item.options.output_dir, item.options.filename),
        )
        return None, 0

    def _fail_invalid_output(self) -> DownloadResult:
        item = self.item
        item.status = ItemStatus.FAILED
        item.percent = 0.0
        item.error = NO_VALID_OUTPUT_MESSAGE
        item.error_type = "OutputValidationError"
        item.error_stage = "Validating"
        item.error_detail = (
            f"Download finished but no valid file was found. "
            f"output_dir={item.options.output_dir!r} filename={item.options.filename!r}"
        )
        result = DownloadResult(
            success=False,
            status="failed",
            error=item.error,
            technical_error=item.error_detail,
            error_type=item.error_type,
            error_stage=item.error_stage,
            duration=item.media.duration,
        )
        item.result = result
        cleanup_bad_output(item.options.output_dir, Path(item.options.filename).stem)
        return result

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D102
        item = self.item
        log.info("WORKER: started for %r (uid=%s)", item.title, item.uid)
        try:
            if item.cancelled:
                item.status = ItemStatus.CANCELLED
                self.bus.emit("download_status", item.uid, ItemStatus.CANCELLED)
                return

            provider = self.registry.get(item.media.platform.value)
            if provider is None:
                raise RuntimeError(
                    f"No provider registered for platform '{item.media.platform.value}'."
                )

            if item.options.extra is None:
                item.options.extra = {}
            item.options.extra["ffmpeg_path"] = self.ffmpeg_path

            existing = self._existing_valid_file()
            if existing:
                item.status = ItemStatus.SKIPPED
                item.output_path = existing
                item.file_size = os.path.getsize(existing)
                item.percent = 100.0
                item.result = DownloadResult(
                    success=True,
                    status="skipped",
                    output_path=existing,
                    file_size=item.file_size,
                    duration=item.media.duration,
                )
                log.info(
                    "WORKER: item skipped (existing file) %s", existing
                )
                self.bus.emit("download_status", item.uid, ItemStatus.SKIPPED)
                self.bus.emit(
                    "download_completed", item.uid, existing, item.file_size
                )
                return

            raw_path = self._download_with_retries(provider)
            if item.cancelled:
                item.status = ItemStatus.CANCELLED
                self.bus.emit("download_status", item.uid, ItemStatus.CANCELLED)
                return

            log.info("WORKER: download function returned for %r", item.title)
            item.status = ItemStatus.VALIDATING
            self.bus.emit("download_status", item.uid, ItemStatus.VALIDATING)

            final_path, final_size = self._resolve_final_output(raw_path)
            if final_path is None:
                result = self._fail_invalid_output()
                log.info(
                    "WORKER: validation failed for %r -> FAILED", item.title
                )
                self.bus.emit("download_status", item.uid, ItemStatus.FAILED)
                self.bus.emit(
                    "download_failed", item.uid, result.error, result.technical_error
                )
                return

            item.output_path = final_path
            item.file_size = final_size
            item.percent = 100.0
            item.status = ItemStatus.COMPLETED
            item.result = DownloadResult(
                success=True,
                status="completed",
                output_path=final_path,
                file_size=final_size,
                duration=item.media.duration,
            )
            log.info(
                "WORKER: validation passed for %r (%d bytes) -> COMPLETED",
                item.title,
                final_size,
            )
            self.bus.emit("download_status", item.uid, ItemStatus.COMPLETED)
            self.bus.emit("download_completed", item.uid, final_path, final_size)
        except Exception as exc:  # noqa: BLE001
            if item.cancelled:
                item.status = ItemStatus.CANCELLED
                result = DownloadResult(
                    success=False,
                    status="cancelled",
                    duration=item.media.duration,
                )
                item.result = result
                self.bus.emit("download_status", item.uid, ItemStatus.CANCELLED)
            else:
                result = self._set_failed(exc)
                self.bus.emit("download_status", item.uid, item.status)
                self.bus.emit(
                    "download_failed", item.uid, result.error, result.technical_error
                )
        finally:
            if self.on_finished:
                try:
                    self.on_finished(item.uid)
                except Exception:  # noqa: BLE001
                    log.exception("WORKER: on_finished callback failed")

    def _download_with_retries(self, provider: BaseProvider) -> str:
        item = self.item
        max_attempts = 1 + self.retry_count
        for attempt in range(1, max_attempts + 1):
            item.attempts = attempt
            item.status = ItemStatus.DOWNLOADING
            item.error = ""
            item.error_type = ""
            item.error_stage = ""
            item.error_detail = ""
            self.bus.emit("download_status", item.uid, ItemStatus.DOWNLOADING)
            log.info("WORKER: starting download attempt %d/%d for %r", attempt, max_attempts, item.title)
            try:
                path = provider.download(
                    item.media, item.options, progress_callback=self._progress
                )
                log.info("WORKER: download function returned output=%r", path)
                return path
            except Exception as exc:  # noqa: BLE001
                if attempt < max_attempts and self._is_retryable(exc):
                    backoff = _RETRY_BACKOFF * attempt
                    log.warning(
                        "WORKER: retry %d/%d for %r after error: %s (backoff %.1fs)",
                        attempt,
                        max_attempts,
                        item.title,
                        exc,
                        backoff,
                    )
                    if item.cancelled:
                        raise
                    time.sleep(backoff)
                    continue
                raise