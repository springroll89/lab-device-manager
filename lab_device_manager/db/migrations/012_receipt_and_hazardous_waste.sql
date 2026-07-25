ALTER TABLE material_container ADD COLUMN source_organization TEXT;
ALTER TABLE material_container ADD COLUMN handover_document_no TEXT;
ALTER TABLE material_container ADD COLUMN handover_document_ref TEXT;
ALTER TABLE material_container ADD COLUMN received_at_ms INTEGER;
ALTER TABLE material_container ADD COLUMN received_by TEXT;
ALTER TABLE material_container ADD COLUMN accepted_by TEXT;
ALTER TABLE material_container ADD COLUMN regulatory_filing_no TEXT;
ALTER TABLE material_container ADD COLUMN regulatory_filing_ref TEXT;

CREATE TABLE IF NOT EXISTS hazardous_waste_container (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waste_code TEXT NOT NULL UNIQUE,
  waste_name TEXT NOT NULL,
  waste_category_code TEXT NOT NULL,
  waste_category_name TEXT,
  physical_state TEXT NOT NULL CHECK(
    physical_state IN ('liquid', 'solid', 'sludge', 'gas', 'mixed')
  ),
  hazard_characteristics_json TEXT NOT NULL DEFAULT '[]',
  composition TEXT NOT NULL,
  quantity REAL NOT NULL DEFAULT 0 CHECK(quantity >= 0),
  unit TEXT NOT NULL,
  package_type TEXT NOT NULL,
  storage_location_id INTEGER NOT NULL
    REFERENCES storage_location(id) ON DELETE RESTRICT,
  source_experiment_id INTEGER REFERENCES experiment(id) ON DELETE RESTRICT,
  source_item_id INTEGER
    REFERENCES material_container(id) ON DELETE RESTRICT,
  status TEXT NOT NULL DEFAULT 'accumulating' CHECK(
    status IN ('accumulating', 'ready_for_transfer', 'transferred')
  ),
  started_at_ms INTEGER NOT NULL,
  sealed_at_ms INTEGER,
  transferred_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  created_by_user_id INTEGER REFERENCES user_account(id),
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_hazardous_waste_status_time
  ON hazardous_waste_container(status, updated_at_ms DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_hazardous_waste_location
  ON hazardous_waste_container(storage_location_id, status);

CREATE TABLE IF NOT EXISTS hazardous_waste_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  waste_container_id INTEGER NOT NULL
    REFERENCES hazardous_waste_container(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK(
    event_type IN (
      'created', 'quantity_added', 'sealed',
      'transfer_registered', 'transfer_completed'
    )
  ),
  quantity_delta REAL NOT NULL DEFAULT 0,
  quantity_after REAL NOT NULL,
  effective_at_ms INTEGER NOT NULL,
  actor TEXT NOT NULL,
  actor_user_id INTEGER REFERENCES user_account(id),
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_hazardous_waste_event_container
  ON hazardous_waste_event(
    waste_container_id, effective_at_ms DESC, id DESC
  );

CREATE TABLE IF NOT EXISTS hazardous_waste_transfer (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  waste_container_id INTEGER NOT NULL
    REFERENCES hazardous_waste_container(id) ON DELETE RESTRICT,
  national_manifest_no TEXT NOT NULL UNIQUE,
  national_system_ref TEXT,
  transfer_at_ms INTEGER NOT NULL,
  quantity REAL NOT NULL CHECK(quantity > 0),
  unit TEXT NOT NULL,
  transporter_name TEXT NOT NULL,
  transporter_license_no TEXT NOT NULL,
  vehicle_no TEXT,
  recipient_name TEXT NOT NULL,
  recipient_permit_no TEXT NOT NULL,
  disposal_method TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'registered' CHECK(
    status IN ('registered', 'completed')
  ),
  registered_at_ms INTEGER NOT NULL,
  registered_by TEXT NOT NULL,
  registered_by_user_id INTEGER REFERENCES user_account(id),
  completed_at_ms INTEGER,
  completed_by TEXT,
  completed_by_user_id INTEGER REFERENCES user_account(id),
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_hazardous_waste_transfer_time
  ON hazardous_waste_transfer(transfer_at_ms DESC, id DESC);
