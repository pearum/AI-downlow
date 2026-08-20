"""Settings page: edit and persist application settings.

Layout is built with QScrollArea + QFormLayout so every row keeps a stable
label/field alignment and the page remains usable at any window size.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config.settings import AppSettings, SettingsManager
from ..download.ffmpeg import ffmpeg_status, find_ffmpeg

_INPUT_HEIGHT = 32
_LABEL_MIN_WIDTH = 190


def _make_form(parent: QWidget) -> QFormLayout:
    form = QFormLayout(parent)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(12)
    return form


def _make_input(widget: QWidget) -> QWidget:
    widget.setMinimumHeight(_INPUT_HEIGHT)
    widget.setMinimumWidth(220)
    return widget


class SettingsPage(QWidget):
    def __init__(self, settings_manager: SettingsManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = settings_manager
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        title = QLabel("Settings")
        title.setObjectName("Title")
        root.addWidget(title)

        # --- General ---------------------------------------------------
        general = QGroupBox("General")
        gform = _make_form(general)
        self.download_folder = QLineEdit()
        _make_input(self.download_folder)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.setMinimumHeight(_INPUT_HEIGHT)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.download_folder, 1)
        folder_row.addWidget(self.browse_button)
        gform.addRow("Download Folder:", folder_row)

        self.filename_template = QLineEdit()
        _make_input(self.filename_template)
        gform.addRow("Filename Template:", self.filename_template)
        hint = QLabel("Available placeholders: {title} {creator} {index} {date}")
        hint.setObjectName("Muted")
        gform.addRow("", hint)

        self.folder_structure = QLineEdit()
        _make_input(self.folder_structure)
        self.folder_structure.setPlaceholderText("e.g. platform/creator/playlist")
        gform.addRow("Folder Structure:", self.folder_structure)

        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 16)
        self.concurrent.setMinimumHeight(_INPUT_HEIGHT)
        gform.addRow("Concurrent Downloads:", self.concurrent)

        self.auto_resume = QCheckBox("Automatically resume paused downloads")
        self.auto_resume.setMinimumHeight(_INPUT_HEIGHT)
        gform.addRow("Auto Resume:", self.auto_resume)

        self.skip_existing = QCheckBox("Skip files that already exist")
        self.skip_existing.setMinimumHeight(_INPUT_HEIGHT)
        gform.addRow("Skip Existing Files:", self.skip_existing)
        root.addWidget(general)

        # --- Video -----------------------------------------------------
        video = QGroupBox("Video")
        vform = _make_form(video)
        self.default_quality = QComboBox()
        self.default_quality.addItems(
            ["Best Available", "2160p", "1440p", "1080p", "720p", "480p", "360p", "Audio Only"]
        )
        _make_input(self.default_quality)
        vform.addRow("Default Quality:", self.default_quality)
        self.default_format = QComboBox()
        self.default_format.addItems(["MP4", "WebM", "MKV", "MP3", "M4A"])
        _make_input(self.default_format)
        vform.addRow("Default Format:", self.default_format)
        self.embed_metadata = QCheckBox("Embed metadata into files (where allowed)")
        self.embed_metadata.setMinimumHeight(_INPUT_HEIGHT)
        vform.addRow("Embed Metadata:", self.embed_metadata)
        root.addWidget(video)

        # --- Network ---------------------------------------------------
        network = QGroupBox("Network")
        nform = _make_form(network)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setSuffix(" s")
        self.timeout.setMinimumHeight(_INPUT_HEIGHT)
        nform.addRow("Timeout:", self.timeout)
        self.retry_count = QSpinBox()
        self.retry_count.setRange(0, 10)
        self.retry_count.setMinimumHeight(_INPUT_HEIGHT)
        nform.addRow("Retry Count:", self.retry_count)
        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setMinimumHeight(_INPUT_HEIGHT)
        nform.addRow("Concurrent Connections:", self.connections)
        root.addWidget(network)

        # --- Appearance ------------------------------------------------
        appearance = QGroupBox("Appearance")
        aform = _make_form(appearance)
        self.theme = QComboBox()
        self.theme.addItems(["dark", "light", "system"])
        _make_input(self.theme)
        aform.addRow("Theme:", self.theme)
        root.addWidget(appearance)

        # --- Advanced --------------------------------------------------
        advanced = QGroupBox("Advanced")
        adv = _make_form(advanced)
        self.ffmpeg_path = QLineEdit()
        _make_input(self.ffmpeg_path)
        self.ffmpeg_browse = QPushButton("Browse...")
        self.ffmpeg_browse.setMinimumHeight(_INPUT_HEIGHT)
        self.ffmpeg_test = QPushButton("Test FFmpeg")
        self.ffmpeg_test.setMinimumHeight(_INPUT_HEIGHT)
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.setSpacing(8)
        ffmpeg_row.addWidget(self.ffmpeg_path, 1)
        ffmpeg_row.addWidget(self.ffmpeg_browse)
        ffmpeg_row.addWidget(self.ffmpeg_test)
        adv.addRow("FFmpeg Path:", ffmpeg_row)
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setObjectName("Muted")
        self.ffmpeg_status.setWordWrap(True)
        adv.addRow("", self.ffmpeg_status)

        self.log_level = QComboBox()
        self.log_level.addItems(["INFO", "DEBUG", "WARNING", "ERROR"])
        _make_input(self.log_level)
        adv.addRow("Log Level:", self.log_level)
        self.debug_mode = QCheckBox("Enable debug mode (shows technical details)")
        self.debug_mode.setMinimumHeight(_INPUT_HEIGHT)
        adv.addRow("Debug Mode:", self.debug_mode)
        root.addWidget(advanced)

        # --- actions ---------------------------------------------------
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.setMinimumHeight(_INPUT_HEIGHT)
        self.reset_button = QPushButton("Reset to Defaults")
        self.reset_button.setMinimumHeight(_INPUT_HEIGHT)
        actions.addStretch(1)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        root.addStretch(1)

        self.browse_button.clicked.connect(self._browse_folder)
        self.ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_test.clicked.connect(self._test_ffmpeg)
        self.save_button.clicked.connect(self._save)
        self.reset_button.clicked.connect(self._reset)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        s: AppSettings = self.manager.settings
        self.download_folder.setText(s.general.download_folder)
        self.filename_template.setText(s.general.filename_template)
        self.folder_structure.setText(s.general.folder_structure)
        self.concurrent.setValue(s.general.concurrent_downloads)
        self.auto_resume.setChecked(s.general.auto_resume)
        self.skip_existing.setChecked(s.general.skip_existing)
        self.default_quality.setCurrentText(s.video.default_quality)
        self.default_format.setCurrentText(s.video.default_format)
        self.embed_metadata.setChecked(s.video.embed_metadata)
        self.timeout.setValue(s.network.timeout)
        self.retry_count.setValue(s.network.retry_count)
        self.connections.setValue(s.network.concurrent_connections)
        self.theme.setCurrentText(s.appearance.theme)
        self.ffmpeg_path.setText(s.advanced.ffmpeg_path)
        self.log_level.setCurrentText(s.advanced.log_level)
        self.debug_mode.setChecked(s.advanced.debug_mode)
        self._refresh_ffmpeg_status(s.advanced.ffmpeg_path)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder")
        if folder:
            self.download_folder.setText(folder)

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ffmpeg executable", "", "ffmpeg (*.exe);;All Files (*)"
        )
        if path:
            self.ffmpeg_path.setText(path)

    def _refresh_ffmpeg_status(self, configured: str = "") -> None:
        ok, path, version = ffmpeg_status(configured)
        if ok:
            self.ffmpeg_status.setObjectName("Success")
            self.ffmpeg_status.setText(
                f"FFmpeg detected — Version: {version}\nPath: {path}"
            )
        else:
            self.ffmpeg_status.setObjectName("Error")
            self.ffmpeg_status.setText(
                "FFmpeg not found.\n\n"
                "Please install FFmpeg (https://ffmpeg.org/download.html) or "
                "select ffmpeg.exe, then click 'Test FFmpeg' again."
            )

    def _test_ffmpeg(self) -> None:
        self._refresh_ffmpeg_status(self.ffmpeg_path.text().strip())

    def _save(self) -> None:
        self._persist()
        QMessageBox.information(self, "Settings", "Settings saved.")

    def _persist(self) -> None:
        self.manager.update(
            general={
                "download_folder": self.download_folder.text().strip(),
                "filename_template": self.filename_template.text().strip()
                or "{creator} - {title}",
                "folder_structure": self.folder_structure.text().strip(),
                "concurrent_downloads": self.concurrent.value(),
                "auto_resume": self.auto_resume.isChecked(),
                "skip_existing": self.skip_existing.isChecked(),
            },
            video={
                "default_quality": self.default_quality.currentText(),
                "default_format": self.default_format.currentText(),
                "embed_metadata": self.embed_metadata.isChecked(),
            },
            network={
                "timeout": self.timeout.value(),
                "retry_count": self.retry_count.value(),
                "concurrent_connections": self.connections.value(),
            },
            appearance={"theme": self.theme.currentText()},
            advanced={
                "ffmpeg_path": self.ffmpeg_path.text().strip(),
                "log_level": self.log_level.currentText(),
                "debug_mode": self.debug_mode.isChecked(),
            },
        )
        self.manager.ensure_download_folder()

    def _reset(self) -> None:
        self.manager._settings = AppSettings()
        self.manager.save()
        self._load()