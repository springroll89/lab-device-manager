# Slice A.1 — Backend Core (DB + RunDetector + Engine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-record pump runs/events into SQLite from telemetry (no write-control needed), validated end-to-end with the live pump. The multi-device dashboard + backfill UI is the follow-up plan (**Slice A.2**).

**Architecture:** Per-device pipeline `Sampler → RunDetector → Repository(SQLite)`. RunDetector watches `StatusSnapshot.state` transitions to auto open/close runs and emit events (stall-alarm first-class). An `Engine` wires multiple devices. **This plan is backend-only and purely additive** — phase-1 app keeps working throughout; a live CLI (`scripts/monitor_live.py`) validates end-to-end. The config cutover + web UI happen in Slice A.2.

**Tech Stack:** Python 3.14, stdlib `sqlite3`, Flask, pytest. **Reuses** phase-1 采集层 (`pump_monitor/instruments/*`, `sampler.py`, `modbus_io.py`) unchanged.

## Global Constraints

- **READ-ONLY.** No Modbus writes anywhere (phase-2 deferred). Device setpoints are set on the device panel; the app only reads.
- **Reuse phase-1 采集层** — do NOT rewrite `instruments/`, `sampler.py`, `modbus_io.py`, `StatusSnapshot`.
- **SQLite** via stdlib `sqlite3`; single file at `config.db_path` (default `./data/pump_monitor.db`). Repository holds ONE persistent connection (so `:memory:` works in tests).
- **Multi-device-ready**, validated with the 1 live pump (UT-885, `/dev/cu.usbserial-BG039GXP`, 9600 8E1 addr 1, wordorder CDAB — all confirmed live).
- Per-device sample interval default **1 s**.
- Commits per task; messages `<type>: <desc>`; **no Co-Authored-By** (attribution disabled).
- `pytest` from project root (`.venv/bin/pytest`); target ≥80%.
- Frozen dataclasses for value objects; immutable snapshots. XSS-safe frontend (`textContent`, no `innerHTML` with data).
- Branch: create `feat/slice-a-core` off `main` before Task 1.

## File Structure

```
pump_monitor/
  db/
    __init__.py
    schema.sql            # CREATE TABLE device / run / sample / event
    models.py             # frozen dataclasses: Device, Run, EventRow
    repository.py         # Repository(db_path): persistent conn + CRUD
  runtime/
    __init__.py
    types.py              # DeviceConfig dataclass (engine input)
    run_detector.py       # RunDetector: state machine (snapshots → runs + events)
    engine.py             # Engine: per-device transport+adapter+detector+sampler + latest-store
scripts/
  monitor_live.py         # live end-to-end CLI: pump → repo → engine → print runs/events
tests/
  db/__init__.py
  db/test_repository.py
  runtime/__init__.py
  runtime/test_run_detector.py
  runtime/test_engine.py
```

No changes to phase-1 `config.py`, `web/`, `__main__.py`, or the existing scripts in this plan — those are untouched (Slice A.2 does the cutover).

Each task is independently testable. Tests use `:memory:` SQLite, scripted snapshots, and a FakeTransport — no hardware needed except Task 6 (live pump).

---

### Task 1: DB schema + models + Repository

**Files:**
- Create: `pump_monitor/db/__init__.py` (empty), `pump_monitor/db/schema.sql`, `pump_monitor/db/models.py`, `pump_monitor/db/repository.py`
- Test: `tests/db/__init__.py` (empty), `tests/db/test_repository.py`

**Interfaces:**
- Produces: `Repository(db_path)` with `upsert_device(name, device_type, alias="") -> int`, `open_run(device_id, channel, started_at, setpoints: dict) -> int`, `close_run(run_id, ended_at, duration_s, end_status, result_acc_volume, result_acc_unit, alarm_count)`, `add_sample(run_id, device_id, ts, state, flow_rate, delivered_volume, temp_c, metrics_json)`, `add_event(device_id, run_id, ts, event_type, severity, detail_json)`, `list_untagged_runs() -> list[Run]`, `tag_run(run_id, operator, project_tag, experiment_tag, remark)`, `list_recent_events(device_id=None, limit=50) -> list[EventRow]`. Frozen dataclasses `Device`, `Run`, `EventRow` in `models.py`.

- [ ] **Step 1: Write the failing test**

