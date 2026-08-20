"""Mock provider used by tests — no real network access."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.core.models import (
    ContentType,
    MediaBundle,
    MediaFormat,
    MediaItem,
    Platform,
)
from app.providers.base import BaseProvider, DownloadOptions


class MockProvider(BaseProvider):
    id = "mock"
    display_name = "Mock Platform"
    platform = Platform.YOUTUBE  # reuse youtube platform to keep registry shape

    def __init__(
        self,
        *,
        fail_download: bool = False,
        fail_metadata: bool = False,
        fail_first: int = 0,
        sleep: float = 0.0,
        playlist_size: int = 3,
        emit_processing: bool = True,
        output_kind: str = "normal",
    ) -> None:
        self.fail_download = fail_download
        self.fail_metadata = fail_metadata
        self.fail_first = max(0, int(fail_first))
        self.sleep = sleep
        self.playlist_size = playlist_size
        self.emit_processing = emit_processing
        self.output_kind = output_kind
        self.download_calls: list[str] = []
        self.bundle = MediaBundle(
            url="https://mock.example/watch?v=abc",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            title="Mock Video",
            creator="Mock Creator",
            item=self._make_item(1),
        )

    def _make_item(self, index: int) -> MediaItem:
        return MediaItem(
            item_id=f"mock-{index}",
            title=f"Mock Video {index}",
            url=f"https://mock.example/watch?v=vid{index}",
            platform=Platform.YOUTUBE,
            content_type=ContentType.VIDEO,
            creator="Mock Creator",
            index=index,
            available_formats=[
                MediaFormat(format_id="1080", label="1080p", height=1080, ext="mp4"),
                MediaFormat(format_id="720", label="720p", height=720, ext="mp4"),
            ],
        )

    def detect_url(self, url: str) -> bool:
        return "mock.example" in url

    def get_metadata(self, url: str) -> MediaBundle:
        if self.fail_metadata:
            from app.core.errors import MetadataError

            raise MetadataError("Mock metadata failure")
        if "/playlist" in url:
            return MediaBundle(
                url=url,
                platform=Platform.YOUTUBE,
                content_type=ContentType.PLAYLIST,
                title="Mock Playlist",
                creator="Mock Creator",
                items=[self._make_item(i) for i in range(1, self.playlist_size + 1)],
            )
        return self.bundle

    def get_playlist_items(self, url: str) -> list[MediaItem]:
        return self.get_metadata(url).items

    def get_album_items(self, url: str) -> list[MediaItem]:
        return []

    def get_available_formats(self, item: MediaItem) -> list[Any]:
        return item.available_formats

    def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        progress_callback: Any = None,
    ) -> str:
        self.download_calls.append(item.item_id)
        if self.sleep:
            time.sleep(self.sleep)
        if self.fail_download or len(self.download_calls) <= self.fail_first:
            from app.core.errors import NetworkError

            raise NetworkError(
                "Mock download failure",
                detail="Mock download failure (technical stack trace)",
            )
        out_dir = Path(options.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (options.filename or f"{item.item_id}.mp4")
        if self.output_kind == "zero_byte":
            target.write_bytes(b"")
            return str(target)
        if self.output_kind == "missing":
            return str(out_dir / f"{item.item_id}_does_not_exist.mp4")
        if self.output_kind == "ffmpeg_rename":
            final = out_dir / f"{target.stem}.mp4"
            final.write_bytes(b"merged-data")
            return str(out_dir / f"{target.stem}.f137.mp4")
        target.write_bytes(b"mockdata")
        if progress_callback:
            if self.emit_processing:
                progress_callback({"downloaded": 50, "total": 100, "status": "downloading"})
                progress_callback({"downloaded": 100, "total": 100, "status": "processing"})
            progress_callback({"downloaded": 100, "total": 100, "status": "finished"})
        return str(target)


class FailingProvider(MockProvider):
    """Always raises on metadata fetch (used for registry tests)."""

    id = "failing"

    def get_metadata(self, url: str) -> MediaBundle:
        from app.core.errors import MetadataError

        raise MetadataError("always fails")
