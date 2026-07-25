ALTER TABLE material_container ADD COLUMN hazardous_status TEXT NOT NULL
  DEFAULT 'not_assessed'
  CHECK(hazardous_status IN (
    'not_assessed', 'listed', 'not_listed', 'pending_review'
  ));
ALTER TABLE material_container ADD COLUMN controlled_categories_json TEXT
  NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_inventory_hazardous_status
  ON material_container(hazardous_status, status);
