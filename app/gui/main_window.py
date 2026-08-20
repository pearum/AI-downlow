"""Main application window with sidebar navigation."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import SettingsManager
from ..collections.engine import CollectionEngine
from ..collections.store import CollectionStore
from ..core.analyzer import MetadataAnalyzer
from ..core.bus import EventBus
from ..database.accounts import AccountsStore
from ..database.history import HistoryDatabase, HistoryEntry
from ..download.manager import DownloadManager
from ..providers.registry import ProviderRegistry
from .accounts import AccountsPage
from .bridge import BusBridge
from .collections import CollectionsPage
from .dashboard import DashboardPage
from .downloads import QueuePage
from .history import HistoryPage
from .settings import SettingsPage
from .theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(
        self,
        registry: ProviderRegistry,
        settings_manager: SettingsManager,
        bus: EventBus,
        db: HistoryDatabase,
        accounts: AccountsStore,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.settings_manager = settings_manager
        self.bus = bus
        self.db = db
        self.accounts = accounts

        self.setWindowTitle("Multi-Platform Video Downloader")
        self.setMinimumSize(QSize(1080, 720))

        self.manager = DownloadManager(
            registry,
            bus,
            concurrency=settings_manager.settings.general.concurrent_downloads,
            ffmpeg_path=settings_manager.settings.advanced.ffmpeg_path,
            retry_count=settings_manager.settings.network.retry_count,
        )
        self.analyzer = MetadataAnalyzer(registry, bus)
        self.collection_store = CollectionStore()
        self.collection_engine = CollectionEngine(
            registry, bus, self.manager, settings_manager, store=self.collection_store
        )
        self.bridge = BusBridge(bus, self)
        self.bridge.start()

        self._build_ui()
        self._apply_theme(settings_manager.settings.appearance.theme)
        self.manager.start()
        self._bind_history_recorder()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 16, 0, 16)
        side_layout.setSpacing(2)

        brand = QLabel("Multi-Platform\nVideo Downloader")
        brand.setObjectName("SectionTitle")
        brand.setWordWrap(True)
        brand.setContentsMargins(16, 0, 16, 16)
        side_layout.addWidget(brand)

        self.stack = QStackedWidget()

        self.dashboard_page = DashboardPage(
            self.registry,
            self.bus,
            self.manager,
            self.analyzer,
            self.settings_manager,
            self.bridge,
        )
        self.queue_page = QueuePage(self.manager, self.bus, self.bridge)
        self.collections_page = CollectionsPage(
            self.collection_engine, self.bus, self.bridge
        )
        self.history_page = HistoryPage(self.db)
        self.accounts_page = AccountsPage(self.registry, self.accounts, self.bus)
        self.settings_page = SettingsPage(self.settings_manager)

        for page in (
            self.dashboard_page,
            self.queue_page,
            self.collections_page,
            self.history_page,
            self.accounts_page,
            self.settings_page,
        ):
            self.stack.addWidget(page)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        for index, label in enumerate(
            [
                "Dashboard",
                "Downloads",
                "Collections",
                "History",
                "Accounts",
                "Settings",
            ]
        ):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setFixedHeight(40)
            self.nav_group.addButton(button, index)
            self._nav_buttons[label] = button
            side_layout.addWidget(button)
            button.clicked.connect(lambda _=False, i=index: self.stack.setCurrentIndex(i))

        side_layout.addStretch(1)
        status_hint = QLabel("v1.0.0")
        status_hint.setObjectName("Muted")
        status_hint.setContentsMargins(16, 8, 16, 0)
        side_layout.addWidget(status_hint)

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)

        self.nav_group.button(0).setChecked(True)
        self.statusBar().showMessage("Ready")
        self.settings_page.save_button.clicked.connect(self._on_settings_saved)
        self.accounts_page.changed.connect(lambda: self.accounts_page.refresh())
        self.collections_page.refresh_resume_combo()

    # ------------------------------------------------------------------
    def _apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication

        apply_theme(QApplication.instance(), theme)

    def _on_settings_saved(self) -> None:
        self.manager.set_concurrency(
            self.settings_manager.settings.general.concurrent_downloads
        )
        self.manager.set_retry_count(
            self.settings_manager.settings.network.retry_count
        )
        self.manager.ffmpeg_path = self.settings_manager.settings.advanced.ffmpeg_path
        self._apply_theme(self.settings_manager.settings.appearance.theme)

    # ------------------------------------------------------------------
    def _bind_history_recorder(self) -> None:
        def on_completed(uid: str, path: str, size: int) -> None:
            item = self.manager.queue.get(uid)
            if item is None:
                return
            status = item.status.value if item.status else "completed"
            self.db.add(
                HistoryEntry(
                    url=item.media.url,
                    platform=item.media.platform.display_name,
                    title=item.media.title,
                    creator=item.media.creator,
                    file_path=path,
                    format=item.options.output_format,
                    resolution=item.options.quality,
                    status=status,
                    file_size=int(size or 0),
                )
            )
            self.history_page.refresh()

        def on_failed(uid: str, message: str, detail: str) -> None:
            item = self.manager.queue.get(uid)
            if item is None:
                return
            self.db.add(
                HistoryEntry(
                    url=item.media.url,
                    platform=item.media.platform.display_name,
                    title=item.media.title,
                    creator=item.media.creator,
                    file_path="",
                    format=item.options.output_format,
                    resolution=item.options.quality,
                    status="failed",
                    error_message=message,
                    error_type=item.error_type or None,
                    error_stage=item.error_stage or None,
                    error_detail=detail or None,
                )
            )
            self.history_page.refresh()

        self.bridge.download_completed.connect(on_completed)
        self.bridge.download_failed.connect(on_failed)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.manager.shutdown()
        super().closeEvent(event)