"""FFmpeg detection / resolution tests."""

from pathlib import Path

import pytest

from app.core.errors import FFmpegNotFoundError
from app.core.models import Platform
from app.download.ffmpeg import (
    ffmpeg_status,
    find_ffmpeg,
    require_ffmpeg,
    verify_ffmpeg,
)
from app.providers.common.yt_adapter import (
    StreamDownloader,
    _map_format_selector,
    _merge_ext,
    _translate_download_error,
    resolve_ffmpeg,
)


class TestResolveFfmpeg:
    def test_empty_when_nothing_found(self, monkeypatch):
        monkeypatch.setattr("app.providers.common.yt_adapter.shutil.which", lambda _: None)
        # Ensure no bundled copies anywhere near the project root.
        project = Path(__file__).resolve().parent.parent
        monkeypatch.setattr(
            "app.providers.common.yt_adapter._runtime_dir", lambda: project / "nonexistent"
        )
        assert resolve_ffmpeg("") == ""

    def test_configured_file(self, tmp_path):
        fake = tmp_path / "ffmpeg.exe"
        fake.write_bytes(b"x")
        assert resolve_ffmpeg(str(fake)) == str(fake)

    def test_configured_without_extension(self, tmp_path):
        fake = tmp_path / "ffmpeg.exe"
        fake.write_bytes(b"x")
        assert resolve_ffmpeg(str(tmp_path / "ffmpeg")) == str(fake)

    def test_bundled_location(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bin"
        bundle_dir.mkdir()
        (bundle_dir / "ffmpeg.exe").write_bytes(b"x")
        monkeypatch.setattr("app.providers.common.yt_adapter.shutil.which", lambda _: None)
        monkeypatch.setattr("app.providers.common.yt_adapter._runtime_dir", lambda: bundle_dir)
        assert resolve_ffmpeg("") == str(bundle_dir / "ffmpeg.exe")


class TestVerify:
    def test_not_found(self, monkeypatch):
        monkeypatch.setattr("app.download.ffmpeg._resolve", lambda _: "")
        ok, msg = verify_ffmpeg()
        assert ok is False
        assert "not found" in msg

    def test_ffmpeg_status_not_found(self, monkeypatch):
        monkeypatch.setattr("app.download.ffmpeg._resolve", lambda _: "")
        ok, path, version = ffmpeg_status()
        assert ok is False and path == "" and version == ""

    def test_require_raises_when_missing(self, monkeypatch):
        monkeypatch.setattr("app.download.ffmpeg._resolve", lambda _: "")
        with pytest.raises(FFmpegNotFoundError) as exc_info:
            require_ffmpeg()
        assert "FFmpeg" in exc_info.value.message

    def test_require_returns_path(self, tmp_path, monkeypatch):
        fake = tmp_path / "ffmpeg.exe"
        fake.write_bytes(b"x")
        monkeypatch.setattr("app.download.ffmpeg._resolve", lambda _: str(fake))
        assert require_ffmpeg() == str(fake)


class TestStreamDownloaderPreflight:
    def _downloader(self, quality="1080p", output_format="MP4", ffmpeg_path=""):
        return StreamDownloader(
            url="https://example.com/watch?v=x",
            output_dir="C:/tmp",
            filename="out.mp4",
            output_format=output_format,
            quality=quality,
            embed_metadata=False,
            ffmpeg_path=ffmpeg_path,
        )

    def test_merge_requires_ffmpeg_raises(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter.resolve_ffmpeg", lambda *_: ""
        )
        with pytest.raises(FFmpegNotFoundError) as exc_info:
            self._downloader().download()
        assert "FFmpeg" in exc_info.value.message
        assert "1080p" in exc_info.value.detail

    def test_audio_extraction_requires_ffmpeg(self, monkeypatch):
        monkeypatch.setattr(
            "app.providers.common.yt_adapter.resolve_ffmpeg", lambda *_: ""
        )
        with pytest.raises(FFmpegNotFoundError):
            self._downloader(quality="Audio Only", output_format="MP3").download()


class TestFormatSelector:
    def test_best_available(self):
        assert _map_format_selector("Best Available", "MP4") == "bestvideo+bestaudio/best"

    def test_audio_only(self):
        assert _map_format_selector("Audio Only", "MP4") == "bestaudio/best"

    def test_audio_format(self):
        assert _map_format_selector("Best Available", "MP3") == "bestaudio/best"

    def test_1080p(self):
        selector = _map_format_selector("1080p", "MP4")
        assert "bestvideo[height<=1080]+bestaudio" in selector
        assert "best[height<=1080]" in selector

    def test_360p(self):
        selector = _map_format_selector("360p", "MP4")
        assert "bestvideo[height<=360]+bestaudio" in selector

    def test_webm_keeps_video_merge(self):
        selector = _map_format_selector("720p", "WebM")
        assert "bestvideo[height<=720]+bestaudio" in selector

    def test_merge_ext(self):
        assert _merge_ext("mp4") == "mp4"
        assert _merge_ext("mkv") == "mkv"
        assert _merge_ext("mp3") == "mp4"


class TestBuildItemUrl:
    def test_flat_entry_uses_url_field(self):
        from app.providers.common.yt_adapter import build_item_from_info

        info = {
            "id": "abc123def45",
            "title": "Flat Entry",
            "url": "https://www.youtube.com/watch?v=abc123def45",
        }
        item = build_item_from_info(info, Platform.YOUTUBE)
        assert item.url == "https://www.youtube.com/watch?v=abc123def45"

    def test_no_url_falls_back_to_watch_url(self):
        from app.providers.common.yt_adapter import build_item_from_info

        info = {"id": "abc123def45", "title": "No URL"}
        item = build_item_from_info(info, Platform.YOUTUBE)
        assert item.url == "https://www.youtube.com/watch?v=abc123def45"

    def test_prefers_page_url_over_direct_media_url(self):
        from app.providers.common.yt_adapter import build_item_from_info

        # Some extractors (e.g. TikTok) expose a direct, expiring CDN media
        # URL under `url`. Feeding that back to yt-dlp makes it use the generic
        # extractor which is rejected with HTTP 403, so the page URL must win.
        info = {
            "id": "x",
            "title": "X",
            "url": "https://cdn.example/stream",
            "webpage_url": "https://www.youtube.com/watch?v=x",
        }
        item = build_item_from_info(info, Platform.YOUTUBE)
        assert item.url == "https://www.youtube.com/watch?v=x"

    def test_original_url_fallback_when_no_webpage_url(self):
        from app.providers.common.yt_adapter import build_item_from_info

        info = {
            "id": "x",
            "title": "X",
            "url": "https://cdn.example/stream",
            "original_url": "https://www.tiktok.com/@user/video/x",
        }
        item = build_item_from_info(info, Platform.TIKTOK)
        assert item.url == "https://www.tiktok.com/@user/video/x"

    def test_direct_url_last_resort(self):
        from app.providers.common.yt_adapter import build_item_from_info

        info = {"id": "x", "title": "X", "url": "https://cdn.example/stream"}
        item = build_item_from_info(info, Platform.YOUTUBE)
        assert item.url == "https://cdn.example/stream"


class TestTranslate:
    def test_ffmpeg_missing(self):
        exc = _translate_download_error(
            RuntimeError(
                "ERROR: You have requested merging of multiple formats but ffmpeg is not installed"
            ),
            stage="Downloading",
        )
        assert isinstance(exc, FFmpegNotFoundError)
        assert exc.stage == "Downloading"

    def test_stage_preserved(self):
        exc = _translate_download_error(
            RuntimeError("HTTP Error 404: Video unavailable"), stage="Extracting media"
        )
        assert exc.stage == "Extracting media"
