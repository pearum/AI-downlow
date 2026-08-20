"""URL helpers: host extraction, platform detection."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ...core.models import Platform

_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "youtube-nocookie.com",
}
_TIKTOK_HOSTS = {
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
}
_FACEBOOK_HOSTS = {
    "facebook.com", "www.facebook.com", "fb.watch", "m.facebook.com",
    "fb.com",
}

_BARE_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def ensure_scheme(url: str) -> str:
    url = url.strip()
    if not _BARE_URL_RE.match(url):
        url = "https://" + url
    return url


def is_valid_url(url: str) -> bool:
    url = ensure_scheme(url)
    if any(ch.isspace() for ch in url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.netloc and len(url) > 8)


def detect_platform(url: str) -> Platform:
    url = ensure_scheme(url)
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return Platform.UNKNOWN
    host = host.removeprefix("www.").split(":")[0]
    # youtu.be
    if host == "youtu.be":
        return Platform.YOUTUBE
    if host in _YOUTUBE_HOSTS:
        return Platform.YOUTUBE
    if host in _TIKTOK_HOSTS:
        return Platform.TIKTOK
    if host in _FACEBOOK_HOSTS:
        return Platform.FACEBOOK
    return Platform.UNKNOWN


def extract_video_id(url: str, platform: Platform) -> str | None:
    """Best-effort extraction of the canonical media id from a URL."""
    if platform == Platform.YOUTUBE:
        return _youtube_video_id(url)
    if platform == Platform.TIKTOK:
        return _tiktok_video_id(url)
    if platform == Platform.FACEBOOK:
        return _facebook_video_id(url)
    return None


def _youtube_video_id(url: str) -> str | None:
    url = ensure_scheme(url)
    if "youtu.be" in url:
        path = urlparse(url).path.lstrip("/")
        video_id = path.split("/")[0].split("?")[0]
        return video_id if len(video_id) == 11 else None
    parsed = urlparse(url)
    if parsed.netloc.lower() == "music.youtube.com":
        query = parsed.query
    else:
        query = parsed.query or (parsed.path if "embed" not in parsed.path else "")
    match = re.search(r"(?:[?&]|^)v=([a-zA-Z0-9_-]{11})", query)
    if match:
        return match.group(1)
    match = re.search(r"/embed/([a-zA-Z0-9_-]{11})", parsed.path)
    if match:
        return match.group(1)
    return None


def _tiktok_video_id(url: str) -> str | None:
    url = ensure_scheme(url)
    match = re.search(r"/video/(\d{15,20})", url)
    if match:
        return match.group(1)
    match = re.search(r"(?:@[\w.-]+/)?video/(\d+)", url)
    if match:
        return match.group(1)
    return None


def _facebook_video_id(url: str) -> str | None:
    url = ensure_scheme(url)
    match = re.search(r"/videos/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"video_id=(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"watch/\?v=(\d+)", url)
    if match:
        return match.group(1)
    return None
