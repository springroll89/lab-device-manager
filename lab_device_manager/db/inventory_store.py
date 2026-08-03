from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timedelta
from typing import Optional


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _item(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["hazards"] = json.loads(result.pop("hazards_json") or "[]")
    result["controlled_categories"] = json.loads(
        result.pop("controlled_categories_json") or "[]"
    )
    result["ghs_pictograms"] = json.loads(
        result.pop("ghs_pictograms_json") or "[]"
    )
    result["is_controlled"] = bool(result["is_controlled"])
    result["dual_control_required"] = bool(
        result["dual_control_required"]
    )
    if (
        result["status"] == "available"
        and result["category"] == "chemical"
        and result["expires_on"]
        and result["expires_on"] < date.today().isoformat()
    ):
        result["status"] = "expired"
    return result


def _movement(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json") or "{}")
    return result


def _location(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["active"] = bool(result["active"])
    result["requires_dual_control"] = bool(
        result["requires_dual_control"]
    )
    result["allowed_storage_groups"] = json.loads(
        result.pop("allowed_storage_groups_json") or "[]"
    )
    result["physical_controls"] = json.loads(
        result.pop("physical_controls_json") or "[]"
    )
    return result


def _approval(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["request_payload"] = json.loads(
        result.pop("request_payload_json") or "{}"
    )
    return result


def _waste(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["hazard_characteristics"] = json.loads(
        result.pop("hazard_characteristics_json") or "[]"
    )
    return result


def _waste_event(row) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json") or "{}")
    return result


class InventoryStore:
    def __init__(self, connection, lock):
        self._conn = connection
        self._lock = lock

    @contextmanager
    def transaction(self):
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _next_code(conn) -> str:
        rows = conn.execute(
            """SELECT container_code FROM material_container
               WHERE container_code GLOB 'PC-[0-9]*'"""
        ).fetchall()
        sequence = 0
        for row in rows:
            suffix = str(row["container_code"])[3:]
            if suffix.isdigit():
                sequence = max(sequence, int(suffix))
        return f"PC-{sequence + 1:04d}"

    def create_item(self, data: dict, *, connection=None) -> dict:
        if connection is not None:
            return self._create_item(connection, data)
        with self.transaction() as conn:
            return self._create_item(conn, data)

    def _create_item(self, conn, data: dict) -> dict:
        existing = conn.execute(
            """SELECT movement.id
               FROM inventory_movement movement
               WHERE movement.client_event_id=?""",
            (data["client_event_id"],),
        ).fetchone()
        if existing is not None:
            row = conn.execute(
                """SELECT item.* FROM material_container item
                   JOIN inventory_movement movement
                     ON movement.item_id=item.id
                   WHERE movement.id=?""",
                (existing["id"],),
            ).fetchone()
            return _item(row)
        code = (
            str(data.get("container_code") or "").strip().upper()
            or self._next_code(conn)
        )
        raw_quantity = data.get("quantity_remaining")
        quantity = (
            None if raw_quantity is None else float(raw_quantity)
        )
        cursor = conn.execute(
            """INSERT INTO material_container(
                 container_code, external_barcode, material_name,
                 supplier, supplier_lot, internal_lot, expires_on,
                 opened_on, status, quantity_remaining, unit,
                 created_at_ms, updated_at_ms, created_by,
                 created_by_user_id, category, location, owner,
                 min_threshold, max_threshold, is_controlled, note,
                 cas_no, spec, hazards_json, sds_url, prepared_by,
                 prepared_date, imported_source, imported_id,
                 hazardous_status, controlled_categories_json,
                 storage_location_id, storage_group, ghs_pictograms_json,
                 sds_revision, sds_verified_at_ms, sds_verified_by,
                 catalog_source, catalog_version, catalog_entry_no,
                 regulatory_reviewed_at_ms, regulatory_reviewed_by,
                 dual_control_required, dual_control_reason,
                 source_organization, handover_document_no,
                 handover_document_ref, received_at_ms, received_by,
                 accepted_by, regulatory_filing_no,
                 regulatory_filing_ref)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                code,
                data.get("external_barcode"),
                data["material_name"],
                data.get("supplier"),
                data.get("supplier_lot"),
                data.get("internal_lot"),
                data.get("expires_on"),
                data.get("opened_on"),
                data.get("status", "available"),
                quantity,
                data["unit"],
                data["created_at_ms"],
                data.get("updated_at_ms", data["created_at_ms"]),
                data["created_by"],
                data.get("created_by_user_id"),
                data.get("category", "chemical"),
                data.get("location"),
                data.get("owner"),
                data.get("min_threshold"),
                data.get("max_threshold"),
                1 if data.get("is_controlled") else 0,
                data.get("note"),
                data.get("cas_no"),
                data.get("spec"),
                _json(data.get("hazards") or []),
                data.get("sds_url"),
                data.get("prepared_by"),
                data.get("prepared_date"),
                data.get("imported_source"),
                data.get("imported_id"),
                data.get("hazardous_status", "not_assessed"),
                _json(data.get("controlled_categories") or []),
                data.get("storage_location_id"),
                data.get("storage_group", "unassessed"),
                _json(data.get("ghs_pictograms") or []),
                data.get("sds_revision"),
                data.get("sds_verified_at_ms"),
                data.get("sds_verified_by"),
                data.get("catalog_source"),
                data.get("catalog_version"),
                data.get("catalog_entry_no"),
                data.get("regulatory_reviewed_at_ms"),
                data.get("regulatory_reviewed_by"),
                1 if data.get("dual_control_required") else 0,
                data.get("dual_control_reason"),
                data.get("source_organization"),
                data.get("handover_document_no"),
                data.get("handover_document_ref"),
                data.get("received_at_ms"),
                data.get("received_by"),
                data.get("accepted_by"),
                data.get("regulatory_filing_no"),
                data.get("regulatory_filing_ref"),
            ),
        )
        initial_action = data.get("initial_action", "registered")
        initial_delta = data.get(
            "initial_delta", 0 if quantity is None else quantity
        )
        conn.execute(
            """INSERT INTO inventory_movement(
                 client_event_id, item_id, action, delta,
                 quantity_after, unit, effective_at_ms, actor,
                 actor_user_id, note, payload_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["client_event_id"],
                cursor.lastrowid,
                initial_action,
                initial_delta,
                quantity,
                data["unit"],
                data.get("initial_movement_at_ms", data["created_at_ms"]),
                data["created_by"],
                data.get("created_by_user_id"),
                data.get("note"),
                _json(
                    {
                        "container_code": code,
                        "source": data.get("imported_source"),
                    }
                ),
            ),
        )
        row = conn.execute(
            "SELECT * FROM material_container WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return _item(row)

    def get_item(self, item_id: int, *, connection=None) -> Optional[dict]:
        conn = connection or self._conn
        context = self._lock if connection is None else nullcontext()
        with context:
            row = conn.execute(
                "SELECT * FROM material_container WHERE id=?", (item_id,)
            ).fetchone()
        return _item(row)

    def get_item_by_code(self, code: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM material_container
                   WHERE container_code=? OR external_barcode=?""",
                (code, code),
            ).fetchone()
        return _item(row)

    def get_item_by_import_identity(
        self,
        source: str,
        imported_id: str,
        *,
        connection=None,
    ) -> Optional[dict]:
        conn = connection or self._conn
        context = self._lock if connection is None else nullcontext()
        with context:
            row = conn.execute(
                """SELECT * FROM material_container
                   WHERE imported_source=? AND imported_id=?""",
                (source, imported_id),
            ).fetchone()
        return _item(row)

    def import_historical_movement(
        self,
        item_id: int,
        data: dict,
        *,
        connection=None,
    ) -> bool:
        if connection is not None:
            return self._import_historical_movement(
                connection, item_id, data
            )
        with self.transaction() as conn:
            return self._import_historical_movement(conn, item_id, data)

    @staticmethod
    def _import_historical_movement(conn, item_id: int, data: dict) -> bool:
        if conn.execute(
            "SELECT 1 FROM material_container WHERE id=?", (item_id,)
        ).fetchone() is None:
            raise LookupError("库存物品不存在")
        cursor = conn.execute(
            """INSERT OR IGNORE INTO inventory_movement(
                 client_event_id, item_id, action, delta,
                 quantity_after, unit, effective_at_ms, actor,
                 actor_user_id, note, payload_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["client_event_id"],
                item_id,
                data["action"],
                data["delta"],
                data["quantity_after"],
                data.get("unit"),
                data["effective_at_ms"],
                data["actor"],
                data.get("actor_user_id"),
                data.get("note"),
                _json(data.get("payload") or {}),
            ),
        )
        return cursor.rowcount == 1

    def list_items(
        self,
        *,
        search: str = "",
        category: str = "",
        status: str = "",
        location: str = "",
        controlled: Optional[bool] = None,
    ) -> list[dict]:
        where = ["1=1"]
        params = []
        if search:
            where.append(
                """(material_name LIKE ? OR container_code LIKE ?
                    OR COALESCE(cas_no,'') LIKE ?
                    OR COALESCE(location,'') LIKE ?
                    OR COALESCE(supplier_lot,'') LIKE ?)"""
            )
            value = f"%{search}%"
            params.extend([value] * 5)
        if category:
            where.append("category=?")
            params.append(category)
        if status == "expired":
            where.append(
                """(
                     status='expired' OR (
                       category='chemical' AND expires_on IS NOT NULL
                       AND expires_on<? AND status='available'
                     )
                   )"""
            )
            params.append(date.today().isoformat())
        elif status == "available":
            where.append(
                """status='available' AND (
                     category!='chemical' OR expires_on IS NULL
                     OR expires_on>=?
                   )"""
            )
            params.append(date.today().isoformat())
        elif status:
            where.append("status=?")
            params.append(status)
        if location:
            where.append("location=?")
            params.append(location)
        if controlled is not None:
            where.append("is_controlled=?")
            params.append(1 if controlled else 0)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM material_container
                    WHERE {' AND '.join(where)}
                    ORDER BY material_name COLLATE NOCASE, id""",
                params,
            ).fetchall()
        return [_item(row) for row in rows]

    def update_item(self, item_id: int, updates: dict, *, connection=None) -> dict:
        allowed = {
            "material_name",
            "category",
            "external_barcode",
            "supplier",
            "supplier_lot",
            "expires_on",
            "opened_on",
            "unit",
            "location",
            "owner",
            "min_threshold",
            "max_threshold",
            "is_controlled",
            "note",
            "cas_no",
            "spec",
            "hazards_json",
            "sds_url",
            "prepared_by",
            "prepared_date",
            "hazardous_status",
            "controlled_categories_json",
            "storage_location_id",
            "storage_group",
            "ghs_pictograms_json",
            "sds_revision",
            "sds_verified_at_ms",
            "sds_verified_by",
            "catalog_source",
            "catalog_version",
            "catalog_entry_no",
            "regulatory_reviewed_at_ms",
            "regulatory_reviewed_by",
            "dual_control_required",
            "dual_control_reason",
            "source_organization",
            "handover_document_no",
            "handover_document_ref",
            "received_at_ms",
            "received_by",
            "accepted_by",
            "regulatory_filing_no",
            "regulatory_filing_ref",
        }
        values = {
            key: value for key, value in updates.items() if key in allowed
        }
        if not values:
            current = self.get_item(item_id)
            if current is None:
                raise LookupError("库存物品不存在")
            return current
        values["updated_at_ms"] = updates["updated_at_ms"]
        assignments = ", ".join(f"{key}=?" for key in values)
        context = (
            nullcontext(connection)
            if connection is not None
            else self.transaction()
        )
        with context as conn:
            cursor = conn.execute(
                f"UPDATE material_container SET {assignments} WHERE id=?",
                [*values.values(), item_id],
            )
            if cursor.rowcount != 1:
                raise LookupError("库存物品不存在")
            row = conn.execute(
                "SELECT * FROM material_container WHERE id=?", (item_id,)
            ).fetchone()
        return _item(row)

    def record_compliance_review(
        self,
        item_id: int,
        data: dict,
        *,
        release: bool,
        connection=None,
    ) -> dict:
        context = (
            nullcontext(connection)
            if connection is not None
            else self.transaction()
        )
        with context as conn:
            item = conn.execute(
                "SELECT * FROM material_container WHERE id=?", (item_id,)
            ).fetchone()
            if item is None:
                raise LookupError("库存物品不存在")
            cursor = conn.execute(
                """INSERT INTO inventory_compliance_review(
                     item_id, reviewed_at_ms, reviewed_by,
                     reviewed_by_user_id, result, checklist_json, note)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    item_id,
                    data["reviewed_at_ms"],
                    data["reviewed_by"],
                    data.get("reviewed_by_user_id"),
                    data["result"],
                    _json(data.get("checklist") or {}),
                    data.get("note"),
                ),
            )
            if (
                release
                and item["status"] == "quarantined"
                and float(item["quantity_remaining"] or 0) > 0
            ):
                conn.execute(
                    """UPDATE material_container
                       SET status='available', updated_at_ms=?
                       WHERE id=?""",
                    (data["reviewed_at_ms"], item_id),
                )
            row = conn.execute(
                "SELECT * FROM inventory_compliance_review WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        result = dict(row)
        result["checklist"] = json.loads(
            result.pop("checklist_json") or "{}"
        )
        return result

    def list_compliance_reviews(self, item_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM inventory_compliance_review
                   WHERE item_id=? ORDER BY reviewed_at_ms DESC, id DESC""",
                (item_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["checklist"] = json.loads(
                item.pop("checklist_json") or "{}"
            )
            result.append(item)
        return result

    def record_movement(
        self,
        item_id: int,
        data: dict,
        *,
        connection=None,
    ) -> dict:
        if connection is not None:
            return self._record_movement(connection, item_id, data)
        with self.transaction() as conn:
            return self._record_movement(conn, item_id, data)

    def _record_movement(self, conn, item_id: int, data: dict) -> dict:
        existing = conn.execute(
            """SELECT movement.*, experiment.batch_id
               FROM inventory_movement movement
               LEFT JOIN experiment
                 ON experiment.id=movement.experiment_id
               WHERE movement.client_event_id=?""",
            (data["client_event_id"],),
        ).fetchone()
        if existing is not None:
            item = conn.execute(
                "SELECT * FROM material_container WHERE id=?",
                (existing["item_id"],),
            ).fetchone()
            return {"item": _item(item), "movement": _movement(existing)}
        item = conn.execute(
            "SELECT * FROM material_container WHERE id=?", (item_id,)
        ).fetchone()
        if item is None:
            raise LookupError("库存物品不存在")
        action = data["action"]
        current = float(item["quantity_remaining"] or 0)
        status = item["status"]
        expired = bool(
            item["category"] == "chemical"
            and item["expires_on"]
            and item["expires_on"] < date.today().isoformat()
        )
        if expired and status in {"available", "empty"}:
            status = "expired"
        if item["status"] == "disposed":
            raise ValueError("物品已处置，不能再执行库存操作")
        quantity = data.get("quantity")
        opened_on = item["opened_on"]
        if action in {"received", "issued", "experiment_used"}:
            quantity = float(quantity)
            if quantity <= 0:
                raise ValueError("数量必须大于 0")
        if action == "received":
            if status not in {"available", "empty"}:
                raise ValueError("当前库存状态不可入库")
            delta = quantity
            quantity_after = current + quantity
            if status == "empty":
                status = "available"
                opened_on = None
        elif action in {"issued", "experiment_used"}:
            if status != "available":
                raise ValueError("当前库存状态不可领用")
            if current < quantity:
                raise ValueError("库存不足，不能完成领用")
            delta = -quantity
            quantity_after = current - quantity
            if quantity_after == 0:
                status = "empty"
        elif action == "adjusted":
            quantity_after = float(data["actual_quantity"])
            if quantity_after < 0:
                raise ValueError("盘点数量不能为负")
            delta = quantity_after - current
            status = (
                status
                if status in {"quarantined", "expired"}
                else "empty"
                if quantity_after == 0
                else "available"
            )
        elif action == "opened":
            if status != "available":
                raise ValueError("只有可用状态的物品可以标记开封")
            delta = 0
            quantity_after = current
        elif action == "quarantined":
            delta = 0
            quantity_after = current
            status = "quarantined"
        elif action == "disposed":
            delta = -current
            quantity_after = 0
            status = "disposed"
        else:
            raise ValueError("不支持的库存操作")
        unit = str(data.get("unit") or item["unit"] or "").strip()
        if action in {"received", "issued", "experiment_used", "adjusted"} and not unit:
            raise ValueError("数量操作必须填写单位")
        if item["unit"] and unit != item["unit"]:
            raise ValueError("库存操作单位与物品单位不一致")
        if action in {"opened", "experiment_used"} and not opened_on:
            opened_on = datetime.fromtimestamp(
                int(data["effective_at_ms"]) / 1000
            ).date().isoformat()
        conn.execute(
            """UPDATE material_container
               SET quantity_remaining=?, status=?, opened_on=?,
                   updated_at_ms=?
               WHERE id=?""",
            (
                quantity_after,
                status,
                opened_on,
                data["effective_at_ms"],
                item_id,
            ),
        )
        cursor = conn.execute(
            """INSERT INTO inventory_movement(
                 client_event_id, item_id, experiment_id,
                 step_instance_id, action, delta, quantity_after, unit,
                 effective_at_ms, actor, actor_user_id, note, payload_json,
                 approved_by, approved_by_user_id, approved_at_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["client_event_id"],
                item_id,
                data.get("experiment_id"),
                data.get("step_instance_id"),
                action,
                delta,
                quantity_after,
                unit,
                data["effective_at_ms"],
                data["actor"],
                data.get("actor_user_id"),
                data.get("note"),
                _json(data.get("payload") or {}),
                data.get("approved_by"),
                data.get("approved_by_user_id"),
                data.get("approved_at_ms"),
            ),
        )
        row = conn.execute(
            """SELECT movement.*, experiment.batch_id
               FROM inventory_movement movement
               LEFT JOIN experiment
                 ON experiment.id=movement.experiment_id
               WHERE movement.id=?""",
            (cursor.lastrowid,),
        ).fetchone()
        current_item = conn.execute(
            "SELECT * FROM material_container WHERE id=?", (item_id,)
        ).fetchone()
        return {
            "item": _item(current_item),
            "movement": _movement(row),
        }

    def list_movements(
        self,
        *,
        item_id: Optional[int] = None,
        action: str = "",
        experiment_id: Optional[int] = None,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        where = ["1=1"]
        params = []
        if item_id is not None:
            where.append("movement.item_id=?")
            params.append(item_id)
        if action:
            where.append("movement.action=?")
            params.append(action)
        if experiment_id is not None:
            where.append("movement.experiment_id=?")
            params.append(experiment_id)
        if from_ms is not None:
            where.append("movement.effective_at_ms>=?")
            params.append(from_ms)
        if to_ms is not None:
            where.append("movement.effective_at_ms<=?")
            params.append(to_ms)
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT movement.*, item.container_code,
                           item.material_name, experiment.batch_id
                    FROM inventory_movement movement
                    JOIN material_container item ON item.id=movement.item_id
                    LEFT JOIN experiment
                      ON experiment.id=movement.experiment_id
                    WHERE {' AND '.join(where)}
                    ORDER BY movement.effective_at_ms DESC,
                             movement.id DESC
                    LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
        return [_movement(row) for row in rows]

    def count_movements(
        self,
        *,
        item_id: Optional[int] = None,
        action: str = "",
        experiment_id: Optional[int] = None,
        from_ms: Optional[int] = None,
        to_ms: Optional[int] = None,
    ) -> int:
        where = ["1=1"]
        params = []
        if item_id is not None:
            where.append("item_id=?")
            params.append(item_id)
        if action:
            where.append("action=?")
            params.append(action)
        if experiment_id is not None:
            where.append("experiment_id=?")
            params.append(experiment_id)
        if from_ms is not None:
            where.append("effective_at_ms>=?")
            params.append(from_ms)
        if to_ms is not None:
            where.append("effective_at_ms<=?")
            params.append(to_ms)
        with self._lock:
            return self._conn.execute(
                f"""SELECT COUNT(*) FROM inventory_movement
                    WHERE {' AND '.join(where)}""",
                params,
            ).fetchone()[0]

    def get_storage_location_by_code(
        self, location_code: str, *, connection=None
    ) -> Optional[dict]:
        conn = connection or self._conn
        context = self._lock if connection is None else nullcontext()
        with context:
            row = conn.execute(
                """SELECT * FROM storage_location
                   WHERE location_code=? AND active=1""",
                (location_code,),
            ).fetchone()
        return _location(row)

    def list_storage_locations(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM storage_location
                   WHERE active=1 ORDER BY display_name, location_code"""
            ).fetchall()
        return [_location(row) for row in rows]

    def create_storage_location(self, data: dict) -> dict:
        with self.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO storage_location(
                     location_code, display_name, storage_condition,
                     active, created_at_ms, created_by, location_type,
                     allowed_storage_groups_json, requires_dual_control,
                     physical_controls_json, compliance_note)
                   VALUES(?,?,?,1,?,?,?,?,?,?,?)""",
                (
                    data["location_code"],
                    data["display_name"],
                    data.get("storage_condition"),
                    data["created_at_ms"],
                    data["created_by"],
                    data["location_type"],
                    _json(data["allowed_storage_groups"]),
                    1 if data.get("requires_dual_control") else 0,
                    _json(data.get("physical_controls") or []),
                    data.get("compliance_note"),
                ),
            )
            row = conn.execute(
                "SELECT * FROM storage_location WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return _location(row)

    def update_storage_location(
        self, location_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            cursor = conn.execute(
                """UPDATE storage_location
                   SET display_name=?, storage_condition=?, location_type=?,
                       allowed_storage_groups_json=?,
                       requires_dual_control=?,
                       physical_controls_json=?, compliance_note=?
                   WHERE id=? AND active=1""",
                (
                    data["display_name"],
                    data.get("storage_condition"),
                    data["location_type"],
                    _json(data["allowed_storage_groups"]),
                    1 if data.get("requires_dual_control") else 0,
                    _json(data.get("physical_controls") or []),
                    data.get("compliance_note"),
                    location_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("库存位置不存在")
            row = conn.execute(
                "SELECT * FROM storage_location WHERE id=?",
                (location_id,),
            ).fetchone()
        return _location(row)

    def list_active_items_at_location(
        self, location_id: int, *, exclude_item_id: Optional[int] = None
    ) -> list[dict]:
        where = [
            "storage_location_id=?",
            "status NOT IN ('disposed', 'empty')",
        ]
        params: list = [location_id]
        if exclude_item_id is not None:
            where.append("id<>?")
            params.append(exclude_item_id)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM material_container
                    WHERE {' AND '.join(where)}
                    ORDER BY id""",
                params,
            ).fetchall()
        return [_item(row) for row in rows]

    def request_operation_approval(self, data: dict) -> dict:
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT approval.*, item.container_code,
                          item.material_name
                   FROM inventory_operation_approval approval
                   JOIN material_container item ON item.id=approval.item_id
                   WHERE approval.client_event_id=?""",
                (data["client_event_id"],),
            ).fetchone()
            if existing is not None:
                return _approval(existing)
            cursor = conn.execute(
                """INSERT INTO inventory_operation_approval(
                     client_event_id, item_id, action, request_payload_json,
                     requested_at_ms, requested_by, requested_by_user_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    data["item_id"],
                    data["action"],
                    _json(data["request_payload"]),
                    data["requested_at_ms"],
                    data["requested_by"],
                    data["requested_by_user_id"],
                ),
            )
            row = conn.execute(
                """SELECT approval.*, item.container_code,
                          item.material_name
                   FROM inventory_operation_approval approval
                   JOIN material_container item ON item.id=approval.item_id
                   WHERE approval.id=?""",
                (cursor.lastrowid,),
            ).fetchone()
        return _approval(row)

    def list_operation_approvals(
        self, *, status: str = "pending"
    ) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT approval.*, item.container_code,
                          item.material_name, item.unit
                   FROM inventory_operation_approval approval
                   JOIN material_container item ON item.id=approval.item_id
                   WHERE approval.status=?
                   ORDER BY approval.requested_at_ms, approval.id""",
                (status,),
            ).fetchall()
        return [_approval(row) for row in rows]

    def decide_operation_approval(
        self,
        approval_id: int,
        *,
        approved: bool,
        decided_at_ms: int,
        decided_by: str,
        decided_by_user_id: int,
        decision_note: Optional[str],
    ) -> dict:
        with self.transaction() as conn:
            row = conn.execute(
                """SELECT approval.*, item.container_code,
                          item.material_name
                   FROM inventory_operation_approval approval
                   JOIN material_container item ON item.id=approval.item_id
                   WHERE approval.id=?""",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise LookupError("待审批操作不存在")
            approval = _approval(row)
            if approval["requested_by_user_id"] == decided_by_user_id:
                raise ValueError("申请人与复核人必须是两个不同账号")
            if approval["status"] != "pending":
                if approval["status"] == "approved":
                    movement = conn.execute(
                        """SELECT movement.*, experiment.batch_id
                           FROM inventory_movement movement
                           LEFT JOIN experiment
                             ON experiment.id=movement.experiment_id
                           WHERE movement.id=?""",
                        (approval["movement_id"],),
                    ).fetchone()
                    item = conn.execute(
                        "SELECT * FROM material_container WHERE id=?",
                        (approval["item_id"],),
                    ).fetchone()
                    return {
                        "approval": approval,
                        "item": _item(item),
                        "movement": _movement(movement),
                    }
                raise ValueError("该操作已经处理，不能重复决定")
            if not approved:
                conn.execute(
                    """UPDATE inventory_operation_approval
                       SET status='rejected', decided_at_ms=?,
                           decided_by=?, decided_by_user_id=?,
                           decision_note=?
                       WHERE id=?""",
                    (
                        decided_at_ms,
                        decided_by,
                        decided_by_user_id,
                        decision_note,
                        approval_id,
                    ),
                )
                rejected = conn.execute(
                    """SELECT approval.*, item.container_code,
                              item.material_name
                       FROM inventory_operation_approval approval
                       JOIN material_container item
                         ON item.id=approval.item_id
                       WHERE approval.id=?""",
                    (approval_id,),
                ).fetchone()
                return {"approval": _approval(rejected)}
            payload = dict(approval["request_payload"])
            payload.update(
                {
                    "approved_by": decided_by,
                    "approved_by_user_id": decided_by_user_id,
                    "approved_at_ms": decided_at_ms,
                }
            )
            result = self._record_movement(
                conn, approval["item_id"], payload
            )
            conn.execute(
                """UPDATE inventory_operation_approval
                   SET status='approved', decided_at_ms=?,
                       decided_by=?, decided_by_user_id=?,
                       decision_note=?, movement_id=?
                   WHERE id=?""",
                (
                    decided_at_ms,
                    decided_by,
                    decided_by_user_id,
                    decision_note,
                    result["movement"]["id"],
                    approval_id,
                ),
            )
            decided = conn.execute(
                """SELECT approval.*, item.container_code,
                          item.material_name
                   FROM inventory_operation_approval approval
                   JOIN material_container item ON item.id=approval.item_id
                   WHERE approval.id=?""",
                (approval_id,),
            ).fetchone()
            result["approval"] = _approval(decided)
            return result

    def create_hazardous_waste(self, data: dict) -> dict:
        with self.transaction() as conn:
            duplicate = conn.execute(
                """SELECT waste.*, location.location_code,
                          location.display_name AS location_name
                   FROM hazardous_waste_container waste
                   JOIN hazardous_waste_event event
                     ON event.waste_container_id=waste.id
                   JOIN storage_location location
                     ON location.id=waste.storage_location_id
                   WHERE event.client_event_id=?""",
                (data["client_event_id"],),
            ).fetchone()
            if duplicate is not None:
                return _waste(duplicate)
            waste_code = data.get("waste_code")
            if not waste_code:
                prefix = f"HW-{datetime.fromtimestamp(data['created_at_ms'] / 1000):%Y}-"
                rows = conn.execute(
                    """SELECT waste_code FROM hazardous_waste_container
                       WHERE waste_code LIKE ?""",
                    (f"{prefix}%",),
                ).fetchall()
                sequence = max(
                    (
                        int(str(row["waste_code"])[len(prefix) :])
                        for row in rows
                        if str(row["waste_code"])[len(prefix) :].isdigit()
                    ),
                    default=0,
                )
                waste_code = f"{prefix}{sequence + 1:04d}"
            cursor = conn.execute(
                """INSERT INTO hazardous_waste_container(
                     waste_code, waste_name, waste_category_code,
                     waste_category_name, physical_state,
                     hazard_characteristics_json, composition, quantity,
                     unit, package_type, storage_location_id,
                     source_experiment_id, source_item_id, status,
                     started_at_ms, created_at_ms, updated_at_ms,
                     created_by, created_by_user_id, note)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'accumulating',?,?,?,?,?,?)""",
                (
                    waste_code,
                    data["waste_name"],
                    data["waste_category_code"],
                    data.get("waste_category_name"),
                    data["physical_state"],
                    _json(data["hazard_characteristics"]),
                    data["composition"],
                    data["quantity"],
                    data["unit"],
                    data["package_type"],
                    data["storage_location_id"],
                    data.get("source_experiment_id"),
                    data.get("source_item_id"),
                    data["started_at_ms"],
                    data["created_at_ms"],
                    data["created_at_ms"],
                    data["created_by"],
                    data.get("created_by_user_id"),
                    data.get("note"),
                ),
            )
            waste_id = cursor.lastrowid
            conn.execute(
                """INSERT INTO hazardous_waste_event(
                     client_event_id, waste_container_id, event_type,
                     quantity_delta, quantity_after, effective_at_ms,
                     actor, actor_user_id, payload_json)
                   VALUES(?,?,'created',?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    waste_id,
                    data["quantity"],
                    data["quantity"],
                    data["created_at_ms"],
                    data["created_by"],
                    data.get("created_by_user_id"),
                    _json({"waste_code": waste_code}),
                ),
            )
            row = conn.execute(
                """SELECT waste.*, location.location_code,
                          location.display_name AS location_name
                   FROM hazardous_waste_container waste
                   JOIN storage_location location
                     ON location.id=waste.storage_location_id
                   WHERE waste.id=?""",
                (waste_id,),
            ).fetchone()
        return _waste(row)

    def list_hazardous_waste(self, status: str = "") -> list[dict]:
        where = "WHERE waste.status=?" if status else ""
        params = (status,) if status else ()
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT waste.*, location.location_code,
                           location.display_name AS location_name
                    FROM hazardous_waste_container waste
                    JOIN storage_location location
                      ON location.id=waste.storage_location_id
                    {where}
                    ORDER BY waste.updated_at_ms DESC, waste.id DESC""",
                params,
            ).fetchall()
        return [_waste(row) for row in rows]

    def get_hazardous_waste(self, waste_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                """SELECT waste.*, location.location_code,
                          location.display_name AS location_name
                   FROM hazardous_waste_container waste
                   JOIN storage_location location
                     ON location.id=waste.storage_location_id
                   WHERE waste.id=?""",
                (waste_id,),
            ).fetchone()
            events = self._conn.execute(
                """SELECT * FROM hazardous_waste_event
                   WHERE waste_container_id=?
                   ORDER BY effective_at_ms DESC, id DESC""",
                (waste_id,),
            ).fetchall()
            transfers = self._conn.execute(
                """SELECT * FROM hazardous_waste_transfer
                   WHERE waste_container_id=?
                   ORDER BY transfer_at_ms DESC, id DESC""",
                (waste_id,),
            ).fetchall()
        waste = _waste(row)
        if waste is None:
            return None
        waste["events"] = [_waste_event(event) for event in events]
        waste["transfers"] = [dict(transfer) for transfer in transfers]
        return waste

    def add_hazardous_waste_quantity(
        self, waste_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            duplicate = conn.execute(
                """SELECT * FROM hazardous_waste_event
                   WHERE client_event_id=?""",
                (data["client_event_id"],),
            ).fetchone()
            waste = conn.execute(
                "SELECT * FROM hazardous_waste_container WHERE id=?",
                (waste_id,),
            ).fetchone()
            if waste is None:
                raise LookupError("危废容器不存在")
            if duplicate is not None:
                return self.get_hazardous_waste(waste_id)
            if waste["status"] != "accumulating":
                raise ValueError("危废容器已封口，不能继续加入")
            quantity_after = float(waste["quantity"]) + data["quantity"]
            conn.execute(
                """UPDATE hazardous_waste_container
                   SET quantity=?, updated_at_ms=? WHERE id=?""",
                (quantity_after, data["effective_at_ms"], waste_id),
            )
            conn.execute(
                """INSERT INTO hazardous_waste_event(
                     client_event_id, waste_container_id, event_type,
                     quantity_delta, quantity_after, effective_at_ms,
                     actor, actor_user_id, payload_json)
                   VALUES(?,?,'quantity_added',?,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    waste_id,
                    data["quantity"],
                    quantity_after,
                    data["effective_at_ms"],
                    data["actor"],
                    data.get("actor_user_id"),
                    _json(data.get("payload") or {}),
                ),
            )
        return self.get_hazardous_waste(waste_id)

    def seal_hazardous_waste(self, waste_id: int, data: dict) -> dict:
        with self.transaction() as conn:
            waste = conn.execute(
                "SELECT * FROM hazardous_waste_container WHERE id=?",
                (waste_id,),
            ).fetchone()
            if waste is None:
                raise LookupError("危废容器不存在")
            duplicate = conn.execute(
                """SELECT 1 FROM hazardous_waste_event
                   WHERE waste_container_id=? AND client_event_id=?""",
                (waste_id, data["client_event_id"]),
            ).fetchone()
            if duplicate is not None:
                return self.get_hazardous_waste(waste_id)
            if waste["status"] != "accumulating":
                raise ValueError("危废容器当前状态不能封口")
            if float(waste["quantity"]) <= 0:
                raise ValueError("空危废容器不能封口待转移")
            conn.execute(
                """UPDATE hazardous_waste_container
                   SET status='ready_for_transfer', sealed_at_ms=?,
                       updated_at_ms=? WHERE id=?""",
                (data["effective_at_ms"], data["effective_at_ms"], waste_id),
            )
            conn.execute(
                """INSERT INTO hazardous_waste_event(
                     client_event_id, waste_container_id, event_type,
                     quantity_delta, quantity_after, effective_at_ms,
                     actor, actor_user_id, payload_json)
                   VALUES(?,?,'sealed',0,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    waste_id,
                    waste["quantity"],
                    data["effective_at_ms"],
                    data["actor"],
                    data.get("actor_user_id"),
                    _json(data.get("payload") or {}),
                ),
            )
        return self.get_hazardous_waste(waste_id)

    def register_hazardous_waste_transfer(
        self, waste_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            waste = conn.execute(
                "SELECT * FROM hazardous_waste_container WHERE id=?",
                (waste_id,),
            ).fetchone()
            if waste is None:
                raise LookupError("危废容器不存在")
            duplicate = conn.execute(
                """SELECT 1 FROM hazardous_waste_event
                   WHERE waste_container_id=? AND client_event_id=?""",
                (waste_id, data["client_event_id"]),
            ).fetchone()
            if duplicate is not None:
                return self.get_hazardous_waste(waste_id)
            if waste["status"] != "ready_for_transfer":
                raise ValueError("危废容器尚未封口待转移")
            cursor = conn.execute(
                """INSERT INTO hazardous_waste_transfer(
                     waste_container_id, national_manifest_no,
                     national_system_ref, transfer_at_ms, quantity, unit,
                     transporter_name, transporter_license_no, vehicle_no,
                     recipient_name, recipient_permit_no, disposal_method,
                     status, registered_at_ms, registered_by,
                     registered_by_user_id, note)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'registered',?,?,?,?)""",
                (
                    waste_id,
                    data["national_manifest_no"],
                    data.get("national_system_ref"),
                    data["transfer_at_ms"],
                    waste["quantity"],
                    waste["unit"],
                    data["transporter_name"],
                    data["transporter_license_no"],
                    data.get("vehicle_no"),
                    data["recipient_name"],
                    data["recipient_permit_no"],
                    data["disposal_method"],
                    data["registered_at_ms"],
                    data["registered_by"],
                    data.get("registered_by_user_id"),
                    data.get("note"),
                ),
            )
            conn.execute(
                """INSERT INTO hazardous_waste_event(
                     client_event_id, waste_container_id, event_type,
                     quantity_delta, quantity_after, effective_at_ms,
                     actor, actor_user_id, payload_json)
                   VALUES(?,?,'transfer_registered',0,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    waste_id,
                    waste["quantity"],
                    data["registered_at_ms"],
                    data["registered_by"],
                    data.get("registered_by_user_id"),
                    _json(
                        {
                            "transfer_id": cursor.lastrowid,
                            "national_manifest_no": data[
                                "national_manifest_no"
                            ],
                        }
                    ),
                ),
            )
        return self.get_hazardous_waste(waste_id)

    def complete_hazardous_waste_transfer(
        self, waste_id: int, data: dict
    ) -> dict:
        with self.transaction() as conn:
            waste = conn.execute(
                "SELECT * FROM hazardous_waste_container WHERE id=?",
                (waste_id,),
            ).fetchone()
            if waste is None:
                raise LookupError("危废容器不存在")
            duplicate = conn.execute(
                """SELECT 1 FROM hazardous_waste_event
                   WHERE waste_container_id=? AND client_event_id=?""",
                (waste_id, data["client_event_id"]),
            ).fetchone()
            if duplicate is not None:
                return self.get_hazardous_waste(waste_id)
            transfer = conn.execute(
                """SELECT * FROM hazardous_waste_transfer
                   WHERE waste_container_id=? AND status='registered'
                   ORDER BY id DESC LIMIT 1""",
                (waste_id,),
            ).fetchone()
            if waste["status"] != "ready_for_transfer" or transfer is None:
                raise ValueError("没有可完成的危废转移联单")
            conn.execute(
                """UPDATE hazardous_waste_transfer
                   SET status='completed', completed_at_ms=?,
                       completed_by=?, completed_by_user_id=?
                   WHERE id=?""",
                (
                    data["effective_at_ms"],
                    data["actor"],
                    data.get("actor_user_id"),
                    transfer["id"],
                ),
            )
            conn.execute(
                """UPDATE hazardous_waste_container
                   SET status='transferred', transferred_at_ms=?,
                       updated_at_ms=? WHERE id=?""",
                (
                    data["effective_at_ms"],
                    data["effective_at_ms"],
                    waste_id,
                ),
            )
            conn.execute(
                """INSERT INTO hazardous_waste_event(
                     client_event_id, waste_container_id, event_type,
                     quantity_delta, quantity_after, effective_at_ms,
                     actor, actor_user_id, payload_json)
                   VALUES(?,?,'transfer_completed',0,?,?,?,?,?)""",
                (
                    data["client_event_id"],
                    waste_id,
                    waste["quantity"],
                    data["effective_at_ms"],
                    data["actor"],
                    data.get("actor_user_id"),
                    _json(
                        {
                            "transfer_id": transfer["id"],
                            "national_manifest_no": transfer[
                                "national_manifest_no"
                            ],
                        }
                    ),
                ),
            )
        return self.get_hazardous_waste(waste_id)

    def get_summary(self, today_text: Optional[str] = None) -> dict:
        today_value = date.fromisoformat(today_text) if today_text else date.today()
        expiry_limit = (today_value + timedelta(days=30)).isoformat()
        today_iso = today_value.isoformat()
        items = self.list_items()
        low_stock_items = [
            item
            for item in items
            if item["quantity_remaining"] is not None
            and item["quantity_remaining"] > 0
            and item["min_threshold"] is not None
            and item["quantity_remaining"] <= item["min_threshold"]
        ]
        over_stock_items = [
            item
            for item in items
            if item["quantity_remaining"] is not None
            and item["max_threshold"] is not None
            and item["quantity_remaining"] >= item["max_threshold"]
        ]
        expiring_items = [
            item
            for item in items
            if item["category"] == "chemical"
            and item["expires_on"] is not None
            and today_iso <= item["expires_on"] <= expiry_limit
        ]
        expired_items = [
            item
            for item in items
            if item["category"] == "chemical"
            and item["expires_on"] is not None
            and item["expires_on"] < today_iso
        ]
        to_dispose_items = [
            item for item in items if item["status"] == "quarantined"
        ]
        used_up_items = [
            item
            for item in items
            if item["status"] == "empty"
            or (
                item["quantity_remaining"] is not None
                and item["quantity_remaining"] <= 0
            )
        ]
        controlled_items = [item for item in items if item["is_controlled"]]
        hazardous_items = [
            item for item in items
            if item["hazardous_status"] == "listed"
        ]
        compliance_incomplete_items = [
            item
            for item in items
            if item["category"] == "chemical"
            and (
                item["hazardous_status"] in {
                    "not_assessed",
                    "pending_review",
                }
                or (
                    item["hazardous_status"] == "listed"
                    and (
                        not item["sds_url"]
                        or not item["sds_verified_at_ms"]
                        or item["storage_group"] == "unassessed"
                        or not item["storage_location_id"]
                    )
                )
                or (
                    item["hazardous_status"] == "not_listed"
                    and not item["regulatory_reviewed_at_ms"]
                )
            )
        ]
        hazard_counts = {}
        hazardous_status_counts = {}
        control_category_counts = {}
        for item in items:
            if item["category"] != "chemical":
                continue
            hazardous_status = item["hazardous_status"]
            hazardous_status_counts[hazardous_status] = (
                hazardous_status_counts.get(hazardous_status, 0) + 1
            )
            for category in item["controlled_categories"]:
                if category:
                    control_category_counts[category] = (
                        control_category_counts.get(category, 0) + 1
                    )
            for hazard in item["hazards"]:
                if hazard:
                    hazard_counts[hazard] = hazard_counts.get(hazard, 0) + 1
        with self._lock:
            last_adjust_at_ms = self._conn.execute(
                """SELECT MAX(effective_at_ms)
                   FROM inventory_movement WHERE action='adjusted'"""
            ).fetchone()[0]
            pending_dual_approvals = self._conn.execute(
                """SELECT COUNT(*) FROM inventory_operation_approval
                   WHERE status='pending'"""
            ).fetchone()[0]
        days_since_last_adjust = None
        if last_adjust_at_ms is not None:
            last_adjust_date = datetime.fromtimestamp(
                last_adjust_at_ms / 1000
            ).date()
            days_since_last_adjust = (today_value - last_adjust_date).days
        return {
            "total_items": len(items),
            "chemical": sum(i["category"] == "chemical" for i in items),
            "consumable": sum(i["category"] == "consumable" for i in items),
            "office": sum(i["category"] == "office" for i in items),
            "low_stock": len(low_stock_items),
            "over_stock": len(over_stock_items),
            "expiring": len(expiring_items),
            "expired": len(expired_items),
            "to_dispose": len(to_dispose_items),
            "used_up": len(used_up_items),
            "controlled": len(controlled_items),
            "hazardous": len(hazardous_items),
            "compliance_incomplete": len(compliance_incomplete_items),
            "compliance_incomplete_items": compliance_incomplete_items,
            "pending_dual_approvals": pending_dual_approvals,
            "low_stock_items": low_stock_items,
            "over_stock_items": over_stock_items,
            "expiring_items": expiring_items,
            "expired_items": expired_items,
            "to_dispose_items": to_dispose_items,
            "used_up_items": used_up_items,
            "controlled_items": controlled_items,
            "hazard_counts": hazard_counts,
            "hazardous_status_counts": hazardous_status_counts,
            "control_category_counts": control_category_counts,
            "last_adjust_at_ms": last_adjust_at_ms,
            "days_since_last_adjust": days_since_last_adjust,
        }
