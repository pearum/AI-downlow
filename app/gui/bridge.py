"""Bridge that forwards EventBus events (worker threads) to Qt signals (main thread)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..core.bus import EventBus


class BusBridge(QObject):
    """Re-emits bus events as Qt signals; safe for GUI thread updates."""

    download_progress = Signal(str, float, object, object, int, int, str)
    download_status = Signal(str, object)
    download_completed = Signal(str, str, int)  # uid, path, verified file size
    download_failed = Signal(str, str, str)
    queue_changed = Signal(object)
    metadata_ready = Signal(str, object)
    metadata_failed = Signal(str, str)
    accounts_changed = Signal()

    collection_scan_started = Signal(str)
    collection_scan_progress = Signal(str, int, int, float)
    collection_scan_ready = Signal(str, object)
    collection_scan_failed = Signal(str, str)
    collection_items_ready = Signal(str, object)
    collection_progress = Signal(str, object)
    collection_finished = Signal(str, object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.bus = bus

    def start(self) -> None:
        bus = self.bus
        bus.connect("download_progress", self._on_progress)
        bus.connect("download_status", self._on_status)
        bus.connect("download_completed", self._on_completed)
        bus.connect("download_failed", self._on_failed)
        bus.connect("queue_changed", self._on_queue_changed)
        bus.connect("metadata_ready", self._on_metadata_ready)
        bus.connect("metadata_failed", self._on_metadata_failed)
        bus.connect("accounts_changed", self._on_accounts_changed)
        bus.connect("collection_scan_started", self._on_collection_scan_started)
        bus.connect("collection_scan_progress", self._on_collection_scan_progress)
        bus.connect("collection_scan_ready", self._on_collection_scan_ready)
        bus.connect("collection_scan_failed", self._on_collection_scan_failed)
        bus.connect("collection_items_ready", self._on_collection_items_ready)
        bus.connect("collection_progress", self._on_collection_progress)
        bus.connect("collection_finished", self._on_collection_finished)

    def _on_progress(self, *args) -> None:
        self.download_progress.emit(*args)

    def _on_status(self, *args) -> None:
        self.download_status.emit(*args)

    def _on_completed(self, *args) -> None:
        self.download_completed.emit(*args)

    def _on_failed(self, *args) -> None:
        self.download_failed.emit(*args)

    def _on_queue_changed(self, *args) -> None:
        self.queue_changed.emit(*args)

    def _on_metadata_ready(self, *args) -> None:
        self.metadata_ready.emit(*args)

    def _on_metadata_failed(self, *args) -> None:
        self.metadata_failed.emit(*args)

    def _on_accounts_changed(self, *args) -> None:
        self.accounts_changed.emit()

    def _on_collection_scan_started(self, *args) -> None:
        self.collection_scan_started.emit(*args)

    def _on_collection_scan_progress(self, *args) -> None:
        self.collection_scan_progress.emit(*args)

    def _on_collection_scan_ready(self, *args) -> None:
        self.collection_scan_ready.emit(*args)

    def _on_collection_scan_failed(self, *args) -> None:
        self.collection_scan_failed.emit(*args)

    def _on_collection_items_ready(self, *args) -> None:
        self.collection_items_ready.emit(*args)

    def _on_collection_progress(self, *args) -> None:
        self.collection_progress.emit(*args)

    def _on_collection_finished(self, *args) -> None:
        self.collection_finished.emit(*args)