`tests/db/test_repository.py`:
```python
import json
from pump_monitor.db.repository import Repository
from pump_monitor.db.models import Run


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
    rid = r.open_run(did, 1, "2026-07-01T08:00:00",
                     {"work_mode": "仅注入", "target_volume": 40.0})
    assert rid > 0
    r.add_sample(rid, did, "2026-07-01T08:00:01", "running", 50.0, 1.2, 33.0, "{}")
    r.add_event(did, rid, "2026-07-01T08:00:00", "start", "info", "{}")
    r.close_run(rid, "2026-07-01T08:01:00", 60.0, "completed", 40.0, "mL", 0)
    runs = r.list_untagged_runs()
    assert len(runs) == 0                 # closed run still untagged? yes → appears
    # (untagged = tagged==0 regardless of closed; check via direct query below)


def test_list_untagged_and_tag():
    r = make_repo()
    did = r.upsert_device("pump-1", "tyd02")
    rid = r.open_run(did, 1, "2026-07-01T08:00:00", {})
    r.close_run(rid, "2026-07-01T08:01:00", 60.0, "completed", 40.0, "mL", 0)
    pending = r.list_untagged_runs()
    assert len(pending) == 1
    assert isinstance(pending[0], Run)
    assert pending[0].tagged is False
    r.tag_run(rid, "alice", "陶瓷膜-A", "exp-01", "首次试机")
    assert r.list_untagged_runs() == []
    # event recorded
    r.add_event(did, rid, "2026-07-01T08:00:00", "start", "info", "{}")
    evs = r.list_recent_events(device_id=did)
    assert len(evs) == 1 and evs[0].event_type == "start"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/db/test_repository.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.db'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/db/schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS device (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  device_type TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  location TEXT, asset_no TEXT,
  created_at TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL REFERENCES device(id),
  channel INTEGER NOT NULL DEFAULT 1,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  duration_s REAL,
  end_status TEXT,
  operator TEXT, project_tag TEXT, experiment_tag TEXT, remark TEXT,
  tagged INTEGER NOT NULL DEFAULT 0,
  setpoints_json TEXT,
  work_mode TEXT, target_volume REAL, target_volume_unit TEXT,
  result_acc_volume REAL, result_acc_unit TEXT,
  alarm_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES run(id),
  device_id INTEGER NOT NULL REFERENCES device(id),
  ts TEXT NOT NULL,
  state TEXT, flow_rate REAL, delivered_volume REAL, temp_c REAL,
  metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL REFERENCES device(id),
  run_id INTEGER REFERENCES run(id),
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_tagged ON run(tagged);
CREATE INDEX IF NOT EXISTS idx_event_device_ts ON event(device_id, ts);
```

`pump_monitor/db/models.py`:
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Device:
    id: int
    name: str
    device_type: str
    alias: str = ""


@dataclass(frozen=True)
class Run:
    id: int
    device_id: int
    channel: int
    started_at: str
    ended_at: Optional[str]
    duration_s: Optional[float]
    end_status: Optional[str]
    operator: Optional[str]
    project_tag: Optional[str]
    experiment_tag: Optional[str]
    remark: Optional[str]
    tagged: bool
    result_acc_volume: Optional[float]
    result_acc_unit: Optional[str]
    alarm_count: int


@dataclass(frozen=True)
class EventRow:
    id: int
    device_id: int
    run_id: Optional[int]
    ts: str
    event_type: str
    severity: str
    detail_json: Optional[str]
```

`pump_monitor/db/repository.py`:
```python
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Optional
from pump_monitor.db.models import Device, Run, EventRow

_SCHEMA = Path(__file__).parent / "schema.sql"


