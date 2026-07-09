from __future__ import annotations
import threading
import time
from typing import Callable, Optional
from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot


class Sampler:
    """Polls an adapter on a fixed interval, pushing each snapshot to on_sample."""

    def __init__(self, adapter, interval_s: float,
                 on_sample: Callable[[StatusSnapshot], None]):
        self.adapter = adapter
        self.interval_s = interval_s
        self.on_sample = on_sample
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._device_id: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            self._device_id = self.adapter.identity()
        except Exception:
            self._device_id = "unknown"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                snap = self.adapter.read_status()
            except Exception as e:
                snap = offline_snapshot(self._device_id or "unknown", str(e))
            try:
                self.on_sample(snap)
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            self._stop.wait(max(0.0, self.interval_s - elapsed))
