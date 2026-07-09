import time
from lab_device_manager.instruments.base import StatusSnapshot


def _snap(**kw):
    base = dict(timestamp=time.time(), state="running",
                work_mode="m", device_id="dev")
    base.update(kw)
    return StatusSnapshot(**base)


def test_metrics_defaults_to_empty_dict():
    s = _snap()
    assert s.metrics == {}


def test_metrics_can_be_populated():
    s = _snap(metrics={"speed": 300, "set_temp": 45})
    assert s.metrics == {"speed": 300, "set_temp": 45}


def test_metrics_is_optional_and_backward_compatible():
    # existing pump-style construction (no metrics) still works
    s = StatusSnapshot(timestamp=time.time(), state="running",
                      work_mode="仅注入", device_id="LeadFluid TYD02")
    assert s.metrics == {}
