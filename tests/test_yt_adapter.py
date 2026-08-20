"""Tests for the yt-dlp adapter: options building, JS runtime detection,
URL classification, playlist paging and error translation.

All tests are offline — the adapter's network boundary is never reached.
"""

from types import SimpleNamespace

import pytest

from app.core.errors import (
    AuthenticationError,
    ContentUnavailableError,
    DownloadBlockedError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)
from app.core.models import Platform
from app.providers.common.yt_adapter import (
    _build_yt_dlp_options,
    _is_playlist_url,
    _resolve_js_runtime,
    _translate_download_error,
)


class TestResolveJsRuntime:
    def test_none_when_no_runtime(self, monkeypatch):
        monkeypatch.delenv("YTDLP_JS_RUNTIME", raising=False)
        monkeypatch.setattr(
            "app.providers.common.yt_adapter.shutil.which", lambda _: None
        )
        assert _resolve_js_runtime() is None

    def test_prefers_configured_path(self, tmp_path, monkeypatch):
        exe = tmp_path / "deno.exe"
        exe.write_bytes(b"x")
        monkeypatch.setenv("YTDLP_JS_RUNTIME", str(exe))
        assert _resolve_js_runtime() == ("deno", str(exe))

    def test_configured_command_resolved_through_path(self, tmp_path, monkeypatch):
        exe = tmp_path / "deno.exe"
        exe.write_bytes(b"x")
        monkeypatch.setenv("YTDLP_JS_RUNTIME", "deno")
        monkeypatch.setattr(
            "app.providers.common.yt_adapter.shutil.which",
            lambda name: str(exe) if name == "deno" else None,
        )
        assert _resolve_js_runtime() == ("deno", str(exe))

    def test_configured_missing_ignored(self, monkeypatch):
        monkeypatch.setenv("YTDLP_JS_RUNTIME", "does-not-exist-xyz")
        monkeypatch.setattr(
            "app.providers.common.yt_adapter.shutil.which", lambda _: None
        )
        assert _resolve_js_runtime() is None

    def test_env_overrides_deno(self, tmp_path, monkeypatch):
        node = tmp_path / "node.exe"
        node.write_bytes(b"x")
        monkeypatch.setenv("YTDLP_JS_RUNTIME", str(node))
        assert _resolve_js_runtime() == ("node", str(node))


class TestBuildOptions:
    def test_always_sets_ejs_component(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._resolve_js_runtime", lambda: None
        )
        opts = _build_yt_dlp_options("youtube")
        assert opts["remote_components"] == {"ejs:github"}
        assert "js_runtimes" not in opts

    def test_no_runtime_means_no_js_runtimes(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._resolve_js_runtime", lambda: None
        )
        opts = _build_yt_dlp_options("tiktok", download=True)
        assert "js_runtimes" not in opts

    def test_runtime_populates_js_runtimes(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._resolve_js_runtime",
            lambda: ("deno", "C:/deno/deno.exe"),
        )
        opts = _build_yt_dlp_options("youtube")
        assert opts["js_runtimes"] == {"deno": {"path": "C:/deno/deno.exe"}}

    def test_skip_download_flag(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._resolve_js_runtime", lambda: None
        )
        assert _build_yt_dlp_options("youtube")["skip_download"] is True
        assert (
            _build_yt_dlp_options("youtube", download=True)["skip_download"] is False
        )

    def test_noplaylist_flag(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._resolve_js_runtime", lambda: None
        )
        assert _build_yt_dlp_options("youtube")["noplaylist"] is True
        assert _build_yt_dlp_options("youtube", playlist=True)["noplaylist"] is False


class TestPlaylistUrlDetection:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/abc123",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
        ],
    )
    def test_youtube_single(self, url):
        assert _is_playlist_url(url, Platform.YOUTUBE) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@channel/videos",
            "https://www.youtube.com/channel/UCabc123",
        ],
    )
    def test_youtube_collection(self, url):
        assert _is_playlist_url(url, Platform.YOUTUBE) is True

    def test_tiktok_single_short_link(self):
        assert (
            _is_playlist_url("https://vm.tiktok.com/ZM1234/", Platform.TIKTOK) is False
        )

    def test_tiktok_video(self):
        assert (
            _is_playlist_url(
                "https://www.tiktok.com/@user/video/7123456789012345678",
                Platform.TIKTOK,
            )
            is False
        )

    def test_tiktok_account(self):
        assert _is_playlist_url("https://www.tiktok.com/@user", Platform.TIKTOK) is True

    def test_tiktok_collection(self):
        assert (
            _is_playlist_url(
                "https://www.tiktok.com/@user/collection/1234567890123456789",
                Platform.TIKTOK,
            )
            is True
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/watch?v=123456789",
            "https://www.facebook.com/username/videos/123456789",
            "https://www.facebook.com/reel/123456789",
            "https://www.facebook.com/photo.php?fbid=123",
        ],
    )
    def test_facebook_single(self, url):
        assert _is_playlist_url(url, Platform.FACEBOOK) is False

    def test_facebook_page(self):
        assert _is_playlist_url("https://www.facebook.com/NatGeo", Platform.FACEBOOK) is True


class TestTranslateError:
    def test_auth_401(self):
        exc = _translate_download_error(RuntimeError("HTTP Error 401: unauthorized"))
        assert isinstance(exc, AuthenticationError)

    def test_auth_login(self):
        exc = _translate_download_error(RuntimeError("Sign in to confirm you're not a bot"))
        assert isinstance(exc, PermissionDeniedError)

    def test_forbidden_403(self):
        exc = _translate_download_error(RuntimeError("HTTP Error 403: Forbidden"))
        assert isinstance(exc, DownloadBlockedError)

    def test_rate_limited(self):
        exc = _translate_download_error(RuntimeError("HTTP Error 429: too many requests"))
        assert isinstance(exc, RateLimitError)

    def test_removed_video(self):
        exc = _translate_download_error(
            RuntimeError("This video has been removed for violating terms")
        )
        assert isinstance(exc, ContentUnavailableError)

    def test_generic_network(self):
        exc = _translate_download_error(RuntimeError("Connection reset"), stage="Downloading")
        assert isinstance(exc, NetworkError)
        assert exc.stage == "Downloading"