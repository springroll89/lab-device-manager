from lab_device_manager.runtime.run_detector import RunDetector
from lab_device_manager.instruments.base import StatusSnapshot


class FakeRepo:
    def __init__(self):
        self.events = []          # list of (event_type, severity) 2-tuples
        self.event_run_ids = []
        self.event_ts_ms = []     # parallel list of ts_ms for add_event calls
        self.runs_opened = 0
        self.runs_closed = []
        self.samples = 0
        self._next = 1
    def open_run(self, did, ch, started_ms, sp):
        self.runs_opened += 1
        rid = self._next; self._next += 1
        return rid
    def close_run(self, rid, ended_ms, end_status, actual_volume, actual_unit,
                  lifetime_acc, lifetime_acc_unit, alarms):
        self.runs_closed.append((end_status, alarms, actual_volume))
    def add_sample(self, rid, did, ts_ms, *a):
        self.samples += 1
    def add_event(self, did, rid, ts_ms, etype, sev, detail):
        self.events.append((etype, sev))
        self.event_run_ids.append(rid)
        self.event_ts_ms.append(ts_ms)


def _snap(state):
    return StatusSnapshot(timestamp=0, state=state, work_mode="仅注入",
                          device_id="d", acc_volume=1.0, acc_unit="mL")


def _snap_with_acc(state, acc=1.0):
    return StatusSnapshot(timestamp=0, state=state, work_mode="仅注入",
                          device_id="d", acc_volume=acc, acc_unit="mL")


def _detector():
    repo = FakeRepo()
    t = {"n": 1751000000.0}   # epoch seconds; detector does int(clock*1000) -> ms
    return RunDetector(1, 1, repo, clock=lambda: (t.__setitem__("n", t["n"] + 1.0), t["n"])[1]), repo


def test_open_close_completed():
    d, repo = _detector()
    d.on_sample(_snap("stopped"))
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    assert repo.runs_opened == 1
    assert repo.runs_closed == [("completed", 0, 0.0)]
    assert ("start", "info") in repo.events
    assert ("stop", "info") in repo.events


def test_stop_event_links_to_run():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    # the stop event must reference the run it closed (run id 1), not None
    assert repo.event_run_ids[repo.events.index(("start", "info"))] == 1
    assert repo.event_run_ids[repo.events.index(("stop", "info"))] == 1


def test_stall_alarm_marks_alarm_abort():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("alarm"))
    d.on_sample(_snap("stopped"))
    assert ("stall_alarm", "critical") in repo.events
    assert repo.runs_closed == [("alarm_abort", 1, 0.0)]


def test_pause_resume():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("paused"))
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    assert ("pause", "warning") in repo.events
    assert ("resume", "info") in repo.events


def test_comms_lost_recover_keeps_run_open():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("offline"))
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    assert ("comms_lost", "critical") in repo.events
    assert ("comms_recover", "info") in repo.events
    assert repo.runs_opened == 1


def test_samples_logged_only_while_run_open():
    d, repo = _detector()
    d.on_sample(_snap("stopped"))      # no run
    d.on_sample(_snap("running"))      # opens (no sample this frame)
    d.on_sample(_snap("running"))      # sample #1
    d.on_sample(_snap("stopped"))      # sample #2 (closing frame), then close
    d.on_sample(_snap("stopped"))      # no run
    assert repo.samples == 2


def test_event_timestamps_are_epoch_ms():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    assert all(isinstance(ts, int) and ts > 1_000_000_000_000 for ts in repo.event_ts_ms)


def test_actual_volume_is_lifetime_delta():
    d, repo = _detector()
    d.on_sample(_snap_with_acc("running", acc=100.0))  # lifetime at open = 100
    d.on_sample(_snap_with_acc("stopped", acc=150.0))  # lifetime at close = 150
    assert repo.runs_closed[-1][2] == 50.0  # actual_volume = 150 - 100


def test_actual_volume_survives_accumulator_reset():
    d, repo = _detector()
    d.on_sample(_snap_with_acc("running", acc=100.0))
    d.on_sample(_snap_with_acc("running", acc=120.0))
    d.on_sample(_snap_with_acc("running", acc=2.0))
    d.on_sample(_snap_with_acc("stopped", acc=7.0))

    assert repo.runs_closed[-1][2] == 27.0


def test_zero_accumulator_at_close_is_not_treated_as_missing():
    d, repo = _detector()
    d.on_sample(_snap_with_acc("running", acc=0.0))
    d.on_sample(_snap_with_acc("stopped", acc=0.0))
    assert repo.runs_closed[-1][2] == 0.0


def test_finalize_closes_active_run_and_marks_comms_interruption():
    d, repo = _detector()
    d.on_sample(_snap_with_acc("running", acc=10.0))
    d.on_sample(_snap("offline"))
    d.finalize()

    assert repo.runs_closed == [("comms_interrupted", 0, 0.0)]
    assert ("interrupted", "warning") in repo.events
