ALTER TABLE experiment_data_source_binding
  ADD COLUMN client_event_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_source_client_event
  ON experiment_data_source_binding(client_event_id)
  WHERE client_event_id IS NOT NULL;
