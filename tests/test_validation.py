"""Phase 13: output validation, zero-byte protection, queue advancement, skip
existing, retry and history-consistency tests.

These explicitly verify that a download is NEVER reported Completed unless the
final output file has been located and validated, and that the queue always
advances to the next item after a worker finishes.
"""

import tempfile
import time
from pathlib import Path

from app.core.bus import EventBus
from app.core.models import ItemStatus, MediaItem, Platform
from app.database.history import HistoryDatabase, HistoryEntry
from app.download.manager import DownloadManager
from app.download.queue import QueueItem
from app.download.validate import (
    expected_targets,
    locate_final_output,
    validate_output_file,
)
from app.providers.base import DownloadOptions
from app.providers.registry import ProviderRegistry

from tests.mocks.providers import MockProvider

_TERMINAL = (
    ItemStatus.COMPLETED,
    ItemStatus.FAILED,
    ItemStatus.CANCELLED,
    ItemStatus.SKIPPED,
)


def make_item(item_id="1", title="Video A") -> MediaItem:
    return MediaItem(
        item_id=item_id,
        title=title,
        url=f"https://mock.example/watch?v={item_id}",
        platform=Platform.YOUTUBE,
        creator="Mock Creator",
    )


def make_queue_item(item: MediaItem | None = None) -> QueueItem:
    item = item or make_item()
    return QueueItem(
        media=item,
        options=DownloadOptions(
            url=item.url,
            output_dir=tempfile.mkdtemp(),
            filename=f"{item.item_id}.mp4",
            skip_existing=True,
        ),
    )


def wait_terminal(qi: QueueItem, m: DownloadManager, timeout: float = 20) -> ItemStatus:
    deadline = time.time() + timeout
    while qi.status not in _TERMINAL and time.time() < deadline:
        time.sleep(0.05)
    return qi.status


def run_one(provider: MockProvider, item: QueueItem | None = None, **kw):
    bus = EventBus()
    reg = ProviderRegistry()
    reg._providers["youtube"] = provider
    m = DownloadManager(reg, bus, **kw)
    qi = item or make_queue_item()
    m.enqueue(qi)
    m.start()
    return m, qi


class TestOutputValidation:
    def test_zero_byte_output_fails(self):
        m, qi = run_one(MockProvider(output_kind="zero_byte"))
        status = wait_terminal(qi, m)
        assert status == ItemStatus.FAILED
        assert qi.error_type == "OutputValidationError"
        assert "missing or 0 bytes" in qi.error
        assert qi.result is not None and qi.result.success is False
        assert qi.file_size == 0
        m.shutdown()

    def test_missing_output_fails(self):
        m, qi = run_one(MockProvider(output_kind="missing"))
        status = wait_terminal(qi, m)
        assert status == ItemStatus.FAILED
        assert qi.error_type == "OutputValidationError"
        assert qi.result.success is False
        m.shutdown()

    def test_valid_output_completes_with_real_size(self):
        m, qi = run_one(MockProvider())
        status = wait_terminal(qi, m)
        assert status == ItemStatus.COMPLETED
        assert qi.percent == 100.0
        assert qi.file_size == len(b"mockdata")
        assert Path(qi.output_path).exists()
        assert qi.result.success is True
        assert qi.result.status == "completed"
        m.shutdown()

    def test_ffmpeg_merge_finds_final_output(self):
        m, qi = run_one(MockProvider(output_kind="ffmpeg_rename"))
        status = wait_terminal(qi, m)
        assert status == ItemStatus.COMPLETED
        assert qi.output_path.endswith(".mp4")
        assert Path(qi.output_path).exists()
        assert qi.file_size == len(b"merged-data")
        m.shutdown()

    def test_progress_100_does_not_imply_completed(self):
        # zero-byte output still reports 100% bytes via the hook — must FAIL.
        m, qi = run_one(MockProvider(output_kind="zero_byte"))
        status = wait_terminal(qi, m)
        assert status == ItemStatus.FAILED
        assert qi.percent == 0.0
        assert qi.status != ItemStatus.COMPLETED
        m.shutdown()

    def test_validating_status_emitted(self):
        m, qi = run_one(MockProvider())
        seen: list[str] = []
        m.bus.connect("download_status", lambda uid, s: seen.append(s.value))
        wait_terminal(qi, m)
        assert "validating" in seen
        assert "completed" in seen
        m.shutdown()

    def test_cleanup_removes_zero_byte_leftover(self):
        item = make_item()
        qi = make_queue_item(item)
        target = Path(qi.options.output_dir) / qi.options.filename
        target.write_bytes(b"")  # a bogus 0-byte file left behind
        m, qi = run_one(MockProvider(output_kind="missing"), item=qi)
        wait_terminal(qi, m)
        assert target.exists() is False
        m.shutdown()


