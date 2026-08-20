"""Dialog showing full download error details.

Shows a human-readable message by default. Technical details are revealed
only when the user chooses (Debug Mode / "Show Technical Details"), and the
raw traceback stays in the log files.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ErrorDetailsDialog(QDialog):
    """Display structured error information for a failed download."""

    def __init__(
        self,
        title: str,
        platform: str,
        url: str,
        stage: str,
        error_type: str,
        message: str,
        detail: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._platform = platform
        self._url = url
        self._stage = stage
        self._error_type = error_type
        self._message = message
        self._detail = detail or ""
        self.setWindowTitle("Download Error Details")
        self.setMinimumSize(560, 440)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        def add_row(label: str, value: str) -> None:
            header = QLabel(f"<b>{label}:</b>")
            header.setTextFormat(Qt.RichText)
            value_label = QLabel(value or "—")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value_label.setWordWrap(True)
            root.addWidget(header)
            root.addWidget(value_label)

        add_row("Title", self._title)
        add_row("Platform", self._platform)
        add_row("URL", self._url)
        add_row("Stage", self._stage or "Unknown")
        add_row("Error Type", self._error_type or "Error")
        add_row("Message", self._message)

        self.tech_header = QLabel("<b>Technical Details</b>")
        self.tech_header.setTextFormat(Qt.RichText)
        self.tech_text = QPlainTextEdit()
        self.tech_text.setReadOnly(True)
        self.tech_text.setPlainText(self._detail or "No additional technical details recorded.")
        self.tech_text.setMaximumHeight(130)
        root.addWidget(self.tech_header)
        root.addWidget(self.tech_text)

        self._apply_debug_visibility()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.copy_button = QPushButton("Copy Error")
        self.copy_button.clicked.connect(self._copy)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

    def _apply_debug_visibility(self) -> None:
        debug = False
        try:
            from ..config.settings import SettingsManager

            debug = SettingsManager().settings.advanced.debug_mode
        except Exception:  # noqa: BLE001
            debug = False
        self.tech_header.setVisible(debug)
        self.tech_text.setVisible(debug)

    def _copy(self) -> None:
        text = (
            f"Title: {self._title}\n"
            f"Platform: {self._platform}\n"
            f"URL: {self._url}\n"
            f"Stage: {self._stage or 'Unknown'}\n"
            f"Error Type: {self._error_type or 'Error'}\n"
            f"Message: {self._message}\n"
            f"Technical Details:\n{self._detail}"
        )
        QGuiApplication.clipboard().setText(text)
