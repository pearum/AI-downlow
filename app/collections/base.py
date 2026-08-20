"""Generic collection / account / series contracts.

Platforms expose grouped content in different shapes: YouTube channels and
playlists, TikTok accounts and collections/series, Facebook pages and albums.
This module defines the platform-neutral ``CollectionProvider`` contract plus
the value objects that flow between the provider layer, the engine and the
GUI. No TikTok-specific logic lives here.
"""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..core.models import ContentType, MediaItem, Platform

#: How many items a single pagination page returns to the GUI.
COLLECTION_PAGE_SIZE = 50

#: Hard cap on how many items a collection scan will ingest into memory.
#: Large accounts (1000+) stay paged; we never materialise the entire
#: platform history if the provider does not expose it.
COLLECTION_MAX_ITEMS = 1000


class CollectionMode(str, enum.Enum):
    SINGLE = "single"
    ACCOUNT = "account"
    SERIES = "series"
    PLAYLIST = "playlist"
    FOLDER = "folder"
    BATCH = "batch"


class CollectionItemStatus(str, enum.Enum):
    READY = "ready"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CollectionItem:
    """A lightweight, queueable item discovered inside a collection."""

    item_id: str
    url: str
    title: str
    platform: Platform = Platform.UNKNOWN
    index: Optional[int] = None
    account_username: Optional[str] = None
    account_display_name: Optional[str] = None
    series_name: Optional[str] = None
    collection_name: Optional[str] = None
    duration: Optional[float] = None
    thumbnail: Optional[str] = None
    upload_date: Optional[datetime] = None
    status: CollectionItemStatus = CollectionItemStatus.READY
    output_path: str = ""
    error_message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_media_item(self) -> MediaItem:
        """Build the MediaItem the download engine consumes."""
        item = MediaItem(
            item_id=self.item_id,
            title=self.title,
            url=self.url,
            platform=self.platform,
            creator=self.account_display_name or self.account_username,
            uploader=self.account_display_name or self.account_username,
            duration=self.duration,
            thumbnail=self.thumbnail,
            upload_date=self.upload_date,
            index=self.index,
            playlist_title=self.collection_name or self.series_name,
        )
        item.extra["collection"] = {
            "collection_url": self.extra.get("collection_url", ""),
            "collection_name": self.collection_name,
            "account_username": self.account_username,
            "account_display_name": self.account_display_name,
            "series_name": self.series_name,
        }
        return item


@dataclass
class CollectionInfo:
    """Result of analyzing an account / series / playlist URL."""

    url: str
    platform: Platform
    collection_type: ContentType
    mode: CollectionMode
    name: str
    account_username: Optional[str] = None
    account_display_name: Optional[str] = None
    description: Optional[str] = None
    total_items: int = 0
    accessible_items: int = 0
    items: list[CollectionItem] = field(default_factory=list)
    has_more: bool = False
    next_cursor: Optional[str] = None
    notice: str = ""
    series: list[str] = field(default_factory=list)
    output_dir: str = ""

    @property
    def is_account(self) -> bool:
        return self.mode in (CollectionMode.ACCOUNT, CollectionMode.SERIES)

    @property
    def count(self) -> int:
        return len(self.items)


class CollectionProvider(abc.ABC):
    """Contract for analyzing and paging through grouped content."""

    id: str = ""
    display_name: str = ""
    platform: Platform = Platform.UNKNOWN

    @abc.abstractmethod
    def supports_collection(self, url: str) -> bool:
        """True if this provider can analyze the URL as a collection."""

    @abc.abstractmethod
    def analyze_collection(self, url: str) -> CollectionInfo:
        """Analyze a profile / series / playlist URL.

        Fetches the discovered items (metadata only, never full video
        objects) and returns the first page.
        """

    @abc.abstractmethod
    def load_more(self, info: CollectionInfo) -> CollectionInfo:
        """Return the next page of items for a previously analyzed URL."""

    def get_item_metadata(self, item: CollectionItem) -> MediaItem:
        """Build the full MediaItem used when queueing an item."""
        return item.to_media_item()


class CollectionProviderRegistry:
    """Holds every platform's collection provider and detects by URL."""

    def __init__(self, providers: Optional[list[CollectionProvider]] = None) -> None:
        self._providers: list[CollectionProvider] = list(providers or [])

    def register(self, provider: CollectionProvider) -> None:
        self._providers.append(provider)

    def all(self) -> list[CollectionProvider]:
        return list(self._providers)

    def detect(self, url: str) -> Optional[CollectionProvider]:
        for provider in self._providers:
            if provider.supports_collection(url):
                return provider
        return None

    def get(self, provider_id: str) -> Optional[CollectionProvider]:
        for provider in self._providers:
            if provider.id == provider_id:
                return provider
        return None