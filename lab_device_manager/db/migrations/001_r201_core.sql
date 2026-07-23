CREATE TABLE IF NOT EXISTS experiment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL UNIQUE,
  membrane_system TEXT NOT NULL CHECK (membrane_system IN ('CEM', 'AEM')),
  recipe_no TEXT NOT NULL,
  recipe_version TEXT NOT NULL,
  sop_code TEXT NOT NULL,
  sop_version TEXT NOT NULL,
  target_viscosity_min_mpas REAL NOT NULL,
  target_viscosity_max_mpas REAL NOT NULL,
  spec_snapshot_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft',
  current_step_code TEXT NOT NULL DEFAULT 'R201-01',
  row_version INTEGER NOT NULL DEFAULT 0,
  operator TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  validation_mode TEXT NOT NULL DEFAULT 'parallel_validation',
  next_step TEXT NOT NULL DEFAULT 'C-320R',
  downstream_route_variant TEXT NOT NULL,
  disposition TEXT,
  created_at_ms INTEGER NOT NULL,
  started_effective_at_ms INTEGER,
  completed_effective_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS step_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_code TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'skipped', 'superseded')),
  started_effective_at_ms INTEGER NOT NULL,
  ended_effective_at_ms INTEGER,
  started_by TEXT NOT NULL,
  ended_by TEXT,
  spec_snapshot_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (experiment_id, step_code, attempt_no)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_step_one_active
  ON step_instance(experiment_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_step_experiment
  ON step_instance(experiment_id, id);

CREATE TABLE IF NOT EXISTS experiment_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER REFERENCES step_instance(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL,
  occurred_at_client_ms INTEGER,
  received_at_server_ms INTEGER NOT NULL,
  client_clock_offset_ms INTEGER,
  clock_sync_status TEXT NOT NULL DEFAULT 'unknown',
  effective_at_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (
    source_type IN ('device_raw', 'device_confirmed', 'manual', 'derived')
  ),
  payload_json TEXT NOT NULL DEFAULT '{}',
  supersedes_event_id INTEGER REFERENCES experiment_event(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_experiment_event_timeline
  ON experiment_event(experiment_id, effective_at_ms, id);

CREATE TABLE IF NOT EXISTS experiment_data_source_binding (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER REFERENCES step_instance(id) ON DELETE RESTRICT,
  device_id INTEGER NOT NULL REFERENCES device(id) ON DELETE RESTRICT,
  run_id INTEGER REFERENCES run(id) ON DELETE RESTRICT,
  device_role TEXT NOT NULL,
  metric_key TEXT NOT NULL,
  channel_selector TEXT,
  linked_at_ms INTEGER NOT NULL,
  unlinked_at_ms INTEGER,
  link_method TEXT NOT NULL CHECK (link_method IN ('automatic', 'manual')),
  confidence REAL
);

CREATE INDEX IF NOT EXISTS idx_data_source_experiment
  ON experiment_data_source_binding(experiment_id, device_role, linked_at_ms);

CREATE TABLE IF NOT EXISTS material_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER REFERENCES step_instance(id) ON DELETE RESTRICT,
  material_name TEXT NOT NULL,
  lot_no TEXT NOT NULL,
  expires_at TEXT,
  opened_at TEXT,
  theoretical_value REAL,
  actual_value REAL NOT NULL,
  unit TEXT NOT NULL,
  appearance TEXT,
  added_at_ms INTEGER,
  operator TEXT NOT NULL,
  reviewer TEXT
);

CREATE TABLE IF NOT EXISTS recipe_parameter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL,
  parameter_code TEXT NOT NULL,
  display_name TEXT NOT NULL,
  target_value REAL,
  actual_value REAL,
  unit TEXT NOT NULL,
  formula TEXT,
  inputs_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at_ms INTEGER,
  UNIQUE (experiment_id, version, parameter_code)
);

CREATE TABLE IF NOT EXISTS measurement (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER REFERENCES step_instance(id) ON DELETE RESTRICT,
  measurement_type TEXT NOT NULL,
  effective_at_ms INTEGER NOT NULL,
  sample_id INTEGER REFERENCES sample(id) ON DELETE RESTRICT,
  source_type TEXT NOT NULL CHECK (
    source_type IN ('device_raw', 'device_confirmed', 'manual', 'derived')
  ),
  valid INTEGER NOT NULL DEFAULT 1,
  invalid_reason TEXT,
  values_json TEXT NOT NULL DEFAULT '{}',
  raw_payload_sha256 TEXT,
  parser_version TEXT,
  operator TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_measurement_experiment
  ON measurement(experiment_id, measurement_type, effective_at_ms, id);

CREATE TABLE IF NOT EXISTS deviation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deviation_no TEXT NOT NULL UNIQUE,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER REFERENCES step_instance(id) ON DELETE RESTRICT,
  opened_at_ms INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  severity TEXT NOT NULL DEFAULT 'warning',
  actual_value TEXT,
  standard_value TEXT,
  description TEXT NOT NULL,
  immediate_action TEXT,
  cause TEXT,
  impact_assessment TEXT,
  capa TEXT,
  due_at_ms INTEGER,
  disposition TEXT,
  opened_by TEXT NOT NULL,
  reviewed_by TEXT,
  reviewed_at_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_deviation_experiment
  ON deviation(experiment_id, status, opened_at_ms);
