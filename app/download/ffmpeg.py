"""FFmpeg discovery and verification helpers."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ..core.errors import FFmpegNotFoundError
from ..providers.common.yt_adapter import resolve_ffmpeg as _resolve

log = logging.getLogger(__name__)


def find_ffmpeg(configured_path: str = "") -> str:
    """Return the path to an ffmpeg executable, or '' if not found."""
    return _resolve(configured_path)


def _run_version(path: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )
    except OSError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg -version timed out"
    first_line = (result.stdout or "").splitlines()[:1]
    version = first_line[0].strip() if first_line else "unknown"
    return result.returncode == 0, version


def verify_ffmpeg(configured_path: str = "") -> tuple[bool, str]:
    """Return (ok, version_string)."""
    path = find_ffmpeg(configured_path)
    if not path:
        return False, "ffmpeg executable not found"
    return _run_version(path)


def ffmpeg_status(configured_path: str = "") -> tuple[bool, str, str]:
    """Return (ok, path, version). Path is '' when not found."""
    path = find_ffmpeg(configured_path)
    if not path:
        return False, "", ""
    ok, version = _run_version(path)
    if not ok:
        return False, path, version
    return True, path, version


def require_ffmpeg(configured_path: str = "") -> str:
    found = find_ffmpeg(configured_path)
    if not found:
        raise FFmpegNotFoundError(
            detail="Install FFmpeg and set its path in Settings → Advanced → "
            "FFmpeg Path, or add ffmpeg.exe to your system PATH."
        )
    return found


def path_is_ffmpeg(candidate: Path) -> bool:
    if not candidate.exists():
        return False
    if candidate.is_file():
        return candidate.name.lower().startswith("ffmpeg")
    return False