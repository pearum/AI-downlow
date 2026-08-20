"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from source without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from app.config.settings import SettingsManager  # noqa: E402
from app.core.bus import EventBus  # noqa: E402
from app.core.logging_setup import setup_logging  # noqa: E402
from app.database.accounts import AccountsStore  # noqa: E402
from app.database.history import HistoryDatabase  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.providers.registry import ProviderRegistry  # noqa: E402

_APP_BASE = Path(__file__).resolve().parent.parent


def main() -> int:
    # Load local development secrets (never committed).
    load_dotenv(_APP_BASE / ".env")

    settings_manager = SettingsManager()
    settings = settings_manager.settings
    log_file = setup_logging(
        log_level=settings.advanced.log_level,
        debug=settings.advanced.debug_mode,
    )

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Multi-Platform Video Downloader")
    app.setOrganizationName("VideoDownloader")
    app.setApplicationVersion("1.0.0")

    registry = ProviderRegistry()
    bus = EventBus()
    db = HistoryDatabase()
    accounts = AccountsStore()

    window = MainWindow(registry, settings_manager, bus, db, accounts)
    window.show()
    exit_code = app.exec()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())