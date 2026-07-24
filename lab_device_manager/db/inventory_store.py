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
    result["is_controlled"] = bool(result["is_controlled"])
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
        quantity = float(data.get("quantity_remaining") or 0)
        cursor = conn.execute(
            """INSERT INTO material_container(
                 container_code, external_barcode, material_name,
                 supplier, supplier_lot, internal_lot, expires_on,
                 opened_on, status, quantity_remaining, unit,
                 created_at_ms, updated_at_ms, created_by,
                 created_by_user_id, category, location, owner,
                 min_threshold, max_threshold, is_controlled, note,
                 cas_no, spec, hazards_json, sds_url, prepared_by,
                 prepared_date, imported_source, imported_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        initial_action = data.get("initial_action", "registered")
        initial_delta = data.get("initial_delta", quantity)
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

    def get_item(self, item_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
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

    def update_item(self, item_id: int, updates: dict) -> dict:
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
        with self.transaction() as conn:
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
        if item["unit"] and unit != item["unit"]:
            raise ValueError("库存操作单位与物品单位不一致")
        opened_on = item["opened_on"]
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
                 effective_at_ms, actor, actor_user_id, note, payload_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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

    def get_summary(self, today_text: Optional[str] = None) -> dict:
        today_value = date.fromisoformat(today_text) if today_text else date.today()
        expiry_limit = (today_value + timedelta(days=30)).isoformat()
        today_iso = today_value.isoformat()
        items = self.list_items()
        low_stock_items = [
            item
            for item in items
            if item["quantity_remaining"] > 0
            and item["min_threshold"] is not None
            and item["quantity_remaining"] <= item["min_threshold"]
        ]
        over_stock_items = [
            item
            for item in items
            if item["max_threshold"] is not None
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
            if item["status"] == "empty" or item["quantity_remaining"] <= 0
        ]
        controlled_items = [item for item in items if item["is_controlled"]]
        hazard_counts = {}
        for item in items:
            if item["category"] != "chemical":
                continue
            for hazard in item["hazards"]:
                if hazard:
                    hazard_counts[hazard] = hazard_counts.get(hazard, 0) + 1
        with self._lock:
            last_adjust_at_ms = self._conn.execute(
                """SELECT MAX(effective_at_ms)
                   FROM inventory_movement WHERE action='adjusted'"""
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
            "low_stock_items": low_stock_items,
            "over_stock_items": over_stock_items,
            "expiring_items": expiring_items,
            "expired_items": expired_items,
            "to_dispose_items": to_dispose_items,
            "used_up_items": used_up_items,
            "controlled_items": controlled_items,
            "hazard_counts": hazard_counts,
            "last_adjust_at_ms": last_adjust_at_ms,
            "days_since_last_adjust": days_since_last_adjust,
        }
