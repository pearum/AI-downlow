"""Final-output validation for the download engine.

A download is only "Completed" once the actual final file has been located
and verified: it must exist, be a regular file, be larger than 0 bytes and be
readable. Temporary / partial / 0-byte files left behind are never treated as
a successful download.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: A download that finished but produced no valid file.
NO_VALID_OUTPUT_MESSAGE = (
    "Download completed without producing a valid output file. "
    "The resulting file is missing or 0 bytes."
)


def validate_output_file(path: str) -> tuple[bool, str, int]:
    """Verify a candidate output file.

    Returns (ok, reason, size). `reason` is empty when ok is True.
    """
    if not path:
        return False, "No output path was returned.", 0
    p = Path(path)
    try:
        if not p.exists():
            return False, "Output file does not exist.", 0
        if not p.is_file():
            return False, "Output path is not a regular file.", 0
        size = p.stat().st_size
        if size <= 0:
            return False, "Output file is 0 bytes.", 0
        with open(p, "rb") as fh:
            fh.read(1)
        return True, "", size
    except OSError as exc:
        return False, f"Output file is not readable: {exc}", 0


def locate_final_output(
    output_dir: str,
    stem: str,
    extensions: Optional[list[str]] = None,
) -> Optional[str]:
    """Find the real final output file after FFmpeg / post-processing.

    The path returned by a downloader before post-processing may point at a
    temporary file (e.g. ``video.fXXX`` / ``video.fYYY``) while the merged
    result is ``Video Title.mp4``. We search ``output_dir`` for the newest
    non-empty file whose stem matches and whose extension is acceptable.
    """
    parent = Path(output_dir)
    if not parent.is_dir():
        return None
    exts = [e.lstrip(".").lower() for e in (extensions or []) if e]
    candidates: list[tuple[float, Path]] = []
    try:
        for p in parent.iterdir():
            if not p.is_file() or p.stat().st_size <= 0:
                continue
            if not p.stem.lower().startswith(stem.lower()):
                continue
            if exts and p.suffix.lstrip(".").lower() not in exts:
                continue
            candidates.append((p.stat().st_mtime, p))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return str(candidates[0][1])


def cleanup_file(path: Optional[str]) -> None:
    """Best-effort removal of a partial / 0-byte / corrupt file."""
    if not path:
        return
    try:
        p = Path(path)
        if p.is_file() and p.stat().st_size <= 0:
            p.unlink(missing_ok=True)
            log.info("WORKER: cleaned up 0-byte file %s", p)
    except OSError:
        pass


def cleanup_bad_output(output_dir: str, stem: str) -> None:
    """Remove zero-byte / partial leftovers for a failed item (best-effort)."""
    parent = Path(output_dir)
    if not parent.is_dir():
        return
    try:
        for p in parent.iterdir():
            if not p.is_file():
                continue
            if not p.stem.lower().startswith(stem.lower()):
                continue
            if p.stat().st_size <= 0:
                p.unlink(missing_ok=True)
                log.info("WORKER: cleaned up partial file %s", p)
    except OSError:
        pass


def expected_targets(output_dir: str, filename: str) -> list[str]:
    """Candidate final paths for a requested filename (incl. merged ext)."""
    targets: list[str] = []
    stem = Path(filename).stem
    suffix = Path(filename).suffix.lstrip(".").lower()
    merged = suffix if suffix in ("mp4", "webm", "mkv") else "mp4"
    for ext in sorted({suffix, merged}):
        if ext:
            targets.append(str(Path(output_dir) / f"{stem}.{ext}"))
    if not targets:
        targets.append(str(Path(output_dir) / filename))
    return targets
