"""Centralized logging configuration.

Logs are written to a rotating file under the app data directory so detailed
diagnostics are always available for debugging, while the GUI only shows
user-friendly messages.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(threadName)s | "
    "%(message)s"
)


def app_data_dir() -> Path:
    """Return a stable per-user directory for config, db and logs."""
    base = os.environ.get("APPDATA")
    if base:
        path = Path(base) / "VideoDownloader"
    else:
        path = Path.home() / ".video_downloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(log_level: str = "INFO", debug: bool = False) -> Path:
    """Configure root logging and return the log file path."""
    if debug:
        log_level = "DEBUG"
    level = getattr(logging, str(log_level).upper(), logging.INFO)

    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.WARNING)
    root.addHandler(console)

    # Keep yt-dlp noise in its own logger at INFO level.
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
    return log_file
