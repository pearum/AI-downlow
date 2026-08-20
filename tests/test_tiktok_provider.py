"""TikTok provider tests — detection, normalization, resolution, errors.

These tests are fully offline. URL detection/normalization are pure string
logic and resolution/error paths use mocked transports instead of touching the
real TikTok servers.
"""

import pytest

from app.core.errors import (
    ContentUnavailableError,
    MetadataError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.models import MediaFormat, MediaItem, Platform
from app.providers.base import DownloadOptions
from app.providers.tiktok import TikTokProvider, _validate_path


class _FakeResponse:
    def __init__(self, url: str, status_code: int = 200) -> None:
        self.url = url
        self.status_code = status_code


class TestDetectUrl:
    def test_short_url(self):
        assert TikTokProvider().detect_url("https://vt.tiktok.com/ZSrVrunN/") is True

    def test_short_url_vm(self):
        assert TikTokProvider().detect_url("https://vm.tiktok.com/ZMabc") is True

    def test_standard_video(self):
        assert (
            TikTokProvider().detect_url(
                "https://www.tiktok.com/@user/video/1234567890123456789"
            )
            is True
        )

    def test_standard_video_query_params(self):
        assert (
            TikTokProvider().detect_url(
                "https://www.tiktok.com/@user/video/1234567890123456789"
                "?lang=en&is_copy_url=1"
            )
            is True
        )

    def test_standard_video_trailing_slash(self):
        assert (
            TikTokProvider().detect_url(
                "https://www.tiktok.com/@user/video/1234567890123456789/"
            )
            is True
        )

    def test_invalid_url(self):
        assert TikTokProvider().detect_url("not a url") is False

    def test_non_tiktok_url(self):
        assert (
            TikTokProvider().detect_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            is False
        )

    def test_empty_url(self):
        assert TikTokProvider().detect_url("") is False


class TestNormalizeUrl:
    def test_strips_whitespace(self):
        assert (
            TikTokProvider.normalize_url("  https://vt.tiktok.com/ZSrVrunN/  ")
            == "https://vt.tiktok.com/ZSrVrunN/"
        )

    def test_adds_scheme(self):
        assert (
            TikTokProvider.normalize_url("www.tiktok.com/@user/video/1234567890123456789")
            == "https://www.tiktok.com/@user/video/1234567890123456789"
        )

    def test_keeps_existing_scheme(self):
        assert (
            TikTokProvider.normalize_url("https://www.tiktok.com/@user/video/123")
            == "https://www.tiktok.com/@user/video/123"
        )


class TestIsShareUrl:
    def test_vt(self):
        assert TikTokProvider.is_share_url("https://vt.tiktok.com/ZSrVrunN/") is True

    def test_vm(self):
        assert TikTokProvider.is_share_url("https://vm.tiktok.com/ZMabc") is True

    def test_standard_not_share(self):
        assert (
            TikTokProvider.is_share_url(
                "https://www.tiktok.com/@user/video/1234567890123456789"
            )
            is False
        )


class TestResolveShareUrl:
    def test_resolves_to_video(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://www.tiktok.com/@user/video/1234567890123456789")

        monkeypatch.setattr("httpx.get", fake_get)
        provider = TikTokProvider()
        resolved = provider.resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")
        assert resolved == "https://www.tiktok.com/@user/video/1234567890123456789"

    def test_homepage_fallback_raises(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://www.tiktok.com/?_r=1")

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(PermissionDeniedError) as excinfo:
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")
        assert "currently unavailable" in excinfo.value.message
        assert "HTTP status" in (excinfo.value.detail or "")

    def test_http_403_raises(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://vt.tiktok.com/ZSrVrunN/", status_code=403)

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(PermissionDeniedError) as excinfo:
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")
        assert "403" in excinfo.value.detail

    def test_http_404_raises(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://vt.tiktok.com/ZSrVrunN/", status_code=404)

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(ContentUnavailableError):
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")

    def test_http_429_raises(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://vt.tiktok.com/ZSrVrunN/", status_code=429)

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(RateLimitError):
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")

    def test_dns_error(self, monkeypatch):
        import httpx

        def fake_get(*args, **kwargs):
            raise httpx.ConnectError("getaddrinfo failed")

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(NetworkError) as excinfo:
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")
        assert "DNS" in excinfo.value.detail

    def test_timeout_error(self, monkeypatch):
        import httpx

        def fake_get(*args, **kwargs):
            raise httpx.ReadTimeout("Read timed out")

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(NetworkError) as excinfo:
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")
        assert "did not respond" in excinfo.value.message

    def test_non_tiktok_destination_raises(self, monkeypatch):
        def fake_get(*args, **kwargs):
            return _FakeResponse("https://example.com/not-tiktok")

        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(MetadataError):
            TikTokProvider().resolve_share_url("https://vt.tiktok.com/ZSrVrunN/")


class TestGetMetadata:
    def test_standard_url_builds_bundle(self, monkeypatch):
        def fake_extract_info(url, platform):
            return {
                "id": "1234567890123456789",
                "title": "A TikTok Video",
                "uploader": "creator",
                "duration": 12.5,
                "thumbnail": "https://p16-sign.tiktokcdn.com/t.jpg",
                "formats": [
                    {
                        "format_id": "0",
                        "height": 1080,
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "aac",
                        "format_note": "720p",
                    },
                    {
                        "format_id": "audio",
                        "vcodec": "none",
                        "acodec": "aac",
                        "abr": 128,
                        "ext": "m4a",
                    },
                ],
            }

        monkeypatch.setattr("app.providers.tiktok.extract_info", fake_extract_info)
        bundle = TikTokProvider().get_metadata(
            "https://www.tiktok.com/@user/video/1234567890123456789"
        )
        assert bundle.item is not None
        assert bundle.item.title == "A TikTok Video"
        assert bundle.item.creator == "creator"
        assert bundle.item.duration == 12.5
        labels = [f.label for f in bundle.item.available_formats]
        assert "1080p" in labels
        assert any(f.is_audio_only for f in bundle.item.available_formats)

    def test_short_url_resolved_before_extraction(self, monkeypatch):
        seen = []

        def fake_extract_info(url, platform):
            seen.append(url)
            return {"id": "1", "title": "Vid", "uploader": "u"}

        def fake_get(*args, **kwargs):
            return _FakeResponse("https://www.tiktok.com/@user/video/1234567890123456789")

        monkeypatch.setattr("app.providers.tiktok.extract_info", fake_extract_info)
        monkeypatch.setattr("httpx.get", fake_get)
        TikTokProvider().get_metadata("https://vt.tiktok.com/ZSrVrunN/")
        assert seen == ["https://www.tiktok.com/@user/video/1234567890123456789"]

    def test_short_url_blocked_raises_before_extraction(self, monkeypatch):
        def fake_extract_info(url, platform):  # pragma: no cover
            raise AssertionError("extract_info must not be called")

        def fake_get(*args, **kwargs):
            return _FakeResponse("https://www.tiktok.com/?_r=1")

        monkeypatch.setattr("app.providers.tiktok.extract_info", fake_extract_info)
        monkeypatch.setattr("httpx.get", fake_get)
        with pytest.raises(PermissionDeniedError):
            TikTokProvider().get_metadata("https://vt.tiktok.com/ZSrVrunN/")

    def test_blocked_extraction_error_maps_to_permission(self, monkeypatch):
        def fake_extract_info(url, platform):
            raise RuntimeError(
                "ERROR: Your IP address is blocked from accessing this post"
            )

        monkeypatch.setattr("app.providers.tiktok.extract_info", fake_extract_info)
        with pytest.raises(PermissionDeniedError) as excinfo:
            TikTokProvider().get_metadata(
                "https://www.tiktok.com/@user/video/1234567890123456789"
            )
        assert "currently unavailable" in excinfo.value.message

    def test_already_translated_network_error_with_block_signal(self):
        from app.core.errors import NetworkError

        exc = NetworkError(
            detail="ERROR: [TikTok] 7440861180777844006: Your IP address "
            "is blocked from accessing this post"
        )
        translated = TikTokProvider.translate_error(exc)
        assert isinstance(translated, PermissionDeniedError)
        assert "currently unavailable" in translated.message
        assert "blocked" in translated.detail


class TestGetAvailableFormats:
    def test_returns_item_formats(self):
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
            available_formats=[
                MediaFormat(format_id="0", label="1080p", height=1080, ext="mp4"),
                MediaFormat(format_id="audio", label="Audio", is_audio_only=True),
            ],
        )
        formats = TikTokProvider().get_available_formats(item)
        assert [f.label for f in formats] == ["1080p", "Audio"]


class TestTranslateError:
    def test_app_error_passthrough(self):
        err = PermissionDeniedError(detail="already mapped")
        assert TikTokProvider.translate_error(err) is err

    def test_blocked_signal(self):
        exc = TikTokProvider.translate_error(
            ValueError("ERROR: [TikTok] Your IP address is blocked from accessing this post")
        )
        assert isinstance(exc, PermissionDeniedError)
        assert "currently unavailable" in exc.message

    def test_forbidden(self):
        exc = TikTokProvider.translate_error(ValueError("HTTP Error 403: Forbidden"))
        assert isinstance(exc, PermissionDeniedError)

    def test_not_found(self):
        exc = TikTokProvider.translate_error(ValueError("HTTP Error 404: removed"))
        assert isinstance(exc, ContentUnavailableError)

    def test_rate_limit(self):
        exc = TikTokProvider.translate_error(ValueError("HTTP Error 429: Too Many Requests"))
        assert isinstance(exc, RateLimitError)

    def test_network(self):
        exc = TikTokProvider.translate_error(ValueError("Connection timed out"))
        assert isinstance(exc, NetworkError)


class TestDownloadUrlSelection:
    """The download must re-extract from the page URL, never the CDN URL."""

    def _tiktok_item(self) -> MediaItem:
        from app.providers.common.yt_adapter import build_item_from_info

        info = {
            "id": "1234567890123456789",
            "title": "A TikTok Video",
            "url": (
                "https://v19-webapp-prime.tiktok.com/video/tos/alisg/abcdef/"
                "?a=1988&mime_type=video_mp4&signature=abc&expire=9999999999"
            ),
            "webpage_url": "https://www.tiktok.com/@user/video/1234567890123456789",
            "original_url": "https://www.tiktok.com/@user/video/1234567890123456789",
            "formats": [{"format_id": "0", "height": 1080, "ext": "mp4"}],
        }
        return build_item_from_info(info, Platform.TIKTOK)

    def test_page_url_wins_over_cdn_url(self):
        item = self._tiktok_item()
        assert item.url == "https://www.tiktok.com/@user/video/1234567890123456789"
        assert "tiktokcdn" not in item.url

    def test_download_uses_page_url(self, monkeypatch, tmp_path):
        seen = {}

        class _FakeDownloader:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def set_progress_callback(self, cb):
                seen["progress_cb"] = cb

            def download(self):
                target = tmp_path / "out.mp4"
                target.write_bytes(b"real-video-data")
                return str(target)

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _FakeDownloader)
        item = self._tiktok_item()
        options = DownloadOptions(
            url=item.url,
            quality="Best Available",
            output_format="MP4",
            output_dir=str(tmp_path),
            filename="out.mp4",
            embed_metadata=True,
            skip_existing=False,
            extra={"ffmpeg_path": ""},
        )
        path = TikTokProvider().download(item, options)
        assert path == str(tmp_path / "out.mp4")
        assert seen["url"] == "https://www.tiktok.com/@user/video/1234567890123456789"
        assert seen["output_dir"] == str(tmp_path)
        assert seen["filename"] == "out.mp4"
        assert seen["quality"] == "Best Available"
        assert seen["output_format"] == "MP4"


class TestDownloadOutputValidation:
    def test_valid_output_returns_path(self, monkeypatch, tmp_path):
        class _FakeDownloader:
            def __init__(self, **kwargs):
                pass

            def set_progress_callback(self, cb):
                pass

            def download(self):
                target = tmp_path / "out.mp4"
                target.write_bytes(b"video-data" * 100)
                return str(target)

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _FakeDownloader)
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
        )
        options = DownloadOptions(
            url=item.url,
            output_dir=str(tmp_path),
            filename="out.mp4",
            extra={"ffmpeg_path": ""},
        )
        path = TikTokProvider().download(item, options)
        assert _validate_path(path) == (True, len(b"video-data" * 100))

    def test_zero_byte_output_detected(self, monkeypatch, tmp_path):
        class _FakeDownloader:
            def __init__(self, **kwargs):
                pass

            def set_progress_callback(self, cb):
                pass

            def download(self):
                (tmp_path / "out.mp4").write_bytes(b"")
                return str(tmp_path / "out.mp4")

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _FakeDownloader)
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
        )
        options = DownloadOptions(
            url=item.url,
            output_dir=str(tmp_path),
            filename="out.mp4",
            extra={"ffmpeg_path": ""},
        )
        path = TikTokProvider().download(item, options)
        ok, size = _validate_path(path)
        assert ok is False
        assert size == 0

    def test_missing_output_detected(self, monkeypatch, tmp_path):
        class _FakeDownloader:
            def __init__(self, **kwargs):
                pass

            def set_progress_callback(self, cb):
                pass

            def download(self):
                return str(tmp_path / "never-created.mp4")

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _FakeDownloader)
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
        )
        options = DownloadOptions(
            url=item.url,
            output_dir=str(tmp_path),
            filename="out.mp4",
            extra={"ffmpeg_path": ""},
        )
        path = TikTokProvider().download(item, options)
        assert _validate_path(path) == (False, 0)


