ALTER TABLE storage_location ADD COLUMN location_type TEXT NOT NULL
  DEFAULT 'general'
  CHECK(location_type IN (
    'general', 'chemical_cabinet', 'flammable_cabinet',
    'acid_alkali_cabinet', 'toxic_cabinet', 'controlled_cabinet',
    'refrigerator', 'waste_storage'
  ));
ALTER TABLE storage_location ADD COLUMN allowed_storage_groups_json TEXT
  NOT NULL DEFAULT '[]';
ALTER TABLE storage_location ADD COLUMN requires_dual_control INTEGER NOT NULL
  DEFAULT 0 CHECK(requires_dual_control IN (0, 1));
ALTER TABLE storage_location ADD COLUMN physical_controls_json TEXT
  NOT NULL DEFAULT '[]';
ALTER TABLE storage_location ADD COLUMN compliance_note TEXT;

ALTER TABLE material_container ADD COLUMN storage_location_id INTEGER
  REFERENCES storage_location(id) ON DELETE RESTRICT;
ALTER TABLE material_container ADD COLUMN storage_group TEXT NOT NULL
  DEFAULT 'unassessed'
  CHECK(storage_group IN (
    'unassessed', 'general_chemical', 'flammable', 'oxidizer',
    'acid', 'alkali', 'toxic', 'water_reactive', 'pyrophoric',
    'compressed_gas', 'refrigerated'
  ));
ALTER TABLE material_container ADD COLUMN ghs_pictograms_json TEXT
  NOT NULL DEFAULT '[]';
ALTER TABLE material_container ADD COLUMN sds_revision TEXT;
ALTER TABLE material_container ADD COLUMN sds_verified_at_ms INTEGER;
ALTER TABLE material_container ADD COLUMN sds_verified_by TEXT;
ALTER TABLE material_container ADD COLUMN catalog_source TEXT;
ALTER TABLE material_container ADD COLUMN catalog_version TEXT;
ALTER TABLE material_container ADD COLUMN catalog_entry_no TEXT;
ALTER TABLE material_container ADD COLUMN regulatory_reviewed_at_ms INTEGER;
ALTER TABLE material_container ADD COLUMN regulatory_reviewed_by TEXT;
ALTER TABLE material_container ADD COLUMN dual_control_required INTEGER NOT NULL
  DEFAULT 0 CHECK(dual_control_required IN (0, 1));
ALTER TABLE material_container ADD COLUMN dual_control_reason TEXT;

ALTER TABLE inventory_movement ADD COLUMN approved_by TEXT;
ALTER TABLE inventory_movement ADD COLUMN approved_by_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE inventory_movement ADD COLUMN approved_at_ms INTEGER;

CREATE TABLE IF NOT EXISTS inventory_operation_approval (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  item_id INTEGER NOT NULL
    REFERENCES material_container(id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action IN (
    'received', 'issued', 'adjusted', 'opened', 'quarantined', 'disposed'
  )),
  request_payload_json TEXT NOT NULL,
  requested_at_ms INTEGER NOT NULL,
  requested_by TEXT NOT NULL,
  requested_by_user_id INTEGER NOT NULL REFERENCES user_account(id),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending', 'approved', 'rejected', 'cancelled')),
  decided_at_ms INTEGER,
  decided_by TEXT,
  decided_by_user_id INTEGER REFERENCES user_account(id),
  decision_note TEXT,
  movement_id INTEGER REFERENCES inventory_movement(id)
);

CREATE INDEX IF NOT EXISTS idx_inventory_approval_status_time
  ON inventory_operation_approval(status, requested_at_ms DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_approval_item
  ON inventory_operation_approval(item_id, requested_at_ms DESC, id DESC);

CREATE TABLE IF NOT EXISTS inventory_compliance_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL
    REFERENCES material_container(id) ON DELETE RESTRICT,
  reviewed_at_ms INTEGER NOT NULL,
  reviewed_by TEXT NOT NULL,
  reviewed_by_user_id INTEGER REFERENCES user_account(id),
  result TEXT NOT NULL CHECK(result IN ('approved', 'needs_correction')),
  checklist_json TEXT NOT NULL DEFAULT '{}',
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_inventory_review_item_time
  ON inventory_compliance_review(item_id, reviewed_at_ms DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_location_group
  ON material_container(storage_location_id, storage_group, status);
