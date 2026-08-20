"""Facebook provider.

Facebook restricts downloading public videos via its official API/OAuth
permissions. This provider handles public, directly-accessible content and
reports a clear message when content requires access that our official
permissions do not cover. We never bypass Facebook's access controls.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.errors import ContentUnavailableError, PermissionDeniedError, URLError
from ..core.models import MediaBundle, MediaItem, Platform
from .base import BaseProvider, DownloadOptions
from .common.url_utils import extract_video_id, is_valid_url
from .common.yt_adapter import StreamDownloader, build_bundle_from_info, extract_info

log = logging.getLogger(__name__)


class FacebookProvider(BaseProvider):
    id = "facebook"
    display_name = "Facebook"
    platform = Platform.FACEBOOK

    def detect_url(self, url: str) -> bool:
        if not is_valid_url(url):
            return False
        lowered = url.lower()
        return any(
            marker in lowered
            for marker in ("facebook.com/", "fb.watch/", "fb.com/")
        )

    def get_metadata(self, url: str) -> MediaBundle:
        try:
            info = extract_info(url, self.platform)
        except Exception as exc:
            if isinstance(exc, PermissionDeniedError):
                raise
            raise self.translate_error(exc, "Failed to fetch Facebook metadata") from exc
        bundle = build_bundle_from_info(info, self.platform, url)
        if bundle.item is None and not bundle.items:
            raise ContentUnavailableError(
                "No playable content found at this Facebook URL."
            )
        return bundle

    def get_playlist_items(self, url: str) -> list[MediaItem]:
        return self.get_metadata(url).items

    def get_album_items(self, url: str) -> list[MediaItem]:
        return []

    def get_available_formats(self, item: MediaItem) -> list[Any]:
        return item.available_formats or []

    def requires_account_for(self, url: str) -> bool:
        return True  # many Facebook videos are login-gated

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
            raise self.translate_error(exc, "Facebook download failed") from exc