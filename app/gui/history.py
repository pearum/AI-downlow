"""History page: read-only view over the download history database.

Failed items expose the complete error through:
- a tooltip on the Error cell
- double-click on a row → Error Details dialog
- right-click context menu → Copy Error
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..database.history import HistoryDatabase, HistoryEntry
from ..utils.humanize import format_bytes
from .error_dialog import ErrorDetailsDialog


class HistoryPage(QWidget):
    def __init__(self, db: HistoryDatabase, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self._entries: list[HistoryEntry] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)

        title = QLabel("History")
        title.setObjectName("Title")
        root.addWidget(title)

        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by title, URL, or creator...")
        self.search_button = QPushButton("Search")
        self.refresh_button = QPushButton("Refresh")
        self.clear_button = QPushButton("Clear History")
        self.clear_button.setObjectName("DangerButton")
        controls.addWidget(self.search_input, 1)
        controls.addWidget(self.search_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        controls.addWidget(self.clear_button)
        root.addLayout(controls)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Title", "Platform", "Quality", "Format", "Status", "Date", "File", "Error"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)   # Title
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Platform
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Quality
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Format
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Status
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Date
        header.setSectionResizeMode(6, QHeaderView.Stretch)   # File
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)  # Error
        header.setMinimumSectionSize(120)
        self.table.setColumnWidth(7, 260)  # visible error text
        root.addWidget(self.table, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        root.addWidget(self.count_label)

        self.search_button.clicked.connect(self._on_search)
        self.refresh_button.clicked.connect(self._on_search)
        self.search_input.returnPressed.connect(self._on_search)
        self.clear_button.clicked.connect(self._on_clear)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self._on_search()

    # ------------------------------------------------------------------
    def _on_search(self) -> None:
        term = self.search_input.text().strip()
        if term:
            self._entries = self.db.search(term)
        else:
            self._entries = self.db.list(limit=500)
        self._populate()

    def _on_clear(self) -> None:
        self.db.clear()
        self._on_search()

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for entry in self._entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            file_text = entry.file_path or ""
            if entry.file_path and entry.file_size:
                file_text = f"{entry.file_path}  ({format_bytes(entry.file_size)})"
            for col, text in (
                (0, entry.title),
                (1, entry.platform),
                (2, entry.resolution),
                (3, entry.format),
                (4, entry.status),
                (5, entry.date_downloaded),
                (6, file_text),
                (7, entry.error_message or ""),
            ):
                item = QTableWidgetItem(str(text))
                if col == 7 and entry.error_message:
                    item.setForeground(Qt.red)
                    item.setToolTip(entry.error_message)
                if col == 6 and entry.file_path:
                    item.setToolTip(entry.file_path)
                self.table.setItem(row, col, item)
        self.count_label.setText(f"{len(self._entries)} record(s)")

    # ------------------------------------------------------------------
    def _entry_at(self, row: int) -> HistoryEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        entry = self._entry_at(item.row())
        if entry is None or entry.status != "failed":
            return
        self._show_error_dialog(entry)

    def _show_error_dialog(self, entry: HistoryEntry) -> None:
        dialog = ErrorDetailsDialog(
            title=entry.title,
            platform=entry.platform,
            url=entry.url,
            stage=entry.error_stage or "",
            error_type=entry.error_type or "",
            message=entry.error_message or "",
            detail=entry.error_detail or entry.error_message or "",
            parent=self,
        )
        dialog.exec()

    def _on_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if item is None:
            return
        entry = self._entry_at(item.row())
        if entry is None:
            return
        menu = QMenu(self)
        copy_error = menu.addAction("Copy Error")
        show_details = menu.addAction("Show Error Details")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == copy_error and entry.error_message:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(entry.error_message)
        elif action == show_details:
            self._show_error_dialog(entry)

    def refresh(self) -> None:
        self._on_search()