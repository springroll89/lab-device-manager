from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import date, datetime, time as datetime_time
from urllib.parse import urlparse


class InventoryError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class InventoryService:
    CATEGORIES = {"chemical", "consumable", "office"}
    STATUSES = {"available", "empty", "quarantined", "expired", "disposed"}
    MOVEMENTS = {
        "received",
        "issued",
        "adjusted",
        "opened",
        "quarantined",
        "disposed",
    }
    AUDIT_ACTIONS = MOVEMENTS | {
        "registered",
        "experiment_used",
        "imported",
    }

    def __init__(self, store, clock_ms=None):
        self.store = store
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @staticmethod
    def _text(value, *, required=False, field="字段") -> str:
        result = str(value or "").strip()
        if required and not result:
            raise InventoryError(f"{field}不能为空")
        if len(result) > 500:
            raise InventoryError(f"{field}过长")
        return result

    @staticmethod
    def _number(value, field: str, *, optional=False):
        if value in (None, "") and optional:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise InventoryError(f"{field}必须是数字") from exc
        if not math.isfinite(result) or result < 0:
            raise InventoryError(f"{field}不能为负数")
        return result

    @staticmethod
    def _date(value, field: str):
        if value in (None, ""):
            return None
        text = str(value)
        try:
            date.fromisoformat(text)
        except ValueError as exc:
            raise InventoryError(f"{field}必须使用 YYYY-MM-DD 格式") from exc
        return text

    @staticmethod
    def _url(value, field: str):
        if value in (None, ""):
            return None
        text = str(value).strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InventoryError(f"{field}必须是 http(s) 地址")
        return text

    def create_item(self, data: dict) -> dict:
        category = self._text(
            data.get("category"), required=True, field="类别"
        )
        if category not in self.CATEGORIES:
            raise InventoryError("类别必须是化学品、耗材或办公用品")
        quantity = self._number(data.get("quantity"), "数量")
        unit = self._text(data.get("unit"), required=True, field="单位")
        minimum = self._number(
            data.get("min_threshold"), "库存下限", optional=True
        )
        maximum = self._number(
            data.get("max_threshold"), "库存上限", optional=True
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise InventoryError("库存下限不能高于库存上限")
        hazards = data.get("hazards") or []
        if not isinstance(hazards, list) or not all(
            isinstance(value, str) for value in hazards
        ):
            raise InventoryError("危险性必须是文本列表")
        payload = {
            "container_code": self._text(data.get("code")),
            "external_barcode": self._text(data.get("external_barcode")) or None,
            "material_name": self._text(
                data.get("name"), required=True, field="名称"
            ),
            "category": category,
            "quantity_remaining": quantity,
            "unit": unit,
            "location": self._text(data.get("location")) or None,
            "owner": self._text(data.get("owner")) or None,
            "min_threshold": minimum,
            "max_threshold": maximum,
            "is_controlled": bool(data.get("is_controlled")),
            "status": "available" if quantity > 0 else "empty",
            "note": self._text(data.get("note")) or None,
            "supplier": self._text(data.get("supplier")) or None,
            "supplier_lot": self._text(
                data.get("lot_no") or data.get("supplier_lot")
            )
            or None,
            "expires_on": self._date(
                data.get("expiry_date") or data.get("expires_on"), "有效期"
            ),
            "opened_on": self._date(
                data.get("opened_date") or data.get("opened_on"), "开封日期"
            ),
            "cas_no": self._text(data.get("cas_no")) or None,
            "spec": self._text(data.get("spec")) or None,
            "hazards": hazards if category == "chemical" else [],
            "sds_url": self._url(data.get("sds_url"), "SDS 链接")
            if category == "chemical"
            else None,
            "prepared_by": self._text(data.get("prepared_by")) or None,
            "prepared_date": self._date(
                data.get("prepared_date"), "配制日期"
            ),
            "created_at_ms": self.clock_ms(),
            "created_by": self._text(
                data.get("created_by"), required=True, field="创建人"
            ),
            "created_by_user_id": data.get("created_by_user_id"),
            "client_event_id": self._text(
                data.get("client_event_id"),
                required=True,
                field="操作编号",
            ),
            "imported_source": data.get("imported_source"),
            "imported_id": data.get("imported_id"),
        }
        try:
            return self.store.create_item(payload)
        except sqlite3.IntegrityError as exc:
            raise InventoryError("物品编号或外部条码已存在", 409) from exc

    def list_items(self, filters: dict) -> list[dict]:
        category = self._text(filters.get("category"))
        if category and category not in self.CATEGORIES:
            raise InventoryError("库存类别无效")
        status = self._text(filters.get("status"))
        if status and status not in self.STATUSES:
            raise InventoryError("库存状态无效")
        controlled_text = self._text(filters.get("controlled")).lower()
        controlled = None
        if controlled_text:
            if controlled_text not in {"true", "false"}:
                raise InventoryError("管制类筛选值无效")
            controlled = controlled_text == "true"
        return self.store.list_items(
            search=self._text(filters.get("search"))[:100],
            category=category,
            status=status,
            location=self._text(filters.get("location")),
            controlled=controlled,
        )

    def get_item(self, item_id: int) -> dict:
        item = self.store.get_item(item_id)
        if item is None:
            raise InventoryError("库存物品不存在", 404)
        return {
            "item": item,
            "movements": self.store.list_movements(item_id=item_id),
        }

    def update_item(self, item_id: int, data: dict) -> dict:
        current = self.store.get_item(item_id)
        if current is None:
            raise InventoryError("库存物品不存在", 404)
        updates = {}
        text_fields = {
            "name": ("material_name", "名称"),
            "category": ("category", "类别"),
            "external_barcode": ("external_barcode", "外部条码"),
            "supplier": ("supplier", "供应商"),
            "lot_no": ("supplier_lot", "供应商批号"),
            "location": ("location", "位置"),
            "owner": ("owner", "负责人"),
            "note": ("note", "备注"),
            "cas_no": ("cas_no", "CAS 号"),
            "spec": ("spec", "规格"),
            "prepared_by": ("prepared_by", "配制人"),
        }
        for exposed, (stored, label) in text_fields.items():
            if exposed in data:
                value = self._text(
                    data.get(exposed),
                    required=exposed in {"name", "category"},
                    field=label,
                )
                updates[stored] = value or None
        category = updates.get("category", current["category"])
        if category not in self.CATEGORIES:
            raise InventoryError("类别必须是化学品、耗材或办公用品")
        if "unit" in data:
            updates["unit"] = self._text(
                data.get("unit"), required=True, field="单位"
            )
        for exposed, stored, label in (
            ("min_threshold", "min_threshold", "库存下限"),
            ("max_threshold", "max_threshold", "库存上限"),
        ):
            if exposed in data:
                updates[stored] = self._number(
                    data.get(exposed), label, optional=True
                )
        minimum = updates.get("min_threshold", current["min_threshold"])
        maximum = updates.get("max_threshold", current["max_threshold"])
        if minimum is not None and maximum is not None and minimum > maximum:
            raise InventoryError("库存下限不能高于库存上限")
        if "is_controlled" in data:
            updates["is_controlled"] = 1 if data["is_controlled"] else 0
        for exposed, stored, label in (
            ("expiry_date", "expires_on", "有效期"),
            ("opened_date", "opened_on", "开封日期"),
            ("prepared_date", "prepared_date", "配制日期"),
        ):
            if exposed in data:
                updates[stored] = self._date(data.get(exposed), label)
        if "sds_url" in data:
            updates["sds_url"] = self._url(data.get("sds_url"), "SDS 链接")
        if "hazards" in data:
            hazards = data.get("hazards") or []
            if not isinstance(hazards, list) or not all(
                isinstance(value, str) for value in hazards
            ):
                raise InventoryError("危险性必须是文本列表")
            updates["hazards_json"] = json.dumps(
                hazards if category == "chemical" else [],
                ensure_ascii=False,
            )
        if category != "chemical":
            updates.update(
                {
                    "cas_no": None,
                    "spec": None,
                    "hazards_json": "[]",
                    "sds_url": None,
                    "opened_on": None,
                    "expires_on": None,
                    "prepared_by": None,
                    "prepared_date": None,
                }
            )
        updates["updated_at_ms"] = self.clock_ms()
        try:
            return self.store.update_item(item_id, updates)
        except sqlite3.IntegrityError as exc:
            raise InventoryError("外部条码已被其他物品使用", 409) from exc

    def record_movement(self, item_id: int, data: dict) -> dict:
        action = self._text(data.get("action"))
        if action not in self.MOVEMENTS:
            raise InventoryError("库存操作类型无效")
        payload = {
            "client_event_id": self._text(
                data.get("client_event_id"),
                required=True,
                field="操作编号",
            ),
            "action": action,
            "quantity": (
                self._number(data.get("quantity"), "数量")
                if action in {"received", "issued"}
                else None
            ),
            "actual_quantity": (
                self._number(data.get("actual_quantity"), "实际数量")
                if action == "adjusted"
                else None
            ),
            "actor": self._text(
                data.get("actor"), required=True, field="操作人"
            ),
            "actor_user_id": data.get("actor_user_id"),
            "effective_at_ms": self.clock_ms(),
            "note": self._text(data.get("note")) or None,
        }
        try:
            return self.store.record_movement(item_id, payload)
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc
        except ValueError as exc:
            status = 409 if "库存不足" in str(exc) else 400
            raise InventoryError(str(exc), status) from exc

    def get_summary(self, today_text=None) -> dict:
        try:
            return self.store.get_summary(today_text)
        except ValueError as exc:
            raise InventoryError("日期必须使用 YYYY-MM-DD 格式") from exc

    def list_movements(self, filters: dict) -> dict:
        action = self._text(filters.get("action"))
        if action and action not in self.AUDIT_ACTIONS:
            raise InventoryError("库存流水类型无效")
        limit = min(max(int(filters.get("limit") or 100), 1), 200)
        offset = max(int(filters.get("offset") or 0), 0)
        item_id = self._positive_int(filters.get("item_id"), "物品编号")
        experiment_id = self._positive_int(
            filters.get("experiment_id"), "实验编号"
        )
        from_ms = self._date_boundary(filters.get("from"), end=False)
        to_ms = self._date_boundary(filters.get("to"), end=True)
        if (
            from_ms is not None
            and to_ms is not None
            and from_ms > to_ms
        ):
            raise InventoryError("开始日期不能晚于结束日期")
        query = {
            "item_id": item_id,
            "action": action,
            "experiment_id": experiment_id,
            "from_ms": from_ms,
            "to_ms": to_ms,
        }
        rows = self.store.list_movements(
            **query, limit=limit, offset=offset
        )
        return {
            "movements": rows,
            "total": self.store.count_movements(**query),
        }

    @staticmethod
    def _positive_int(value, field: str):
        if value in (None, ""):
            return None
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise InventoryError(f"{field}必须是整数") from exc
        if result <= 0:
            raise InventoryError(f"{field}必须大于 0")
        return result

    @staticmethod
    def _date_boundary(value, *, end: bool):
        if value in (None, ""):
            return None
        try:
            parsed = date.fromisoformat(str(value))
        except ValueError as exc:
            raise InventoryError("流水日期必须使用 YYYY-MM-DD 格式") from exc
        boundary = datetime.combine(
            parsed,
            datetime_time.max if end else datetime_time.min,
        )
        return int(boundary.timestamp() * 1000)
