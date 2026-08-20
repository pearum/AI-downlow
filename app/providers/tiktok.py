"""TikTok provider.

Follows the same adapter-based approach as the YouTube provider. TikTok
share/short URLs (vm./vt. hosts) are resolved to their canonical destination
before metadata extraction so yt-dlp is always handed a real video URL.

Compliance: content that TikTok does not expose publicly — including content
TikTok actively blocks from automated access — is reported as unavailable with
a clear reason. Nothing here bypasses CAPTCHA, anti-bot, DRM, authentication or
rate-limit protections.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..core.errors import (
    AppError,
    AuthenticationError,
    ContentUnavailableError,
    MetadataError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    URLError,
)
from ..core.models import MediaBundle, MediaItem, Platform
from .base import BaseProvider, DownloadOptions
from .common.url_utils import detect_platform, ensure_scheme, is_valid_url
from .common.yt_adapter import StreamDownloader, build_bundle_from_info, extract_info

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: Short-link hosts that must be resolved to a real URL before extraction.
_SHARE_HOSTS = ("vm.tiktok.com", "vt.tiktok.com")

#: Phrases that indicate the platform rejected the request outright.
_BLOCKED_SIGNALS = (
    "ip address is blocked",
    "your ip is blocked",
    "blocked from accessing",
    "access denied",
    "forbidden",
)

_ACCESS_UNAVAILABLE_MESSAGE = (
    "TikTok access is currently unavailable for this URL."
)
_ACCESS_UNAVAILABLE_DETAIL = (
    "The platform requires access that is not available through the "
    "configured provider."
)


class TikTokProvider(BaseProvider):
    id = "tiktok"
    display_name = "TikTok"
    platform = Platform.TIKTOK

    # -- URL handling ------------------------------------------------
    @staticmethod
    def normalize_url(url: str) -> str:
        """Canonical form: no surrounding whitespace, https scheme present."""
        return ensure_scheme(url.strip())

    def detect_url(self, url: str) -> bool:
        if not is_valid_url(url):
            return False
        return detect_platform(url) is Platform.TIKTOK

    @staticmethod
    def is_share_url(url: str) -> bool:
        """True for vm./vt. short/share links that must be resolved first."""
        lowered = ensure_scheme(url).lower()
        return any(host in lowered for host in _SHARE_HOSTS)

    def resolve_share_url(self, url: str) -> str:
        """Follow redirects for a short link and return the resolved URL.

        Raises an AppError with a clear reason when the link cannot be
        resolved (expired link or the platform serving its home page instead
        of the content).
        """
        log.info("[TIKTOK] Resolving short URL: %s", url)
        try:
            response = httpx.get(
                url,
                headers={
                    "User-Agent": _UA,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                    ),
                },
                follow_redirects=True,
                timeout=30.0,
                verify=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._categorize_http_error(exc) from exc

        final = str(response.url).strip()
        log.info("[TIKTOK] Resolved URL: %s", final)

        if self._is_homepage_fallback(final):
            raise PermissionDeniedError(
                _ACCESS_UNAVAILABLE_MESSAGE,
                detail=(
                    f"{_ACCESS_UNAVAILABLE_DETAIL}\n"
                    f"The short link redirected to {final!r} instead of a video. "
                    "The link may be expired, or the platform blocked the request.\n"
                    f"HTTP status: {response.status_code}"
                ),
                stage="Resolving URL",
            )
        if response.status_code >= 400:
            raise self._status_error(
                response.status_code, stage="Resolving URL"
            )
        if not self.detect_url(final):
            raise MetadataError(
                "The TikTok short link could not be resolved to a TikTok URL.",
                detail=f"Resolved to {final!r} (HTTP {response.status_code}).",
                stage="Resolving URL",
            )
        return final

    @staticmethod
    def _is_homepage_fallback(final_url: str) -> bool:
        try:
            parsed = urlparse(final_url)
        except ValueError:
            return False
        netloc = parsed.netloc.lower().removeprefix("www.")
        if netloc == "tiktok.com" and parsed.path in ("", "/"):
            return True
        return False

    # -- metadata ----------------------------------------------------
    def get_metadata(self, url: str) -> MediaBundle:
        log.info("[TIKTOK] Input URL: %s", url)
        url = self.normalize_url(url)
        log.info("[TIKTOK] Detected: %s", self.detect_url(url))
        if self.is_share_url(url):
            url = self.resolve_share_url(url)

        log.info("[TIKTOK] Extracting metadata: %s", url)
        try:
            info = extract_info(url, self.platform)
        except Exception as exc:  # noqa: BLE001
            raise self.translate_error(exc, "Failed to fetch TikTok metadata") from exc

        bundle = build_bundle_from_info(info, self.platform, url)
        if bundle.item is None and not bundle.items:
            raise ContentUnavailableError(
                "No playable content found at this TikTok URL."
            )
        if bundle.item is not None:
            log.info("[TIKTOK] Title: %s", bundle.item.title)
            log.info(
                "[TIKTOK] Formats: %s",
                [f.label for f in bundle.item.available_formats],
            )
        return bundle

    def get_playlist_items(self, url: str) -> list[MediaItem]:
        return self.get_metadata(url).items

    def get_album_items(self, url: str) -> list[MediaItem]:
        return []

    def get_available_formats(self, item: MediaItem) -> list[Any]:
        return item.available_formats or []

    def requires_account_for(self, url: str) -> bool:
        return False

    # -- download ----------------------------------------------------
    def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        progress_callback: Any = None,
    ) -> str:
        log.info("[TIKTOK] Download URL: %s", item.url)
        log.info("[TIKTOK] Available formats: %s", [f.label for f in item.available_formats])
        log.info("[TIKTOK] Selected format: %s", options.quality)
        log.info(
            "[TIKTOK] Output template: %s",
            str(Path(options.output_dir) / options.filename),
        )
        log.info(
            "[TIKTOK] FFmpeg configured: %r",
            options.extra.get("ffmpeg_path", "") if options.extra else "",
        )
        try:
            downloader = StreamDownloader(
                url=item.url,
                output_dir=options.output_dir,
                filename=options.filename,
                output_format=options.output_format,
                quality=options.quality,
                embed_metadata=options.embed_metadata,
                ffmpeg_path=options.extra.get("ffmpeg_path", "") if options.extra else "",
            )
            if progress_callback is not None:
                downloader.set_progress_callback(progress_callback)
            path = downloader.download()
        except Exception as exc:  # noqa: BLE001
            log.warning("[TIKTOK] Download failed: %s", exc)
            raise self.translate_error(exc, "TikTok download failed") from exc

        log.info("[TIKTOK] Output: %s", path)
        ok, size = _validate_path(path)
        log.info("[TIKTOK] Validation: %s (%s bytes)", "OK" if ok else "FAILED", size)
        log.info("[TIKTOK] Result: %s", "OK" if ok else "FAILED")
        return path

    # -- error handling ----------------------------------------------
    @staticmethod
    def translate_error(exc: Exception, fallback: str = "") -> Exception:
        """TikTok-aware translation of low-level exceptions to AppError."""
        if isinstance(exc, AppError):
            detail = exc.detail or ""
            text = f"{exc.message} {detail}".lower()
            if any(signal in text for signal in _BLOCKED_SIGNALS):
                reason = detail or exc.message
                return PermissionDeniedError(
                    _ACCESS_UNAVAILABLE_MESSAGE,
                    detail=f"{_ACCESS_UNAVAILABLE_DETAIL}\nReason: {reason}",
                    stage=exc.stage or "Extracting media",
                )
            if isinstance(exc, NetworkError):
                # A generic NetworkError can still carry the real yt-dlp
                # message in `detail`; surface the actual failure instead of
                # letting "A network error occurred..." hide it.
                return TikTokProvider._refine_network_error(exc, text)
            return exc
        message = str(exc).strip() or fallback or exc.__class__.__name__
        text = message.lower()
        if any(signal in text for signal in _BLOCKED_SIGNALS):
            return PermissionDeniedError(
                _ACCESS_UNAVAILABLE_MESSAGE,
                detail=f"{_ACCESS_UNAVAILABLE_DETAIL}\nReason: {message}",
                stage="Extracting media",
            )
        return BaseProvider.translate_error(exc, fallback)

    @staticmethod
    def _refine_network_error(exc: AppError, text: str) -> AppError:
        """Re-classify a generic NetworkError from its technical detail."""
        stage = exc.stage or "Extracting media"
        if any(s in text for s in ("timeout", "timed out", "timed-out")):
            return NetworkError(
                f"TikTok request timed out. {exc.detail or ''}".strip(),
                detail=exc.detail or "",
                stage=stage,
            )
        if any(s in text for s in ("getaddrinfo", "name resolution", "failed to resolve")):
            return NetworkError(
                "TikTok could not be reached.",
                detail=f"DNS resolution failed.\n{exc.detail or ''}",
                stage=stage,
            )
        if any(s in text for s in ("ssl", "tls", "certificate", "handshake")):
            return NetworkError(
                "A secure connection to TikTok could not be established.",
                detail=exc.detail or "",
                stage=stage,
            )
        if "http error 401" in text or "http 401" in text:
            return AuthenticationError(detail=exc.detail or "", stage=stage)
        if "http error 403" in text or "http 403" in text or "403" in text:
            return PermissionDeniedError(
                _ACCESS_UNAVAILABLE_MESSAGE,
                detail=f"{_ACCESS_UNAVAILABLE_DETAIL}\nHTTP 403 during {stage}.",
                stage=stage,
            )
        if "http error 404" in text or "http 404" in text or "404" in text:
            return ContentUnavailableError(detail=exc.detail or "", stage=stage)
        if "429" in text or "rate limit" in text or "too many" in text:
            return RateLimitError(detail=exc.detail or "", stage=stage)
        if "unexpected response from webpage request" in text:
            return MetadataError(
                "TikTok extraction is currently unavailable with the "
                "installed downloader backend. The platform response is "
                "incompatible with the current TikTok extractor.",
                detail=exc.detail or "",
                stage=stage,
            )
        if any(
            s in text
            for s in ("cannot parse data", "extractor failure", "unable to extract")
        ):
            return MetadataError(detail=exc.detail or "", stage=stage)
        # Last resort: never hide the real reason behind the generic
        # "A network error occurred..." phrase when the platform actually
        # told us something concrete.
        reason = (exc.detail or "").strip()
        if reason:
            return NetworkError(
                reason[:500],
                detail=reason,
                stage=stage,
            )
        return exc

    @staticmethod
    def _categorize_http_error(exc: Exception) -> Exception:
        """Map raw httpx exceptions to specific NetworkError sub-messages."""
        name = exc.__class__.__name__
        text = str(exc).lower()
        if isinstance(exc, httpx.TimeoutException):
            return NetworkError(
                "TikTok did not respond in time.",
                detail=f"{name}: {exc}",
                stage="Resolving URL",
            )
        if (
            "getaddrinfo" in text
            or "name resolution" in text
            or "failed to resolve" in text
            or "nodename nor servname" in text
        ):
            return NetworkError(
                "TikTok could not be reached.",
                detail=f"{name}: DNS resolution failed ({exc})",
                stage="Resolving URL",
            )
        if (
            "ssl" in text
            or "tls" in text
            or "certificate" in text
            or "handshake" in text
        ):
            return NetworkError(
                "A secure connection to TikTok could not be established.",
                detail=f"{name}: {exc}",
                stage="Resolving URL",
            )
        if "connect" in text or "connection" in text:
            return NetworkError(
                "Connection to TikTok failed.",
                detail=f"{name}: {exc}",
                stage="Resolving URL",
            )
        return NetworkError(
            "A network error occurred while communicating with TikTok.",
            detail=f"{name}: {exc}",
            stage="Resolving URL",
        )

    @staticmethod
    def _status_error(status: int, stage: str = "") -> Exception:
        if status == 401:
            return AuthenticationError(detail=f"HTTP {status} during {stage}.", stage=stage)
        if status == 403:
            return PermissionDeniedError(
                _ACCESS_UNAVAILABLE_MESSAGE,
                detail=(
                    f"{_ACCESS_UNAVAILABLE_DETAIL}\n"
                    f"The TikTok server rejected the request.\n"
                    f"HTTP status: {status}"
                ),
                stage=stage,
            )
        if status == 404:
            return ContentUnavailableError(
                detail=f"HTTP {status} during {stage}.", stage=stage
            )
        if status == 429:
            return RateLimitError(detail=f"HTTP {status} during {stage}.", stage=stage)
        if status >= 500:
            return NetworkError(
                "The TikTok server could not process the request.",
                detail=f"HTTP {status} during {stage}.",
                stage=stage,
            )
        return NetworkError(
            detail=f"HTTP {status} during {stage}.", stage=stage
        )


def _validate_path(path: str) -> tuple[bool, int]:
    try:
        p = Path(path)
        if not p.is_file():
            return False, 0
        size = p.stat().st_size
        return size > 0, size
    except OSError:
        return False, 0
