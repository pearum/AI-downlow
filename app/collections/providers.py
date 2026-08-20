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
from ..providers.common.yt_adapter import (
    build_item_from_info,
    extract_info,
    extract_playlist_page,
)
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
    """Shared yt-dlp powered paging machinery for collection providers.

    Pagination is real: every page is a fresh yt-dlp extraction limited to a
    ``playlist_items`` range, so new items genuinely discovered on each
    ``load_more`` call. No synthetic cursors or tokens are invented.
    """

    _page_size = COLLECTION_PAGE_SIZE
    _max_items = COLLECTION_MAX_ITEMS
    _translate_error = staticmethod(  # type: ignore[assignment]
        lambda exc, fallback="": exc
    )

    def __init__(self) -> None:
        self._entries: dict[str, list[CollectionItem]] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._last_page: dict[str, int] = {}

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
            self._fetch(url, page=0)
        return self._build_info(url)

    def load_more(self, info: CollectionInfo) -> CollectionInfo:
        url = self._normalize(info.url)
        if url not in self._entries:
            self._fetch(url, page=0)
        discovered = len(self._entries.get(url, []))
        if discovered >= self._max_items:
            meta = self._meta.setdefault(url, {})
            if "limit_notice" not in meta:
                meta["limit_notice"] = (
                    f"Discovery stopped after {discovered} items. "
                    "Use a smaller account or refine the source."
                )
            return self._build_info(url)
        page = self._last_page.get(url, 0) + 1
        self._fetch(url, page=page, append=True)
        return self._build_info(url)

    # -- internals ----------------------------------------------------
    def _fetch(self, url: str, page: int, append: bool = False) -> None:
        log.info(
            "[COLLECTION %s] Analyzing %s (page %d)", self.id, url, page + 1
        )
        try:
            if self._detect_mode(url, {}) == CollectionMode.SINGLE:
                raw = extract_info(url, self.platform, playlist=False)
            else:
                raw = extract_playlist_page(
                    url, self.platform, page * self._page_size, self._page_size
                )
        except Exception as exc:  # noqa: BLE001
            raise self._translate_error(
                exc, f"Failed to analyze {self.display_name} collection"
            ) from exc

        mode = self._detect_mode(url, raw)
        name = self._collection_name(url, raw, mode)
        account_username = self._account_from_url(url)
        account_display = (
            raw.get("uploader") or raw.get("channel") or account_username
        )
        page_items = self._entries_from_info(
            raw, url, account_username, account_display, mode, name
        )

        if not page_items and page == 0:
            raise ContentUnavailableError(
                "No playable content was found for this URL."
            )

        total = _safe_int(raw.get("playlist_count"))
        meta = self._meta.get(url) or {}
        meta.update(
            {
                "mode": mode,
                "collection_type": self._collection_type(url, mode),
                "name": name,
                "account_username": account_username,
                "account_display_name": account_display,
                "description": raw.get("description"),
                "total_items": total or meta.get("total_items") or 0,
                "_last_page_full": len(page_items) >= self._page_size,
            }
        )
        # Series / notice info is only recomputed once entries change.
        meta["series"] = self._series_names(url, page_items)
        meta["notice"] = self._notice_for(url, mode, page_items)
        self._meta[url] = meta

        if append:
            known = self._entries.setdefault(url, [])
            existing = {i.item_id for i in known}
            for item in page_items:
                if item.item_id not in existing:
                    known.append(item)
        else:
            self._entries[url] = list(page_items)
        self._last_page[url] = page
        log.info(
            "[COLLECTION %s] page %d: %d new item(s); %d discovered for %s",
            self.id,
            page + 1,
            len(page_items),
            len(self._entries.get(url, [])),
            url,
        )

    def _entries_from_info(
        self,
        info: dict[str, Any],
        collection_url: str,
        account_username: Optional[str],
        account_display: Optional[str],
        mode: CollectionMode,
        collection_name: str,
    ) -> list[CollectionItem]:
        entries = (
            info.get("entries")
            if info.get("_type") == "playlist" or info.get("entries")
            else None
        )
        items: list[CollectionItem] = []
        if entries is not None:
            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                if entry.get("_type") == "playlist":
                    continue
                items.append(
                    self._to_collection_item(
                        entry,
                        idx,
                        collection_url,
                        account_username,
                        account_display,
                        mode,
                        collection_name,
                    )
                )
        elif info.get("id") and info.get("_type") != "playlist":
            items.append(
                self._to_collection_item(
                    info,
                    0,
                    collection_url,
                    account_username,
                    account_display,
                    mode,
                    collection_name,
                )
            )
        return items

    def _to_collection_item(
        self,
        entry: dict[str, Any],
        idx: int,
        collection_url: str,
        account_username: Optional[str],
        account_display: Optional[str],
        mode: CollectionMode,
        collection_name: str,
    ) -> CollectionItem:
        mi = build_item_from_info(entry, self.platform)
        series = self._series_from_entry(entry, mode, collection_name)
        keep_collection_name = mode in (
            CollectionMode.PLAYLIST,
            CollectionMode.SERIES,
        )
        return CollectionItem(
            item_id=str(mi.item_id or idx),
            url=mi.url or entry.get("webpage_url") or entry.get("url") or "",
            title=mi.title,
            platform=self.platform,
            index=mi.index if mi.index is not None else idx + 1,
            account_username=account_username,
            account_display_name=mi.creator or account_display,
            series_name=series,
            collection_name=collection_name if keep_collection_name else None,
            duration=mi.duration,
            thumbnail=mi.thumbnail,
            upload_date=mi.upload_date,
            extra={"collection_url": collection_url},
        )

    def _build_info(self, url: str) -> CollectionInfo:
        meta = self._meta.get(url) or {}
        entries = self._entries.get(url) or []
        page = self._last_page.get(url, 0)
        start = page * self._page_size
        slice_ = entries[start : start + self._page_size]
        discovered = len(entries)
        total = int(meta.get("total_items") or 0)
        last_full = bool(meta.get("_last_page_full"))
        if total and discovered >= total:
            has_more = False
        elif total and discovered < total:
            has_more = True
        else:
            has_more = last_full
        notice = meta.get("notice", "")
        if meta.get("limit_notice"):
            notice = f"{notice}\n{meta['limit_notice']}".strip()
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
            accessible_items=discovered,
            discovered_items=discovered,
            loaded_items=len(slice_),
            items=slice_,
            has_more=has_more,
            next_cursor=str(page + 1) if has_more else None,
            notice=notice,
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

    def _series_from_entry(
        self,
        entry: dict[str, Any],
        mode: CollectionMode,
        collection_name: str,
    ) -> Optional[str]:
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


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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
        if (
            "/@" in url
            or "/channel/" in lowered
            or "/c/" in lowered
            or "/user/" in lowered
        ):
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
        match = re.search(r"/c/([\w.-]+)", path)
        if match:
            return match.group(1)
        return None

    def _collection_name(
        self, url: str, info: dict[str, Any], mode: CollectionMode
    ) -> str:
        if mode == CollectionMode.PLAYLIST:
            return str(
                info.get("title")
                or info.get("playlist_title")
                or info.get("playlist")
                or "Playlist"
            )
        if mode == CollectionMode.ACCOUNT:
            return str(
                info.get("uploader")
                or info.get("channel")
                or info.get("title")
                or info.get("channel_follower_count")
                or "Channel"
            )
        return str(info.get("title") or "YouTube video")

    def _series_from_entry(
        self,
        entry: dict[str, Any],
        mode: CollectionMode,
        collection_name: str,
    ) -> Optional[str]:
        if mode == CollectionMode.PLAYLIST:
            return collection_name or None
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
        if mode == CollectionMode.ACCOUNT:
            return str(
                info.get("uploader")
                or info.get("creator")
                or info.get("title")
                or "TikTok account"
            )
        return str(
            info.get("title") or info.get("playlist_title") or "TikTok collection"
        )

    def _series_from_entry(
        self,
        entry: dict[str, Any],
        mode: CollectionMode,
        collection_name: str,
    ) -> Optional[str]:
        if mode == CollectionMode.SERIES:
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
            return collection_name or None
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

    def _collection_name(
        self, url: str, info: dict[str, Any], mode: CollectionMode
    ) -> str:
        if mode == CollectionMode.ACCOUNT:
            return str(
                info.get("uploader")
                or info.get("channel")
                or info.get("title")
                or "Facebook page"
            )
        return str(
            info.get("title") or info.get("playlist_title") or "Facebook collection"
        )

    def _series_from_entry(
        self,
        entry: dict[str, Any],
        mode: CollectionMode,
        collection_name: str,
    ) -> Optional[str]:
        if mode == CollectionMode.PLAYLIST:
            return collection_name or None
        return None


def build_collection_registry() -> CollectionProviderRegistry:
    """Create the default registry with every platform's collection provider."""
    return CollectionProviderRegistry(
        [
            TikTokCollectionProvider(),
            YouTubeCollectionProvider(),
            FacebookCollectionProvider(),
        ]
    )