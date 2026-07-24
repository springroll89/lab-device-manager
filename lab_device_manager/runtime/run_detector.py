from __future__ import annotations
import json
import time
from typing import Callable, Optional
from lab_device_manager.instruments.base import StatusSnapshot


class RunDetector:
    """Turns a StatusSnapshot stream into run records + events (written to repo).
    State machine over StatusSnapshot.state: stopped/running/paused/alarm/offline."""

    def __init__(self, device_id: int, channel: int, repo,
                 clock: Callable[[], float] = time.time,
                 on_event: Optional[Callable[[dict], None]] = None):
        self.device_id = device_id
        self.channel = channel
        self.repo = repo
        self.clock = clock
        self.on_event = on_event
        self._state = "stopped"
        self._run_id: Optional[int] = None
        self._run_start_ms: int = 0
        self._had_alarm = False
        self._alarm_count = 0
        self._lifetime_start: float = 0.0
        self._last_acc: Optional[float] = None
        self._last_acc_unit: Optional[str] = None
        self._had_comms_loss = False

    def on_sample(self, snap: StatusSnapshot):
        now_ms = int(self.clock() * 1000)
        new = snap.state
        old = self._state

        if new == "offline":
            if old != "offline":
                self._emit(now_ms, "comms_lost", "critical")
                if self._run_id is not None:
                    self._had_comms_loss = True
            self._state = "offline"
            return
        if old == "offline":
            self._emit(now_ms, "comms_recover", "info")

        if self._run_id is not None:
            self._last_acc = snap.acc_volume
            self._last_acc_unit = snap.acc_unit
            self.repo.add_sample(self._run_id, self.device_id, now_ms, snap.state,
                                 snap.flow_rpm, snap.acc_volume, snap.temp_c,
                                 json.dumps(snap.metrics, ensure_ascii=False))

        if new == "alarm" and old != "alarm":
            self._emit(now_ms, "stall_alarm", "critical")
            self._had_alarm = True
            self._alarm_count += 1
        elif old == "alarm" and new != "alarm":
            self._emit(now_ms, "alarm_clear", "info")

        if new == "running" and self._run_id is None:
            self._run_start_ms = now_ms
            self._lifetime_start = snap.acc_volume if snap.acc_volume is not None else 0.0
            self._run_id = self.repo.open_run(
                self.device_id, self.channel, now_ms,
                dict(snap.metrics, work_mode=snap.work_mode))
            self._had_alarm = False
            self._alarm_count = 0
            self._last_acc = snap.acc_volume
            self._last_acc_unit = snap.acc_unit
            self._had_comms_loss = False
            self._emit(now_ms, "start", "info")
        elif new == "paused" and old == "running" and self._run_id is not None:
            self._emit(now_ms, "pause", "warning")
        elif new == "running" and old == "paused":
            self._emit(now_ms, "resume", "info")
        elif new == "stopped" and self._run_id is not None:
            end_status = (
                "alarm_abort"
                if self._had_alarm
                else (
                    "comms_interrupted"
                    if self._had_comms_loss
                    else "completed"
                )
            )
            closed = self._run_id
            ending_acc = (
                snap.acc_volume if snap.acc_volume is not None else 0.0
            )
            actual = ending_acc - self._lifetime_start
            self.repo.close_run(closed, now_ms, end_status,
                                actual, snap.acc_unit,
                                snap.acc_volume, snap.acc_unit, self._alarm_count)
            self._run_id = None
            self._emit(now_ms, "stop", "info", run_id=closed)

        self._state = new

    def finalize(self, end_status: str = "interrupted_shutdown"):
        if self._run_id is None:
            return
        now_ms = int(self.clock() * 1000)
        run_id = self._run_id
        actual = (
            self._last_acc - self._lifetime_start
            if self._last_acc is not None
            else None
        )
        if self._had_comms_loss:
            end_status = "comms_interrupted"
        self.repo.close_run(
            run_id,
            now_ms,
            end_status,
            actual,
            self._last_acc_unit,
            self._last_acc,
            self._last_acc_unit,
            self._alarm_count,
        )
        self._run_id = None
        self._emit(now_ms, "interrupted", "warning", run_id=run_id)
        self._state = "stopped"

    def _emit(self, ts_ms: int, event_type: str, severity: str, run_id=None):
        rid = self._run_id if run_id is None else run_id
        self.repo.add_event(self.device_id, rid, ts_ms, event_type, severity, "{}")
        if self.on_event:
            self.on_event({"device_id": self.device_id, "run_id": rid,
                           "ts_ms": ts_ms, "event_type": event_type, "severity": severity})
