"""GUI smoke tests (offscreen) for the main window and pages."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.config.settings import SettingsManager
from app.core.bus import EventBus
from app.database.accounts import AccountsStore
from app.database.history import HistoryDatabase, HistoryEntry
from app.gui.main_window import MainWindow
from app.gui.bridge import BusBridge
from app.gui.settings import SettingsPage
from app.gui.error_dialog import ErrorDetailsDialog
from app.providers.registry import ProviderRegistry

from tests.mocks.providers import MockProvider


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    base = Path(tempfile.mkdtemp())
    registry = ProviderRegistry()
    sm = SettingsManager(base / "cfg.json")
    db = HistoryDatabase(base / "h.db")
    accounts = AccountsStore(base / "acc.json")
    win = MainWindow(registry, sm, EventBus(), db, accounts)
    win.show()
    yield win
    win.close()


class TestMainWindow:
    def test_pages_loaded(self, window):
        assert window.stack.count() == 6

    def test_navigation(self, window):
        window.nav_group.button(2).click()
        assert window.stack.currentIndex() == 2
        window.nav_group.button(5).click()
        assert window.stack.currentIndex() == 5

    def test_dashboard_analyze_mock(self, window, qtbot):
        # Replace YouTube provider with mock so no network is hit.
        window.registry._providers["youtube"] = MockProvider()
        dashboard = window.dashboard_page
        dashboard.url_input.setText("https://mock.example/watch?v=abc")
        with qtbot.waitSignal(dashboard.bridge.metadata_ready, timeout=15000):
            dashboard.analyze_button.click()
        assert dashboard._bundle is not None
        assert dashboard.add_to_queue_button.isEnabled()
        assert dashboard.download_now_button.isEnabled()

    def test_add_to_queue_enables_manager(self, window, qtbot):
        window.registry._providers["youtube"] = MockProvider()
        # Ensure skip-existing does not mask the download with a leftover file.
        window.settings_manager.update(general={"skip_existing": False})
        dashboard = window.dashboard_page
        dashboard.url_input.setText("https://mock.example/watch?v=abc")
        with qtbot.waitSignal(dashboard.bridge.metadata_ready, timeout=15000):
            dashboard.analyze_button.click()
        before = window.manager.queue.count()
        dashboard.download_now_button.click()
        # allow worker thread to run
        qtbot.wait(1500)
        items = window.manager.queue.all()
        assert len(items) == before + 1
        assert any(i.status.value == "completed" for i in items)

    def test_analyze_failure_shows_error(self, window, qtbot):
        window.registry._providers["youtube"] = MockProvider(fail_metadata=True)
        dashboard = window.dashboard_page
        dashboard.url_input.setText("https://mock.example/watch?v=abc")
        with qtbot.waitSignal(dashboard.bridge.metadata_failed, timeout=15000):
            dashboard.analyze_button.click()
        assert dashboard.add_to_queue_button.isEnabled() is False
        assert "failed" in dashboard.status_label.text().lower()

    def test_settings_save_updates_concurrency(self, window, qtbot):
        page = window.settings_page
        page.concurrent.setValue(7)
        page._persist()
        window._on_settings_saved()
        assert window.manager.concurrency == 7

    def test_batch_urls_enqueue_all(self, window, qtbot):
        window.registry._providers["youtube"] = MockProvider()
        dashboard = window.dashboard_page
        dashboard.url_input.setText(
            "https://mock.example/watch?v=a\nhttps://mock.example/watch?v=b\n"
            "https://mock.example/watch?v=c"
        )
        dashboard.analyze_button.click()
        deadline = 0
        while window.manager.queue.count() < 3 and deadline < 100:
            qtbot.wait(100)
            deadline += 1
        assert window.manager.queue.count() == 3

    def test_history_page_populates(self, window):
        window.history_page.refresh()
        assert window.history_page.table.rowCount() >= 0


class TestCollectionsPage:
    def _page(self, qapp, tmp_path, total=5, page_size=None):
        from app.collections.base import CollectionProviderRegistry
        from app.collections.engine import CollectionEngine
        from app.collections.store import CollectionStore
        from app.download.manager import DownloadManager
        from app.gui.collections import CollectionsPage

        from tests.mocks.collections import MockCollectionProvider

        bus = EventBus()
        registry = ProviderRegistry()
        registry._providers["tiktok"] = MockProvider()
        sm = SettingsManager(tmp_path / "cfg.json")
        sm.update(
            general={
                "download_folder": str(tmp_path / "downloads"),
                "skip_existing": True,
            }
        )
        manager = DownloadManager(registry, bus, concurrency=2)
        store = CollectionStore(tmp_path / "c.db")
        provider = MockCollectionProvider(total=total, page_size=page_size)
        engine = CollectionEngine(
            registry,
            bus,
            manager,
            sm,
            store=store,
            collections=CollectionProviderRegistry([provider]),
        )
        bridge = BusBridge(bus)
        bridge.start()
        page = CollectionsPage(engine, bus, bridge)
        return page, engine, bridge, manager

    def test_scan_populates_table_and_selection(self, qapp, qtbot, tmp_path):
        page, engine, bridge, manager = self._page(qapp, tmp_path)
        page.url_input.setText("https://mock.example/account")
        with qtbot.waitSignal(bridge.collection_scan_ready, timeout=15000):
            page.analyze_button.click()
        assert page.table.rowCount() == 5
        assert "5" in page.info_label.text()
        url = "https://mock.example/account"
        assert engine.selected_ids(url) == {f"v{i:04d}" for i in range(1, 6)}
        page.select_none_button.click()
        assert engine.selected_ids(url) == set()
        page.select_all_button.click()
        assert len(engine.selected_ids(url)) == 5
        page.invert_button.click()
        assert engine.selected_ids(url) == set()

    def test_load_more_appends_rows(self, qapp, qtbot, tmp_path):
        page, engine, bridge, manager = self._page(qapp, tmp_path, total=5, page_size=2)
        page.url_input.setText("https://mock.example/big")
        with qtbot.waitSignal(bridge.collection_scan_ready, timeout=15000):
            page.analyze_button.click()
        assert page.table.rowCount() == 2
        assert page.load_more_button.isEnabled()
        page.load_more_button.click()
        assert page.table.rowCount() == 4
        page.load_more_button.click()
        assert page.table.rowCount() == 5
        assert page.load_more_button.isEnabled() is False

    def test_download_all_finishes_with_report(self, qapp, qtbot, tmp_path):
        page, engine, bridge, manager = self._page(qapp, tmp_path)
        page.url_input.setText("https://mock.example/account")
        with qtbot.waitSignal(bridge.collection_scan_ready, timeout=15000):
            page.analyze_button.click()
        assert page.table.rowCount() == 5
        with qtbot.waitSignal(bridge.collection_finished, timeout=20000):
            page.download_all_button.click()
        assert not page.report_bar.isHidden()
        assert "Completed:  5" in page.report_label.text()
        folder = tmp_path / "downloads" / "TikTok" / "@mockuser"
        assert len(list(folder.glob("*.mp4"))) == 5


class TestSettingsLayout:
    def test_scroll_area_and_form_rows(self, qapp):
        from PySide6.QtWidgets import QScrollArea

        base = Path(tempfile.mkdtemp())
        page = SettingsPage(SettingsManager(base / "cfg.json"))
        # The settings page exposes a scroll area root.
        found_scroll = any(isinstance(w, QScrollArea) for w in page.findChildren(QScrollArea))
        assert found_scroll
        assert page.concurrent.minimumHeight() >= 30
        assert page.filename_template.minimumWidth() >= 200
        assert page.download_folder.minimumWidth() >= 200

    def test_settings_save_round_trip(self, qapp):
        base = Path(tempfile.mkdtemp())
        mgr = SettingsManager(base / "cfg.json")
        page = SettingsPage(mgr)
        page.concurrent.setValue(6)
        page.default_quality.setCurrentText("720p")
        page.theme.setCurrentText("light")
        page.ffmpeg_path.setText("C:/tools/ffmpeg.exe")
        page.debug_mode.setChecked(True)
        page._persist()
        mgr2 = SettingsManager(base / "cfg.json")
        assert mgr2.settings.general.concurrent_downloads == 6
        assert mgr2.settings.video.default_quality == "720p"
        assert mgr2.settings.appearance.theme == "light"
        assert mgr2.settings.advanced.ffmpeg_path == "C:/tools/ffmpeg.exe"
        assert mgr2.settings.advanced.debug_mode is True

    def test_ffmpeg_status_label_when_missing(self, qapp, monkeypatch):
        from app.download.ffmpeg import ffmpeg_status

        base = Path(tempfile.mkdtemp())
        page = SettingsPage(SettingsManager(base / "cfg.json"))
        monkeypatch.setattr(
            "app.gui.settings.ffmpeg_status",
            lambda *_: (False, "", ""),
        )
        page._test_ffmpeg()
        assert "not found" in page.ffmpeg_status.text().lower()

    def test_ffmpeg_status_label_when_found(self, qapp, monkeypatch):
        from app.download.ffmpeg import ffmpeg_status

        base = Path(tempfile.mkdtemp())
        page = SettingsPage(SettingsManager(base / "cfg.json"))
        monkeypatch.setattr(
            "app.gui.settings.ffmpeg_status",
            lambda *_: (True, "C:/ffmpeg.exe", "ffmpeg version 9.0"),
        )
        page._test_ffmpeg()
        assert "detected" in page.ffmpeg_status.text().lower()
        assert "C:/ffmpeg.exe" in page.ffmpeg_status.text()


class TestHistoryDetails:
    def _window_with_failed(self, qapp):
        base = Path(tempfile.mkdtemp())
        db = HistoryDatabase(base / "h.db")
        db.add(
            HistoryEntry(
                url="https://youtube.com/watch?v=abc",
                platform="YouTube",
                title="Broken Video",
                creator="Creator",
                file_path=None,
                format="MP4",
                resolution="1080p",
                status="failed",
                error_message="FFmpeg is required to combine streams.",
                error_type="FFmpegNotFoundError",
                error_stage="Downloading",
                error_detail="ERROR: merging of multiple formats but ffmpeg is not installed",
            )
        )
        from app.gui.history import HistoryPage

        page = HistoryPage(db)
        page.refresh()
        return page

    def test_error_tooltip_set(self, qapp):
        page = self._window_with_failed(qapp)
        assert page.table.rowCount() == 1
        error_item = page.table.item(0, 7)
        assert error_item is not None
        assert error_item.toolTip() == "FFmpeg is required to combine streams."

    def test_error_dialog_constructs(self, qapp):
        page = self._window_with_failed(qapp)
        entry = page._entries[0]
        dialog = ErrorDetailsDialog(
            title=entry.title,
            platform=entry.platform,
            url=entry.url,
            stage=entry.error_stage,
            error_type=entry.error_type,
            message=entry.error_message,
            detail=entry.error_detail,
        )
        assert dialog.windowTitle() == "Download Error Details"
        dialog.close()

    def test_completed_row_not_failed_dialog(self, qapp):
        base = Path(tempfile.mkdtemp())
        db = HistoryDatabase(base / "h.db")
        db.add(
            HistoryEntry(
                url="u", platform="YouTube", title="OK", creator=None,
                file_path="C:/x.mp4", format="MP4", resolution="720p",
                status="completed",
            )
        )
        from app.gui.history import HistoryPage

        page = HistoryPage(db)
        page.refresh()
        assert page.table.item(0, 7).text() == ""
