from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Device:
    id: int
    name: str
    device_type: str
    alias: str = ""
    location: Optional[str] = None
    asset_no: Optional[str] = None
    created_at: Optional[str] = None
    is_active: int = 1


@dataclass(frozen=True)
class Run:
    id: int
    device_id: int
    channel: int
    started_ms: int
    ended_ms: Optional[int]
    duration_ms: Optional[int]
    end_status: Optional[str]
    operator: Optional[str]
    project_tag: Optional[str]
    experiment_tag: Optional[str]
    remark: Optional[str]
    tagged: bool
    work_mode: Optional[str]
    target_volume: Optional[float]
    target_volume_unit: Optional[str]
    result_acc_volume: Optional[float]
    result_acc_unit: Optional[str]
    actual_volume: Optional[float]
    actual_unit: Optional[str]
    alarm_count: int
    setpoints_json: Optional[str] = None
    operator_user_id: Optional[int] = None


@dataclass(frozen=True)
class SampleRow:
    id: int
    run_id: Optional[int]
    device_id: int
    ts_ms: int
    state: Optional[str]
    flow_rate: Optional[float]
    delivered_volume: Optional[float]
    temp_c: Optional[float]
    metrics_json: Optional[str]


@dataclass(frozen=True)
class EventRow:
    id: int
    device_id: int
    run_id: Optional[int]
    ts_ms: int
    event_type: str
    severity: str
    detail_json: Optional[str]


def fmt_ts_ms(ms: int) -> str:
    """Epoch ms -> 'YYYY-MM-DD HH:MM:SS' (local) for display."""
    import time as _t
    s = ms / 1000.0
    return _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(s))
