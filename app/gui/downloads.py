"""Queue page: shows live download queue with controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.bus import EventBus
from ..core.models import ItemStatus
from ..download.manager import DownloadManager
from ..utils.humanize import format_bytes, format_eta, format_speed
from .bridge import BusBridge

log = logging.getLogger(__name__)

_STATUS_LABEL = {
    ItemStatus.PENDING: "Waiting",
    ItemStatus.QUEUED: "Queued",
    ItemStatus.ANALYZING: "Analyzing",
    ItemStatus.DOWNLOADING: "Downloading",
    ItemStatus.PROCESSING: "Processing",
    ItemStatus.VALIDATING: "Validating",
    ItemStatus.PAUSED: "Paused",
    ItemStatus.COMPLETED: "Completed",
    ItemStatus.FAILED: "Failed",
    ItemStatus.CANCELLED: "Cancelled",
    ItemStatus.SKIPPED: "Skipped",
}

_DASH = "—"


def _reset_row(table, row) -> None:
    """Clear progress bar and speed/eta/size cells for an inactive row."""
    bar = table.cellWidget(row, 3)
    if bar is not None:
        bar.setValue(0)
    for col in (4, 5, 6):
        item = table.item(row, col)
        if item is not None:
            item.setText(_DASH)


class QueuePage(QWidget):
    def __init__(
        self,
        manager: DownloadManager,
        bus: EventBus,
        bridge: BusBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.bus = bus
        self.bridge = bridge
        self._rows: dict[str, int] = {}
        self._build_ui()
        self._connect()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Download Queue")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        root.addLayout(header)

        controls = QHBoxLayout()
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.retry_button = QPushButton("Retry Failed")
        self.clear_button = QPushButton("Clear Completed")
        self.cancel_all_button = QPushButton("Cancel All")
        self.cancel_all_button.setObjectName("DangerButton")
        for b in (
            self.pause_button,
            self.resume_button,
            self.retry_button,
            self.clear_button,
            self.cancel_all_button,
        ):
            controls.addWidget(b)
        controls.addStretch(1)
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        root.addLayout(controls)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Platform", "Status", "Progress", "Speed", "ETA", "Size"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 7):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents
            )
        root.addWidget(self.table, 1)

        # context actions for selected rows
        bottom = QHBoxLayout()
        self.selected_cancel_button = QPushButton("Cancel Selected")
        self.selected_retry_button = QPushButton("Retry Selected")
        self.selected_remove_button = QPushButton("Remove Selected")
        bottom.addWidget(self.selected_cancel_button)
        bottom.addWidget(self.selected_retry_button)
        bottom.addWidget(self.selected_remove_button)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.empty_label = QLabel(
            "No downloads yet. Paste a URL on the Dashboard to get started."
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_label)

    def _connect(self) -> None:
        self.pause_button.clicked.connect(lambda: (self.manager.pause(), self._sync_controls()))
        self.resume_button.clicked.connect(lambda: (self.manager.resume(), self._sync_controls()))
        self.retry_button.clicked.connect(self.manager.retry_all_failed)
        self.clear_button.clicked.connect(self.manager.clear_completed)
        self.cancel_all_button.clicked.connect(self._cancel_all)
        self.selected_cancel_button.clicked.connect(self._cancel_selected)
        self.selected_retry_button.clicked.connect(self._retry_selected)
        self.selected_remove_button.clicked.connect(self._remove_selected)

        self.bridge.download_progress.connect(self._on_progress)
        self.bridge.download_status.connect(self._on_status)
        self.bridge.download_completed.connect(self._on_completed)
        self.bridge.download_failed.connect(self._on_failed)
        self.bridge.queue_changed.connect(self._on_queue_changed)

    # ------------------------------------------------------------------
    def _sync_controls(self) -> None:
        paused = self.manager.is_paused
        has_items = self.manager.queue.count() > 0
        self.pause_button.setEnabled(has_items and not paused)
        self.resume_button.setEnabled(has_items and paused)

    def _row_for(self, uid: str) -> int:
        if uid in self._rows:
            return self._rows[uid]
        item = self.manager.queue.get(uid)
        if item is None:
            return -1
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col in range(7):
            self.table.setItem(row, col, QTableWidgetItem(""))
        title_item = QTableWidgetItem(item.title)
        context = []
        if item.collection_url:
            context.append(f"Collection: {item.collection_name or item.collection_url}")
        if item.account:
            context.append(f"Account: {item.account}")
        if item.series:
            context.append(f"Series: {item.series}")
        if context:
            title_item.setToolTip("\n".join(context))
        self.table.setItem(row, 0, title_item)
        self.table.setItem(row, 1, QTableWidgetItem(item.platform))
        self.table.setCellWidget(row, 3, QProgressBar())
        self.table.cellWidget(row, 3).setRange(0, 1000)
        self._rows[uid] = row
        return row

    def _set_text(self, uid: str, col: int, text: str) -> None:
        row = self._row_for(uid)
        if row < 0:
            return
        self.table.item(row, col).setText(text)

    def _on_progress(self, uid, percent, speed, eta, downloaded, total, status) -> None:
        row = self._row_for(uid)
        if row < 0:
            return
        bar = self.table.cellWidget(row, 3)
        bar.setValue(int(percent * 10))
        label = _STATUS_LABEL.get(ItemStatus(status), status)
        self.table.item(row, 2).setText(label)
        self.table.item(row, 4).setText(format_speed(speed) if speed else _DASH)
        self.table.item(row, 5).setText(format_eta(eta) if eta else _DASH)
        size_text = (
            f"{format_bytes(downloaded)} / {format_bytes(total)}"
            if total
            else format_bytes(downloaded) if downloaded else _DASH
        )
        self.table.item(row, 6).setText(size_text)

    def _on_status(self, uid, status) -> None:
        row = self._row_for(uid)
        if row < 0:
            return
        status = ItemStatus(status)
        self.table.item(row, 2).setText(_STATUS_LABEL.get(status, str(status)))
        bar = self.table.cellWidget(row, 3)
        if status == ItemStatus.COMPLETED:
            bar.setValue(1000)
        elif status in (
            ItemStatus.QUEUED,
            ItemStatus.PENDING,
            ItemStatus.FAILED,
            ItemStatus.CANCELLED,
            ItemStatus.SKIPPED,
        ):
            _reset_row(self.table, row)
        self._sync_controls()

    def _on_completed(self, uid, path, size) -> None:
        row = self._row_for(uid)
        if row >= 0:
            self.table.item(row, 2).setText("Completed")
            self.table.item(row, 4).setText(_DASH)
            self.table.item(row, 5).setText(_DASH)
            if size:
                self.table.item(row, 6).setText(format_bytes(size))
            bar = self.table.cellWidget(row, 3)
            bar.setValue(1000)
            self.table.item(row, 1).setToolTip(path)
        self.empty_label.setText("")
        self._sync_controls()

    def _on_failed(self, uid, message, detail) -> None:
        row = self._row_for(uid)
        if row >= 0:
            self.table.item(row, 2).setText("Failed")
            self.table.item(row, 2).setToolTip(message)
            _reset_row(self.table, row)
            self.table.item(row, 6).setText(_DASH)
        self._sync_controls()

    def _on_queue_changed(self, items) -> None:
        self._sync_controls()
        if not items:
            self.empty_label.setText(
                "No downloads yet. Paste a URL on the Dashboard to get started."
            )

    # ------------------------------------------------------------------
    def _selected_uids(self) -> list[str]:
        uids: list[str] = []
        for row in self.table.selectionModel().selectedRows():
            for uid, idx in self._rows.items():
                if idx == row.row():
                    uids.append(uid)
                    break
        return uids

    def _cancel_all(self) -> None:
        for item in self.manager.queue.all():
            self.manager.cancel(item.uid)

    def _cancel_selected(self) -> None:
        for uid in self._selected_uids():
            self.manager.cancel(uid)

    def _retry_selected(self) -> None:
        for uid in self._selected_uids():
            self.manager.retry(uid)

    def _remove_selected(self) -> None:
        for uid in self._selected_uids():
            row = self._rows.pop(uid, None)
            item = self.manager.queue.remove(uid)
            if row is not None:
                self.table.removeRow(row)
                self._rebuild_rows_index()
        self.bus.emit("queue_changed", self.manager.queue.all())

    def _rebuild_rows_index(self) -> None:
        new_index: dict[str, int] = {}
        for uid, old_row in self._rows.items():
            new_index[uid] = old_row
        # rows after removal keep their indexes (removeRow shifts them down)
        for uid in list(self._rows):
            if self._rows[uid] >= self.table.rowCount():
                del self._rows[uid]