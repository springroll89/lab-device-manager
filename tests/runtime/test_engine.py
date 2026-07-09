import time
from lab_device_manager.runtime.engine import Engine
from lab_device_manager.runtime.types import DeviceConfig
from lab_device_manager.db.repository import Repository
from lab_device_manager.instruments.base import StatusSnapshot


def _snap(state):
    return StatusSnapshot(timestamp=time.time(), state=state, work_mode="仅注入",
                          device_id="d", acc_volume=1.0, acc_unit="mL")


class ScriptedAdapter:
    def __init__(self, seq):
        self.seq = seq
        self.i = 0
    def identity(self):
        return "d"
    def read_status(self):
        s = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return s


def test_engine_records_run_to_db():
    repo = Repository(":memory:")
    seq = [_snap("stopped"), _snap("running"), _snap("running"), _snap("stopped")]
    devices = [DeviceConfig(name="pump-1", type="tyd02")]
    def factory(dc):
        return ScriptedAdapter(seq), (lambda: None)
    eng = Engine(repo, devices, sample_interval_s=0.02, adapter_factory=factory)
    eng.start()
    time.sleep(0.25)
    eng.stop()
    runs = repo.list_untagged_runs()
    assert len(runs) == 1
    assert runs[0].end_status == "completed"
    assert 1 in eng.latest()                     # device_id 1 present in latest-store


def test_tolerates_unreachable_device():
    repo = Repository(":memory:")
    seq = [_snap("stopped"), _snap("running"), _snap("stopped")]
    devices = [DeviceConfig(name="dead", type="tyd02"),
               DeviceConfig(name="live", type="tyd02")]
    def factory(dc):
        if dc.name == "dead":
            raise OSError("no such port")
        return ScriptedAdapter(seq), (lambda: None)
    eng = Engine(repo, devices, sample_interval_s=0.02, adapter_factory=factory)
    eng.start()                                   # must NOT raise on the dead device
    time.sleep(0.25)
    eng.stop()
    latest = eng.latest()
    assert len(latest) == 2                       # both registered
    assert any(s.state == "offline" for s in latest.values())   # dead device offline
    assert len(repo.list_untagged_runs()) == 1    # live produced a run; dead produced none
