import dataclasses
import time
from lab_device_manager.instruments.base import StatusSnapshot, offline_snapshot


def _snap(**kw):
    base = dict(timestamp=time.time(), state="running",
                work_mode="仅注入", device_id="LeadFluid TYD02")
    base.update(kw)
    return StatusSnapshot(**base)


def test_snapshot_is_frozen():
    s = _snap()
    try:
        s.state = "stopped"
        assert False, "should be frozen"
    except dataclasses.FrozenInstanceError:
        pass


def test_snapshot_defaults():
    s = _snap()
    assert s.flow_rpm is None
    assert s.alarm is False
    assert s.acc_unit == ""
    assert s.progress_pct is None


def test_offline_snapshot():
    s = offline_snapshot("dev1", "timeout")
    assert s.state == "offline"
    assert s.device_id == "dev1"
