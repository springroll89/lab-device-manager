import json
from lab_device_manager.db.repository import Repository
from lab_device_manager.db.models import Run


def make_repo():
    return Repository(":memory:")


def test_upsert_device_idempotent_by_name():
    r = make_repo()
    d1 = r.upsert_device("pump-1", "tyd02", alias="注射泵")
    d2 = r.upsert_device("pump-1", "tyd02", alias="注射泵")
    assert d1 == d2                       # same name → same id


def test_open_and_close_run():
    r = make_repo()
    did = r.upsert_device("pump-1", "tyd02")
    rid = r.open_run(did, 1, 1751000000_000,
                     {"work_mode": "仅注入", "target_volume": 40.0})
    assert rid > 0
    r.add_sample(rid, did, 1751000001_000, "running", 50.0, 1.2, 33.0, "{}")
    r.add_event(did, rid, 1751000000_000, "start", "info", "{}")
    r.close_run(rid, 1751000060_000, "completed", 50.0, "mL", 150.0, "mL", 0)
    rows = r.list_untagged_runs()
    assert len(rows) == 1
    assert rows[0].started_ms == 1751000000_000
    assert rows[0].ended_ms == 1751000060_000
    assert rows[0].duration_ms == 60_000
    assert rows[0].actual_volume == 50.0


def test_list_untagged_and_tag():
    r = make_repo()
    did = r.upsert_device("pump-1", "tyd02")
    rid = r.open_run(did, 1, 1751000000_000, {})
    r.close_run(rid, 1751000060_000, "completed", 40.0, "mL", 40.0, "mL", 0)
    pending = r.list_untagged_runs()
    assert len(pending) == 1
    assert isinstance(pending[0], Run)
    assert pending[0].tagged is False
    r.tag_run(rid, "alice", "陶瓷膜-A", "exp-01", "首次试机")
    assert r.list_untagged_runs() == []
    # event recorded
    r.add_event(did, rid, 1751000000_000, "start", "info", "{}")
    evs = r.list_recent_events(device_id=did)
    assert len(evs) == 1 and evs[0].event_type == "start"


def test_list_runs_for_device_isolates_by_device_id():
    """list_runs_for_device must ONLY return runs for the given device —
    a device-detail page must never see another device's runs."""
    r = make_repo()
    d1 = r.upsert_device("pump-1", "tyd02")
    d2 = r.upsert_device("pump-2", "tyd02")
    r1 = r.open_run(d1, 1, 1751000000_000, {})
    r2 = r.open_run(d2, 1, 1751000100_000, {})
    r.close_run(r1, 1751000060_000, "completed", 10.0, "mL", 10.0, "mL", 0)
    r.close_run(r2, 1751000160_000, "completed", 20.0, "mL", 20.0, "mL", 0)
    only_d1 = r.list_runs_for_device(d1)
    only_d2 = r.list_runs_for_device(d2)
    assert [x.id for x in only_d1] == [r1]
    assert [x.id for x in only_d2] == [r2]
    assert all(x.device_id == d1 for x in only_d1)
    assert all(x.device_id == d2 for x in only_d2)


def _seed_runs(repo):
    d1 = repo.upsert_device("pump-1", "tyd02", alias="P1")
    d2 = repo.upsert_device("pump-2", "tyd02", alias="P2")
    r1 = repo.open_run(d1, 1, 1751000000_000,
                       {"operator": "alice", "project_tag": "陶瓷膜-A"})
    repo.close_run(r1, 1751000060_000, "completed", 10.0, "mL", 10.0, "mL", 0)
    repo.tag_run(r1, "alice", "陶瓷膜-A", "exp-01", "")
    r2 = repo.open_run(d2, 1, 1751001000_000, {"project_tag": "陶瓷膜-B"})
    repo.close_run(r2, 1751001600_000, "alarm_abort", 5.0, "mL", 5.0, "mL", 1)
    # samples + events for r1
    repo.add_sample(r1, d1, 1751000001_000, "running", 1.0, 1.0, 33.0, "{}")
    repo.add_sample(r1, d1, 1751000002_000, "running", 1.1, 2.0, 33.1, "{}")
    repo.add_event(d1, r1, 1751000000_000, "start", "info", "{}")
    repo.add_event(d1, r1, 1751000060_000, "stop", "info", "{}")
    return d1, d2, r1, r2


def test_list_runs_filtered_by_device():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(device_id=d1)
    assert [x.id for x in rows] == [r1]


def test_list_runs_filtered_by_project_and_status():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(project_tag="陶瓷膜-B", end_status="alarm_abort")
    assert [x.id for x in rows] == [r2]


def test_list_runs_pagination():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(limit=1, offset=0)
    assert len(rows) == 1 and rows[0].id == r2
    rows = r.list_runs(limit=1, offset=1)
    assert len(rows) == 1 and rows[0].id == r1


def test_get_run_returns_run_or_none():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    found = r.get_run(r1)
    assert found is not None and found.id == r1 and found.device_id == d1
    assert r.get_run(99999) is None


def test_list_samples_for_run_ordered_by_time():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    samples = r.list_samples_for_run(r1)
    assert len(samples) == 2
    assert samples[0].ts_ms < samples[1].ts_ms


def test_list_events_for_run_ordered_by_time():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    events = r.list_events_for_run(r1)
    assert len(events) == 2
    assert events[0].event_type == "start" and events[1].event_type == "stop"


def test_open_run_persists_setpoint_tags():
    r = make_repo()
    did = r.upsert_device("pump-1", "tyd02")
    rid = r.open_run(did, 1, 1751000000_000,
                     {"operator": "alice", "project_tag": "陶瓷膜-A",
                      "experiment_tag": "exp-01"})
    run = r.get_run(rid)
    assert run.operator == "alice"
    assert run.project_tag == "陶瓷膜-A"
    assert run.experiment_tag == "exp-01"


def test_list_runs_filtered_by_operator():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(operator="alice")
    assert [x.id for x in rows] == [r1]


def test_list_runs_filtered_by_experiment_tag():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(experiment_tag="exp-01")
    assert [x.id for x in rows] == [r1]


def test_list_runs_filtered_by_time_range():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(start_ms=1751000000_000, end_ms=1751000060_000)
    assert [x.id for x in rows] == [r1]
    rows = r.list_runs(start_ms=1751001000_000, end_ms=1751001600_000)
    assert [x.id for x in rows] == [r2]


def test_list_runs_filtered_by_tagged():
    r = make_repo()
    d1, d2, r1, r2 = _seed_runs(r)
    rows = r.list_runs(tagged=True)
    assert [x.id for x in rows] == [r1]
    rows = r.list_runs(tagged=False)
    assert [x.id for x in rows] == [r2]
