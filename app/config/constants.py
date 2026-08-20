"""Application constants."""

from __future__ import annotations

from pathlib import Path

APP_NAME = "Multi-Platform Video Downloader"
APP_VERSION = "1.0.0"
ORG_NAME = "VideoDownloader"
CONFIG_FILE = "config.json"

SUPPORTED_PROVIDERS = ["youtube", "tiktok", "facebook"]

QUALITY_LABELS = [
    "Best Available",
    "2160p",
    "1440p",
    "1080p",
    "720p",
    "480p",
    "360p",
    "Audio Only",
]

FORMAT_LABELS = ["MP4", "WebM", "MKV", "MP3", "M4A"]

# Characters not allowed in Windows filenames (plus control chars and
# trailing dots/spaces handled separately).
INVALID_FILENAME_CHARS = '<>:"/\\|?*\x00-\x1f'

# Reserved Windows device names, case-insensitive, without extension.
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

DEFAULT_DOWNLOAD_FOLDER = str(Path.home() / "Downloads" / "Videos")

THUMBNAIL_CACHE_DIR = "thumbnails"
