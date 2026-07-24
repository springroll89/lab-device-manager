import time
from lab_device_manager.sampler import Sampler
from lab_device_manager.instruments.base import StatusSnapshot


def _snap():
    return StatusSnapshot(timestamp=time.time(), state="running",
                          work_mode="m", device_id="dev")


def test_sampler_identity_failure_uses_unknown():
    class BadIdAdapter:
        def identity(self):
            raise RuntimeError("no identity")
        def read_status(self):
            return _snap()
    s = Sampler(BadIdAdapter(), interval_s=0.02, on_sample=lambda _s: None)
    s.start()
    time.sleep(0.08)
    s.stop()
    assert s._device_id == "unknown"


def test_sampler_survives_and_logs_on_sample_exception(caplog):
    class GoodAdapter:
        def identity(self):
            return "dev"
        def read_status(self):
            return _snap()
    def bad_cb(snap):
        raise RuntimeError("callback boom")
    with caplog.at_level("ERROR", logger="lab_device_manager.sampler"):
        s = Sampler(GoodAdapter(), interval_s=0.02, on_sample=bad_cb)
        s.start()
        time.sleep(0.08)
        s.stop()
    assert not s._thread.is_alive()
    assert "sample callback failed for device dev" in caplog.text
