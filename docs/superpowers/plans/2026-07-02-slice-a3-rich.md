# Slice A.3 — Rich Telemetry + Volume Fix + Device-Detail Page Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Enrich pump telemetry (target/actual/lifetime volumes, full process settings, syringe), fix the misleading volume display (show single-run actual, not lifetime), enrich CSV, and add a device-detail page.

**Architecture:** TYD02Adapter reads mode-specific setpoints + process settings into `metrics`; RunDetector captures lifetime at run open/close → computes `actual_volume` (delta) and passes full `metrics` as setpoints; run table gains `actual_volume`/`actual_unit` (the rest stays in `setpoints_json`); dashboard + device-detail page render the rich data.

**Tech Stack:** Python 3.14, SQLite, Flask, pytest. Reuses A.1/A.2 code.

## Global Constraints

- **READ-ONLY** (no device writes). Reuse A.1/A.2 db/runtime/web.
- **Volume semantics** (live-confirmed): 设定=target (mode structure 4128/4146/4164/4194 or 4015 for continuous); 实际=actual_volume = lifetime(1032) end−start (1035 消耗 resets on stop, unreliable at close); lifetime=1032 (device stat).
- **Syringe**: 4021=code (9=BD Glass 100ml, 1 data point); capacity via 4090/4092. Vendor NAME needs a code map (deferred — show code + capacity for now).
- 提前预警% has no register (not exposed — skip). 1052/1054 max/min flow unsupported on this firmware (skip).
- Epoch-ms timestamps (A.2). SQLite, persistent conn, busy_timeout. XSS-safe frontend. `.venv/bin/pytest`, no Co-Authored-By.
- Branch `feat/slice-a3` off `main`. Dev DBs recreated (schema change via `CREATE TABLE` — no migration).

## File Structure
```
pump_monitor/
  instruments/leadfluid_tyd02.py  # MODIFY read_status: +mode-structure, +process-settings, +syringe into metrics
  db/schema.sql                   # MODIFY run: +actual_volume REAL, +actual_unit TEXT
  db/models.py                    # MODIFY Run: +actual_volume, +actual_unit
  db/repository.py                # MODIFY close_run: +actual_volume, +actual_unit; open_run stores metrics as setpoints
  runtime/run_detector.py         # MODIFY: capture lifetime at open/close→actual delta; pass full metrics to open_run
  web/app.py                      # MODIFY /api/runs + CSV: expose setpoints_json + actual_volume; +GET /device/<id>
  web/static/index.html           # MODIFY: card shows single-run actual + config; link to detail
  web/static/app.js               # MODIFY: render new fields; +device-detail navigation
  web/static/device.html          # CREATE: device-detail page
  web/static/device.js            # CREATE: detail page logic
tests/
  instruments/test_tyd02_adapter.py  # MODIFY: FakeClient returns mode-structure bytes; assert new metrics
  db/test_repository.py               # MODIFY: actual_volume in close_run + Run
  runtime/test_run_detector.py        # MODIFY: assert actual_volume delta + setpoints passed
  web/test_app.py                     # MODIFY: /api/runs includes setpoints + actual; +/device/<id>
```

---

### Task 1: Expand TYD02Adapter — mode-structure + process-settings + syringe

**Files:**
- Modify: `pump_monitor/instruments/leadfluid_tyd02.py`
- Test: `tests/instruments/test_tyd02_adapter.py`

**Interfaces:**
- Produces: `read_status()` now populates `StatusSnapshot.metrics` with: `target_volume`, `target_unit`, `inject_rate`, `inject_rate_unit`, `pause_delay_ms`, `repeat_count`, `force`, `stall_alarm_enabled`, `syringe_code`, `syringe_capacity`, `syringe_unit_code`. A helper `_read_mode_setpoints(client, mode, wo) -> dict` reads the mode-specific structure.

- [ ] **Step 1: Add the failing test**