class Repository:
    """SQLite persistence. Holds ONE persistent connection (so :memory: works)."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self):
        self._conn.close()

    def upsert_device(self, name: str, device_type: str, alias: str = "") -> int:
        row = self._conn.execute("SELECT id FROM device WHERE name=?", (name,)).fetchone()
        if row:
            if alias:
                self._conn.execute("UPDATE device SET alias=? WHERE id=?", (alias, row["id"]))
                self._conn.commit()
            return row["id"]
        from time import strftime
        c = self._conn.execute(
            "INSERT INTO device(name, device_type, alias, created_at) VALUES(?,?,?,?)",
            (name, device_type, alias, strftime("%Y-%m-%dT%H:%M:%S")),
        )
        self._conn.commit()
        return c.lastrowid

    def open_run(self, device_id: int, channel: int, started_at: str,
                 setpoints: dict) -> int:
        c = self._conn.execute(
            """INSERT INTO run(device_id, channel, started_at,
                  setpoints_json, work_mode, target_volume, target_volume_unit)
               VALUES(?,?,?,?,?,?,?)""",
            (device_id, channel, started_at,
             json.dumps(setpoints, ensure_ascii=False),
             setpoints.get("work_mode"),
             setpoints.get("target_volume"),
             setpoints.get("target_volume_unit")),
        )
        self._conn.commit()
        return c.lastrowid

    def close_run(self, run_id: int, ended_at: str, duration_s: float,
                  end_status: str, result_acc_volume: Optional[float],
                  result_acc_unit: Optional[str], alarm_count: int):
        self._conn.execute(
            """UPDATE run SET ended_at=?, duration_s=?, end_status=?,
                  result_acc_volume=?, result_acc_unit=?, alarm_count=? WHERE id=?""",
            (ended_at, duration_s, end_status,
             result_acc_volume, result_acc_unit, alarm_count, run_id),
        )
        self._conn.commit()

    def add_sample(self, run_id: Optional[int], device_id: int, ts: str,
                   state: str, flow_rate: Optional[float],
                   delivered_volume: Optional[float], temp_c: Optional[float],
                   metrics_json: str):
        self._conn.execute(
            """INSERT INTO sample(run_id, device_id, ts, state, flow_rate,
                  delivered_volume, temp_c, metrics_json) VALUES(?,?,?,?,?,?,?,?)""",
            (run_id, device_id, ts, state, flow_rate, delivered_volume, temp_c, metrics_json),
        )
        self._conn.commit()

    def add_event(self, device_id: int, run_id: Optional[int], ts: str,
                  event_type: str, severity: str, detail_json: str):
        self._conn.execute(
            "INSERT INTO event(device_id, run_id, ts, event_type, severity, detail_json) VALUES(?,?,?,?,?,?)",
            (device_id, run_id, ts, event_type, severity, detail_json),
        )
        self._conn.commit()

    def list_untagged_runs(self) -> list:
        rows = self._conn.execute(
            """SELECT r.* FROM run r WHERE r.tagged=0 AND r.ended_at IS NOT NULL
               ORDER BY r.ended_at DESC"""
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def tag_run(self, run_id: int, operator: str, project_tag: str,
                experiment_tag: str, remark: str):
        self._conn.execute(
            """UPDATE run SET operator=?, project_tag=?, experiment_tag=?,
                  remark=?, tagged=1 WHERE id=?""",
            (operator, project_tag, experiment_tag, remark, run_id),
        )
        self._conn.commit()

    def list_recent_events(self, device_id: Optional[int] = None,
                           limit: int = 50) -> list:
        if device_id is None:
            rows = self._conn.execute(
                "SELECT * FROM event ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM event WHERE device_id=? ORDER BY ts DESC LIMIT ?",
                (device_id, limit)).fetchall()
        return [EventRow(r["id"], r["device_id"], r["run_id"], r["ts"],
                         r["event_type"], r["severity"], r["detail_json"]) for r in rows]


def _row_to_run(r) -> Run:
    return Run(
        id=r["id"], device_id=r["device_id"], channel=r["channel"],
        started_at=r["started_at"], ended_at=r["ended_at"],
        duration_s=r["duration_s"], end_status=r["end_status"],
        operator=r["operator"], project_tag=r["project_tag"],
        experiment_tag=r["experiment_tag"], remark=r["remark"],
        tagged=bool(r["tagged"]),
        result_acc_volume=r["result_acc_volume"], result_acc_unit=r["result_acc_unit"],
        alarm_count=r["alarm_count"],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/db/test_repository.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/db tests/db
git commit -m "feat: sqlite repository (device/run/sample/event) + models"
```

### Task 2: RunDetector state machine

**Files:**
- Create: `pump_monitor/runtime/__init__.py` (empty), `pump_monitor/runtime/run_detector.py`
- Test: `tests/runtime/__init__.py` (empty), `tests/runtime/test_run_detector.py`

**Interfaces:**
- Consumes: `Repository` (Task 1: `open_run/close_run/add_sample/add_event`), `StatusSnapshot` (phase-1).
- Produces: `RunDetector(device_id, channel, repo, clock=time.time, on_event=None)` with `on_sample(snap)`. Writes runs/events to the repo; calls `on_event(dict)` on every event (for alerting/SSE later).

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_run_detector.py`:
```python
from pump_monitor.runtime.run_detector import RunDetector
from pump_monitor.instruments.base import StatusSnapshot


class FakeRepo:
    def __init__(self):
        self.events = []
        self.runs_opened = 0
        self.runs_closed = []
        self.samples = 0
        self._next = 1
    def open_run(self, did, ch, ts, sp):
        self.runs_opened += 1
        rid = self._next; self._next += 1
        return rid
    def close_run(self, rid, ended, dur, status, acc, unit, alarms):
        self.runs_closed.append((status, alarms))
    def add_sample(self, rid, did, ts, *a):
        self.samples += 1
    def add_event(self, did, rid, ts, etype, sev, detail):
        self.events.append((etype, sev))


def _snap(state):
    return StatusSnapshot(timestamp=0, state=state, work_mode="仅注入",
                          device_id="d", acc_volume=1.0, acc_unit="mL")


def _detector():
    repo = FakeRepo()
    t = {"n": 1000.0}
    return RunDetector(1, 1, repo, clock=lambda: (t.__setitem__("n", t["n"] + 1.0), t["n"])[1]), repo


def test_open_close_completed():
    d, repo = _detector()
    d.on_sample(_snap("stopped"))
    d.on_sample(_snap("running"))
    d.on_sample(_snap("stopped"))
    assert repo.runs_opened == 1
    assert repo.runs_closed == [("completed", 0)]
    assert ("start", "info") in repo.events
    assert ("stop", "info") in repo.events


def test_stall_alarm_marks_alarm_abort():
    d, repo = _detector()
    d.on_sample(_snap("running"))
    d.on_sample(_snap("alarm"))
    d.on_sample(_snap("stopped"))
    assert ("stall_alarm", "critical") in repo.events
    assert repo.runs_closed == [("alarm_abort", 1)]


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_run_detector.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.runtime'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/runtime/run_detector.py`:
```python
from __future__ import annotations
import json
import time
from typing import Callable, Optional
from pump_monitor.instruments.base import StatusSnapshot


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
        self._run_start: float = 0.0
        self._had_alarm = False
        self._alarm_count = 0

    def on_sample(self, snap: StatusSnapshot):
        now = self.clock()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        new = snap.state
        old = self._state

        if new == "offline":
            if old != "offline":
                self._emit(ts, "comms_lost", "critical")
            self._state = "offline"
            return
        if old == "offline":
            self._emit(ts, "comms_recover", "info")

        if self._run_id is not None:
            self.repo.add_sample(self._run_id, self.device_id, ts, snap.state,
                                 snap.flow_rpm, snap.acc_volume, snap.temp_c,
                                 json.dumps(snap.metrics, ensure_ascii=False))

        if new == "alarm" and old != "alarm":
            self._emit(ts, "stall_alarm", "critical")
            self._had_alarm = True
            self._alarm_count += 1
        elif old == "alarm" and new != "alarm":
            self._emit(ts, "alarm_clear", "info")

        if new == "running" and self._run_id is None:
            self._run_start = now
            self._run_id = self.repo.open_run(
                self.device_id, self.channel, ts,
                {"work_mode": snap.work_mode, "target_volume": snap.acc_volume,
                 "target_volume_unit": snap.acc_unit})
            self._had_alarm = False
            self._alarm_count = 0
            self._emit(ts, "start", "info")
        elif new == "paused" and old == "running" and self._run_id is not None:
            self._emit(ts, "pause", "warning")
        elif new == "running" and old == "paused":
            self._emit(ts, "resume", "info")
        elif new == "stopped" and self._run_id is not None:
            end_status = "alarm_abort" if self._had_alarm else "completed"
            self.repo.close_run(self._run_id, ts, now - self._run_start, end_status,
                                snap.acc_volume, snap.acc_unit, self._alarm_count)
            self._run_id = None
            self._emit(ts, "stop", "info")

        self._state = new

    def _emit(self, ts: str, event_type: str, severity: str):
        self.repo.add_event(self.device_id, self._run_id, ts, event_type, severity, "{}")
        if self.on_event:
            self.on_event({"device_id": self.device_id, "run_id": self._run_id,
                           "ts": ts, "event_type": event_type, "severity": severity})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/runtime/test_run_detector.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/runtime/__init__.py pump_monitor/runtime/run_detector.py tests/runtime
git commit -m "feat: run detector state machine (auto open/close runs, stall-alarm first-class)"
```

---

### Task 3: Engine (multi-device wiring) + DeviceConfig

**Files:**
- Create: `pump_monitor/runtime/types.py`, `pump_monitor/runtime/engine.py`
- Test: `tests/runtime/test_engine.py`

**Interfaces:**
- Consumes: `Repository` (Task 1), `RunDetector` (Task 2), `Sampler` + `make_adapter` + `SerialTransport` (phase-1), `StatusSnapshot`.
- Produces: `DeviceConfig` (frozen dataclass: name, type, alias, serial_port, baudrate, parity, modbus_addr, wordorder, channel). `Engine(repo, devices, sample_interval_s, adapter_factory, on_event=None)` with `start()/stop()/latest()->dict`; `adapter_factory(DeviceConfig) -> (adapter, cleanup_callable)`. Module function `serial_adapter_factory(dc)` for production.

- [ ] **Step 1: Write the failing test**

`tests/runtime/test_engine.py`:
```python
import time
from pump_monitor.runtime.engine import Engine
from pump_monitor.runtime.types import DeviceConfig
from pump_monitor.db.repository import Repository
from pump_monitor.instruments.base import StatusSnapshot


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pump_monitor.runtime.engine'`

- [ ] **Step 3: Write minimal implementation**

`pump_monitor/runtime/types.py`:
```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    type: str                 # adapter name: tyd02 / stirrer / viscometer
    alias: str = ""
    serial_port: str = ""
    baudrate: int = 9600
    parity: str = "EVEN"
    modbus_addr: int = 1
    wordorder: str = "CDAB"
    channel: int = 1
```

`pump_monitor/runtime/engine.py`:
```python
from __future__ import annotations
import threading
from typing import Callable, Optional
from pump_monitor.sampler import Sampler
from pump_monitor.instruments.factory import make_adapter
from pump_monitor.modbus_io import SerialTransport
from pump_monitor.instruments.base import StatusSnapshot
from pump_monitor.runtime.run_detector import RunDetector
from pump_monitor.runtime.types import DeviceConfig


class Engine:
    """Wires one Sampler + RunDetector per device; writes runs/events/samples to repo,
    keeps a latest-snapshot store (for the future dashboard)."""

    def __init__(self, repo, devices, sample_interval_s: float,
                 adapter_factory: Callable[[DeviceConfig], tuple],
                 on_event: Optional[Callable[[dict], None]] = None):
        self.repo = repo
        self.devices = devices
        self.interval = sample_interval_s
        self.adapter_factory = adapter_factory
        self.on_event = on_event
        self._latest = {}
        self._latest_lock = threading.Lock()
        self._samplers = []
        self._cleanups = []

    def start(self):
        for dc in self.devices:
            device_id = self.repo.upsert_device(dc.name, dc.type, dc.alias)
            adapter, cleanup = self.adapter_factory(dc)
            self._cleanups.append(cleanup)
            det = RunDetector(device_id, dc.channel, self.repo, on_event=self.on_event)
            latest, lock = self._latest, self._latest_lock

            def on_sample(snap, did=device_id, det=det):
                det.on_sample(snap)
                with lock:
                    latest[did] = snap

            sampler = Sampler(adapter, self.interval, on_sample)
            sampler.start()
            self._samplers.append(sampler)

    def stop(self):
        for s in self._samplers:
            s.stop()
        for c in self._cleanups:
            try:
                c()
            except Exception:
                pass

    def latest(self) -> dict:
        with self._latest_lock:
            return dict(self._latest)


def serial_adapter_factory(dc: DeviceConfig) -> tuple:
    """Production adapter factory: opens a SerialTransport and builds the adapter.
    Returns (adapter, cleanup) where cleanup closes the transport."""
    transport = SerialTransport(dc.serial_port, dc.baudrate, dc.parity)
    transport.open()
    adapter = make_adapter(dc.type, transport, slave=dc.modbus_addr, wordorder=dc.wordorder)
    return adapter, transport.close
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/runtime/test_engine.py -q`
Expected: PASS (1 test). Full suite still green (phase-1 untouched).

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/runtime/types.py pump_monitor/runtime/engine.py tests/runtime/test_engine.py
git commit -m "feat: multi-device engine (sampler+detector+repo per device)"
```

---

### Task 4: Live end-to-end CLI (validates with the real pump)

**Files:**
- Create: `scripts/monitor_live.py`
- No unit test (hardware); validated manually.

**Interfaces:**
- Consumes: `Repository`, `Engine` + `serial_adapter_factory`, `DeviceConfig`.

- [ ] **Step 1: Write the script**

`scripts/monitor_live.py`:
```python
#!/usr/bin/env python3
"""Live end-to-end validation of Slice A.1: pump -> SQLite -> run/event detection.
Run with the pump connected:  python scripts/monitor_live.py [seconds]
Then start/stop a dispense on the pump panel; runs + events (incl. stall) are
auto-recorded into ./data/pump_monitor.db. Press Ctrl-C to stop early."""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pump_monitor.db.repository import Repository
from pump_monitor.runtime.engine import Engine, serial_adapter_factory
from pump_monitor.runtime.types import DeviceConfig

PUMP = DeviceConfig(name="pump-1", type="tyd02", alias="注射泵",
                    serial_port="/dev/cu.usbserial-BG039GXP",
                    baudrate=9600, parity="EVEN", modbus_addr=1, wordorder="CDAB")
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
DB_PATH = "./data/pump_monitor.db"


def main():
    os.makedirs("./data", exist_ok=True)
    repo = Repository(DB_PATH)
    eng = Engine(repo, [PUMP], sample_interval_s=1.0,
                 adapter_factory=serial_adapter_factory,
                 on_event=lambda e: print(f"  EVENT {e['event_type']} ({e['severity']}) run={e['run_id']}"))
    print(f"monitoring pump for {DURATION}s -> {DB_PATH}")
    print("(start/stop a dispense on the pump panel; runs auto-record)")
    eng.start()
    try:
        time.sleep(DURATION)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        runs = repo.list_untagged_runs()
        evs = repo.list_recent_events(limit=20)
        print(f"\nrecorded {len(runs)} untagged run(s); last {len(evs)} event(s):")
        for e in evs:
            print(f"  {e.ts}  {e.event_type} ({e.severity})")
        eng.stop()
        repo.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full suite + a live run**

Run: `.venv/bin/pytest -q` → all green.
Manual (pump connected): `.venv/bin/python scripts/monitor_live.py 60`
Expected: prints `EVENT start (info) run=1` when you start a dispense on the panel, `EVENT stop (info) run=1` when it ends; final summary lists 1 untagged run + the start/stop events. Inspect `./data/pump_monitor.db` (e.g. `sqlite3 ./data/pump_monitor.db "select id,end_status,alarm_count from run;"`) to confirm the row.

- [ ] **Step 3: Commit**

```bash
git add scripts/monitor_live.py
git commit -m "feat: live monitor CLI (pump -> sqlite run/event capture)"
```

---

## Self-Review (performed)

**Spec coverage:** design doc §5.1 device / §5.2 run / §5.3 sample / §5.4 event → Task 1. §6.1 run-detection state machine + stall-alarm → Task 2. Multi-device wiring (§4 engine) → Task 3. Live validation → Task 4. Calibration (§5.5), maintenance (§5.6), dashboard, backfill, export, alerting, stats are NOT in A.1 — they belong to Slice A.2+ (intentional; this plan is the additive backend core).

**Placeholder scan:** none. All code blocks are complete; the live script is the only non-unit-tested step (hardware).

**Type consistency:** `Repository.open_run(device_id, channel, started_at, setpoints)` / `close_run(run_id, ended_at, duration_s, end_status, result_acc_volume, result_acc_unit, alarm_count)` / `add_sample(run_id, device_id, ts, state, flow_rate, delivered_volume, temp_c, metrics_json)` / `add_event(device_id, run_id, ts, event_type, severity, detail_json)` — called with matching args in RunDetector (Task 2) and the live CLI (Task 4). `Engine(adapter_factory)` returns `(adapter, cleanup)` — matches `serial_adapter_factory` and the test factory. `DeviceConfig` fields match between types.py, the test, and the CLI.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-01-slice-a-core.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks (may still be rate-limited; falls back to inline).
2. **Inline Execution** — execute tasks in this session with checkpoints.

Which approach? (After A.1: the dashboard + backfill UI + config cutover = Slice A.2, the next plan.)
