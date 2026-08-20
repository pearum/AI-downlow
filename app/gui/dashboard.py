"""Dashboard page: URL input, analysis, item/collection listing, download actions."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config.constants import FORMAT_LABELS, QUALITY_LABELS
from ..core.analyzer import MetadataAnalyzer
from ..core.bus import EventBus
from ..core.models import ContentType, MediaBundle, MediaItem, Platform
from ..download.manager import DownloadManager
from ..download.queue import QueueItem
from ..providers.base import DownloadOptions
from ..providers.registry import ProviderRegistry
from ..utils.filenames import build_filename
from ..utils.humanize import format_duration
from .bridge import BusBridge

log = logging.getLogger(__name__)


class _SelectableRow(QFrame):
    def __init__(self, item: MediaItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.check = QCheckBox()
        title = QLabel(item.title or "Untitled")
        title.setWordWrap(True)
        self.index_label = QLabel()
        self.index_label.setObjectName("Muted")
        layout.addWidget(self.check)
        if item.index is not None:
            layout.addWidget(self.index_label)
        layout.addWidget(title, 1)
        duration = QLabel(format_duration(item.duration))
        duration.setObjectName("Muted")
        layout.addWidget(duration)

    def set_index_text(self, text: str) -> None:
        self.index_label.setText(text)


class DashboardPage(QWidget):
    """Main page for pasting URLs and starting downloads."""

    def __init__(
        self,
        registry: ProviderRegistry,
        bus: EventBus,
        manager: DownloadManager,
        analyzer: MetadataAnalyzer,
        settings_provider,
        bridge: BusBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.bus = bus
        self.manager = manager
        self.analyzer = analyzer
        self.settings = settings_provider
        self.bridge = bridge
        self._bundle: MediaBundle | None = None
        self._selected_items: list[MediaItem] = []
        self._rows: list[_SelectableRow] = []
        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("Paste URL")
        title.setObjectName("Title")
        root.addWidget(title)

        input_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Paste one or more YouTube / TikTok / Facebook URLs, one per line..."
        )
        self.url_input.setMinimumHeight(40)
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.setObjectName("PrimaryButton")
        input_row.addWidget(self.url_input, 1)
        input_row.addWidget(self.analyze_button)
        root.addLayout(input_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        root.addWidget(self.status_label)

        # --- metadata area --------------------------------------------
        self.meta_area = QScrollArea()
        self.meta_area.setWidgetResizable(True)
        self.meta_area.setFrameShape(QFrame.NoFrame)
        meta_widget = QWidget()
        self.meta_layout = QVBoxLayout(meta_widget)
        self.meta_layout.setContentsMargins(0, 0, 8, 0)
        self.meta_layout.setSpacing(8)
        self.meta_area.setWidget(meta_widget)
        root.addWidget(self.meta_area, 1)

        # --- actions ---------------------------------------------------
        action_row = QHBoxLayout()
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_LABELS)
        self.format_combo = QComboBox()
        self.format_combo.addItems(FORMAT_LABELS)
        self.quality_combo.setCurrentText(
            self.settings.settings.video.default_quality
        )
        self.format_combo.setCurrentText(self.settings.settings.video.default_format)
        self.add_to_queue_button = QPushButton("Add to Queue")
        self.add_to_queue_button.setEnabled(False)
        self.download_now_button = QPushButton("Download Now")
        self.download_now_button.setObjectName("PrimaryButton")
        self.download_now_button.setEnabled(False)
        action_row.addWidget(QLabel("Quality:"))
        action_row.addWidget(self.quality_combo)
        action_row.addWidget(QLabel("Format:"))
        action_row.addWidget(self.format_combo)
        action_row.addStretch(1)
        action_row.addWidget(self.add_to_queue_button)
        action_row.addWidget(self.download_now_button)
        root.addLayout(action_row)

    def _connect(self) -> None:
        self.analyze_button.clicked.connect(self._on_analyze_clicked)
        self.url_input.returnPressed.connect(self._on_analyze_clicked)
        self.add_to_queue_button.clicked.connect(lambda: self._on_add(False))
        self.download_now_button.clicked.connect(lambda: self._on_add(True))
        self.bridge.metadata_ready.connect(self._on_metadata_ready)
        self.bridge.metadata_failed.connect(self._on_metadata_failed)

    # ------------------------------------------------------------------
    def _clear_meta(self) -> None:
        while self.meta_layout.count():
            child = self.meta_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_analyze_clicked(self) -> None:
        text = self.url_input.text().strip()
        if not text:
            return
        self._clear_meta()
        self._bundle = None
        self._selected_items = []
        self._rows = []
        self.add_to_queue_button.setEnabled(False)
        self.download_now_button.setEnabled(False)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_line = lines[0]

        if len(lines) > 1:
            # Batch mode: analyze each URL and enqueue results directly.
            self.status_label.setObjectName("Muted")
            self.status_label.setText(f"Analyzing {len(lines)} URLs in batch mode...")
            self._batch_urls = set(lines)
            self.bus.emit("analysis_started", text)
            for url in lines:
                self.analyzer.analyze(url)
            return

        self._batch_urls = set()
        self.status_label.setObjectName("Muted")
        self.status_label.setText(f"Analyzing {first_line} ...")
        self.bus.emit("analysis_started", text)
        self.analyzer.analyze(first_line)

    def _on_metadata_ready(self, url: str, bundle: MediaBundle) -> None:
        if url in getattr(self, "_batch_urls", set()):
            self._enqueue_bundle(bundle)
            remaining = len(self._batch_urls) - 1
            self.status_label.setText(
                f"Batch: {url} added. {remaining} URL(s) remaining..."
            )
            return
        if self._analyzed_url != url:
            return
        self._bundle = bundle
        self._build_meta(bundle)
        self.status_label.setText("")
        self._populate_selection(bundle)
        self.add_to_queue_button.setEnabled(True)
        self.download_now_button.setEnabled(True)

    def _enqueue_bundle(self, bundle: MediaBundle) -> None:
        items = bundle.items if bundle.is_collection else ([bundle.item] if bundle.item else [])
        queue_items = [self._build_queue_item(item) for item in items]
        self.manager.enqueue_many(queue_items)
        self.manager.start()

    def _on_metadata_failed(self, url: str, message: str) -> None:
        if url in getattr(self, "_batch_urls", set()):
            self.status_label.setObjectName("Error")
            self.status_label.setText(f"Batch: {url} failed ({message})")
            return
        if self._analyzed_url != url:
            return
        self.status_label.setObjectName("Error")
        self.status_label.setText(f"Analysis failed: {message}")
        self.add_to_queue_button.setEnabled(False)
        self.download_now_button.setEnabled(False)

    @property
    def _analyzed_url(self) -> str:
        return self.url_input.text().splitlines()[0].strip() if self.url_input.text() else ""

    # ------------------------------------------------------------------
    def _build_meta(self, bundle: MediaBundle) -> None:
        self._clear_meta()
        meta = self.meta_layout

        platform_label = QLabel(
            f"Platform: {bundle.platform.display_name}    "
            f"Type: {bundle.content_type.value.capitalize()}    "
            f"Videos: {bundle.count}"
        )
        platform_label.setObjectName("Muted")
        meta.addWidget(platform_label)

        title_label = QLabel(bundle.title or "Untitled")
        title_label.setObjectName("SectionTitle")
        title_label.setWordWrap(True)
        meta.addWidget(title_label)

        if bundle.creator:
            creator = QLabel(f"Creator: {bundle.creator}")
            creator.setWordWrap(True)
            meta.addWidget(creator)

    def _populate_selection(self, bundle: MediaBundle) -> None:
        for widget in self._rows:
            widget.deleteLater()
        self._rows.clear()

        items = bundle.items if bundle.is_collection else ([bundle.item] if bundle.item else [])
        if not items:
            return

        if bundle.is_collection:
            select_row = QHBoxLayout()
            self._select_all_check = QCheckBox("Select All")
            select_none = QPushButton("Select None")
            select_none.setFlat(True)

            def _set_all(state: int) -> None:
                checked = state == Qt.CheckState.Checked.value
                for row in self._rows:
                    row.check.setChecked(checked)

            self._select_all_check.stateChanged.connect(_set_all)
            select_row.addWidget(self._select_all_check)
            select_row.addWidget(select_none)
            select_none.clicked.connect(lambda: self._select_all_check.setChecked(False))
            self.meta_layout.addLayout(select_row)

        for item in items:
            row = _SelectableRow(item)
            if item.index is not None:
                row.set_index_text(f"{item.index:03d}")
            row.check.setChecked(True)
            self.meta_layout.addWidget(row)
            self._rows.append(row)
        self.meta_layout.addStretch(1)

    # ------------------------------------------------------------------
    def _selected(self) -> list[MediaItem]:
        return [row.item for row in self._rows if row.check.isChecked()]

    def _build_filename(self, item: MediaItem, ext: str) -> str:
        template = self.settings.settings.general.filename_template
        date_str = item.upload_date.strftime("%Y-%m-%d") if item.upload_date else None
        return build_filename(
            template,
            title=item.title,
            creator=item.creator or item.uploader,
            index=item.index,
            date_str=date_str,
            ext=ext,
        )

    def _output_dir(self, item: MediaItem) -> Path:
        base = Path(self.settings.ensure_download_folder())
        structure = self.settings.settings.general.folder_structure
        parts: list[str] = []
        for token in structure.split("/"):
            token = token.strip()
            if not token:
                continue
            if token == "platform":
                parts.append(item.platform.display_name)
            elif token == "creator":
                name = item.creator or item.uploader or "Unknown"
                parts.append(self._safe(name))
            elif token == "playlist":
                name = item.playlist_title or "Playlist"
                parts.append(self._safe(name))
            elif token == "year":
                if item.upload_date:
                    parts.append(str(item.upload_date.year))
            elif token == "month":
                if item.upload_date:
                    parts.append(f"{item.upload_date.month:02d}")
        if parts:
            return base.joinpath(*parts)
        return base

    @staticmethod
    def _safe(name: str) -> str:
        from ..utils.filenames import sanitize_filename

        return sanitize_filename(name)

    def _build_queue_item(self, item: MediaItem) -> QueueItem:
        ext = self.format_combo.currentText().lower()
        filename = self._build_filename(item, ext)
        options = DownloadOptions(
            url=item.url,
            quality=self.quality_combo.currentText(),
            output_format=self.format_combo.currentText(),
            output_dir=str(self._output_dir(item)),
            filename=filename,
            embed_metadata=self.settings.settings.video.embed_metadata,
            skip_existing=self.settings.settings.general.skip_existing,
        )
        return QueueItem(media=item, options=options)

    def _on_add(self, download_now: bool) -> None:
        selected = self._selected()
        if not selected:
            return
        items = [self._build_queue_item(item) for item in selected]
        self.manager.enqueue_many(items)
        if download_now:
            self.manager.start()
        self.status_label.setText(f"Added {len(items)} item(s) to the queue.")