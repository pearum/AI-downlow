"""Safe filename construction for Windows.

Handles:
- stripping illegal characters
- trimming trailing dots/spaces
- reserved device names
- duplicate names  ->  Name (1).mp4
- max length handling
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..config.constants import WINDOWS_RESERVED_NAMES

_MAX_FILENAME_LENGTH = 150
_MAX_STEM_LENGTH = _MAX_FILENAME_LENGTH - 8  # reserve room for " (123)" + ext

# Keep letters, digits, space, and a small set of safe punctuation.
_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")
_STRIP_RE = re.compile(r"[.,;:\s]+$")
_EDGE_SEP_RE = re.compile(r"^\s*[-_.]+\s*|\s*[-_.]+\s*$")


def sanitize_filename(name: str) -> str:
    """Return a Windows-safe filename (no extension)."""
    if not name:
        return "Untitled"
    # Normalize unicode (NFKC) and drop control chars.
    normalized = unicodedata.normalize("NFKC", name)
    # Turn newlines/tabs into spaces before removing control characters.
    normalized = normalized.replace("\t", " ").replace("\n", " ").replace("\r", " ")
    cleaned = _ILLEGAL_RE.sub("", normalized)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    cleaned = _EDGE_SEP_RE.sub("", cleaned).strip()
    cleaned = _STRIP_RE.sub("", cleaned)
    if not cleaned:
        return "Untitled"
    if len(cleaned) > _MAX_STEM_LENGTH:
        cleaned = cleaned[:_MAX_STEM_LENGTH].rstrip()
    return cleaned


def _handle_reserved(name: str) -> str:
    stem = Path(name).stem.upper()
    if stem in WINDOWS_RESERVED_NAMES:
        path = Path(name)
        return f"{path.stem} (file){path.suffix}"
    return name


def make_unique_path(target: Path) -> Path:
    """Return a path that does not exist yet, appending (1), (2)..."""
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_filename(
    template: str,
    *,
    title: str,
    creator: str | None,
    index: int | None,
    date_str: str | None,
    ext: str,
) -> str:
    """Build a safe base filename from a template then return `name.ext`."""
    safe_title = sanitize_filename(title or "Untitled")
    safe_creator = sanitize_filename(creator) if creator else ""
    try:
        base = template.format(
            title=safe_title,
            creator=safe_creator,
            index=index,
            date=date_str,
        )
    except (KeyError, IndexError, ValueError):
        base = template
    base = sanitize_filename(base)
    if not base:
        base = safe_title or "Untitled"
    if index is not None:
        base = f"{index:03d} - {base}"
    name = _handle_reserved(f"{base}.{ext}")
    return name
