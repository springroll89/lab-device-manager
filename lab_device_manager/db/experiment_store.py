from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator, Optional


def _json(value) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _decode_row(row: sqlite3.Row | None, json_fields: dict[str, str]) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    for stored, exposed in json_fields.items():
        raw = result.pop(stored, None)
        try:
            result[exposed] = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            result[exposed] = {}
    return result


class ExperimentStore:
    """SQLite persistence for the R-201 experiment domain."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock):
        self._conn = connection
        self._lock = lock

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _experiment(row) -> Optional[dict]:
        return _decode_row(row, {"spec_snapshot_json": "spec_snapshot"})

    @staticmethod
    def _step(row) -> Optional[dict]:
        return _decode_row(
            row,
            {
                "spec_snapshot_json": "spec_snapshot",
                "result_json": "result",
            },
        )

    @staticmethod
    def _event(row) -> Optional[dict]:
        return _decode_row(row, {"payload_json": "payload"})

    @staticmethod
    def _measurement(row) -> Optional[dict]:
        result = _decode_row(row, {"values_json": "values"})
        if result is not None:
            result["valid"] = bool(result["valid"])
        return result

    @staticmethod
    def _trace_event(row) -> Optional[dict]:
        return _decode_row(row, {"payload_json": "payload"})

    @staticmethod
    def _storage_location(row) -> Optional[dict]:
        if row is None:
            return None
        result = dict(row)
        result["active"] = bool(result["active"])
        return result

    def create_experiment(
        self, data: dict, now_ms: int, event: Optional[dict] = None
    ) -> dict:
        route = "CEM_WITH_F801" if data["membrane_system"] == "CEM" else "AEM_WITHOUT_F801"
        with self.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO experiment(
                     batch_id, membrane_system, recipe_no, recipe_version,
                     sop_code, sop_version, target_viscosity_min_mpas,
                     target_viscosity_max_mpas, spec_snapshot_json, operator,
                     reviewer, downstream_route_variant, created_at_ms, updated_at_ms,
                     snapshot_sha256)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["batch_id"],
                    data["membrane_system"],
                    data["recipe_no"],
                    data["recipe_version"],
                    data["sop_code"],
                    data["sop_version"],
                    data["target_viscosity_min_mpas"],
                    data["target_viscosity_max_mpas"],
                    _json(data.get("spec_snapshot")),
                    data["operator"],
                    data["reviewer"],
                    route,
                    now_ms,
                    now_ms,
                    data.get("snapshot_sha256"),
                ),
            )
            for parameter in data.get("recipe_parameters") or []:
                conn.execute(
                    """INSERT INTO recipe_parameter(
                         experiment_id, version, parameter_code, display_name,
                         target_value, actual_value, unit, formula, inputs_json,
                         source, reviewed_by, reviewed_at_ms)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        cursor.lastrowid,
                        parameter.get("version", 1),
                        parameter["parameter_code"],
                        parameter["display_name"],
                        parameter.get("target_value"),
                        parameter.get("actual_value"),
                        parameter["unit"],
                        parameter.get("formula"),
                        _json(parameter.get("inputs")),
                        parameter["source"],
                        parameter.get("reviewed_by"),
                        parameter.get("reviewed_at_ms"),
                    ),
                )
            row = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            if event is not None:
                self.add_experiment_event(
                    cursor.lastrowid, event, connection=conn
                )
            trace_cursor = conn.execute(
                """INSERT INTO trace_item(
                     experiment_id, item_code, item_type, display_name,
                     source_step_code, sequence_no, status, quantity, unit,
                     creation_group_id, creation_index, created_at_ms,
                     updated_at_ms, created_by)
                   VALUES(?,?, 'batch', ?, NULL, 1, 'active', NULL, NULL,
                          ?, 1, ?, ?, ?)""",
                (
                    cursor.lastrowid,
                    data["batch_id"],
                    f"{data['membrane_system']} 实验批次",
                    f"trace-batch-{cursor.lastrowid}",
                    now_ms,
                    now_ms,
                    data["operator"],
                ),
            )
            conn.execute(
                """INSERT INTO trace_event(
                     client_event_id, experiment_id, trace_item_id,
                     event_type, effective_at_ms, actor, payload_json)
                   VALUES(?,?,?,'created',?,?,?)""",
                (
                    f"trace-batch-created-{cursor.lastrowid}",
                    cursor.lastrowid,
                    trace_cursor.lastrowid,
                    event["effective_at_ms"] if event is not None else now_ms,
                    event["actor"] if event is not None else data["operator"],
                    _json(
                        {
                            "item_code": data["batch_id"],
                            "item_type": "batch",
                        }
                    ),
                ),
            )
        return self._experiment(row)

    def get_experiment(self, experiment_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
        return self._experiment(row)

    def list_experiments(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM experiment ORDER BY created_at_ms DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._experiment(row) for row in rows]

    def get_event_by_client_id(self, client_event_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM experiment_event WHERE client_event_id=?",
                (client_event_id,),
            ).fetchone()
        return self._event(row)

    def add_experiment_event(
        self,
        experiment_id: int,
        data: dict,
        step_instance_id: Optional[int] = None,
        connection: Optional[sqlite3.Connection] = None,
    ) -> dict:
        conn = connection or self._conn
        with self._lock:
            existing = conn.execute(
                "SELECT * FROM experiment_event WHERE client_event_id=?",
                (data["client_event_id"],),
            ).fetchone()
            if existing:
                return self._event(existing)
            cursor = conn.execute(
                """INSERT INTO experiment_event(
                     client_event_id, experiment_id, step_instance_id, event_type,
                     occurred_at_client_ms, received_at_server_ms,
                     client_clock_offset_ms, clock_sync_status, effective_at_ms,
                     actor, source_type, payload_json, supersedes_event_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    experiment_id,
                    step_instance_id,
                    data["event_type"],
                    data.get("occurred_at_client_ms"),
                    data["received_at_server_ms"],
                    data.get("client_clock_offset_ms"),
                    data.get("clock_sync_status", "unknown"),
                    data["effective_at_ms"],
                    data["actor"],
                    data.get("source_type", "manual"),
                    _json(data.get("payload")),
                    data.get("supersedes_event_id"),
                ),
            )
            if connection is None:
                conn.commit()
            row = conn.execute(
                "SELECT * FROM experiment_event WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._event(row)

    def list_experiment_events(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM experiment_event WHERE experiment_id=?
                   ORDER BY effective_at_ms, id""",
                (experiment_id,),
            ).fetchall()
        return [self._event(row) for row in rows]

    def start_step(
        self,
        experiment_id: int,
        step_code: str,
        expected_version: int,
        effective_at_ms: int,
        actor: str,
        event: dict,
    ) -> tuple[dict, dict]:
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
            if current is None:
                raise LookupError("experiment not found")
            if current["row_version"] != expected_version:
                raise RuntimeError("row version conflict")
            attempt = conn.execute(
                """SELECT COALESCE(MAX(attempt_no), 0) + 1
                   FROM step_instance WHERE experiment_id=? AND step_code=?""",
                (experiment_id, step_code),
            ).fetchone()[0]
            cursor = conn.execute(
                """INSERT INTO step_instance(
                     experiment_id, step_code, attempt_no, status,
                     started_effective_at_ms, started_by, spec_snapshot_json)
                   VALUES(?,?,?,'active',?,?,?)""",
                (
                    experiment_id,
                    step_code,
                    attempt,
                    effective_at_ms,
                    actor,
                    current["spec_snapshot_json"],
                ),
            )
            new_version = expected_version + 1
            updated = conn.execute(
                """UPDATE experiment SET status='in_progress', row_version=?,
                     started_effective_at_ms=COALESCE(started_effective_at_ms, ?),
                     updated_at_ms=? WHERE id=? AND row_version=?""",
                (
                    new_version,
                    effective_at_ms,
                    event["received_at_server_ms"],
                    experiment_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("row version conflict")
            self.add_experiment_event(
                experiment_id, event, cursor.lastrowid, connection=conn
            )
            exp_row = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
            step_row = conn.execute(
                "SELECT * FROM step_instance WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
        return self._experiment(exp_row), self._step(step_row)

    def complete_step(
        self,
        experiment_id: int,
        step_code: str,
        expected_version: int,
        next_step_code: str,
        result: dict,
        effective_at_ms: int,
        actor: str,
        event: dict,
        deviations: Optional[list[dict]] = None,
        materials: Optional[list[dict]] = None,
    ) -> tuple[dict, dict, list[dict]]:
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
            if current is None:
                raise LookupError("experiment not found")
            if current["row_version"] != expected_version:
                raise RuntimeError("row version conflict")
            step_row = conn.execute(
                """SELECT * FROM step_instance
                   WHERE experiment_id=? AND step_code=? AND status='active'
                   ORDER BY attempt_no DESC LIMIT 1""",
                (experiment_id, step_code),
            ).fetchone()
            if step_row is None:
                raise RuntimeError("step is not active")
            conn.execute(
                """UPDATE step_instance SET status='completed',
                     ended_effective_at_ms=?, ended_by=?, result_json=?
                   WHERE id=?""",
                (effective_at_ms, actor, _json(result), step_row["id"]),
            )
            new_version = expected_version + 1
            conn.execute(
                """UPDATE experiment SET current_step_code=?, row_version=?,
                     updated_at_ms=? WHERE id=? AND row_version=?""",
                (
                    next_step_code,
                    new_version,
                    event["received_at_server_ms"],
                    experiment_id,
                    expected_version,
                ),
            )
            self.add_experiment_event(
                experiment_id, event, step_row["id"], connection=conn
            )
            created_deviations = []
            for deviation in deviations or []:
                deviation_data = dict(deviation)
                deviation_data["step_instance_id"] = step_row["id"]
                deviation_data["deviation_no"] = self._next_deviation_no(
                    conn, experiment_id
                )
                deviation_event = dict(deviation_data["event"])
                deviation_event["payload"] = {
                    **(deviation_event.get("payload") or {}),
                    "deviation_no": deviation_data["deviation_no"],
                }
                deviation_data["event"] = deviation_event
                created_deviations.append(
                    self._insert_deviation(conn, experiment_id, deviation_data)
                )
                self.add_experiment_event(
                    experiment_id,
                    deviation_event,
                    step_row["id"],
                    connection=conn,
                )
            for material in materials or []:
                conn.execute(
                    """INSERT INTO material_usage(
                         experiment_id, step_instance_id, material_name, lot_no,
                         expires_at, opened_at, theoretical_value, actual_value,
                         unit, appearance, added_at_ms, operator, reviewer)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        experiment_id,
                        step_row["id"],
                        material["material_name"],
                        material["lot_no"],
                        material.get("expires_at"),
                        material.get("opened_at"),
                        material.get("theoretical_value"),
                        material["actual_value"],
                        material["unit"],
                        material.get("appearance"),
                        material.get("added_at_ms", effective_at_ms),
                        material.get("operator", actor),
                        material.get("reviewer"),
                    ),
                )
            exp_row = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
            completed_row = conn.execute(
                "SELECT * FROM step_instance WHERE id=?", (step_row["id"],)
            ).fetchone()
        return (
            self._experiment(exp_row),
            self._step(completed_row),
            created_deviations,
        )

    def list_steps(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM step_instance WHERE experiment_id=?
                   ORDER BY id""",
                (experiment_id,),
            ).fetchall()
        return [self._step(row) for row in rows]

    def get_active_step(self, experiment_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM step_instance
                   WHERE experiment_id=? AND status='active'
                   ORDER BY id DESC LIMIT 1""",
                (experiment_id,),
            ).fetchone()
        return self._step(row)

    def add_measurement(
        self,
        experiment_id: int,
        data: dict,
        step_instance_id: Optional[int],
        event: Optional[dict] = None,
    ) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM measurement WHERE client_event_id=?",
                (data["client_event_id"],),
            ).fetchone()
            if existing:
                return self._measurement(existing)
            cursor = conn.execute(
                """INSERT INTO measurement(
                     client_event_id, experiment_id, step_instance_id,
                     measurement_type, effective_at_ms, sample_id, source_type,
                     valid, invalid_reason, values_json, raw_payload_sha256,
                     parser_version, operator)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    experiment_id,
                    step_instance_id,
                    data["measurement_type"],
                    data["effective_at_ms"],
                    data.get("sample_id"),
                    data.get("source_type", "manual"),
                    1 if data.get("valid", True) else 0,
                    data.get("invalid_reason"),
                    _json(data.get("values")),
                    data.get("raw_payload_sha256"),
                    data.get("parser_version"),
                    data["operator"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM measurement WHERE id=?", (cursor.lastrowid,)
            ).fetchone()
            if event is not None:
                self.add_experiment_event(
                    experiment_id,
                    event,
                    step_instance_id,
                    connection=conn,
                )
        return self._measurement(row)

    def list_measurements(
        self, experiment_id: int, measurement_type: Optional[str] = None
    ) -> list[dict]:
        with self._lock:
            if measurement_type is None:
                rows = self._conn.execute(
                    """SELECT * FROM measurement WHERE experiment_id=?
                       ORDER BY effective_at_ms, id""",
                    (experiment_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM measurement
                       WHERE experiment_id=? AND measurement_type=?
                       ORDER BY effective_at_ms, id""",
                    (experiment_id, measurement_type),
                ).fetchall()
        return [self._measurement(row) for row in rows]

    def list_material_usages(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM material_usage WHERE experiment_id=?
                   ORDER BY id""",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_batch_trace_item(
        self, experiment_id: int, now_ms: int, actor: str
    ) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT id FROM trace_item
                   WHERE experiment_id=? AND item_type='batch'""",
                (experiment_id,),
            ).fetchone()
            if existing is not None:
                item_id = existing["id"]
            else:
                experiment = conn.execute(
                    "SELECT * FROM experiment WHERE id=?", (experiment_id,)
                ).fetchone()
                if experiment is None:
                    raise LookupError("experiment not found")
                cursor = conn.execute(
                    """INSERT INTO trace_item(
                         experiment_id, item_code, item_type, display_name,
                         source_step_code, sequence_no, status, quantity, unit,
                         creation_group_id, creation_index, created_at_ms,
                         updated_at_ms, created_by)
                       VALUES(?,?, 'batch', ?, NULL, 1, 'active', NULL, NULL,
                              ?, 1, ?, ?, ?)""",
                    (
                        experiment_id,
                        experiment["batch_id"],
                        f"{experiment['membrane_system']} 实验批次",
                        f"trace-batch-{experiment_id}",
                        now_ms,
                        now_ms,
                        actor,
                    ),
                )
                item_id = cursor.lastrowid
                conn.execute(
                    """INSERT INTO trace_event(
                         client_event_id, experiment_id, trace_item_id,
                         event_type, effective_at_ms, actor, payload_json)
                       VALUES(?,?,?,'created',?,?,?)""",
                    (
                        f"trace-batch-created-{experiment_id}",
                        experiment_id,
                        item_id,
                        now_ms,
                        actor,
                        _json(
                            {
                                "item_code": experiment["batch_id"],
                                "item_type": "batch",
                            }
                        ),
                    ),
                )
        return self.get_trace_item(item_id)

    def create_trace_items(
        self, experiment_id: int, data: dict
    ) -> list[dict]:
        group_id = data["creation_group_id"]
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT id FROM trace_item
                   WHERE experiment_id=? AND creation_group_id=?
                   ORDER BY creation_index""",
                (experiment_id, group_id),
            ).fetchall()
            if existing:
                item_ids = [row["id"] for row in existing]
            else:
                experiment = conn.execute(
                    "SELECT * FROM experiment WHERE id=?", (experiment_id,)
                ).fetchone()
                if experiment is None:
                    raise LookupError("experiment not found")
                item_type = data["item_type"]
                source_step = data.get("source_step_code")
                count = int(data["container_count"])
                current_max = conn.execute(
                    """SELECT COALESCE(MAX(sequence_no), 0)
                       FROM trace_item
                       WHERE experiment_id=? AND item_type=?
                         AND source_step_code IS ?""",
                    (experiment_id, item_type, source_step),
                ).fetchone()[0]
                batch_item = conn.execute(
                    """SELECT * FROM trace_item
                       WHERE experiment_id=? AND item_type='batch'""",
                    (experiment_id,),
                ).fetchone()
                if batch_item is None:
                    raise LookupError("batch trace item not found")
                if item_type == "final_product":
                    parent = conn.execute(
                        """SELECT * FROM trace_item
                           WHERE experiment_id=? AND item_type='intermediate'
                             AND status NOT IN ('disposed', 'consumed')
                           ORDER BY id DESC LIMIT 1""",
                        (experiment_id,),
                    ).fetchone() or batch_item
                    code_prefix = f"{experiment['batch_id']}-FP"
                elif item_type == "intermediate":
                    parent = batch_item
                    step_suffix = str(source_step or "R201-00").split("-")[-1]
                    code_prefix = (
                        f"{experiment['batch_id']}-IP{step_suffix}"
                    )
                else:
                    raise ValueError("unsupported trace item type")
                item_ids = []
                for index in range(1, count + 1):
                    sequence = current_max + index
                    item_code = f"{code_prefix}-{sequence:02d}"
                    cursor = conn.execute(
                        """INSERT INTO trace_item(
                             experiment_id, item_code, item_type, display_name,
                             source_step_code, sequence_no, status, quantity,
                             unit, creation_group_id, creation_index,
                             created_at_ms, updated_at_ms, created_by)
                           VALUES(?,?,?,?,?,?,'active',?,?,?,?,?,?,?)""",
                        (
                            experiment_id,
                            item_code,
                            item_type,
                            data["display_name"],
                            source_step,
                            sequence,
                            data.get("quantity"),
                            data.get("unit"),
                            group_id,
                            index,
                            data["effective_at_ms"],
                            data["effective_at_ms"],
                            data["actor"],
                        ),
                    )
                    item_id = cursor.lastrowid
                    item_ids.append(item_id)
                    conn.execute(
                        """INSERT INTO trace_item_relation(
                             parent_item_id, child_item_id, relation_type)
                           VALUES(?,?,'originated_from')""",
                        (parent["id"], item_id),
                    )
                    conn.execute(
                        """INSERT INTO trace_event(
                             client_event_id, experiment_id, trace_item_id,
                             event_type, effective_at_ms, actor, payload_json)
                           VALUES(?,?,?,'created',?,?,?)""",
                        (
                            f"{group_id}:{index}:created",
                            experiment_id,
                            item_id,
                            data["effective_at_ms"],
                            data["actor"],
                            _json(
                                {
                                    "item_code": item_code,
                                    "item_type": item_type,
                                    "source_step_code": source_step,
                                }
                            ),
                        ),
                    )
        return [self.get_trace_item(item_id) for item_id in item_ids]

    def get_trace_item(self, trace_item_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trace_item WHERE id=?", (trace_item_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            parents = self._conn.execute(
                """SELECT p.id, p.item_code, p.item_type, p.display_name,
                          r.relation_type, r.quantity, r.unit
                   FROM trace_item_relation r
                   JOIN trace_item p ON p.id=r.parent_item_id
                   WHERE r.child_item_id=? ORDER BY p.id""",
                (trace_item_id,),
            ).fetchall()
            result["parents"] = [dict(parent) for parent in parents]
        result["events"] = self.list_trace_events(trace_item_id)
        return result

    def get_trace_item_by_code(self, item_code: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM trace_item WHERE item_code=?",
                (item_code,),
            ).fetchone()
        return self.get_trace_item(row["id"]) if row is not None else None

    def list_trace_items(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id FROM trace_item WHERE experiment_id=?
                   ORDER BY created_at_ms, id""",
                (experiment_id,),
            ).fetchall()
        return [self.get_trace_item(row["id"]) for row in rows]

    def list_trace_events(self, trace_item_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM trace_event WHERE trace_item_id=?
                   ORDER BY effective_at_ms, id""",
                (trace_item_id,),
            ).fetchall()
        return [self._trace_event(row) for row in rows]

    def create_storage_location(self, data: dict) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM storage_location WHERE location_code=?",
                (data["location_code"],),
            ).fetchone()
            if existing is not None:
                location_id = existing["id"]
            else:
                cursor = conn.execute(
                    """INSERT INTO storage_location(
                         location_code, display_name, storage_condition,
                         active, created_at_ms, created_by)
                       VALUES(?,?,?,1,?,?)""",
                    (
                        data["location_code"],
                        data["display_name"],
                        data.get("storage_condition"),
                        data["created_at_ms"],
                        data["actor"],
                    ),
                )
                location_id = cursor.lastrowid
        return self.get_storage_location(location_id)

    def get_storage_location(self, location_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM storage_location WHERE id=?", (location_id,)
            ).fetchone()
        return self._storage_location(row)

    def get_storage_location_by_code(
        self, location_code: str
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM storage_location WHERE location_code=?",
                (location_code,),
            ).fetchone()
        return self._storage_location(row)

    def list_storage_locations(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM storage_location WHERE active=1
                   ORDER BY display_name, location_code"""
            ).fetchall()
        return [self._storage_location(row) for row in rows]

    def transition_trace_item(self, trace_item_id: int, data: dict) -> dict:
        with self.transaction() as conn:
            duplicate = conn.execute(
                "SELECT * FROM trace_event WHERE client_event_id=?",
                (data["client_event_id"],),
            ).fetchone()
            if duplicate is not None:
                item_id = duplicate["trace_item_id"]
            else:
                item = conn.execute(
                    "SELECT * FROM trace_item WHERE id=?",
                    (trace_item_id,),
                ).fetchone()
                if item is None:
                    raise LookupError("trace item not found")
                action = data["action"]
                if action == "store":
                    if item["status"] != "active":
                        raise RuntimeError(
                            f"trace item status {item['status']} cannot be stored"
                        )
                    location = conn.execute(
                        """SELECT * FROM storage_location
                           WHERE location_code=? AND active=1""",
                        (data["location_code"],),
                    ).fetchone()
                    if location is None:
                        raise LookupError("storage location not found")
                    status = "stored"
                    location_code = location["location_code"]
                    hold_until_ms = data.get("hold_until_ms")
                    event_type = "stored"
                elif action == "retrieve":
                    if item["status"] != "stored":
                        raise RuntimeError(
                            f"trace item status {item['status']} cannot be retrieved"
                        )
                    status = "active"
                    location_code = None
                    hold_until_ms = None
                    event_type = "retrieved"
                else:
                    raise ValueError("unsupported trace item action")
                conn.execute(
                    """UPDATE trace_item SET status=?,
                         storage_location_code=?, hold_until_ms=?,
                         updated_at_ms=? WHERE id=?""",
                    (
                        status,
                        location_code,
                        hold_until_ms,
                        data["effective_at_ms"],
                        trace_item_id,
                    ),
                )
                conn.execute(
                    """INSERT INTO trace_event(
                         client_event_id, experiment_id, trace_item_id,
                         event_type, effective_at_ms, actor, location_code,
                         payload_json)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        data["client_event_id"],
                        item["experiment_id"],
                        trace_item_id,
                        event_type,
                        data["effective_at_ms"],
                        data["actor"],
                        data.get("location_code"),
                        _json(
                            {
                                "hold_until_ms": data.get("hold_until_ms"),
                                "previous_status": item["status"],
                            }
                        ),
                    ),
                )
                item_id = trace_item_id
        return self.get_trace_item(item_id)

    def request_trace_label_print(
        self, trace_item_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT * FROM label_print_job
                   WHERE client_event_id=?""",
                (data["client_event_id"],),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            item = conn.execute(
                "SELECT * FROM trace_item WHERE id=?", (trace_item_id,)
            ).fetchone()
            if item is None:
                raise LookupError("trace item not found")
            cursor = conn.execute(
                """INSERT INTO label_print_job(
                     client_event_id, experiment_id, trace_item_id,
                     storage_location_id, label_kind, reason, copies, status,
                     requested_at_ms, requested_by)
                   VALUES(?,?,?,NULL,'trace_item',?,?,'ready',?,?)""",
                (
                    data["client_event_id"],
                    item["experiment_id"],
                    trace_item_id,
                    data["reason"],
                    data["copies"],
                    data["requested_at_ms"],
                    data["actor"],
                ),
            )
            event_type = (
                "label_reprint_requested"
                if data["reason"] == "reprint"
                else "label_print_requested"
            )
            conn.execute(
                """INSERT INTO trace_event(
                     client_event_id, experiment_id, trace_item_id,
                     event_type, effective_at_ms, actor, payload_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    item["experiment_id"],
                    trace_item_id,
                    event_type,
                    data["requested_at_ms"],
                    data["actor"],
                    _json(
                        {
                            "copies": data["copies"],
                            "reason": data["reason"],
                        }
                    ),
                ),
            )
            row = conn.execute(
                "SELECT * FROM label_print_job WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def request_location_label_print(
        self, location_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT * FROM label_print_job
                   WHERE client_event_id=?""",
                (data["client_event_id"],),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            location = conn.execute(
                "SELECT * FROM storage_location WHERE id=?",
                (location_id,),
            ).fetchone()
            if location is None:
                raise LookupError("storage location not found")
            cursor = conn.execute(
                """INSERT INTO label_print_job(
                     client_event_id, experiment_id, trace_item_id,
                     storage_location_id, label_kind, reason, copies, status,
                     requested_at_ms, requested_by)
                   VALUES(?,NULL,NULL,?,'location',?,?,'ready',?,?)""",
                (
                    data["client_event_id"],
                    location_id,
                    data["reason"],
                    data["copies"],
                    data["requested_at_ms"],
                    data["actor"],
                ),
            )
            row = conn.execute(
                "SELECT * FROM label_print_job WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def list_label_print_jobs(
        self, experiment_id: int
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM label_print_job WHERE experiment_id=?
                   ORDER BY requested_at_ms, id""",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recipe_parameters(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM recipe_parameter WHERE experiment_id=?
                   ORDER BY version, id""",
                (experiment_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["inputs"] = json.loads(item.pop("inputs_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["inputs"] = {}
            result.append(item)
        return result

    def add_data_source_binding(
        self,
        experiment_id: int,
        data: dict,
        event: Optional[dict] = None,
    ) -> dict:
        with self.transaction() as conn:
            if data.get("client_event_id"):
                existing = conn.execute(
                    """SELECT * FROM experiment_data_source_binding
                       WHERE client_event_id=?""",
                    (data["client_event_id"],),
                ).fetchone()
                if existing is not None:
                    return dict(existing)
            cursor = conn.execute(
                """INSERT INTO experiment_data_source_binding(
                     experiment_id, step_instance_id, device_id, run_id,
                     device_role, metric_key, channel_selector, linked_at_ms,
                     unlinked_at_ms, link_method, confidence, client_event_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    experiment_id,
                    data.get("step_instance_id"),
                    data["device_id"],
                    data.get("run_id"),
                    data["device_role"],
                    data["metric_key"],
                    data.get("channel_selector"),
                    data["linked_at_ms"],
                    data.get("unlinked_at_ms"),
                    data.get("link_method", "manual"),
                    data.get("confidence"),
                    data.get("client_event_id"),
                ),
            )
            if event is not None:
                self.add_experiment_event(
                    experiment_id,
                    event,
                    data.get("step_instance_id"),
                    connection=conn,
                )
            row = conn.execute(
                "SELECT * FROM experiment_data_source_binding WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def replace_active_role_bindings(
        self,
        experiment_id: int,
        *,
        device_id: int,
        role_metrics: dict[str, str],
        linked_at_ms: int,
        link_method: str,
        client_event_id: str,
        step_instance_id: Optional[int] = None,
        event: Optional[dict] = None,
    ) -> list[dict]:
        """原子切换一个批次的过程设备，并保留旧绑定历史。"""
        roles = tuple(role_metrics)
        if not roles:
            return []
        placeholders = ",".join("?" for _ in roles)
        with self.transaction() as conn:
            conn.execute(
                f"""UPDATE experiment_data_source_binding
                    SET unlinked_at_ms=?
                    WHERE experiment_id=?
                      AND device_role IN ({placeholders})
                      AND unlinked_at_ms IS NULL""",
                (linked_at_ms, experiment_id, *roles),
            )
            binding_ids = []
            for role, metric_key in role_metrics.items():
                cursor = conn.execute(
                    """INSERT INTO experiment_data_source_binding(
                         experiment_id, step_instance_id, device_id, run_id,
                         device_role, metric_key, channel_selector,
                         linked_at_ms, unlinked_at_ms, link_method, confidence,
                         client_event_id)
                       VALUES(?,?,?,NULL,?,?,NULL,?,NULL,?,NULL,?)""",
                    (
                        experiment_id,
                        step_instance_id,
                        device_id,
                        role,
                        metric_key,
                        linked_at_ms,
                        link_method,
                        f"{client_event_id}:{role}",
                    ),
                )
                binding_ids.append(cursor.lastrowid)
            if event is not None:
                self.add_experiment_event(
                    experiment_id,
                    event,
                    step_instance_id,
                    connection=conn,
                )
            rows = conn.execute(
                f"""SELECT * FROM experiment_data_source_binding
                    WHERE id IN ({','.join('?' for _ in binding_ids)})
                    ORDER BY id""",
                tuple(binding_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_data_source_binding_by_client_event(
        self, client_event_id: str
    ) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM experiment_data_source_binding
                   WHERE client_event_id=?""",
                (client_event_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_data_source_bindings(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM experiment_data_source_binding
                   WHERE experiment_id=? ORDER BY linked_at_ms, id""",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_bound_samples(
        self, experiment_id: int, device_role: str, limit: int = 1000
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT
                     b.id AS binding_id, b.device_id, b.run_id AS bound_run_id,
                     b.device_role, b.metric_key, b.channel_selector,
                     b.linked_at_ms, b.unlinked_at_ms,
                     s.id AS sample_id, s.run_id, s.ts_ms, s.state,
                     s.flow_rate, s.delivered_volume, s.temp_c, s.metrics_json
                   FROM experiment_data_source_binding b
                   JOIN sample s
                     ON s.device_id=b.device_id
                    AND s.ts_ms>=b.linked_at_ms
                    AND (b.unlinked_at_ms IS NULL OR s.ts_ms<=b.unlinked_at_ms)
                    AND (b.run_id IS NULL OR s.run_id=b.run_id)
                   WHERE b.experiment_id=? AND b.device_role=?
                   ORDER BY s.ts_ms DESC, s.id DESC
                   LIMIT ?""",
                (experiment_id, device_role, limit),
            ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metrics"] = {}
            result.append(item)
        return result

    @staticmethod
    def _insert_deviation(
        conn: sqlite3.Connection, experiment_id: int, data: dict
    ) -> dict:
        cursor = conn.execute(
            """INSERT INTO deviation(
                 deviation_no, experiment_id, step_instance_id, opened_at_ms,
                 status, severity, actual_value, standard_value, description,
                 immediate_action, opened_by, cause, impact_assessment, capa,
                 disposition, reviewed_by, reviewed_at_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["deviation_no"],
                experiment_id,
                data.get("step_instance_id"),
                data["opened_at_ms"],
                data.get("status", "open"),
                data.get("severity", "warning"),
                data.get("actual_value"),
                data.get("standard_value"),
                data["description"],
                data.get("immediate_action"),
                data["opened_by"],
                data.get("cause"),
                data.get("impact_assessment"),
                data.get("capa"),
                data.get("disposition"),
                data.get("reviewed_by"),
                data.get("reviewed_at_ms"),
            ),
        )
        row = conn.execute(
            "SELECT * FROM deviation WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)

    @staticmethod
    def _next_deviation_no(
        conn: sqlite3.Connection, experiment_id: int
    ) -> str:
        experiment = conn.execute(
            "SELECT batch_id FROM experiment WHERE id=?",
            (experiment_id,),
        ).fetchone()
        if experiment is None:
            raise LookupError("experiment not found")
        rows = conn.execute(
            "SELECT deviation_no FROM deviation WHERE experiment_id=?",
            (experiment_id,),
        ).fetchall()
        prefix = f"{experiment['batch_id']}-DEV-"
        sequence = 0
        for row in rows:
            number = str(row["deviation_no"])
            suffix = number[len(prefix):] if number.startswith(prefix) else ""
            if suffix.isdigit():
                sequence = max(sequence, int(suffix))
        return f"{prefix}{sequence + 1:02d}"

    def add_deviation(
        self, experiment_id: int, data: dict, event: Optional[dict] = None
    ) -> dict:
        with self.transaction() as conn:
            if event is not None:
                existing_event = conn.execute(
                    """SELECT payload_json FROM experiment_event
                       WHERE client_event_id=?""",
                    (event["client_event_id"],),
                ).fetchone()
                if existing_event is not None:
                    try:
                        payload = json.loads(
                            existing_event["payload_json"] or "{}"
                        )
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    existing = conn.execute(
                        "SELECT * FROM deviation WHERE deviation_no=?",
                        (payload.get("deviation_no"),),
                    ).fetchone()
                    if existing is None:
                        raise RuntimeError(
                            "idempotent deviation result is unavailable"
                        )
                    return dict(existing)
            deviation_data = dict(data)
            deviation_data["deviation_no"] = self._next_deviation_no(
                conn, experiment_id
            )
            if event is not None:
                event = dict(event)
                event["payload"] = {
                    **(event.get("payload") or {}),
                    "deviation_no": deviation_data["deviation_no"],
                }
            created = self._insert_deviation(
                conn, experiment_id, deviation_data
            )
            if event is not None:
                self.add_experiment_event(
                    experiment_id,
                    event,
                    deviation_data.get("step_instance_id"),
                    connection=conn,
                )
        return created

    def get_deviation_by_no(self, deviation_no: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deviation WHERE deviation_no=?",
                (deviation_no,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_deviation(self, deviation_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM deviation WHERE id=?", (deviation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def resolve_deviation(
        self,
        experiment_id: int,
        deviation_id: int,
        data: dict,
        event: dict,
        expected_version: Optional[int] = None,
        experiment_updates: Optional[dict] = None,
        supersede_step_codes: tuple[str, ...] = (),
    ) -> dict:
        with self.transaction() as conn:
            current = conn.execute(
                """SELECT * FROM deviation
                   WHERE id=? AND experiment_id=?""",
                (deviation_id, experiment_id),
            ).fetchone()
            if current is None:
                raise LookupError("deviation not found")
            experiment = conn.execute(
                "SELECT * FROM experiment WHERE id=?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                raise LookupError("experiment not found")
            if (
                expected_version is None
                or experiment["row_version"] != expected_version
            ):
                raise RuntimeError("row version conflict")
            if experiment_updates:
                if supersede_step_codes:
                    placeholders = ",".join(
                        "?" for _ in supersede_step_codes
                    )
                    conn.execute(
                        f"""UPDATE step_instance SET status='superseded'
                            WHERE experiment_id=?
                              AND step_code IN ({placeholders})
                              AND status IN ('active', 'completed')""",
                        (experiment_id, *supersede_step_codes),
                    )
                allowed = {
                    "status",
                    "current_step_code",
                    "disposition",
                    "completed_effective_at_ms",
                }
                columns = [
                    key for key in experiment_updates if key in allowed
                ]
                sets = [f"{key}=?" for key in columns]
                values = [experiment_updates[key] for key in columns]
                sets.extend(["row_version=?", "updated_at_ms=?"])
                values.extend(
                    [
                        expected_version + 1,
                        event["received_at_server_ms"],
                        experiment_id,
                        expected_version,
                    ]
                )
                cursor = conn.execute(
                    f"""UPDATE experiment SET {', '.join(sets)}
                        WHERE id=? AND row_version=?""",
                    values,
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("row version conflict")
            conn.execute(
                """UPDATE deviation SET status='closed', cause=?,
                     impact_assessment=?, capa=?, disposition=?,
                     reviewed_by=?, reviewed_at_ms=?
                   WHERE id=? AND experiment_id=?""",
                (
                    data.get("cause"),
                    data["impact_assessment"],
                    data.get("capa"),
                    data["disposition"],
                    data["reviewed_by"],
                    event["effective_at_ms"],
                    deviation_id,
                    experiment_id,
                ),
            )
            self.add_experiment_event(
                experiment_id,
                event,
                current["step_instance_id"],
                connection=conn,
            )
            row = conn.execute(
                "SELECT * FROM deviation WHERE id=?", (deviation_id,)
            ).fetchone()
        return dict(row)

    def list_deviations(self, experiment_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM deviation WHERE experiment_id=?
                   ORDER BY opened_at_ms, id""",
                (experiment_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition_experiment(
        self,
        experiment_id: int,
        expected_version: int,
        updates: dict,
        event: dict,
        require_no_open_deviations: bool = False,
    ) -> dict:
        allowed = {
            "status",
            "current_step_code",
            "disposition",
            "completed_effective_at_ms",
        }
        columns = [key for key in updates if key in allowed]
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
            if current is None:
                raise LookupError("experiment not found")
            if current["row_version"] != expected_version:
                raise RuntimeError("row version conflict")
            if require_no_open_deviations:
                open_deviation = conn.execute(
                    """SELECT 1 FROM deviation
                       WHERE experiment_id=? AND status!='closed' LIMIT 1""",
                    (experiment_id,),
                ).fetchone()
                if open_deviation is not None:
                    raise RuntimeError(
                        "open deviations must be assessed and closed"
                    )
            values = [updates[key] for key in columns]
            sets = [f"{key}=?" for key in columns]
            sets.extend(["row_version=?", "updated_at_ms=?"])
            values.extend(
                [
                    expected_version + 1,
                    event["received_at_server_ms"],
                    experiment_id,
                    expected_version,
                ]
            )
            cursor = conn.execute(
                f"""UPDATE experiment SET {', '.join(sets)}
                    WHERE id=? AND row_version=?""",
                values,
            )
            if cursor.rowcount != 1:
                raise RuntimeError("row version conflict")
            self.add_experiment_event(
                experiment_id, event, connection=conn
            )
            row = conn.execute(
                "SELECT * FROM experiment WHERE id=?", (experiment_id,)
            ).fetchone()
        return self._experiment(row)