class TestDownloadFailureHandling:
    def test_generic_error_maps_to_permission(self, monkeypatch, tmp_path):
        def _raise(**kwargs):
            raise RuntimeError("HTTP Error 403: Forbidden")

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _raise)
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
        )
        options = DownloadOptions(
            url=item.url,
            output_dir=str(tmp_path),
            filename="out.mp4",
            extra={"ffmpeg_path": ""},
        )
        with pytest.raises(PermissionDeniedError) as excinfo:
            TikTokProvider().download(item, options)
        assert "currently unavailable" in excinfo.value.message

    def test_download_error_preserves_stage(self, monkeypatch, tmp_path):
        def _raise(**kwargs):
            raise NetworkError(
                "Connection timed out",
                detail="ERROR: [generic] timeout",
                stage="Downloading",
            )

        monkeypatch.setattr("app.providers.tiktok.StreamDownloader", _raise)
        item = MediaItem(
            item_id="1",
            title="t",
            url="https://www.tiktok.com/@u/video/1",
            platform=Platform.TIKTOK,
        )
        options = DownloadOptions(
            url=item.url,
            output_dir=str(tmp_path),
            filename="out.mp4",
            extra={"ffmpeg_path": ""},
        )
        with pytest.raises(NetworkError) as excinfo:
            TikTokProvider().download(item, options)
        assert excinfo.value.stage == "Downloading"
        assert "timed out" in excinfo.value.message
