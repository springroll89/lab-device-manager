import time
from lab_device_manager.sampler import Sampler
from lab_device_manager.instruments.base import StatusSnapshot


class FakeAdapter:
    def __init__(self, seq):
        self.seq = list(seq)
    def identity(self):
        return "dev"
    def read_status(self):
        item = self.seq.pop(0)
        self.seq.append(item)            # cycle so it never runs dry
        if isinstance(item, Exception):
            raise item
        return item


def _snap(n):
    return StatusSnapshot(timestamp=time.time(), state="running",
                          work_mode="m", device_id="dev", temp_c=float(n))


def test_sampler_emits_samples():
    adapter = FakeAdapter([_snap(1), _snap(2)])
    out = []
    s = Sampler(adapter, interval_s=0.02, on_sample=out.append)
    s.start()
    time.sleep(0.12)
    s.stop()
    assert len(out) >= 2
    assert out[0].temp_c in (1.0, 2.0)


def test_sampler_emits_offline_on_error():
    adapter = FakeAdapter([RuntimeError("boom"), _snap(7)])
    out = []
    s = Sampler(adapter, interval_s=0.02, on_sample=out.append)
    s.start()
    time.sleep(0.12)
    s.stop()
    assert any(x.state == "offline" for x in out)
    assert any(x.temp_c == 7.0 for x in out)