class TestQueueAdvancement:
    def _status_indexes(self, bus: EventBus, uid: str, status: ItemStatus) -> list[int]:
        out: list[int] = []
        bus.connect(
            "download_status",
            lambda u, s, _uid=uid, _status=status, _out=out: (
                _out.append(len(_out))
                if u == _uid and str(s) == str(_status)
                else None
            ),
        )
        return out

    def test_item2_starts_after_item1_completes(self):
        bus = EventBus()
        reg = ProviderRegistry()
        reg._providers["youtube"] = MockProvider(sleep=0.05)
        m = DownloadManager(reg, bus, concurrency=1)
        order: list[tuple[str, str]] = []
        bus.connect("download_status", lambda uid, s: order.append((uid, s.value)))
        i1 = make_queue_item(make_item("1", "One"))
        i2 = make_queue_item(make_item("2", "Two"))
        m.enqueue_many([i1, i2])
        m.start()
        wait_terminal(i1, m)
        wait_terminal(i2, m)
        assert i1.status == ItemStatus.COMPLETED
        assert i2.status == ItemStatus.COMPLETED
        i1_done = next(i for i, (uid, s) in enumerate(order) if uid == i1.uid and s == "completed")
        i2_start = next(i for i, (uid, s) in enumerate(order) if uid == i2.uid and s == "downloading")
        assert i2_start > i1_done, "item 2 must not start before item 1 completes"
        m.shutdown()

    def test_item2_starts_after_item1_fails(self):
        bus = EventBus()
        reg = ProviderRegistry()
        reg._providers["youtube"] = MockProvider(fail_first=1)  # only item 1 fails
        m = DownloadManager(reg, bus, concurrency=1)
        order: list[tuple[str, str]] = []
        bus.connect("download_status", lambda uid, s: order.append((uid, s.value)))
        i1 = make_queue_item(make_item("1", "One"))
        i2 = make_queue_item(make_item("2", "Two"))
        m.enqueue_many([i1, i2])
        m.start()
        wait_terminal(i1, m)
        wait_terminal(i2, m)
        assert i1.status == ItemStatus.FAILED
        assert i2.status == ItemStatus.COMPLETED
        i1_failed = next(i for i, (uid, s) in enumerate(order) if uid == i1.uid and s == "failed")
        i2_start = next(i for i, (uid, s) in enumerate(order) if uid == i2.uid and s == "downloading")
        assert i2_start > i1_failed
        m.shutdown()

    def test_concurrency_one_never_runs_two(self):
        bus = EventBus()
        reg = ProviderRegistry()
        reg._providers["youtube"] = MockProvider(sleep=0.3)
        m = DownloadManager(reg, bus, concurrency=1)
        running: list[tuple[float, str]] = []

        def on_status(uid, s):
            if str(s) == "downloading":
                running.append((time.time(), uid))

        bus.connect("download_status", on_status)
        items = [make_queue_item(make_item(str(i), f"V{i}")) for i in range(3)]
        m.enqueue_many(items)
        m.start()
        for qi in items:
            wait_terminal(qi, m)
        # With concurrency=1, download starts must be strictly sequential.
        for a, b in zip(running, running[1:]):
            assert b[0] >= a[0] + 0.1
        m.shutdown()


class TestSkipExisting:
    def test_skip_existing_valid_file(self):
        qi = make_queue_item()
        target = Path(qi.options.output_dir) / qi.options.filename
        target.write_bytes(b"existing-nonzero")
        provider = MockProvider()
        m, qi = run_one(provider, item=qi)
        status = wait_terminal(qi, m)
        assert status == ItemStatus.SKIPPED
        assert provider.download_calls == []
        assert qi.result.status == "skipped"
        assert qi.result.success is True
        m.shutdown()

    def test_skip_existing_zero_byte_does_not_skip(self):
        qi = make_queue_item()
        target = Path(qi.options.output_dir) / qi.options.filename
        target.write_bytes(b"")  # 0-byte existing file is NOT valid
        provider = MockProvider()
        m, qi = run_one(provider, item=qi)
        status = wait_terminal(qi, m)
        assert status == ItemStatus.COMPLETED
        assert len(provider.download_calls) == 1
        assert target.read_bytes() == b"mockdata"
        m.shutdown()

    def test_skip_disabled_overwrites(self):
        qi = make_queue_item()
        target = Path(qi.options.output_dir) / qi.options.filename
        target.write_bytes(b"old")
        qi.options.skip_existing = False
        provider = MockProvider()
        m, qi = run_one(provider, item=qi)
        status = wait_terminal(qi, m)
        assert status == ItemStatus.COMPLETED
        assert len(provider.download_calls) == 1
        assert target.read_bytes() == b"mockdata"
        m.shutdown()


