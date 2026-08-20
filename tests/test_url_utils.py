"""URL detection and platform detection tests."""

import pytest

from app.core.models import Platform
from app.providers.common.url_utils import (
    detect_platform,
    ensure_scheme,
    extract_video_id,
    is_valid_url,
)


class TestEnsureScheme:
    def test_adds_https(self):
        assert ensure_scheme("youtube.com/watch?v=abc") == "https://youtube.com/watch?v=abc"

    def test_keeps_existing(self):
        assert ensure_scheme("http://youtu.be/abc") == "http://youtu.be/abc"

    def test_strips_whitespace(self):
        assert ensure_scheme("  tiktok.com/@user  ") == "https://tiktok.com/@user"


class TestIsValidUrl:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://youtube.com/watch?v=abc", True),
            ("youtube.com/watch?v=abc", True),
            ("https://facebook.com", True),
            ("", False),
            ("not a url", False),  # whitespace is invalid
            ("ht tp://x", False),
        ],
    )
    def test_validity(self, url, expected):
        assert is_valid_url(url) is expected


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", Platform.YOUTUBE),
            ("https://youtu.be/dQw4w9WgXcQ", Platform.YOUTUBE),
            ("https://music.youtube.com/watch?v=abc", Platform.YOUTUBE),
            ("https://www.tiktok.com/@user/video/1234567890123456789", Platform.TIKTOK),
            ("https://vm.tiktok.com/ZMabc", Platform.TIKTOK),
            ("https://vt.tiktok.com/ZSrVrunN/", Platform.TIKTOK),
            ("https://www.tiktok.com/@user/video/1234567890123456789?lang=en", Platform.TIKTOK),
            ("https://www.tiktok.com/@user/video/1234567890123456789/", Platform.TIKTOK),
            ("https://www.facebook.com/videos/1234567890", Platform.FACEBOOK),
            ("https://fb.watch/abc123", Platform.FACEBOOK),
            ("https://example.com/x", Platform.UNKNOWN),
            ("", Platform.UNKNOWN),
        ],
    )
    def test_detection(self, url, expected):
        assert detect_platform(url) is expected


class TestExtractVideoId:
    def test_youtube_standard(self):
        assert extract_video_id(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", Platform.YOUTUBE
        ) == "dQw4w9WgXcQ"

    def test_youtube_short(self):
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ", Platform.YOUTUBE) == "dQw4w9WgXcQ"

    def test_youtube_embed(self):
        assert extract_video_id(
            "https://www.youtube.com/embed/dQw4w9WgXcQ", Platform.YOUTUBE
        ) == "dQw4w9WgXcQ"

    def test_tiktok(self):
        assert extract_video_id(
            "https://www.tiktok.com/@user/video/7345678901234567890", Platform.TIKTOK
        ) == "7345678901234567890"

    def test_facebook(self):
        assert extract_video_id(
            "https://www.facebook.com/user/videos/123456789", Platform.FACEBOOK
        ) == "123456789"

    def test_invalid_youtube_id_length(self):
        assert (
            extract_video_id("https://youtu.be/short", Platform.YOUTUBE) is None
        )
