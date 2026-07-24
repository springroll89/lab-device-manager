ALTER TABLE experiment ADD COLUMN operator_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE experiment ADD COLUMN reviewer_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE experiment_event ADD COLUMN actor_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE step_instance ADD COLUMN started_by_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE step_instance ADD COLUMN ended_by_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE measurement ADD COLUMN operator_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE deviation ADD COLUMN opened_by_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE deviation ADD COLUMN reviewed_by_user_id INTEGER
  REFERENCES user_account(id);
ALTER TABLE run ADD COLUMN operator_user_id INTEGER
  REFERENCES user_account(id);

UPDATE experiment
SET operator_user_id = (
  SELECT MIN(id) FROM user_account
  WHERE display_name = experiment.operator AND is_active = 1
)
WHERE (
  SELECT COUNT(*) FROM user_account
  WHERE display_name = experiment.operator AND is_active = 1
) = 1;

UPDATE experiment
SET reviewer_user_id = (
  SELECT MIN(id) FROM user_account
  WHERE display_name = experiment.reviewer AND is_active = 1
)
WHERE reviewer <> '' AND (
  SELECT COUNT(*) FROM user_account
  WHERE display_name = experiment.reviewer AND is_active = 1
) = 1;

CREATE INDEX IF NOT EXISTS idx_experiment_operator_user
  ON experiment(operator_user_id, status);
CREATE INDEX IF NOT EXISTS idx_experiment_event_actor_user
  ON experiment_event(actor_user_id, effective_at_ms);
CREATE INDEX IF NOT EXISTS idx_run_operator_user
  ON run(operator_user_id, started_ms);