class TestRetryValidation:
    def test_retry_success_is_validated(self):
        provider = MockProvider(fail_first=1)
        m, qi = run_one(provider, retry_count=3)
        status = wait_terminal(qi, m)
        assert status == ItemStatus.COMPLETED
        assert qi.attempts == 2
        assert qi.result.success is True
        assert provider.download_calls == ["1", "1"]
        m.shutdown()

    def test_retry_exhausted_stays_failed(self):
        provider = MockProvider(fail_download=True)
        m, qi = run_one(provider, retry_count=3)
        status = wait_terminal(qi, m, timeout=40)
        assert status == ItemStatus.FAILED
        assert len(provider.download_calls) == 4  # initial + 3 retries
        assert qi.status != ItemStatus.COMPLETED
        m.shutdown()


class TestHistoryConsistency:
    def test_history_matches_download_result(self):
        bus = EventBus()
        reg = ProviderRegistry()
        reg._providers["youtube"] = MockProvider()
        m = DownloadManager(reg, bus)
        db = HistoryDatabase(Path(tempfile.mkdtemp()) / "h.db")

        def record(uid, path, size):
            item = m.queue.get(uid)
            db.add(
                HistoryEntry(
                    url=item.media.url,
                    platform="YouTube",
                    title=item.media.title,
                    creator=item.media.creator,
                    file_path=path,
                    format=item.options.output_format,
                    resolution=item.options.quality,
                    status="completed",
                    file_size=size,
                )
            )

        bus.connect("download_completed", record)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        wait_terminal(qi, m)
        rows = db.list()
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].file_path == qi.output_path
        assert rows[0].file_size == qi.file_size
        m.shutdown()

    def test_history_failed_has_error(self):
        bus = EventBus()
        reg = ProviderRegistry()
        reg._providers["youtube"] = MockProvider(fail_download=True)
        m = DownloadManager(reg, bus)
        db = HistoryDatabase(Path(tempfile.mkdtemp()) / "h.db")

        def record(uid, msg, detail):
            item = m.queue.get(uid)
            db.add(
                HistoryEntry(
                    url=item.media.url,
                    platform="YouTube",
                    title=item.media.title,
                    creator=item.media.creator,
                    file_path=None,
                    format=item.options.output_format,
                    resolution=item.options.quality,
                    status="failed",
                    error_message=msg,
                    error_type=item.error_type,
                    error_stage=item.error_stage,
                    error_detail=detail,
                )
            )

        bus.connect("download_failed", record)
        qi = make_queue_item()
        m.enqueue(qi)
        m.start()
        wait_terminal(qi, m)
        rows = db.list()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert "mock download failure" in (rows[0].error_message or "").lower()
        m.shutdown()


class TestValidateHelpers:
    def test_validate_zero_byte(self, tmp_path):
        p = tmp_path / "x.mp4"
        p.write_bytes(b"")
        ok, reason, size = validate_output_file(str(p))
        assert ok is False
        assert "0 bytes" in reason
        assert size == 0

    def test_validate_missing(self, tmp_path):
        ok, reason, _ = validate_output_file(str(tmp_path / "nope.mp4"))
        assert ok is False
        assert "does not exist" in reason

    def test_validate_ok(self, tmp_path):
        p = tmp_path / "ok.mp4"
        p.write_bytes(b"hello")
        ok, reason, size = validate_output_file(str(p))
        assert ok is True
        assert reason == ""
        assert size == 5

    def test_locate_final_output_prefers_newest(self, tmp_path):
        (tmp_path / "clip.mp4").write_bytes(b"old")
        final = tmp_path / "clip.mp4"
        (tmp_path / "clip.webm").write_bytes(b"newer")
        newer = tmp_path / "clip.webm"
        # give the second file a newer mtime
        import os

        os.utime(newer, (time.time() + 5, time.time() + 5))
        found = locate_final_output(str(tmp_path), "clip", ["mp4", "webm"])
        assert found == str(newer)

    def test_expected_targets_covers_merged_ext(self):
        import os

        targets = expected_targets("C:/out", "My Song.mp3")
        norm = [os.path.normpath(t) for t in targets]
        assert os.path.normpath("C:/out/My Song.mp3") in norm
        assert os.path.normpath("C:/out/My Song.mp4") in norm

    def test_expected_targets_video_merge_single_ext(self):
        import os

        targets = expected_targets("C:/out", "My Video.webm")
        assert len(targets) == 1
        assert os.path.normpath(targets[0]) == os.path.normpath("C:/out/My Video.webm")
