CREATE TABLE IF NOT EXISTS storage_location (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  location_code TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  storage_condition TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at_ms INTEGER NOT NULL,
  created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  item_code TEXT NOT NULL UNIQUE,
  item_type TEXT NOT NULL CHECK (
    item_type IN ('batch', 'intermediate', 'final_product')
  ),
  display_name TEXT NOT NULL,
  source_step_code TEXT,
  sequence_no INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (
    status IN ('active', 'stored', 'consumed', 'completed', 'disposed')
  ),
  quantity REAL,
  unit TEXT,
  storage_location_code TEXT
    REFERENCES storage_location(location_code) ON DELETE RESTRICT,
  hold_until_ms INTEGER,
  creation_group_id TEXT NOT NULL,
  creation_index INTEGER NOT NULL DEFAULT 1,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  UNIQUE (creation_group_id, creation_index),
  UNIQUE (
    experiment_id, item_type, source_step_code, sequence_no
  )
);

CREATE INDEX IF NOT EXISTS idx_trace_item_experiment
  ON trace_item(experiment_id, item_type, id);
CREATE INDEX IF NOT EXISTS idx_trace_item_status
  ON trace_item(experiment_id, status, updated_at_ms);

CREATE TABLE IF NOT EXISTS trace_item_relation (
  parent_item_id INTEGER NOT NULL
    REFERENCES trace_item(id) ON DELETE RESTRICT,
  child_item_id INTEGER NOT NULL
    REFERENCES trace_item(id) ON DELETE RESTRICT,
  relation_type TEXT NOT NULL DEFAULT 'originated_from' CHECK (
    relation_type IN ('originated_from', 'split_from', 'merged_from')
  ),
  quantity REAL,
  unit TEXT,
  PRIMARY KEY (parent_item_id, child_item_id, relation_type),
  CHECK (parent_item_id <> child_item_id)
);

CREATE INDEX IF NOT EXISTS idx_trace_relation_child
  ON trace_item_relation(child_item_id, parent_item_id);

CREATE TABLE IF NOT EXISTS trace_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  experiment_id INTEGER NOT NULL REFERENCES experiment(id) ON DELETE RESTRICT,
  trace_item_id INTEGER NOT NULL REFERENCES trace_item(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created', 'stored', 'retrieved', 'consumed', 'completed', 'disposed',
      'label_print_requested', 'label_reprint_requested'
    )
  ),
  effective_at_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  location_code TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trace_event_item
  ON trace_event(trace_item_id, effective_at_ms, id);
CREATE INDEX IF NOT EXISTS idx_trace_event_experiment
  ON trace_event(experiment_id, effective_at_ms, id);

CREATE TABLE IF NOT EXISTS label_print_job (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  experiment_id INTEGER REFERENCES experiment(id) ON DELETE RESTRICT,
  trace_item_id INTEGER REFERENCES trace_item(id) ON DELETE RESTRICT,
  storage_location_id INTEGER
    REFERENCES storage_location(id) ON DELETE RESTRICT,
  label_kind TEXT NOT NULL CHECK (label_kind IN ('trace_item', 'location')),
  reason TEXT NOT NULL CHECK (reason IN ('initial', 'reprint')),
  copies INTEGER NOT NULL DEFAULT 1 CHECK (copies BETWEEN 1 AND 20),
  status TEXT NOT NULL DEFAULT 'ready' CHECK (
    status IN ('ready', 'cancelled')
  ),
  template_version TEXT NOT NULL DEFAULT 'trace-v1',
  requested_at_ms INTEGER NOT NULL,
  requested_by TEXT NOT NULL,
  CHECK (
    (label_kind='trace_item' AND trace_item_id IS NOT NULL)
    OR
    (label_kind='location' AND storage_location_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_label_print_experiment
  ON label_print_job(experiment_id, requested_at_ms, id);
