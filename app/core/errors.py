"""Application-wide exception hierarchy.

All errors raised by the application should derive from AppError so the GUI
can translate them into user friendly messages without ever exposing a raw
Python traceback.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors."""

    user_message = "An unexpected error occurred."
    stage = "unknown"

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: str | None = None,
        stage: str | None = None,
    ):
        self.message = message or self.user_message
        self.detail = detail
        if stage is not None:
            self.stage = stage
        super().__init__(self.message)

    @property
    def error_type(self) -> str:
        return self.__class__.__name__

    def to_dict(self) -> dict[str, str]:
        """Structured, serializable representation used by history/UI."""
        return {
            "stage": self.stage,
            "error_type": self.error_type,
            "message": self.message,
            "detail": self.detail or "",
        }

    def to_user_string(self) -> str:
        if self.detail:
            return f"{self.message}\n\n{self.detail}"
        return self.message


class URLError(AppError):
    user_message = "The provided URL is invalid or malformed."


class UnsupportedPlatformError(AppError):
    user_message = "This URL is not supported by any installed provider."


class MetadataError(AppError):
    user_message = "Could not fetch information for this URL."


class ContentUnavailableError(AppError):
    user_message = "This content is unavailable or has been removed."


class PermissionDeniedError(AppError):
    user_message = (
        "This content cannot be accessed through the available official "
        "permissions. You may need to grant additional access in the "
        "Accounts section."
    )


class AuthenticationError(AppError):
    user_message = "Authentication failed or has expired. Please reconnect the account."


class RateLimitError(AppError):
    user_message = "The platform rate limit was reached. Try again later."


class DownloadBlockedError(AppError):
    user_message = (
        "The platform refused this download request. The platform may be "
        "detecting automated access from your network, or the content may be "
        "restricted in your region. Verify the video is still publicly "
        "available in a browser, then try again later."
    )


class NetworkError(AppError):
    user_message = "A network error occurred while communicating with the platform."


class DownloadError(AppError):
    user_message = "The download failed."


class MergeError(AppError):
    user_message = "Failed to merge video and audio streams."


class FFmpegNotFoundError(AppError):
    user_message = (
        "FFmpeg is required to combine video and audio streams or to extract "
        "audio. Install FFmpeg and set its path in Settings → Advanced → "
        "FFmpeg Path, then try again."
    )


class DiskFullError(AppError):
    user_message = "The disk does not have enough free space to save this file."


class UnsupportedFormatError(AppError):
    user_message = "The requested output format is not available for this content."


class AccountNotConnectedError(AppError):
    user_message = (
        "This provider requires a connected account. Open the Accounts "
        "section to connect."
    )


def build_error_info(exc: BaseException, stage: str = "unknown") -> dict[str, str]:
    """Normalize any exception into a structured error record.

    Returns a dict with keys: stage, error_type, message, detail.
    `message` is always human-readable; `detail` carries the technical text.
    """
    if isinstance(exc, AppError):
        resolved_stage = exc.stage
        if not resolved_stage or resolved_stage == "unknown":
            resolved_stage = stage
        return {
            "stage": resolved_stage,
            "error_type": exc.error_type,
            "message": exc.message,
            "detail": exc.detail or "",
        }
    return {
        "stage": stage,
        "error_type": exc.__class__.__name__,
        "message": str(exc) or exc.__class__.__name__,
        "detail": f"{exc.__class__.__module__}.{exc.__class__.__name__}: {exc}",
    }
