"""Metadata analyzer: fetches bundle info on a worker thread and publishes
results on the event bus."""

from __future__ import annotations

import logging
import threading

from ..core.bus import EventBus
from ..providers.registry import ProviderRegistry

log = logging.getLogger(__name__)


class MetadataAnalyzer:
    """Analyzes URLs in background threads."""

    def __init__(self, registry: ProviderRegistry, bus: EventBus) -> None:
        self.registry = registry
        self.bus = bus
        self._lock = threading.Lock()
        self._active: dict[str, threading.Thread] = {}

    def analyze(self, url: str) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(url,),
            name=f"analyze-{abs(hash(url)) % 1000}",
            daemon=True,
        )
        with self._lock:
            self._active[url] = thread
        thread.start()

    def _run(self, url: str) -> None:
        try:
            provider = self.registry.require(url)
            bundle = provider.get_metadata(url)
            self.bus.emit("metadata_ready", url, bundle)
        except Exception as exc:  # noqa: BLE001
            log.exception("Metadata analysis failed for %s", url)
            self.bus.emit("metadata_failed", url, str(exc))
        finally:
            with self._lock:
                self._active.pop(url, None)