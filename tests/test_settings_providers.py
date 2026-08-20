"""Settings persistence and provider interface / registry tests."""

import tempfile
from pathlib import Path

import pytest

from app.config.settings import AppSettings, SettingsManager
from app.core.errors import MetadataError, UnsupportedPlatformError
from app.core.models import ContentType, MediaBundle, MediaItem, Platform
from app.providers.base import BaseProvider, DownloadOptions
from app.providers.registry import ProviderRegistry

from tests.mocks.providers import MockProvider


class TestSettingsManager:
    def _manager(self):
        return SettingsManager(Path(tempfile.mkdtemp()) / "config.json")

    def test_defaults(self):
        m = self._manager()
        s = m.settings
        assert s.general.concurrent_downloads == 3
        assert s.video.default_quality == "Best Available"
        assert s.appearance.theme == "dark"

    def test_update_and_persist(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        m = SettingsManager(path)
        m.update(general={"concurrent_downloads": 5, "download_folder": "D:/dl"})
        m.update(appearance={"theme": "light"})
        m2 = SettingsManager(path)
        assert m2.settings.general.concurrent_downloads == 5
        assert m2.settings.general.download_folder == "D:/dl"
        assert m2.settings.appearance.theme == "light"

    def test_corrupt_file_falls_back_to_defaults(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        path.write_text("{ not valid json ", encoding="utf-8")
        m = SettingsManager(path)
        assert m.settings.general.concurrent_downloads == 3

    def test_round_trip_dict(self):
        s = AppSettings()
        s.general.filename_template = "{index} - {title}"
        s.video.default_format = "MKV"
        restored = AppSettings.from_dict(s.to_dict())
        assert restored.general.filename_template == "{index} - {title}"
        assert restored.video.default_format == "MKV"


class TestProviderRegistry:
    def test_defaults_registered(self):
        reg = ProviderRegistry()
        ids = {p.id for p in reg.all()}
        assert {"youtube", "tiktok", "facebook"} <= ids

    def test_detect_youtube(self):
        reg = ProviderRegistry()
        provider = reg.detect("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert provider is not None and provider.id == "youtube"

    def test_detect_tiktok(self):
        reg = ProviderRegistry()
        provider = reg.detect("https://www.tiktok.com/@u/video/1234567890123456789")
        assert provider is not None and provider.id == "tiktok"

    def test_detect_facebook(self):
        reg = ProviderRegistry()
        provider = reg.detect("https://www.facebook.com/videos/12345")
        assert provider is not None and provider.id == "facebook"

    def test_unsupported(self):
        reg = ProviderRegistry()
        with pytest.raises(UnsupportedPlatformError):
            reg.require("https://example.com/somewhere")

    def test_register_custom(self):
        reg = ProviderRegistry()
        reg.register(MockProvider())
        assert reg.get("mock") is not None


class TestProviderInterface:
    def test_interface_surface(self):
        for method in (
            "detect_url",
            "get_metadata",
            "get_playlist_items",
            "get_album_items",
            "get_available_formats",
            "download",
        ):
            assert callable(getattr(BaseProvider, method)), method

    def test_mock_provider_contract(self):
        p = MockProvider()
        assert p.detect_url("https://mock.example/x") is True
        bundle = p.get_metadata("https://mock.example/watch?v=1")
        assert isinstance(bundle, MediaBundle)
        assert bundle.item is not None
        items = p.get_playlist_items("https://mock.example/playlist")
        assert isinstance(items, list) and len(items) == 3
        assert len(p.get_available_formats(bundle.item)) == 2

    def test_metadata_failure_raises_app_error(self):
        p = MockProvider(fail_metadata=True)
        with pytest.raises(MetadataError):
            p.get_metadata("https://mock.example/watch?v=1")

    def test_bundle_is_collection(self):
        p = MockProvider()
        bundle = p.get_metadata("https://mock.example/playlist")
        assert bundle.is_collection is True
        assert bundle.content_type == ContentType.PLAYLIST
        assert bundle.count == 3

    def test_single_bundle(self):
        p = MockProvider()
        bundle = p.get_metadata("https://mock.example/watch?v=1")
        assert bundle.is_collection is False
        assert bundle.count == 1


class TestErrorTranslation:
    def test_translate_401(self):
        exc = MockProvider.translate_error(ValueError("HTTP Error 401: Unauthorized"))
        assert "auth" in str(type(exc)).lower()

    def test_translate_403_permission(self):
        exc = MockProvider.translate_error(ValueError("HTTP Error 403: private video"))
        assert "permission" in str(type(exc)).lower() or "permission" in exc.message.lower()

    def test_translate_rate_limit(self):
        exc = MockProvider.translate_error(ValueError("HTTP Error 429: Too Many Requests"))
        assert "rate" in str(type(exc)).lower()

    def test_translate_not_found(self):
        exc = MockProvider.translate_error(ValueError("HTTP Error 404: removed"))
        assert "unavailable" in str(type(exc)).lower() or "unavailable" in exc.message.lower()
