"""Abstract provider interface.

Every platform ships its own provider class that implements this contract.
The download engine and GUI depend only on this interface, never on a
specific platform implementation, so new platforms can be added by dropping
in a new provider and registering it in the registry.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from ..core.errors import (
    AppError,
    AuthenticationError,
    ContentUnavailableError,
    MetadataError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    UnsupportedPlatformError,
    URLError,
)
from ..core.models import MediaBundle, MediaItem, Platform


@dataclass
class DownloadOptions:
    """Options passed to a provider's download method."""

    url: str
    quality: str = "Best Available"
    output_format: str = "MP4"
    output_dir: str = ""
    filename: str = ""  # pre-built safe filename (with ext)
    embed_metadata: bool = True
    skip_existing: bool = True
    extra: dict[str, Any] = None  # type: ignore[assignment]


class BaseProvider(abc.ABC):
    """Contract every platform provider must implement."""

    id: str = ""
    display_name: str = ""
    platform: Platform = Platform.UNKNOWN

    #: Whether this provider requires an authenticated account to work.
    requires_auth: bool = False

    @abc.abstractmethod
    def detect_url(self, url: str) -> bool:
        """Return True if this provider handles the given URL."""

    @abc.abstractmethod
    def get_metadata(self, url: str) -> MediaBundle:
        """Fetch metadata for a URL.

        Returns a MediaBundle that may contain a single MediaItem or a
        collection of items (playlist/album/channel/profile).
        """

    @abc.abstractmethod
    def get_playlist_items(self, url: str) -> list[MediaItem]:
        """Return the items of a playlist/list."""

    @abc.abstractmethod
    def get_album_items(self, url: str) -> list[MediaItem]:
        """Return the items of an album/collection."""

    @abc.abstractmethod
    def get_available_formats(self, item: MediaItem) -> list[Any]:
        """Return the downloadable formats for a single item."""

    @abc.abstractmethod
    def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        progress_callback: Any = None,
    ) -> str:
        """Download one item.

        progress_callback is an optional callable receiving a dict with
        keys: downloaded, total, speed, eta, status, filename.
        Returns the final file path.
        """

    def requires_account_for(self, url: str) -> bool:
        """Whether the given content needs an account on this provider."""
        return False

    # -- error translation helpers -------------------------------------
    @staticmethod
    def translate_error(exc: Exception, fallback: str = "") -> Exception:
        """Map raw low-level exceptions to user-facing AppError subclasses.

        Already-translated AppError instances pass through unchanged so their
        human message and technical detail are preserved.
        """
        if isinstance(exc, AppError):
            return exc
        message = str(exc).strip() or fallback or exc.__class__.__name__
        text = message.lower()
        if isinstance(exc, (URLError, UnsupportedPlatformError)):
            return exc
        if "http 401" in text or "authentication" in text or "unauthorized" in text:
            return AuthenticationError(detail=message)
        if "http 403" in text or "permission" in text or "private" in text or "logged in" in text:
            return PermissionDeniedError(detail=message)
        if "429" in text or "rate limit" in text or "too many requests" in text:
            return RateLimitError(detail=message)
        if "404" in text or "not found" in text or "removed" in text or "unavailable" in text:
            return ContentUnavailableError(detail=message)
        if "timeout" in text or "network" in text or "connection" in text or "http error" in text:
            return NetworkError(detail=message)
        return MetadataError(detail=message)
