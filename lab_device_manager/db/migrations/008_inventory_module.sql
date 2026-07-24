ALTER TABLE material_container ADD COLUMN category TEXT NOT NULL
  DEFAULT 'chemical'
  CHECK(category IN ('chemical', 'consumable', 'office'));
ALTER TABLE material_container ADD COLUMN location TEXT;
ALTER TABLE material_container ADD COLUMN owner TEXT;
ALTER TABLE material_container ADD COLUMN min_threshold REAL;
ALTER TABLE material_container ADD COLUMN max_threshold REAL;
ALTER TABLE material_container ADD COLUMN is_controlled INTEGER NOT NULL
  DEFAULT 0 CHECK(is_controlled IN (0, 1));
ALTER TABLE material_container ADD COLUMN note TEXT;
ALTER TABLE material_container ADD COLUMN cas_no TEXT;
ALTER TABLE material_container ADD COLUMN spec TEXT;
ALTER TABLE material_container ADD COLUMN hazards_json TEXT NOT NULL
  DEFAULT '[]';
ALTER TABLE material_container ADD COLUMN sds_url TEXT;
ALTER TABLE material_container ADD COLUMN prepared_by TEXT;
ALTER TABLE material_container ADD COLUMN prepared_date TEXT;
ALTER TABLE material_container ADD COLUMN imported_source TEXT;
ALTER TABLE material_container ADD COLUMN imported_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_import_identity
  ON material_container(imported_source, imported_id)
  WHERE imported_source IS NOT NULL AND imported_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_category_status
  ON material_container(category, status, material_name);
CREATE INDEX IF NOT EXISTS idx_inventory_expiry
  ON material_container(expires_on, status);

CREATE TABLE IF NOT EXISTS inventory_movement (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  item_id INTEGER NOT NULL
    REFERENCES material_container(id) ON DELETE RESTRICT,
  experiment_id INTEGER
    REFERENCES experiment(id) ON DELETE RESTRICT,
  step_instance_id INTEGER
    REFERENCES step_instance(id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN (
    'registered', 'received', 'issued', 'adjusted',
    'experiment_used', 'opened', 'quarantined', 'disposed', 'imported'
  )),
  delta REAL NOT NULL,
  quantity_after REAL,
  unit TEXT,
  effective_at_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  actor_user_id INTEGER REFERENCES user_account(id),
  note TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_inventory_movement_item_time
  ON inventory_movement(item_id, effective_at_ms DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_movement_experiment
  ON inventory_movement(experiment_id, effective_at_ms, id);
CREATE INDEX IF NOT EXISTS idx_inventory_movement_action_time
  ON inventory_movement(action, effective_at_ms DESC, id DESC);
