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
  started_ms INTEGER NOT NULL,
  ended_ms INTEGER,
  duration_ms INTEGER,
  end_status TEXT,
  operator TEXT, project_tag TEXT, experiment_tag TEXT, remark TEXT,
  tagged INTEGER NOT NULL DEFAULT 0,
  setpoints_json TEXT,
  work_mode TEXT, target_volume REAL, target_volume_unit TEXT,
  result_acc_volume REAL, result_acc_unit TEXT,
  actual_volume REAL, actual_unit TEXT,
  alarm_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sample (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES run(id),
  device_id INTEGER NOT NULL REFERENCES device(id),
  ts_ms INTEGER NOT NULL,
  state TEXT, flow_rate REAL, delivered_volume REAL, temp_c REAL,
  metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id INTEGER NOT NULL REFERENCES device(id),
  run_id INTEGER REFERENCES run(id),
  ts_ms INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_tagged ON run(tagged);
CREATE INDEX IF NOT EXISTS idx_run_device_start ON run(device_id, started_ms);
CREATE INDEX IF NOT EXISTS idx_run_started ON run(started_ms);
CREATE INDEX IF NOT EXISTS idx_run_status ON run(end_status);
CREATE INDEX IF NOT EXISTS idx_run_project ON run(project_tag);
CREATE INDEX IF NOT EXISTS idx_run_work_mode ON run(work_mode);
CREATE INDEX IF NOT EXISTS idx_run_alarm_count ON run(alarm_count);
CREATE INDEX IF NOT EXISTS idx_sample_run_ts ON sample(run_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_event_run_ts ON event(run_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_event_device_ts ON event(device_id, ts_ms);
