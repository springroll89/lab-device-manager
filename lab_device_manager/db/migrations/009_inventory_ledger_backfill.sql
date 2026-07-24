INSERT OR IGNORE INTO inventory_movement(
  client_event_id, item_id, experiment_id, action, delta,
  quantity_after, unit, effective_at_ms, actor, actor_user_id,
  note, payload_json
)
SELECT
  'legacy:' || event.client_event_id,
  event.material_container_id,
  event.experiment_id,
  CASE event.event_type
    WHEN 'used' THEN 'experiment_used'
    WHEN 'opened' THEN 'opened'
    WHEN 'adjusted' THEN 'adjusted'
    WHEN 'quarantined' THEN 'quarantined'
    WHEN 'disposed' THEN 'disposed'
    ELSE 'imported'
  END,
  CASE WHEN event.event_type='used'
    THEN -COALESCE(event.quantity, 0)
    ELSE 0
  END,
  NULL,
  event.unit,
  event.effective_at_ms,
  event.actor,
  event.actor_user_id,
  '由旧版原材料流水迁移',
  event.payload_json
FROM material_container_event event;

INSERT OR IGNORE INTO inventory_movement(
  client_event_id, item_id, action, delta, quantity_after, unit,
  effective_at_ms, actor, actor_user_id, note, payload_json
)
SELECT
  'legacy-balance:' || container.id,
  container.id,
  'imported',
  0,
  container.quantity_remaining,
  container.unit,
  container.updated_at_ms,
  container.created_by,
  container.created_by_user_id,
  '旧版库存迁移时余额',
  '{"source":"material_container"}'
FROM material_container container;
