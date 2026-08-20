"""Structured result produced by a download worker.

The worker must never report plain `True`. Every finished attempt yields a
`DownloadResult` so the GUI, queue and history all share the same verified
source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DownloadResult:
    """Outcome of one queue item after download + output validation."""

    success: bool = False
    status: str = "failed"  # completed | failed | cancelled | skipped
    output_path: Optional[str] = None
    file_size: int = 0
    duration: Optional[float] = None
    error: str = ""
    technical_error: str = ""
    error_type: str = ""
    error_stage: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "status": self.status,
            "output_path": self.output_path,
            "file_size": self.file_size,
            "duration": self.duration,
            "error": self.error,
            "technical_error": self.technical_error,
            "error_type": self.error_type,
            "error_stage": self.error_stage,
        }


def failed_result(
    message: str,
    *,
    technical: str = "",
    error_type: str = "",
    error_stage: str = "",
) -> DownloadResult:
    return DownloadResult(
        success=False,
        status="failed",
        error=message,
        technical_error=technical,
        error_type=error_type,
        error_stage=error_stage,
    )
