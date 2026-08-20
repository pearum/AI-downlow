"""Signal bus - a lightweight publish/subscribe mechanism.

Used to decouple the download engine (background threads) from the GUI layer
(Qt signals) so the engine has zero dependency on Qt.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)


class Signal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: list[Callable[..., Any]] = []

    def connect(self, slot: Callable[..., Any]) -> None:
        with self._lock:
            if slot not in self._slots:
                self._slots.append(slot)

    def disconnect(self, slot: Callable[..., Any]) -> None:
        with self._lock:
            if slot in self._slots:
                self._slots.remove(slot)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            slots = list(self._slots)
        for slot in slots:
            try:
                slot(*args, **kwargs)
            except Exception:  # noqa: BLE001 - slots must not break the bus
                log.exception("Error in signal handler %r", slot)


class EventBus:
    """Thread-safe named-signal bus."""

    def __init__(self) -> None:
        self._signals: dict[str, Signal] = defaultdict(Signal)

    def connect(self, name: str, slot: Callable[..., Any]) -> None:
        self._signals[name].connect(slot)

    def disconnect(self, name: str, slot: Callable[..., Any]) -> None:
        self._signals[name].disconnect(slot)

    def emit(self, name: str, *args: Any, **kwargs: Any) -> None:
        self._signals[name].emit(*args, **kwargs)
