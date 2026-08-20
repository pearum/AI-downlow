"""Settings model + JSON persistence.

Settings are plain dataclasses persisted to a JSON file inside the app data
directory. Secrets (OAuth tokens) are *never* stored here; the Accounts store
keeps them separately and optionally encrypted.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.constants import DEFAULT_DOWNLOAD_FOLDER
from ..core.logging_setup import app_data_dir
from .constants import CONFIG_FILE

log = logging.getLogger(__name__)

DEFAULT_FILENAME_TEMPLATE = "{creator} - {title}"


@dataclass
class GeneralSettings:
    download_folder: str = DEFAULT_DOWNLOAD_FOLDER
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    folder_structure: str = "platform/creator"  # "", platform, platform/creator, ...
    concurrent_downloads: int = 3
    auto_resume: bool = True
    skip_existing: bool = True


@dataclass
class VideoSettings:
    default_quality: str = "Best Available"
    default_format: str = "MP4"
    embed_metadata: bool = True


@dataclass
class NetworkSettings:
    timeout: int = 30
    retry_count: int = 3
    concurrent_connections: int = 3


@dataclass
class AppearanceSettings:
    theme: str = "dark"  # "dark" | "light" | "system"


@dataclass
class AdvancedSettings:
    ffmpeg_path: str = ""  # empty => search PATH
    log_level: str = "INFO"
    debug_mode: bool = False


@dataclass
class AppSettings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    video: VideoSettings = field(default_factory=VideoSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSettings":
        return cls(
            general=GeneralSettings(**data.get("general", {})),
            video=VideoSettings(**data.get("video", {})),
            network=NetworkSettings(**data.get("network", {})),
            appearance=AppearanceSettings(**data.get("appearance", {})),
            advanced=AdvancedSettings(**data.get("advanced", {})),
        )


class SettingsManager:
    """Loads and saves AppSettings to disk."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (app_data_dir() / CONFIG_FILE)
        self._settings = AppSettings()
        self._loaded = False
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._settings = AppSettings.from_dict(raw)
            self._loaded = True
        except Exception:  # noqa: BLE001 - corrupt config must not crash app
            log.exception("Failed to load settings from %s", self.path)
            self._settings = AppSettings()
            self._loaded = False

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._settings.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            log.exception("Failed to save settings to %s", self.path)

    def update(self, **section_kwargs: dict[str, Any]) -> None:
        """Update sections: update(general={...}, network={...})."""
        for name, values in section_kwargs.items():
            current = getattr(self._settings, name)
            for key, value in values.items():
                if hasattr(current, key):
                    setattr(current, key, value)
        self.save()

    def as_dict(self) -> dict[str, Any]:
        return self._settings.to_dict()

    def ensure_download_folder(self) -> Path:
        folder = Path(os.path.expandvars(self._settings.general.download_folder))
        folder.mkdir(parents=True, exist_ok=True)
        return folder
