"""Provider registry: platform detection and dispatch."""

from __future__ import annotations

import logging

from ..core.errors import UnsupportedPlatformError
from ..core.models import Platform
from .base import BaseProvider
from .common.url_utils import detect_platform, is_valid_url
from .facebook import FacebookProvider
from .tiktok import TikTokProvider
from .youtube import YouTubeProvider

log = logging.getLogger(__name__)


class ProviderRegistry:
    """Maps platform ids to provider instances."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        for provider in (YouTubeProvider, TikTokProvider, FacebookProvider):
            instance = provider()
            self._providers[instance.id] = instance

    def register(self, provider: BaseProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> BaseProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[BaseProvider]:
        return list(self._providers.values())

    def detect(self, url: str) -> BaseProvider | None:
        """Find a provider that claims the given URL."""
        if not is_valid_url(url):
            raise UnsupportedPlatformError("The provided text is not a valid URL.")
        for provider in self._providers.values():
            if provider.detect_url(url):
                return provider
        return None

    def require(self, url: str) -> BaseProvider:
        provider = self.detect(url)
        if provider is None:
            raise UnsupportedPlatformError(
                "No provider supports this URL. Supported platforms: "
                "YouTube, TikTok, Facebook."
            )
        return provider