Append to `tests/instruments/test_tyd02_adapter.py`:
```python
def u16(v):
    import struct
    return struct.pack(">H", v)

def f32cdab(v):
    import struct
    b = struct.pack(">f", v)
    return bytes([b[2], b[3], b[0], b[1]])

def test_read_status_includes_rich_metrics():
    # base regs 0..15 (stirrer-style block not used here; use the pump's read_status which reads specific addrs)
    # The adapter reads specific input+holding registers; FakeClient must provide all of them.
    # Reuse the existing _adapter() helper which sets up input(1000..1047) + holding(4017..4025).
    # Add the NEW holding registers the adapter will read: 4021,4027,4087,4090,4092,4093-4097,4128(mode struct)
    inp, hold = _adapter_inputs()  # helper that builds the standard input+holding dict (refactor from _adapter)
    hold[(4021, 1)] = u16(9)           # syringe code = BD Glass 100ml
    hold[(4027, 1)] = u16(100)         # force
    hold[(4087, 1)] = u16(1)           # stall alarm enabled
    hold[(4090, 2)] = f32cdab(100.0)   # syringe capacity
    hold[(4092, 1)] = u16(2)           # syringe unit = mL
    hold[(4093, 1)] = u16(0)           # pause h
    hold[(4094, 1)] = u16(0)           # pause m
    hold[(4095, 1)] = u16(5)           # pause s
    hold[(4096, 1)] = u16(0)           # pause ms
    hold[(4097, 1)] = u16(3)           # repeat count
    hold[(4128, 6)] = f32cdab(50.0) + f32cdab(5.0) + u16(2) + u16(2)  # target 50mL @ 5.0 mL/min
    s = StirrerAdapter._adapter_pump(inp, hold).read_status()  # or however _adapter constructs
    assert s.metrics["syringe_code"] == 9
    assert s.metrics["syringe_capacity"] == 100.0
    assert s.metrics["target_volume"] == 50.0
    assert s.metrics["inject_rate"] == 5.0
    assert s.metrics["pause_delay_ms"] == 5000
    assert s.metrics["repeat_count"] == 3
    assert s.metrics["force"] == 100
    assert s.metrics["stall_alarm_enabled"] is True
```
NOTE: the existing `_adapter()` helper in this test file builds `inp` + `hold` dicts internally. **Refactor** `_adapter()` to expose an `_adapter_inputs()` helper returning `(inp, hold)` so the new test can add registers. If the existing test file's `_adapter` is in a different test module, adapt accordingly — the key is the FakeClient must return bytes for ALL addresses `read_status` queries (old + new).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/instruments/test_tyd02_adapter.py -q`
Expected: FAIL — `KeyError: 'syringe_code'` (metrics doesn't have it yet).

- [ ] **Step 3: Implement — add `_read_mode_setpoints` + expand `read_status` metrics**

Add to `pump_monitor/instruments/leadfluid_tyd02.py`:
```python
def _read_mode_setpoints(client, mode, wo):
    """Read target volume + flow rate from the mode-specific data structure."""
    result = {"target_volume": None, "target_unit": "", "inject_rate": None, "inject_rate_unit": ""}
    try:
        if mode == 0:      # 仅注入 @4128
            s = client.read_holding_registers(4128, 6)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[8:10]), "")
            result["inject_rate_unit"] = {0: "nL/min", 1: "uL/min", 2: "mL/min"}.get(decode_uint16(s[10:12]), "")
        elif mode == 1:    # 仅抽取 @4146
            s = client.read_holding_registers(4146, 6)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[8:10]), "")
        elif mode in (2, 3):  # 抽取注入@4164 / 注入抽取@4194
            base = 4164 if mode == 2 else 4194
            s = client.read_holding_registers(base, 10)
            result["target_volume"] = decode_float(s[0:4], wo)
            result["inject_rate"] = decode_float(s[4:8], wo)
            result["target_unit"] = VOLUME_UNITS.get(decode_uint16(s[8:10]), "")
        elif mode == 4:    # 连续 @4015
            s = client.read_holding_registers(4015, 2)
            result["inject_rate"] = decode_float(s[0:4], wo)
    except Exception:
        pass   # mode structure unreadable → leave None
    return result
```

In `read_status()`, after building the existing `metrics` dict (which already has alarm_flags etc.), add:
```python
        # --- rich config (mode structure + process settings + syringe) ---
        ms = _read_mode_setpoints(c, mode, wo)
        pause_h = decode_uint16(c.read_holding_registers(4093, 1))
        pause_m = decode_uint16(c.read_holding_registers(4094, 1))
        pause_s = decode_uint16(c.read_holding_registers(4095, 1))
        pause_ms_raw = decode_uint16(c.read_holding_registers(4096, 1))
        syr_code = decode_uint16(c.read_holding_registers(4021, 1))
        syr_cap_raw = c.read_holding_registers(4090, 2)
        syr_unit = decode_uint16(c.read_holding_registers(4092, 1))
        metrics["target_volume"] = ms["target_volume"]
        metrics["target_unit"] = ms["target_unit"]
        metrics["inject_rate"] = ms["inject_rate"]
        metrics["inject_rate_unit"] = ms["inject_rate_unit"]
        metrics["pause_delay_ms"] = pause_h * 3600000 + pause_m * 60000 + pause_s * 1000 + pause_ms_raw
        metrics["repeat_count"] = decode_uint16(c.read_holding_registers(4097, 1))
        metrics["force"] = decode_uint16(c.read_holding_registers(4027, 1))
        metrics["stall_alarm_enabled"] = bool(decode_uint16(c.read_holding_registers(4087, 1)))
        metrics["syringe_code"] = syr_code
        metrics["syringe_capacity"] = decode_float(syr_cap_raw, wo)
        metrics["syringe_unit_code"] = syr_unit
```

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `.venv/bin/pytest tests/instruments/test_tyd02_adapter.py -q` → PASS. `.venv/bin/pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/instruments/leadfluid_tyd02.py tests/instruments/test_tyd02_adapter.py
git commit -m "feat: expand tyd02 telemetry (mode-structure target/rate + process settings + syringe)"
```

### Task 2: Schema + RunDetector actual-volume delta + Repository

**Files:**
- Modify: `pump_monitor/db/schema.sql` (+actual_volume, +actual_unit in run), `pump_monitor/db/models.py` (Run +actual_volume/actual_unit), `pump_monitor/db/repository.py` (close_run +actual_volume/actual_unit), `pump_monitor/runtime/run_detector.py` (capture lifetime at open/close → delta)
- Test: `tests/db/test_repository.py`, `tests/runtime/test_run_detector.py`

**Interfaces:**
- `close_run(run_id, ended_ms, end_status, actual_volume, actual_unit, lifetime_acc, lifetime_acc_unit, alarm_count)`.
- RunDetector: `_lifetime_start` captured at open_run; at close, `actual = snap.acc_volume - self._lifetime_start`; passes actual + snap.acc_volume(lifetime) to close_run.
- open_run: now receives `dict(snap.metrics, work_mode=snap.work_mode)` as setpoints (rich config stored in setpoints_json).

- [ ] **Step 1: Update the failing tests**

`tests/runtime/test_run_detector.py` — FakeRepo.close_run must accept the new params; add a delta assertion:
```python
# FakeRepo.close_run signature update:
def close_run(self, rid, ended_ms, end_status, actual_volume, actual_unit, lifetime_acc, lifetime_acc_unit, alarms):
    self.runs_closed.append((end_status, alarms, actual_volume))

def test_actual_volume_is_lifetime_delta():
    d, repo = _detector()
    d.on_sample(_snap_with_acc("running", acc=100.0))  # lifetime at open = 100
    d.on_sample(_snap_with_acc("stopped", acc=150.0))  # lifetime at close = 150
    assert repo.runs_closed[-1][2] == 50.0  # actual_volume = 150 - 100
```
(Add a `_snap_with_acc(state, acc=1.0)` helper that sets `acc_volume=acc` on the snapshot.)

`tests/db/test_repository.py` — update close_run call:
```python
r.close_run(rid, 1751000060_000, "completed", 50.0, "mL", 150.0, "mL", 0)
```
+ assert `rows[0].actual_volume == 50.0`.

- [ ] **Step 2: Run → FAIL** (signature mismatch).

- [ ] **Step 3: Implement**

`pump_monitor/db/schema.sql` — in the `run` table, after `result_acc_unit`, add:
```sql
  actual_volume REAL,
  actual_unit TEXT,
```

`pump_monitor/db/models.py` — add to `Run` (after `result_acc_unit`):
```python
    actual_volume: Optional[float]
    actual_unit: Optional[str]
```
+ update `_row_to_run` in repository.py to read them.

`pump_monitor/db/repository.py` — update `close_run`:
```python
    def close_run(self, run_id, ended_ms, end_status, actual_volume, actual_unit,
                  lifetime_acc, lifetime_acc_unit, alarm_count):
        self._conn.execute(
            """UPDATE run SET ended_ms=?, duration_ms=?-started_ms, end_status=?,
                  result_acc_volume=?, result_acc_unit=?, actual_volume=?, actual_unit=?, alarm_count=? WHERE id=?""",
            (ended_ms, ended_ms, end_status, lifetime_acc, lifetime_acc_unit,
             actual_volume, actual_unit, alarm_count, run_id))
        self._conn.commit()
```

`pump_monitor/runtime/run_detector.py` — in `_open_run` (or the open branch):
```python
            self._lifetime_start = snap.acc_volume if snap.acc_volume is not None else 0.0
            self._run_id = self.repo.open_run(
                self.device_id, self.channel, now_ms,
                dict(snap.metrics, work_mode=snap.work_mode))
```
In the close branch:
```python
            actual = (snap.acc_volume or 0.0) - self._lifetime_start
            self.repo.close_run(self._run_id, now_ms, end_status,
                                actual, snap.acc_unit,
                                snap.acc_volume, snap.acc_unit, self._alarm_count)
```
(Add `self._lifetime_start = 0.0` in `__init__`.)

- [ ] **Step 4: Run → PASS** + full suite green.

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/db/ pump_monitor/runtime/run_detector.py tests/
git commit -m "feat: actual_volume (lifetime delta) + rich setpoints in run records"
```

---

### Task 3: API enrichment + device-detail endpoint

**Files:**
- Modify: `pump_monitor/web/app.py` — `/api/runs` returns setpoints_json parsed + actual_volume; CSV includes new columns; NEW `GET /device/<id>` (serves device.html); NEW `GET /api/devices/<id>` (device meta + latest + recent runs).
- Test: `tests/web/test_app.py`

- [ ] **Step 1: Write the failing test**
```python
def test_runs_include_setpoints_and_actual(tmp_path):
    app, repo, did = _app(tmp_path)
    rid = repo.open_run(did, 1, 1751000000_000, {"syringe_code": 9, "target_volume": 50.0})
    repo.close_run(rid, 1751000060_000, "completed", 50.0, "mL", 150.0, "mL", 0)
    r = app.test_client().get("/api/runs").get_json()[0]
    assert r["actual_volume"] == 50.0
    assert r["setpoints"]["syringe_code"] == 9
```

- [ ] **Step 2: Run → FAIL** (`actual_volume` and `setpoints` not in response).

- [ ] **Step 3: Implement**

In `web/app.py`, update `_run_to_dict`:
```python
import json as _json
def _run_to_dict(run):
    d = { ...existing fields... }
    d["actual_volume"] = run.actual_volume
    d["actual_unit"] = run.actual_unit
    # parse setpoints_json for the API consumer
    try:
        d["setpoints"] = _json.loads(run_setpoints_json) if run_setpoints_json else {}
    except Exception:
        d["setpoints"] = {}
    return d
```
NOTE: the Run dataclass doesn't carry `setpoints_json` — add it to the Run model (read from the DB row), OR query it separately in the endpoint. Simplest: add `setpoints_json: Optional[str]` to the Run model + read it in `_row_to_run`. Then `_run_to_dict` parses it.

Add device-detail routes:
```python
    @app.get("/device/<int:device_id>")
    def device_page(device_id):
        return send_from_directory(app.static_folder, "device.html")

    @app.get("/api/devices/<int:device_id>")
    def device_detail(device_id):
        latest = engine.latest()
        dmap = engine.device_map()
        snap = latest.get(device_id)
        dc = dmap.get(device_id)
        runs = repo.list_runs(limit=20)  # could filter by device_id
        return jsonify({
            "device": {"id": device_id, "name": dc.name if dc else "", "alias": dc.alias if dc else "",
                       "type": dc.type if dc else ""},
            "latest": _snap_to_dict(snap) if snap else None,
            "metrics": snap.metrics if snap else {},
            "runs": [_run_to_dict(r) for r in runs],
        })
```

Update CSV export to include: `actual_volume, actual_unit, syringe_code, target_volume, inject_rate, pause_delay_ms, repeat_count, force` — pull from `setpoints_json` (parse via json.loads per row). Add `_csv_safe` to each.

- [ ] **Step 4: Run → PASS** + full suite green.

- [ ] **Step 5: Commit**

```bash
git add pump_monitor/web/app.py pump_monitor/db/models.py pump_monitor/db/repository.py tests/web/test_app.py
git commit -m "feat: api enrich (setpoints+actual_volume in runs, csv, device-detail endpoint)"
```

---

### Task 4: Dashboard display fix + device-detail page

**Files:**
- Modify: `web/static/index.html` (card: show 单次实际 + config; clickable → detail), `web/static/app.js` (render metrics; navigate to detail)
- Create: `web/static/device.html`, `web/static/device.js`

- [ ] **Step 1: Implement**

`index.html` card changes — each card gets a `data-device-id` attribute + `onclick="location.href='/device/'+id"`. Card content shows: state, **单次实际液量** (actual_volume if available, else acc_volume with label "累计"), flow, temp, 读数时刻, + a small config line (mode + syringe_code + target).

`app.js pollStatus` — render actual_volume from `L.metrics.actual_volume || L.acc_volume` (labeled appropriately). Add config line from metrics.

`device.html` — a detail page: device name/alias, full config (mode/syringe/pause/repeat/force/target/inject_rate), lifetime累计, latest status, recent runs table. Polls `/api/devices/<id>` every 2s.

`device.js` — fetch `/api/devices/<id>` (from URL `?id=` or path), render config + runs. XSS-safe (textContent/DOM).

- [ ] **Step 2: Run suite (green) + node --check (syntax) + import smoke.**

- [ ] **Step 3: Commit**

```bash
git add pump_monitor/web/static/
git commit -m "feat: dashboard volume fix + device-detail page"
```

---

### Task 5: Live validation

- [ ] **Step 1:** `rm -f ./data/pump_monitor.db && .venv/bin/python -m pump_monitor` → start a dispense → STOP → verify:
  - Dashboard card shows **单次实际液量** (actual_volume, the delta) not lifetime.
  - Card has config line (mode/syringe/target).
  - Click card → device-detail page shows full config + runs.
  - CSV export has actual_volume + syringe_code + target + etc.
  - DB: `select actual_volume, result_acc_volume from run` — actual = delta, result_acc = lifetime snapshot.

- [ ] **Step 2:** Record results in ledger; commit any fixes.

---

## Self-Review (performed)

**Spec coverage:** mode-structure target/rate (Task 1, `_read_mode_setpoints`); process settings pause/repeat/force/stall (Task 1); syringe code+capacity (Task 1); actual_volume delta fix (Task 2); setpoints_json enrichment (Task 2); API + CSV new fields (Task 3); device-detail page (Task 3+4); display fix (Task 4). Gap: 提前预警% (no register, accepted skip); syringe vendor NAME (code only, deferred); 1052/1054 (unsupported, skip).

**Placeholder scan:** the Task 1 test references `_adapter_inputs()` which must be refactored from the existing `_adapter()` — this is a concrete instruction, not a placeholder. The device-detail frontend (Task 4) is described with concrete endpoints but the HTML/JS is left to the implementer's judgment within the XSS-safe constraint (consistent with A.2 Task 4's approach).

**Type consistency:** `close_run(run_id, ended_ms, end_status, actual_volume, actual_unit, lifetime_acc, lifetime_acc_unit, alarm_count)` — used consistently in RunDetector (Task 2) + repository (Task 2) + test (Task 2). `_run_to_dict` includes `actual_volume` + `setpoints` (Task 3). Run model has `actual_volume/actual_unit` (Task 2).

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-02-slice-a3-rich.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — this session, batched with checkpoints.

⚠️ Task 1's test requires refactoring the existing `_adapter()` test helper — the implementer must handle this. Which approach?
