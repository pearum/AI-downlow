"""Thin wrapper around yt-dlp used by the platform providers.

Kept inside the provider layer so a different extraction backend can be
substituted later without touching the GUI or download engine.

Compliance notes:
- We do NOT pass cookies, do NOT bypass DRM/CAPTCHA, do NOT authenticate with
  stolen sessions. Content requiring login surfaces a clear error and the
  user is pointed to official permissions (Accounts section).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...core.errors import (
    AppError,
    DownloadBlockedError,
    FFmpegNotFoundError,
    NetworkError,
    PermissionDeniedError,
    URLError,
)
from ...core.models import (
    ContentType,
    MediaBundle,
    MediaFormat,
    MediaItem,
    Platform,
    parse_upload_date,
)
from .env import API_KEYS

log = logging.getLogger(__name__)

_OPTS_CACHE: dict[str, Any] = {}


def _user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )


def _base_opts(provider: str) -> dict[str, Any]:
    key = f"{provider}:base"
    if key in _OPTS_CACHE:
        return _OPTS_CACHE[key]
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "http_headers": {"User-Agent": _user_agent()},
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": False,
        "cachedir": False,
        "logger": _YtLogger(),
        # Allow yt-dlp to fetch the EJS challenge solver so YouTube's
        # "n" challenge can be solved when a JS runtime (deno/node) exists.
        "remote_components": {"ejs:github"},
    }
    api_key = API_KEYS.get(provider)
    if api_key:
        opts["extractor_args"] = {provider: {"api_key": [api_key]}}
    _OPTS_CACHE[key] = opts
    return opts


class _YtLogger:
    def debug(self, msg):  # noqa: D102
        log.debug("yt-dlp: %s", msg)

    def info(self, msg):  # noqa: D102
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg):  # noqa: D102
        log.info("yt-dlp warn: %s", msg)

    def error(self, msg):  # noqa: D102
        log.warning("yt-dlp error: %s", msg)


def _resolve_download_backend():
    """Lazily import yt-dlp so tests can run without it installed."""
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The extraction library (yt-dlp) is not installed. "
            "Run: pip install -r requirements.txt"
        ) from exc
    return yt_dlp


def resolve_ffmpeg(configured_path: str = "") -> str:
    """Locate an ffmpeg binary.

    Search order:
      1. Explicitly configured path (Settings → Advanced → FFmpeg Path)
      2. Bundled location: <executable dir>/ffmpeg/ffmpeg.exe and
         <executable dir>/ffmpeg.exe (PyInstaller-friendly)
      3. The system PATH
    """
    candidates: list[Path] = []
    if configured_path:
        cfg = Path(configured_path)
        candidates.append(cfg)
        candidates.append(cfg.with_suffix(".exe"))
        candidates.append(Path(cfg).parent / "ffmpeg.exe")

    exe_dir = _runtime_dir()
    candidates.extend(
        [
            exe_dir / "ffmpeg" / "ffmpeg.exe",
            exe_dir / "ffmpeg.exe",
            exe_dir / "bin" / "ffmpeg.exe",
        ]
    )
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve() if candidate.exists() else candidate
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    found = shutil.which("ffmpeg")
    if found:
        return found
    return ""


def _runtime_dir() -> Path:
    """Directory containing the executable / bundle (works for PyInstaller)."""
    import sys

    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        base = Path(bundle) if bundle else Path(sys.executable).parent
        return base
    # Development: the project root (where app/ lives) plus the current dir.
    project = Path(__file__).resolve().parent.parent.parent.parent
    return project


def check_ffmpeg(configured_path: str = "") -> str:
    found = resolve_ffmpeg(configured_path)
    if not found:
        raise FFmpegNotFoundError()
    return found


def extract_info(url: str, platform: Platform) -> dict[str, Any]:
    """Fetch raw info dict for a URL using yt-dlp."""
    yt = _resolve_download_backend()
    opts = dict(_base_opts(platform.value))
    opts["skip_download"] = True
    try:
        with yt.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except yt.utils.DownloadError as exc:
        raise _translate_download_error(exc, stage="Extracting media") from exc
    except Exception as exc:  # noqa: BLE001
        raise NetworkError(detail=str(exc), stage="Extracting media") from exc


def _translate_download_error(
    exc: Exception, stage: str = "Downloading"
) -> Exception:
    text = str(exc).lower()
    if (
        "login" in text
        or "sign in" in text
        or "private video" in text
        or "logged in" in text
        or "auth" in text
    ):
        return PermissionDeniedError(detail=str(exc), stage=stage)
    if (
        "http error 403" in text
        or "403: forbidden" in text
        or "http 403" in text
        or "forbidden" in text
    ):
        # The platform rejected the actual media request (anti-bot /
        # region / client blocking). Never masquerade this as a generic
        # network error: report the real 403 reason.
        return DownloadBlockedError(detail=str(exc), stage=stage)
    if "rate" in text or "429" in text or "too many" in text:
        from ...core.errors import RateLimitError

        return RateLimitError(detail=str(exc), stage=stage)
    if (
        "video unavailable" in text
        or "removed" in text
        or "not available" in text
        or "deleted" in text
    ):
        from ...core.errors import ContentUnavailableError

        return ContentUnavailableError(detail=str(exc), stage=stage)
    if "ffmpeg is not installed" in text or "requested merging of multiple formats" in text:
        from ...core.errors import FFmpegNotFoundError

        return FFmpegNotFoundError(
            stage=stage,
            detail=str(exc),
        )
    if "ffmpeg" in text or "postprocessing" in text:
        from ...core.errors import MergeError

        return MergeError(detail=str(exc), stage=stage)
    if "unsupported url" in text or "not a valid url" in text:
        return URLError(detail=str(exc), stage=stage)
    return NetworkError(detail=str(exc), stage=stage)


def _build_format(f: dict[str, Any], item_id: str) -> MediaFormat:
    return MediaFormat(
        format_id=str(f.get("format_id", "")),
        label=_format_label(f),
        quality=_format_quality(f),
        width=f.get("width"),
        height=f.get("height"),
        fps=f.get("fps"),
        ext=f.get("ext") or "mp4",
        vcodec=f.get("vcodec"),
        acodec=f.get("acodec"),
        filesize=f.get("filesize"),
        filesize_approx=f.get("filesize_approx"),
        tbr=f.get("tbr"),
        is_audio_only=(f.get("vcodec") == "none"),
        note=f.get("format_note"),
    )


def _format_quality(f: dict[str, Any]) -> str | None:
    if f.get("vcodec") == "none":
        return "audio only"
    height = f.get("height")
    if height:
        return f"{height}p"
    tbr = f.get("tbr")
    if tbr:
        return f"{tbr:.0f}k"
    return None


def _format_label(f: dict[str, Any]) -> str:
    height = f.get("height")
    note = f.get("format_note")
    if f.get("vcodec") == "none":
        tbr = f.get("abr") or f.get("tbr")
        return f"Audio {tbr:.0f}k" if tbr else "Audio"
    if height:
        fps = f.get("fps")
        base = f"{height}p"
        if fps and fps > 30:
            base += f" {fps:g}fps"
        if note and note.isdigit() and not height:
            pass
        return base
    return note or str(f.get("format_id", "unknown"))


def build_item_from_info(info: dict[str, Any], platform: Platform) -> MediaItem:
    item_id = str(info.get("id") or info.get("url") or "")
    url = _pick_item_url(info, platform, item_id)
    item = MediaItem(
        item_id=item_id,
        title=str(info.get("title") or info.get("track") or "Untitled"),
        url=url,
        platform=platform,
        content_type=ContentType.VIDEO,
        creator=info.get("uploader") or info.get("channel") or info.get("artist"),
        uploader=info.get("uploader") or info.get("channel"),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        description=info.get("description"),
        upload_date=parse_upload_date(info.get("upload_date")),
        index=info.get("playlist_index"),
        playlist_title=info.get("playlist_title") or info.get("playlist"),
        extra=info,
    )
    formats = [f for f in info.get("formats") or [] if isinstance(f, dict)]
    item.available_formats = [
        _build_format(f, item.item_id) for f in formats if f.get("format_id")
    ]
    return item


def _pick_item_url(info: dict[str, Any], platform: Platform, item_id: str) -> str:
    """Choose the most reliable URL for a (possibly flat) info dict.

    The *page* URL (webpage_url / original_url) is preferred over the raw
    media URL. Some extractors (e.g. TikTok) expose a direct, expiring CDN
    media URL under ``url``; feeding that back to yt-dlp later makes it fall
    back to the generic extractor, which is rejected with HTTP 403. Re-running
    the platform extractor against the page URL yields fresh media URLs.
    """
    for key in ("webpage_url", "original_url", "url"):
        value = info.get(key)
        if isinstance(value, str) and value:
            return value
    if item_id:
        if platform == Platform.YOUTUBE:
            return f"https://www.youtube.com/watch?v={item_id}"
        if platform == Platform.TIKTOK:
            return f"https://www.tiktok.com/video/{item_id}"
        if platform == Platform.FACEBOOK:
            return f"https://www.facebook.com/watch?v={item_id}"
    return ""


def build_bundle_from_info(
    info: dict[str, Any], platform: Platform, url: str
) -> MediaBundle:
    if info.get("_type") == "playlist" or info.get("entries"):
        title = str(info.get("title") or "Playlist")
        entries = []
        for entry in info.get("entries") or []:
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            if entry.get("_type") == "playlist":
                continue
            entries.append(build_item_from_info(entry, platform))
        content_type = _playlist_content_type(info, platform)
        bundle = MediaBundle(
            url=url,
            platform=platform,
            content_type=content_type,
            title=title,
            creator=info.get("uploader") or info.get("channel"),
            description=info.get("description"),
            thumbnail=info.get("thumbnail"),
            items=entries,
            webpage_url=info.get("webpage_url"),
        )
        return bundle
    item = build_item_from_info(info, platform)
    return MediaBundle(
        url=url,
        platform=platform,
        content_type=ContentType.VIDEO,
        title=item.title,
        creator=item.creator,
        description=item.description,
        thumbnail=item.thumbnail,
        item=item,
        webpage_url=info.get("webpage_url"),
    )


def _playlist_content_type(info: dict[str, Any], platform: Platform) -> ContentType:
    url = str(info.get("webpage_url") or "")
    if platform == Platform.YOUTUBE:
        if "/playlist" in url or "/mix" in url:
            return ContentType.PLAYLIST
        if "/channel/" in url or "/@user" in url or "/c/" in url:
            return ContentType.CHANNEL
        return ContentType.PLAYLIST
    if platform == Platform.TIKTOK:
        return ContentType.PROFILE if "@" in url else ContentType.COLLECTION
    if platform == Platform.FACEBOOK:
        if "/photos" in url or "/album" in url:
            return ContentType.ALBUM
        return ContentType.PROFILE
    return ContentType.COLLECTION


class StreamDownloader:
    """Performs the actual stream download with progress reporting."""

    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        output_format: str,
        quality: str,
        embed_metadata: bool,
        ffmpeg_path: str = "",
    ) -> None:
        self.url = url
        self.output_dir = output_dir
        self.filename = filename
        self.output_format = output_format.lower()
        self.quality = quality
        self.embed_metadata = embed_metadata
        self.ffmpeg_path = ffmpeg_path
        self._progress_cb: Callable[[dict[str, Any]], None] | None = None

    def set_progress_callback(self, cb: Callable[[dict[str, Any]], None]) -> None:
        self._progress_cb = cb

    def _emit(self, **kwargs: Any) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(kwargs)
            except Exception:  # noqa: BLE001
                log.exception("Progress callback failed")

    def download(self) -> str:
        yt = _resolve_download_backend()
        target = str(Path(self.output_dir) / self.filename)
        format_selector = _map_format_selector(self.quality, self.output_format)
        needs_merge = "+" in format_selector or self.output_format in (
            "mp3",
            "m4a",
            "mkv",
        )

        ffmpeg = ""
        if self.ffmpeg_path:
            ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        else:
            ffmpeg = resolve_ffmpeg()

        log.info("DOWNLOAD: url=%s", self.url)
        log.info("DOWNLOAD: format_selector=%s", format_selector)
        log.info("DOWNLOAD: outtmpl=%s", target)
        log.info("DOWNLOAD: merge_needed=%s", needs_merge)
        log.info("DOWNLOAD: ffmpeg=%s", ffmpeg or "NOT FOUND")

        if needs_merge and not ffmpeg:
            raise FFmpegNotFoundError(
                detail=(
                    f"Selected format '{self.quality}' / '{self.output_format}' "
                    f"requires merging separate video and audio streams "
                    f"(format selector: {format_selector})."
                )
            )

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": {"default": target},
            "format": format_selector,
            "merge_output_format": _merge_ext(self.output_format),
            "http_headers": {"User-Agent": _user_agent()},
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "cachedir": False,
            "logger": _YtLogger(),
            "noprogress": True,
        }
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
        if self.embed_metadata:
            opts["writethumbnail"] = False
            opts["writesubtitles"] = False
        if self.output_format == "mp3":
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        elif self.output_format == "m4a":
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "192",
                }
            ]

        class ProgressHook:
            def __call__(self, d: dict[str, Any]) -> None:
                if d.get("status") in ("downloading", "finished"):
                    self.owner._emit_from_hook(d)

        hook = ProgressHook()
        hook.owner = self
        opts["progress_hooks"] = [hook]

        try:
            with yt.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                if info is None:
                    raise NetworkError(
                        "Download returned no result.", stage="Downloading"
                    )
                return self._final_path(target, info, ydl)
        except yt.utils.DownloadError as exc:
            raise _translate_download_error(exc, stage="Downloading") from exc
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AppError):
                raise
            raise NetworkError(detail=str(exc), stage="Downloading") from exc

    def _emit_from_hook(self, d: dict[str, Any]) -> None:
        if d.get("status") == "downloading":
            self._emit(
                status="downloading",
                downloaded=d.get("downloaded_bytes", 0) or 0,
                total=d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                speed=d.get("speed"),
                eta=d.get("eta"),
                filename=d.get("filename", ""),
            )
        elif d.get("status") == "finished":
            self._emit(status="processing", filename=d.get("filename", ""))

    def _final_path(
        self, target: str, info: dict[str, Any], ydl: Any
    ) -> str:
        request = ydl.prepare_filename(info)
        if Path(request).exists():
            return request
        final = Path(target)
        if final.exists():
            return str(final)
        # audio extraction renames extension
        candidates = [
            target,
            str(Path(target).with_suffix("." + self.output_format)),
        ]
        for c in candidates:
            p = Path(c)
            if p.exists():
                return str(p)
        # search directory for newest file matching stem
        stem = Path(target).stem
        parent = Path(target).parent
        matches = sorted(
            (p for p in parent.glob(f"{stem}*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if matches:
            return str(matches[0])
        return target


def _map_format_selector(quality: str, output_format: str) -> str:
    """Map app-level quality selection to a yt-dlp format selector."""
    fmt = output_format.lower()
    audio = fmt in ("mp3", "m4a")
    if quality == "Audio Only" or audio:
        return "bestaudio/best"
    height = {
        "Best Available": None,
        "2160p": "2160",
        "1440p": "1440",
        "1080p": "1080",
        "720p": "720",
        "480p": "480",
        "360p": "360",
    }.get(quality)
    if height is None:
        return "bestvideo+bestaudio/best"
    return (
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
    )


def _merge_ext(output_format: str) -> str:
    if output_format in ("mp4", "webm", "mkv"):
        return output_format
    return "mp4"
