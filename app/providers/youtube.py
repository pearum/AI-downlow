"""YouTube provider.

Extraction uses yt-dlp behind the `yt_adapter` wrapper. Login-required or
private content is surfaced as a clear permission error; we do not bypass
access controls.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.errors import (
    ContentUnavailableError,
    MetadataError,
    PermissionDeniedError,
    URLError,
)
from ..core.models import MediaBundle, MediaItem, Platform
from .base import BaseProvider, DownloadOptions
from .common.url_utils import extract_video_id, is_valid_url
from .common.yt_adapter import StreamDownloader, build_bundle_from_info, extract_info

log = logging.getLogger(__name__)


class YouTubeProvider(BaseProvider):
    id = "youtube"
    display_name = "YouTube"
    platform = Platform.YOUTUBE

    def detect_url(self, url: str) -> bool:
        if not is_valid_url(url):
            return False
        video_id = extract_video_id(url, self.platform)
        if video_id:
            return True
        lowered = url.lower()
        return any(
            marker in lowered
            for marker in ("youtube.com/", "youtu.be/", "music.youtube.com/")
        )

    def get_metadata(self, url: str) -> MediaBundle:
        try:
            info = extract_info(url, self.platform)
        except Exception as exc:
            raise self.translate_error(exc, "Failed to fetch YouTube metadata") from exc
        bundle = build_bundle_from_info(info, self.platform, url)
        if bundle.item is None and not bundle.items:
            raise ContentUnavailableError(
                "No playable content found at this YouTube URL."
            )
        return bundle

    def get_playlist_items(self, url: str) -> list[MediaItem]:
        bundle = self.get_metadata(url)
        return bundle.items

    def get_album_items(self, url: str) -> list[MediaItem]:
        return []

    def get_available_formats(self, item: MediaItem) -> list[Any]:
        return item.available_formats or []

    def requires_account_for(self, url: str) -> bool:
        return False

    def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        progress_callback: Any = None,
    ) -> str:
        try:
            downloader = StreamDownloader(
                url=item.url,
                output_dir=options.output_dir,
                filename=options.filename,
                output_format=options.output_format,
                quality=options.quality,
                embed_metadata=options.embed_metadata,
                ffmpeg_path=options.extra.get("ffmpeg_path", "") if options.extra else "",
            )
            if progress_callback is not None:
                downloader.set_progress_callback(progress_callback)
            return downloader.download()
        except Exception as exc:
            raise self.translate_error(exc, "YouTube download failed") from exc
