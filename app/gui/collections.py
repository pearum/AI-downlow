"""Collections page: analyze accounts / series / playlists and download in bulk.

The page drives the CollectionEngine (background scan + queue) and renders
discovered items in a selectable table. Nothing here performs network I/O on
the GUI thread.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..collections.base import (
    COLLECTION_PAGE_SIZE,
    CollectionInfo,
    CollectionItem,
    CollectionItemStatus,
    CollectionMode,
)
from ..collections.engine import CollectionEngine
from ..config.constants import SUPPORTED_PROVIDERS
from ..core.bus import EventBus
from ..utils.humanize import format_duration
from .bridge import BusBridge

log = logging.getLogger(__name__)

_MODE_RADIOS = [
    ("Single Video", CollectionMode.SINGLE),
    ("Account", CollectionMode.ACCOUNT),
    ("Series / Folder", CollectionMode.SERIES),
    ("Playlist / List", CollectionMode.PLAYLIST),
]

_STATUS_LABEL = {
    CollectionItemStatus.READY: "Ready",
    CollectionItemStatus.QUEUED: "Queued",
    CollectionItemStatus.DOWNLOADING: "Downloading",
    CollectionItemStatus.COMPLETED: "Completed",
    CollectionItemStatus.SKIPPED: "Skipped",
    CollectionItemStatus.FAILED: "Failed",
    CollectionItemStatus.CANCELLED: "Cancelled",
}


class CollectionsPage(QWidget):
    def __init__(
        self,
        engine: CollectionEngine,
        bus: EventBus,
        bridge: BusBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.bus = bus
        self.bridge = bridge
        self._info: Optional[CollectionInfo] = None
        self._page_items: list[CollectionItem] = []
        self._row_item: dict[int, CollectionItem] = {}
        self._build_ui()
        self._connect()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("Collections")
        title.setObjectName("Title")
        root.addWidget(title)

        # -- URL row ----------------------------------------------------
        url_row = QHBoxLayout()
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(
            [p.capitalize() for p in SUPPORTED_PROVIDERS]
        )
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Profile / Collection / Playlist URL — e.g. https://www.tiktok.com/@username"
        )
        self.url_input.setMinimumHeight(36)
        self.scan_button = QPushButton("Scan")
        self.scan_button.setMinimumHeight(36)
        url_row.addWidget(QLabel("Platform:"))
        url_row.addWidget(self.platform_combo)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.scan_button)
        root.addLayout(url_row)

        # -- mode radios ------------------------------------------------
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Download Mode:"))
        self._mode_group: list[QRadioButton] = []
        for label, mode in _MODE_RADIOS:
            radio = QRadioButton(label)
            radio.mode = mode  # type: ignore[attr-defined]
            self._mode_group.append(radio)
            mode_row.addWidget(radio)
        self._mode_group[0].setChecked(True)
        mode_row.addStretch(1)
        self.analyze_button = QPushButton("Analyze Collection")
        self.analyze_button.setObjectName("PrimaryButton")
        self.analyze_button.setMinimumHeight(36)
        mode_row.addWidget(self.analyze_button)
        root.addLayout(mode_row)

        # -- scan progress ----------------------------------------------
        self.scan_status = QLabel("")
        self.scan_status.setObjectName("Muted")
        self.scan_status.setWordWrap(True)
        root.addWidget(self.scan_status)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 1000)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(False)
        root.addWidget(self.scan_progress)

        self.info_label = QLabel("")
        self.info_label.setObjectName("SectionTitle")
        self.info_label.setWordWrap(True)
        root.addWidget(self.info_label)

        self.selection_label = QLabel("")
        self.selection_label.setObjectName("Muted")
        root.addWidget(self.selection_label)

        # -- items table ------------------------------------------------
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Select", "Index", "Title", "Account", "Series", "Duration", "Status"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        # -- selection buttons ------------------------------------------
        sel_row = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.select_none_button = QPushButton("Select None")
        self.invert_button = QPushButton("Invert Selection")
        self.load_more_button = QPushButton("Load More")
        self.load_more_button.setEnabled(False)
        for b in (
            self.select_all_button,
            self.select_none_button,
            self.invert_button,
            self.load_more_button,
        ):
            sel_row.addWidget(b)
        sel_row.addStretch(1)
        root.addLayout(sel_row)

        # -- action buttons ---------------------------------------------
        action_row = QHBoxLayout()
        self.add_selected_button = QPushButton("Add Selected to Queue")
        self.download_selected_button = QPushButton("Download Selected")
        self.download_selected_button.setObjectName("PrimaryButton")
        self.download_all_button = QPushButton("Download All")
        self.retry_failed_button = QPushButton("Retry Failed")
        for b in (
            self.add_selected_button,
            self.download_selected_button,
            self.download_all_button,
            self.retry_failed_button,
        ):
            b.setEnabled(False)
            action_row.addWidget(b)
        action_row.addStretch(1)
        root.addLayout(action_row)

        # -- resume row -------------------------------------------------
        resume_row = QHBoxLayout()
        resume_row.addWidget(QLabel("Resume:"))
        self.resume_combo = QComboBox()
        self.resume_combo.setMinimumWidth(300)
        self.resume_button = QPushButton("Resume Collection")
        resume_row.addWidget(self.resume_combo, 1)
        resume_row.addWidget(self.resume_button)
        root.addLayout(resume_row)

        # -- report bar -------------------------------------------------
        self.report_bar = QFrame()
        self.report_bar.setVisible(False)
        report_layout = QHBoxLayout(self.report_bar)
        report_layout.setContentsMargins(0, 0, 0, 0)
        self.report_label = QLabel("")
        self.report_label.setObjectName("Success")
        self.report_label.setWordWrap(True)
        self.view_failed_button = QPushButton("View Failed")
        self.open_folder_button = QPushButton("Open Folder")
        self.report_retry_button = QPushButton("Retry Failed")
        report_layout.addWidget(self.report_label, 1)
        report_layout.addWidget(self.view_failed_button)
        report_layout.addWidget(self.open_folder_button)
        report_layout.addWidget(self.report_retry_button)
        root.addWidget(self.report_bar)

        root.addStretch(1)

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self.scan_button.clicked.connect(self._on_scan_clicked)
        self.analyze_button.clicked.connect(self._on_analyze_clicked)
        self.select_all_button.clicked.connect(self._on_select_all)
        self.select_none_button.clicked.connect(self._on_select_none)
        self.invert_button.clicked.connect(self._on_invert)
        self.load_more_button.clicked.connect(self._on_load_more)
        self.add_selected_button.clicked.connect(lambda: self._on_add(False))
        self.download_selected_button.clicked.connect(lambda: self._on_add(True))
        self.download_all_button.clicked.connect(self._on_download_all)
        self.retry_failed_button.clicked.connect(self._on_retry_failed)
        self.resume_button.clicked.connect(self._on_resume)
        self.report_retry_button.clicked.connect(self._on_retry_failed)
        self.view_failed_button.clicked.connect(self._on_view_failed)
        self.open_folder_button.clicked.connect(self._on_open_folder)

        self.bridge.collection_scan_started.connect(self._on_scan_started)
        self.bridge.collection_scan_progress.connect(self._on_scan_progress)
        self.bridge.collection_scan_ready.connect(self._on_scan_ready)
        self.bridge.collection_scan_failed.connect(self._on_scan_failed)
        self.bridge.collection_finished.connect(self._on_collection_finished)
        self.bridge.collection_progress.connect(self._on_collection_progress)

    # ------------------------------------------------------------------
    # URL / mode detection
    # ------------------------------------------------------------------
    def _current_url(self) -> str:
        return self.url_input.text().strip()

    def _selected_mode(self) -> Optional[CollectionMode]:
        for radio in self._mode_group:
            if radio.isChecked():
                return radio.mode  # type: ignore[attr-defined]
        return CollectionMode.SINGLE

    def _on_scan_clicked(self) -> None:
        url = self._current_url()
        if not url:
            return
        try:
            from ..providers.common.url_utils import detect_platform

            platform = detect_platform(url)
            if platform.value in SUPPORTED_PROVIDERS:
                self.platform_combo.setCurrentText(platform.display_name)
                self.scan_status.setText(
                    f"Detected: {platform.display_name}. Choose a download mode, "
                    "then click 'Analyze Collection'."
                )
            else:
                self.scan_status.setText(
                    "Platform not recognized. Supported: YouTube, TikTok, Facebook."
                )
        except Exception:  # noqa: BLE001
            self.scan_status.setText("Could not detect the platform from the URL.")

    def _on_analyze_clicked(self) -> None:
        url = self._current_url()
        if not url:
            self.scan_status.setText("Enter a profile / collection URL first.")
            return
        self._clear_items()
        self.engine.scan(url)

    # ------------------------------------------------------------------
    def _clear_items(self) -> None:
        self.table.setRowCount(0)
        self._page_items = []
        self._row_item = {}
        self._info = None
        self.info_label.setText("")
        self.selection_label.setText("")
        self.report_bar.setVisible(False)
        for button in (
            self.add_selected_button,
            self.download_selected_button,
            self.download_all_button,
            self.retry_failed_button,
        ):
            button.setEnabled(False)

    # ------------------------------------------------------------------
    # Scan events
    # ------------------------------------------------------------------
    def _on_scan_started(self, url: str) -> None:
        self.scan_progress.setVisible(True)
        self.scan_progress.setValue(0)
        self.scan_status.setObjectName("Muted")
        self.scan_status.setText("Scanning account...")
        self.analyze_button.setEnabled(False)

    def _on_scan_progress(self, url: str, found: int, processed: int, percent: float) -> None:
        self.scan_progress.setValue(int(percent * 10))
        if found:
            self.scan_status.setText(
                f"Items found: {found}    Processed: {processed} / {found}"
            )

    def _on_scan_ready(self, url: str, info: CollectionInfo) -> None:
        if url != self._current_url():
            return
        self.scan_progress.setVisible(False)
        self.scan_progress.setValue(0)
        self.analyze_button.setEnabled(True)
        self._info = info
        self._refresh_available_label()
        if info.account_username:
            self.info_label.setText(
                self.info_label.text() + f"   ·   @{info.account_username}"
            )
        if info.notice:
            self.scan_status.setObjectName("Muted")
            self.scan_status.setText(info.notice)
        else:
            self.scan_status.setText("")
        self._append_items(info.items)
        self.load_more_button.setEnabled(info.has_more)
        for button in (
            self.add_selected_button,
            self.download_selected_button,
            self.download_all_button,
            self.retry_failed_button,
        ):
            button.setEnabled(True)
        self._refresh_selection_label()

    def _on_scan_failed(self, url: str, message: str) -> None:
        if url != self._current_url():
            return
        self.scan_progress.setVisible(False)
        self.analyze_button.setEnabled(True)
        self.scan_status.setObjectName("Error")
        self.scan_status.setText(f"Analysis failed: {message}")
        self.info_label.setText("")

    # ------------------------------------------------------------------
    def _append_items(self, items: list[CollectionItem]) -> None:
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QCheckBox()
            check.setChecked(item.item_id in self.engine.selected_ids(self._current_url()))
            check.stateChanged.connect(
                lambda state, r=row: self._on_checkbox_toggled(r, state)
            )
            self.table.setCellWidget(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(self._index_text(item)))
            self.table.setItem(row, 2, QTableWidgetItem(item.title))
            self.table.setItem(row, 3, QTableWidgetItem(item.account_username or ""))
            self.table.setItem(row, 4, QTableWidgetItem(item.series_name or ""))
            self.table.setItem(row, 5, QTableWidgetItem(format_duration(item.duration)))
            status_item = QTableWidgetItem(_STATUS_LABEL.get(item.status, str(item.status)))
            if item.status == CollectionItemStatus.FAILED:
                status_item.setForeground(Qt.red)
                status_item.setToolTip(item.error_message)
            self.table.setItem(row, 6, status_item)
            self._row_item[row] = item
            self._page_items.append(item)

    @staticmethod
    def _index_text(item: CollectionItem) -> str:
        return f"{item.index:03d}" if item.index is not None else "—"

    def _on_checkbox_toggled(self, row: int, state: int) -> None:
        url = self._current_url()
        if not url:
            return
        item = self._row_item.get(row)
        if item is None:
            return
        selected = self.engine.selected_ids(url)
        if state == Qt.CheckState.Checked.value:
            selected.add(item.item_id)
        else:
            selected.discard(item.item_id)
        self.engine.set_selected(url, selected)
        self._refresh_selection_label()

    def _refresh_selection_label(self) -> None:
        url = self._current_url()
        if not url:
            return
        discovered = self.engine.discovered_items(url)
        selected = self.engine.selected_ids(url)
        self.selection_label.setText(
            f"Selected: {len(selected)} / {len(discovered)}"
        )

    def _on_select_all(self) -> None:
        url = self._current_url()
        if not url:
            return
        self.engine.select_all(url)
        for row, item in self._row_item.items():
            check = self.table.cellWidget(row, 0)
            if isinstance(check, QCheckBox):
                check.setChecked(True)
        self._refresh_selection_label()

    def _on_select_none(self) -> None:
        url = self._current_url()
        if not url:
            return
        self.engine.select_none(url)
        for row in range(self.table.rowCount()):
            check = self.table.cellWidget(row, 0)
            if isinstance(check, QCheckBox):
                check.setChecked(False)
        self._refresh_selection_label()

    def _on_invert(self) -> None:
        url = self._current_url()
        if not url:
            return
        self.engine.invert_selection(url)
        selected = self.engine.selected_ids(url)
        for row, item in self._row_item.items():
            check = self.table.cellWidget(row, 0)
            if isinstance(check, QCheckBox):
                check.setChecked(item.item_id in selected)
        self._refresh_selection_label()

    # ------------------------------------------------------------------
    def _on_load_more(self) -> None:
        url = self._current_url()
        if not url:
            return
        next_info = self.engine.load_more(url)
        if next_info:
            self._append_items(next_info.items)
            self.load_more_button.setEnabled(next_info.has_more)
            if self._info is not None:
                self._info = self.engine.current_info(url)
            self._refresh_available_label()
            self._refresh_selection_label()

    def _refresh_available_label(self) -> None:
        info = self._info
        if info is None:
            return
        available = info.accessible_items or info.discovered_items or len(info.items)
        text = (
            f"{info.name}   ·   {info.platform.display_name}   ·   "
            f"Available items: {available}"
        )
        if info.has_more:
            text += "   (more may be available — use Load More)"
        self.info_label.setText(text)

    # ------------------------------------------------------------------
    def _on_add(self, download_now: bool) -> None:
        url = self._current_url()
        if not url:
            return
        selected = self.engine.selected_ids(url)
        if not selected:
            return
        count = self.engine.add_to_queue(url, selected)
        if count:
            self.scan_status.setObjectName("Success")
            self.scan_status.setText(f"Added {count} item(s) to the download queue.")

    def _on_download_all(self) -> None:
        url = self._current_url()
        if not url:
            return
        count = self.engine.download_all(url)
        if count:
            self.scan_status.setObjectName("Success")
            self.scan_status.setText(f"Downloading all {count} item(s).")

    def _on_retry_failed(self) -> None:
        url = self._current_url()
        if not url:
            return
        count = self.engine.retry_failed(url)
        if count:
            self.scan_status.setObjectName("Success")
            self.scan_status.setText(f"Retrying {count} failed item(s).")
            self.report_bar.setVisible(False)
        else:
            self.scan_status.setText("No failed items to retry.")

    # ------------------------------------------------------------------
    def _on_resume(self) -> None:
        url = self.resume_combo.currentData()
        if not url:
            return
        self.url_input.setText(url)
        self._clear_items()
        info = self.engine.resume(url)
        if info is None:
            self.scan_status.setObjectName("Error")
            self.scan_status.setText("No saved collection found for that URL.")
            return
        self._info = info
        self.info_label.setText(
            f"{info.name}   ·   {info.platform.display_name}   ·   "
            f"Saved items: {info.accessible_items or info.discovered_items or len(info.items)}"
        )
        self._append_items(info.items)
        for button in (
            self.add_selected_button,
            self.download_selected_button,
            self.download_all_button,
            self.retry_failed_button,
        ):
            button.setEnabled(True)
        stats = self.engine.store.stats(url)
        self.scan_status.setText(
            f"Resumed. Completed: {stats['completed']}   "
            f"Skipped: {stats['skipped']}   Failed: {stats['failed']}   "
            f"Remaining: {stats['total'] - stats['completed'] - stats['skipped']}"
        )
        self._refresh_selection_label()

    def refresh_resume_combo(self) -> None:
        self.resume_combo.clear()
        for col in self.engine.store.list_collections():
            label = f"{col['name']}  ({col['platform']})"
            self.resume_combo.addItem(label, col["url"])

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _on_collection_progress(self, url: str, snapshot: dict) -> None:
        if url != self._current_url():
            return
        if not snapshot.get("finished"):
            return
        self._show_report(snapshot)

    def _on_collection_finished(self, url: str, snapshot: dict) -> None:
        if url != self._current_url():
            return
        self._show_report(snapshot)

    def _show_report(self, snapshot: dict) -> None:
        self.report_bar.setVisible(True)
        self.report_label.setText(
            "Collection Complete\n\n"
            f"Total:      {snapshot['total']}\n"
            f"Completed:  {snapshot['completed']}\n"
            f"Skipped:    {snapshot['skipped']}\n"
            f"Failed:     {snapshot['failed']}"
        )

    def _on_view_failed(self) -> None:
        url = self._current_url()
        if not url:
            return
        col = self.engine.store.get_collection(url)
        if col is None:
            return
        rows = self.engine.store.get_items_by_status(int(col["id"]), ["failed"])
        if not rows:
            QMessageBox.information(self, "Failed Items", "No failed items.")
            return
        lines = [f"{r.get('title')}: {r.get('error_message') or 'failed'}" for r in rows]
        QMessageBox.warning(
            self, "Failed Items", "\n".join(lines[:50]) if len(lines) > 50 else "\n".join(lines)
        )

    def _on_open_folder(self) -> None:
        url = self._current_url()
        if not url:
            return
        run = self.engine.run_for(url)
        folder = run.output_dir if run else ""
        if not folder:
            col = self.engine.store.get_collection(url)
            if col is None:
                return
            from ..core.models import Platform

            base = Path(self.engine.settings.ensure_download_folder())
            account = col.get("account_username") or "Unknown"
            folder = str(base / col["platform"].capitalize() / account)
        QDesktopServices.openUrl(Path(folder).as_uri())