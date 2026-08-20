"""Human-readable formatting helpers."""

from __future__ import annotations


def format_bytes(num: float | int | None) -> str:
    if num is None:
        return "—"
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_speed(bytes_per_sec: float | None) -> str:
    if bytes_per_sec is None or bytes_per_sec < 0:
        return "—"
    return f"{format_bytes(bytes_per_sec)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN guard
        return "—"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return format_eta(seconds)
