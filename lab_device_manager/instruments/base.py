from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol


@dataclass(frozen=True)
class StatusSnapshot:
    timestamp: float
    state: str                       # running | paused | stopped | alarm | offline
    work_mode: str
    device_id: str
    flow_rpm: Optional[float] = None
    acc_volume: Optional[float] = None
    acc_unit: str = ""
    consumed_volume: Optional[float] = None
    consumed_unit: str = ""
    remaining_volume: Optional[float] = None
    remaining_unit: str = ""
    elapsed_ms: Optional[int] = None
    remaining_ms: Optional[int] = None
    cycles: Optional[int] = None
    progress_pct: Optional[float] = None
    temp_c: Optional[float] = None
    alarm: bool = False
    error_code: Optional[int] = None
    metrics: Dict[str, object] = field(default_factory=dict)  # instrument-specific extras


class InstrumentAdapter(Protocol):
    def identity(self) -> str: ...
    def read_status(self) -> StatusSnapshot: ...


def offline_snapshot(device_id: str, reason: str = "") -> StatusSnapshot:
    return StatusSnapshot(timestamp=time.time(), state="offline",
                          work_mode="", device_id=device_id)
