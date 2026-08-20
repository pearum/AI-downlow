"""Mock collection provider — offline, deterministic, paged.

Exposes an account-style collection (and optionally series names) so the
engine and GUI can be tested without any network access.
"""

from __future__ import annotations

from typing import Any, Optional

from app.collections.base import (
    COLLECTION_PAGE_SIZE,
    CollectionInfo,
    CollectionItem,
    CollectionItemStatus,
    CollectionMode,
    CollectionProvider,
)
from app.core.models import ContentType, Platform


class MockCollectionProvider(CollectionProvider):
    id = "mock"
    display_name = "Mock Platform"
    platform = Platform.TIKTOK

    def __init__(
        self,
        total: int = 5,
        series: Optional[str] = None,
        page_size: Optional[int] = None,
        fail_scan: bool = False,
    ) -> None:
        self.total = total
        self.series_name = series
        self.page_size = page_size or COLLECTION_PAGE_SIZE
        self.fail_scan = fail_scan
        self._entries: dict[str, list[CollectionItem]] = {}
        self._page_index: dict[str, int] = {}
        self.scan_calls = 0

    def supports_collection(self, url: str) -> bool:
        return "mock.example" in url

    def analyze_collection(self, url: str) -> CollectionInfo:
        self.scan_calls += 1
        if self.fail_scan:
            from app.core.errors import MetadataError

            raise MetadataError("Mock scan failure")
        items = self._make_items(url)
        self._entries[url] = items
        self._page_index[url] = 0
        return self._build_info(url, 0)

    def load_more(self, info: CollectionInfo) -> CollectionInfo:
        url = info.url
        idx = self._page_index.get(url, 0)
        self._page_index[url] = idx + 1
        return self._build_info(url, idx + 1)

    def _make_items(self, url: str) -> list[CollectionItem]:
        account = "@mockuser"
        items: list[CollectionItem] = []
        for i in range(1, self.total + 1):
            items.append(
                CollectionItem(
                    item_id=f"v{i:04d}",
                    url=f"https://mock.example/watch?v=v{i:04d}",
                    title=(
                        f"Episode {i:02d}"
                        if self.series_name
                        else f"Video {i:03d}"
                    ),
                    platform=self.platform,
                    index=i,
                    account_username=account,
                    account_display_name="Mock User",
                    series_name=self.series_name,
                    duration=60.0 + i,
                    extra={"collection_url": url},
                )
            )
        return items

    def _build_info(self, url: str, page: int) -> CollectionInfo:
        entries = self._entries.get(url, [])
        start = page * self.page_size
        slice_ = entries[start : start + self.page_size]
        total = len(entries)
        return CollectionInfo(
            url=url,
            platform=self.platform,
            collection_type=(
                ContentType.COLLECTION if self.series_name else ContentType.PROFILE
            ),
            mode=(
                CollectionMode.SERIES
                if self.series_name
                else CollectionMode.ACCOUNT
            ),
            name=url,
            account_username="@mockuser",
            account_display_name="Mock User",
            total_items=total,
            accessible_items=total,
            items=slice_,
            has_more=start + self.page_size < total,
            next_cursor=str(page + 1) if start + self.page_size < total else None,
            series=[self.series_name] if self.series_name else [],
        )