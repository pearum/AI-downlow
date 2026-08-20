"""Concrete collection providers for YouTube, TikTok and Facebook.

All of them reuse the yt-dlp adapter. Nothing here bypasses access controls:
login-gated, private or IP-blocked content surfaces the platform provider's
accurate error unchanged. Series/folder information is only reported when the
platform actually exposes it (e.g. TikTok collection URLs); it is never
fabricated.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from ..core.errors import (
    AppError,
    ContentUnavailableError,
    MetadataError,
)
from ..core.models import ContentType, MediaItem, Platform
from ..providers.common.url_utils import ensure_scheme
from ..providers.common.yt_adapter import build_item_from_info, extract_info
from ..providers.tiktok import TikTokProvider
from ..utils.filenames import sanitize_filename
from .base import (
    COLLECTION_MAX_ITEMS,
    COLLECTION_PAGE_SIZE,
    CollectionInfo,
    CollectionItem,
    CollectionMode,
    CollectionProvider,
    CollectionProviderRegistry,
)

log = logging.getLogger(__name__)

NOT_AVAILABLE_NOTICE = (
    "Series/folder information is not available through the current provider."
)


class _YtCollectionProvider(CollectionProvider):
    """Shared yt-dlp powered paging machinery for collection providers."""

    _page_size = COLLECTION_PAGE_SIZE
    _max_items = COLLECTION_MAX_ITEMS
    _translate_error = staticmethod(  # type: ignore[assignment]
        lambda exc, fallback="": exc
    )

    def __init__(self) -> None:
        self._entries: dict[str, list[CollectionItem]] = {}
        self._page_index: dict[str, int] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    # -- URL helpers -------------------------------------------------
    @staticmethod
    def _normalize(url: str) -> str:
        return ensure_scheme(url.strip())

    def _account_from_url(self, url: str) -> Optional[str]:
        return None

    # -- CollectionProvider -------------------------------------------
    def analyze_collection(self, url: str) -> CollectionInfo:
        url = self._normalize(url)
        if url not in self._entries:
            self._fetch(url)
        self._page_index[url] = 0
        return self._build_info(url, page=0)

    def load_more(self, info: CollectionInfo) -> CollectionInfo:
        url = self._normalize(info.url)
        if url not in self._entries:
            self._fetch(url)
        idx = self._page_index.get(url, 0)
        self._page_index[url] = idx + 1
        return self._build_info(url, page=idx + 1)

    # -- internals ----------------------------------------------------
    def _fetch(self, url: str) -> None:
        log.info("[COLLECTION %s] Analyzing %s", self.id, url)
        try:
            info = extract_info(url, self.platform)
        except Exception as exc:  # noqa: BLE001
            raise self._translate_error(exc, f"Failed to analyze {self.display_name} collection") from exc

        account_username = self._account_from_url(url)
        account_display = info.get("uploader") or info.get("channel") or account_username
        mode = self._detect_mode(url, info)
        name = self._collection_name(url, info, mode)

        entries = self._entries_from_info(info, url, account_username, account_display)

        meta: dict[str, Any] = {
            "mode": mode,
            "collection_type": self._collection_type(url, mode),
            "name": name,
            "account_username": account_username,
            "account_display_name": account_display,
            "description": info.get("description"),
            "series": self._series_names(url, entries),
            "notice": self._notice_for(url, mode, entries),
        }
        self._meta[url] = meta
        self._entries[url] = entries
        log.info(
            "[COLLECTION %s] %d item(s) discovered for %s",
            self.id,
            len(entries),
            url,
        )

    def _entries_from_info(
        self,
        info: dict[str, Any],
        collection_url: str,
        account_username: Optional[str],
        account_display: Optional[str],
    ) -> list[CollectionItem]:
        entries = info.get("entries") if info.get("_type") == "playlist" or info.get("entries") else None
        items: list[CollectionItem] = []
        if entries is not None:
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                if entry.get("_type") == "playlist":
                    continue
                items.append(
                    self._to_collection_item(
                        entry, idx, collection_url, account_username, account_display
                    )
                )
                if len(items) >= self._max_items:
                    break
        elif info.get("id"):
            items.append(
                self._to_collection_item(
                    info, 0, collection_url, account_username, account_display
                )
            )
        if not items:
            raise ContentUnavailableError(
                "No playable content was found for this URL."
            )
        return items

    def _to_collection_item(
        self,
        entry: dict[str, Any],
        idx: int,
        collection_url: str,
        account_username: Optional[str],
        account_display: Optional[str],
    ) -> CollectionItem:
        mi = build_item_from_info(entry, self.platform)
        series = self._series_from_entry(entry)
        return CollectionItem(
            item_id=str(mi.item_id or idx),
            url=mi.url or entry.get("webpage_url") or entry.get("url") or "",
            title=mi.title,
            platform=self.platform,
            index=mi.index if mi.index is not None else idx + 1,
            account_username=account_username,
            account_display_name=mi.creator or account_display,
            series_name=series,
            duration=mi.duration,
            thumbnail=mi.thumbnail,
            upload_date=mi.upload_date,
            extra={"collection_url": collection_url},
        )

    def _build_info(self, url: str, page: int) -> CollectionInfo:
        meta = self._meta.get(url) or {}
        entries = self._entries.get(url) or []
        start = page * self._page_size
        slice_ = entries[start : start + self._page_size]
        total = len(entries)
        has_more = start + self._page_size < total
        return CollectionInfo(
            url=url,
            platform=self.platform,
            collection_type=meta.get("collection_type", ContentType.COLLECTION),
            mode=meta.get("mode", CollectionMode.SINGLE),
            name=meta.get("name") or "Collection",
            account_username=meta.get("account_username"),
            account_display_name=meta.get("account_display_name"),
            description=meta.get("description"),
            total_items=total,
            accessible_items=total,
            items=slice_,
            has_more=has_more,
            next_cursor=str(page + 1) if has_more else None,
            notice=meta.get("notice", ""),
            series=meta.get("series", []),
        )

    # -- overridable per platform -------------------------------------
    def _detect_mode(self, url: str, info: dict[str, Any]) -> CollectionMode:
        return CollectionMode.SINGLE

    def _collection_type(self, url: str, mode: CollectionMode) -> ContentType:
        return ContentType.COLLECTION

    def _collection_name(
        self, url: str, info: dict[str, Any], mode: CollectionMode
    ) -> str:
        return str(info.get("title") or info.get("playlist_title") or "Collection")

    def _series_from_entry(self, entry: dict[str, Any]) -> Optional[str]:
        return None

    def _series_names(
        self, url: str, entries: list[CollectionItem]
    ) -> list[str]:
        names: list[str] = []
        for item in entries:
            if item.series_name and item.series_name not in names:
                names.append(item.series_name)
        return names

    def _notice_for(
        self, url: str, mode: CollectionMode, entries: list[CollectionItem]
    ) -> str:
        if mode in (CollectionMode.ACCOUNT, CollectionMode.SERIES) and not any(
            e.series_name for e in entries
        ):
            return NOT_AVAILABLE_NOTICE
        return ""


class YouTubeCollectionProvider(_YtCollectionProvider):
    id = "youtube"
    display_name = "YouTube"
    platform = Platform.YOUTUBE

    @staticmethod
    def _translate_error(exc: Exception, fallback: str = "") -> Exception:
        from ..providers.youtube import YouTubeProvider

        return YouTubeProvider.translate_error(exc, fallback)

    def supports_collection(self, url: str) -> bool:
        lowered = ensure_scheme(url.strip()).lower()
        return any(
            marker in lowered
            for marker in (
                "youtube.com/",
                "youtu.be/",
                "music.youtube.com/",
                "youtube-nocookie.com/",
            )
        )

    def _detect_mode(self, url: str, info: dict[str, Any]) -> CollectionMode:
        lowered = url.lower()
        if "/playlist" in lowered or "/mix" in lowered:
            return CollectionMode.PLAYLIST
        if "/@" in url or "/channel/" in lowered or "/c/" in lowered or "/user/" in lowered:
            return CollectionMode.ACCOUNT
        return CollectionMode.SINGLE

    def _collection_type(self, url: str, mode: CollectionMode) -> ContentType:
        if mode == CollectionMode.PLAYLIST:
            return ContentType.PLAYLIST
        if mode == CollectionMode.ACCOUNT:
            return ContentType.CHANNEL
        return ContentType.VIDEO

    def _account_from_url(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        path = parsed.path
        match = re.search(r"/@([\w.-]+)", path)
        if match:
            return match.group(1)
        match = re.search(r"/channel/([\w-]+)", path)
        if match:
            return match.group(1)
        match = re.search(r"/user/([\w.-]+)", path)
        if match:
            return match.group(1)
        return None


class TikTokCollectionProvider(_YtCollectionProvider):
    id = "tiktok"
    display_name = "TikTok"
    platform = Platform.TIKTOK

    @staticmethod
    def _translate_error(exc: Exception, fallback: str = "") -> Exception:
        return TikTokProvider.translate_error(exc, fallback)

    def supports_collection(self, url: str) -> bool:
        from ..providers.common.url_utils import detect_platform

        try:
            return detect_platform(url) is Platform.TIKTOK
        except Exception:  # noqa: BLE001
            return False

    def _detect_mode(self, url: str, info: dict[str, Any]) -> CollectionMode:
        path = urlparse(url).path
        if "/collection/" in path:
            return CollectionMode.SERIES
        if "/video/" in path:
            return CollectionMode.SINGLE
        if "@" in path:
            return CollectionMode.ACCOUNT
        return CollectionMode.SINGLE

    def _collection_type(self, url: str, mode: CollectionMode) -> ContentType:
        if mode == CollectionMode.SERIES:
            return ContentType.COLLECTION
        if mode == CollectionMode.ACCOUNT:
            return ContentType.PROFILE
        return ContentType.VIDEO

    def _account_from_url(self, url: str) -> Optional[str]:
        match = re.search(r"/@([\w.-]+)", urlparse(url).path)
        return match.group(1) if match else None

    def _collection_name(
        self, url: str, info: dict[str, Any], mode: CollectionMode
    ) -> str:
        if mode == CollectionMode.SERIES:
            match = re.search(r"/collection/([^/?#]+)", urlparse(url).path)
            if match:
                # The slug ends with "-<numeric-id>"; strip it for display.
                slug = match.group(1)
                stripped = re.sub(r"-\d+$", "", slug)
                return sanitize_filename(stripped or slug)
        return str(info.get("title") or info.get("playlist_title") or "TikTok collection")

    def _series_from_entry(self, entry: dict[str, Any]) -> Optional[str]:
        for key in ("collection_name", "collection", "series", "album"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return sanitize_filename(value.strip())
            if isinstance(value, dict) and value.get("title"):
                return sanitize_filename(str(value["title"]))
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("title"):
                        return sanitize_filename(str(item["title"]))
        return None


class FacebookCollectionProvider(_YtCollectionProvider):
    id = "facebook"
    display_name = "Facebook"
    platform = Platform.FACEBOOK

    @staticmethod
    def _translate_error(exc: Exception, fallback: str = "") -> Exception:
        from ..providers.facebook import FacebookProvider

        return FacebookProvider.translate_error(exc, fallback)

    def supports_collection(self, url: str) -> bool:
        lowered = ensure_scheme(url.strip()).lower()
        return any(
            marker in lowered
            for marker in ("facebook.com/", "fb.watch/", "fb.com/", "m.facebook.com/")
        )

    def _detect_mode(self, url: str, info: dict[str, Any]) -> CollectionMode:
        lowered = url.lower()
        if "/videos/" in lowered or "/watch" in lowered or "/reel/" in lowered:
            return CollectionMode.SINGLE
        if "/playlist/" in lowered or "/album" in lowered or "/photos" in lowered:
            return CollectionMode.PLAYLIST
        if "/pages/" in lowered or "facebook.com/" in lowered:
            return CollectionMode.ACCOUNT
        return CollectionMode.SINGLE

    def _collection_type(self, url: str, mode: CollectionMode) -> ContentType:
        if mode == CollectionMode.PLAYLIST:
            return ContentType.ALBUM
        if mode == CollectionMode.ACCOUNT:
            return ContentType.PROFILE
        return ContentType.VIDEO

    def _account_from_url(self, url: str) -> Optional[str]:
        path = urlparse(url).path.strip("/")
        if not path:
            return None
        first = path.split("/")[0]
        return first if first and "." not in first else None


def build_collection_registry() -> CollectionProviderRegistry:
    """Create the default registry with every platform's collection provider."""
    return CollectionProviderRegistry(
        [
            TikTokCollectionProvider(),
            YouTubeCollectionProvider(),
            FacebookCollectionProvider(),
        ]
    )