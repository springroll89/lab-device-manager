CREATE TABLE IF NOT EXISTS device_reservation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER NOT NULL
    REFERENCES experiment(id) ON DELETE RESTRICT,
  device_id INTEGER NOT NULL REFERENCES device(id) ON DELETE RESTRICT,
  device_type TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT 'process'
    CHECK(purpose IN ('process', 'measurement')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active', 'released', 'expired')),
  reserved_at_ms INTEGER NOT NULL,
  released_at_ms INTEGER,
  expires_at_ms INTEGER,
  reserved_by TEXT NOT NULL,
  reserved_by_user_id INTEGER REFERENCES user_account(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_device_one_active_reservation
  ON device_reservation(device_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_device_reservation_experiment
  ON device_reservation(experiment_id, status, device_type);

CREATE TABLE IF NOT EXISTS material_container (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  container_code TEXT NOT NULL UNIQUE,
  external_barcode TEXT UNIQUE,
  material_name TEXT NOT NULL,
  supplier TEXT,
  supplier_lot TEXT,
  internal_lot TEXT,
  expires_on TEXT,
  opened_on TEXT,
  status TEXT NOT NULL DEFAULT 'available'
    CHECK(status IN ('available', 'empty', 'quarantined', 'expired', 'disposed')),
  quantity_remaining REAL,
  unit TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  created_by_user_id INTEGER REFERENCES user_account(id)
);

CREATE INDEX IF NOT EXISTS idx_material_container_lookup
  ON material_container(material_name, supplier_lot, status);

CREATE TABLE IF NOT EXISTS material_container_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  material_container_id INTEGER NOT NULL
    REFERENCES material_container(id) ON DELETE RESTRICT,
  experiment_id INTEGER REFERENCES experiment(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL
    CHECK(event_type IN ('registered', 'opened', 'used', 'adjusted', 'quarantined', 'disposed')),
  quantity REAL,
  unit TEXT,
  effective_at_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  actor_user_id INTEGER REFERENCES user_account(id),
  payload_json TEXT NOT NULL DEFAULT '{}'
);

ALTER TABLE material_usage ADD COLUMN material_container_id INTEGER
  REFERENCES material_container(id);
ALTER TABLE material_usage ADD COLUMN container_code TEXT;
