"""Core models shared across the application.

These dataclasses are the contract between providers, the download engine,
the GUI and the history database.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


class Platform(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    UNKNOWN = "unknown"

    @property
    def display_name(self) -> str:
        return {
            Platform.YOUTUBE: "YouTube",
            Platform.TIKTOK: "TikTok",
            Platform.FACEBOOK: "Facebook",
            Platform.UNKNOWN: "Unknown",
        }[self]


class ContentType(str, enum.Enum):
    VIDEO = "video"
    PLAYLIST = "playlist"
    CHANNEL = "channel"
    ALBUM = "album"
    PROFILE = "profile"
    COLLECTION = "collection"


class ItemStatus(str, enum.Enum):
    PENDING = "pending"
    ANALYZING = "analyzing"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    VALIDATING = "validating"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class VideoQuality(str, enum.Enum):
    BEST = "best"
    Q2160 = "2160"
    Q1440 = "1440"
    Q1080 = "1080"
    Q720 = "720"
    Q480 = "480"
    Q360 = "360"
    AUDIO_ONLY = "audio_only"


class OutputFormat(str, enum.Enum):
    MP4 = "mp4"
    WEBM = "webm"
    MKV = "mkv"
    MP3 = "mp3"
    M4A = "m4a"


@dataclass
class MediaItem:
    """A single downloadable media item (video, audio, track...)."""

    item_id: str
    title: str
    url: str
    platform: Platform = Platform.UNKNOWN
    content_type: ContentType = ContentType.VIDEO
    creator: Optional[str] = None
    uploader: Optional[str] = None
    duration: Optional[float] = None
    thumbnail: Optional[str] = None
    description: Optional[str] = None
    upload_date: Optional[datetime] = None
    index: Optional[int] = None
    playlist_title: Optional[str] = None
    available_formats: list["MediaFormat"] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaFormat:
    """A single downloadable format / variant for a MediaItem."""

    format_id: str
    label: str
    quality: Optional[str] = None  # e.g. "1080p", "audio only"
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    ext: str = "mp4"
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    filesize: Optional[int] = None
    filesize_approx: Optional[int] = None
    bitrate: Optional[float] = None
    tbr: Optional[float] = None
    is_audio_only: bool = False
    note: Optional[str] = None
    audio_format_id: Optional[str] = None  # separate audio stream to merge


@dataclass
class MediaBundle:
    """Result of analyzing a URL: either a single item or a collection."""

    url: str
    platform: Platform
    content_type: ContentType
    title: str
    creator: Optional[str] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    item: Optional[MediaItem] = None
    items: list[MediaItem] = field(default_factory=list)
    webpage_url: Optional[str] = None

    @property
    def is_collection(self) -> bool:
        return self.content_type != ContentType.VIDEO and bool(self.items)

    @property
    def count(self) -> int:
        if self.item is not None:
            return 1
        return len(self.items)


def sanitize_duration(seconds: Optional[float]) -> Optional[float]:
    """Normalize a raw duration value to seconds (float or None)."""
    if seconds is None:
        return None
    try:
        return max(0.0, float(seconds))
    except (TypeError, ValueError):
        return None


_YEAR_RE = re.compile(r"^(\d{4})(\d{2})?\d{2}?$")


def parse_upload_date(raw: Any) -> Optional[datetime]:
    """Parse yt-dlp style upload_date (YYYYMMDD) into a datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d")
    except ValueError:
        pass
    match = _YEAR_RE.match(text)
    if match and len(match.group(0)) == 8:
        try:
            return datetime.strptime(match.group(0), "%Y%m%d")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
