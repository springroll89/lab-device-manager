from __future__ import annotations

import json
import math
import re
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
    HAZARDOUS_STATUSES = {
        "not_assessed",
        "listed",
        "not_listed",
        "pending_review",
    }
    CONTROL_CATEGORIES = {
        "易制爆",
        "易制毒第一类",
        "易制毒第二类",
        "易制毒第三类",
        "剧毒",
        "重大危险源",
        "药品类易制毒",
        "内部受控",
    }
    STORAGE_GROUPS = {
        "unassessed",
        "general_chemical",
        "flammable",
        "oxidizer",
        "acid",
        "alkali",
        "toxic",
        "water_reactive",
        "pyrophoric",
        "compressed_gas",
        "refrigerated",
    }
    LOCATION_TYPES = {
        "general",
        "chemical_cabinet",
        "flammable_cabinet",
        "acid_alkali_cabinet",
        "toxic_cabinet",
        "controlled_cabinet",
        "refrigerator",
        "waste_storage",
    }
    PHYSICAL_CONTROLS = {
        "通风",
        "防泄漏托盘",
        "防火",
        "防爆",
        "温度监控",
        "视频监控",
        "入侵报警",
        "双人双锁",
    }
    WASTE_PHYSICAL_STATES = {
        "liquid",
        "solid",
        "sludge",
        "gas",
        "mixed",
    }
    WASTE_HAZARD_CHARACTERISTICS = {
        "腐蚀性",
        "毒性",
        "易燃性",
        "反应性",
        "感染性",
    }
    GHS_PICTOGRAMS = {
        "GHS01",
        "GHS02",
        "GHS03",
        "GHS04",
        "GHS05",
        "GHS06",
        "GHS07",
        "GHS08",
        "GHS09",
    }
    DUAL_CONTROL_CATEGORIES = {"剧毒", "重大危险源", "药品类易制毒"}
    REGULATED_RECEIPT_CATEGORIES = {
        "易制爆",
        "易制毒第一类",
        "易制毒第二类",
        "易制毒第三类",
        "剧毒",
        "药品类易制毒",
    }
    DUAL_CONTROL_ACTIONS = {"received", "issued", "adjusted", "disposed"}
    INCOMPATIBLE_STORAGE_GROUPS = {
        "flammable": {"oxidizer"},
        "oxidizer": {"flammable", "water_reactive", "pyrophoric"},
        "acid": {"alkali", "water_reactive"},
        "alkali": {"acid"},
        "water_reactive": {"acid", "oxidizer", "general_chemical"},
        "pyrophoric": {"oxidizer", "general_chemical"},
        "compressed_gas": {
            "flammable",
            "oxidizer",
            "acid",
            "alkali",
            "toxic",
            "water_reactive",
            "pyrophoric",
            "general_chemical",
        },
    }
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

    @classmethod
    def _choice(cls, value, allowed: set[str], field: str) -> str:
        text = cls._text(value)
        if text not in allowed:
            raise InventoryError(f"{field}无效")
        return text

    @classmethod
    def _control_categories(cls, value) -> list[str]:
        categories = value or []
        if not isinstance(categories, list) or not all(
            isinstance(item, str) for item in categories
        ):
            raise InventoryError("特殊管制类别必须是文本列表")
        normalized = list(dict.fromkeys(item.strip() for item in categories))
        if any(item not in cls.CONTROL_CATEGORIES for item in normalized):
            raise InventoryError("特殊管制类别无效")
        return normalized

    @classmethod
    def _list_choices(
        cls, value, allowed: set[str], field: str
    ) -> list[str]:
        values = value or []
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise InventoryError(f"{field}必须是文本列表")
        normalized = list(
            dict.fromkeys(item.strip() for item in values if item.strip())
        )
        if any(item not in allowed for item in normalized):
            raise InventoryError(f"{field}包含无效值")
        return normalized

    def _resolve_storage_location(
        self,
        location_code: str,
        storage_group: str,
        *,
        item_id: int | None = None,
    ) -> dict | None:
        if not location_code:
            return None
        location = self.store.get_storage_location_by_code(
            location_code.upper()
        )
        if location is None:
            raise InventoryError(
                "存放位置必须选择已配置的合规库位，不能填写任意文字"
            )
        allowed = set(location["allowed_storage_groups"])
        if storage_group == "unassessed":
            raise InventoryError("必须先判定储存组，才能分配合规库位")
        if storage_group not in allowed:
            raise InventoryError(
                f"库位 {location['location_code']} 未允许存放该储存组"
            )
        incompatible = self.INCOMPATIBLE_STORAGE_GROUPS.get(
            storage_group, set()
        )
        existing = self.store.list_active_items_at_location(
            location["id"], exclude_item_id=item_id
        )
        conflict = next(
            (
                item
                for item in existing
                if item["storage_group"] in incompatible
                or storage_group
                in self.INCOMPATIBLE_STORAGE_GROUPS.get(
                    item["storage_group"], set()
                )
            ),
            None,
        )
        if conflict is not None:
            raise InventoryError(
                "禁忌混存：该库位已有"
                f"{conflict['material_name']}（{conflict['storage_group']}）"
            )
        return location

    @staticmethod
    def _hazardous_data_complete(item: dict) -> tuple[bool, str]:
        if item.get("hazardous_status") in {
            "not_assessed",
            "pending_review",
        }:
            return False, "危化品判定尚未完成"
        if item.get("hazardous_status") == "not_listed" and not item.get(
            "regulatory_reviewed_at_ms"
        ):
            return False, "CAS 未匹配目录不等于非危险品，仍需完成法规复核"
        if item.get("hazardous_status") != "listed":
            if set(item.get("controlled_categories") or []).intersection(
                InventoryService.REGULATED_RECEIPT_CATEGORIES
            ) and (
                not item.get("source_organization")
                or not item.get("handover_document_no")
            ):
                return False, "管制化学品缺少来源单位或交付凭证"
            return True, ""
        if not item.get("hazards"):
            return False, "危险性分类未填写"
        if not item.get("sds_url") or not item.get("sds_verified_at_ms"):
            return False, "供应商 SDS 尚未登记并核验"
        if item.get("storage_group") == "unassessed":
            return False, "储存组尚未判定"
        if not item.get("storage_location_id"):
            return False, "尚未分配合规库位"
        if set(item.get("controlled_categories") or []).intersection(
            InventoryService.REGULATED_RECEIPT_CATEGORIES
        ) and (
            not item.get("source_organization")
            or not item.get("handover_document_no")
        ):
            return False, "管制化学品缺少来源单位或交付凭证"
        return True, ""

    def _require_operable_chemical(self, item: dict, action: str) -> None:
        if item["category"] != "chemical" or action not in {
            "issued",
            "opened",
        }:
            return
        complete, reason = self._hazardous_data_complete(item)
        if not complete:
            raise InventoryError(f"合规资料不完整，禁止领用：{reason}", 409)
        if item["storage_location_id"]:
            self._resolve_storage_location(
                item.get("location") or "",
                item.get("storage_group") or "unassessed",
                item_id=item["id"],
            )

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
        hazardous_status = self._choice(
            data.get("hazardous_status") or "not_assessed",
            self.HAZARDOUS_STATUSES,
            "危化品判定",
        )
        controlled_categories = self._control_categories(
            data.get("controlled_categories")
        )
        storage_group = self._choice(
            data.get("storage_group") or "unassessed",
            self.STORAGE_GROUPS,
            "储存组",
        )
        ghs_pictograms = self._list_choices(
            data.get("ghs_pictograms"),
            self.GHS_PICTOGRAMS,
            "GHS 象形图",
        )
        if category != "chemical":
            hazardous_status = "not_assessed"
            controlled_categories = []
            storage_group = "unassessed"
            ghs_pictograms = []
        actor = self._text(
            data.get("created_by"), required=True, field="创建人"
        )
        now_ms = self.clock_ms()
        location_code = self._text(data.get("location")).upper()
        location = (
            self._resolve_storage_location(location_code, storage_group)
            if category == "chemical" and location_code
            else None
        )
        sds_url = (
            self._url(data.get("sds_url"), "SDS 链接")
            if category == "chemical"
            else None
        )
        sds_verified = bool(data.get("sds_verified"))
        regulatory_reviewed = bool(data.get("regulatory_review_confirmed"))
        received_date = self._date(
            data.get("received_date"), "接收日期"
        )
        dual_control_required = bool(
            data.get("dual_control_required")
            or self.DUAL_CONTROL_CATEGORIES.intersection(
                controlled_categories
            )
            or (location and location["requires_dual_control"])
        )
        initial_status = "available" if quantity > 0 else "empty"
        if category == "chemical" and hazardous_status in {
            "not_assessed",
            "pending_review",
        }:
            initial_status = "quarantined"
        payload = {
            "container_code": self._text(data.get("code")),
            "external_barcode": self._text(data.get("external_barcode")) or None,
            "material_name": self._text(
                data.get("name"), required=True, field="名称"
            ),
            "category": category,
            "quantity_remaining": quantity,
            "unit": unit,
            "location": location_code or None,
            "storage_location_id": location["id"] if location else None,
            "storage_group": storage_group,
            "owner": self._text(data.get("owner")) or None,
            "min_threshold": minimum,
            "max_threshold": maximum,
            "is_controlled": bool(
                data.get("is_controlled") or controlled_categories
            ),
            "status": initial_status,
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
            "ghs_pictograms": ghs_pictograms,
            "hazardous_status": hazardous_status,
            "controlled_categories": controlled_categories,
            "sds_url": sds_url,
            "sds_revision": self._text(data.get("sds_revision")) or None,
            "sds_verified_at_ms": now_ms if sds_url and sds_verified else None,
            "sds_verified_by": actor if sds_url and sds_verified else None,
            "catalog_source": self._text(data.get("catalog_source")) or None,
            "catalog_version": self._text(data.get("catalog_version")) or None,
            "catalog_entry_no": self._text(
                data.get("catalog_entry_no")
            )
            or None,
            "regulatory_reviewed_at_ms": (
                now_ms if regulatory_reviewed else None
            ),
            "regulatory_reviewed_by": (
                actor if regulatory_reviewed else None
            ),
            "dual_control_required": dual_control_required,
            "dual_control_reason": self._text(
                data.get("dual_control_reason")
            )
            or (
                "特殊管制类别或库位要求双人控制"
                if dual_control_required
                else None
            ),
            "source_organization": self._text(
                data.get("source_organization")
            )
            or None,
            "handover_document_no": self._text(
                data.get("handover_document_no")
            )
            or None,
            "handover_document_ref": self._text(
                data.get("handover_document_ref")
            )
            or None,
            "received_at_ms": (
                int(
                    datetime.combine(
                        date.fromisoformat(received_date),
                        datetime_time.min,
                    ).timestamp()
                    * 1000
                )
                if received_date
                else now_ms
            ),
            "received_by": self._text(data.get("received_by")) or actor,
            "accepted_by": self._text(data.get("accepted_by")) or None,
            "regulatory_filing_no": self._text(
                data.get("regulatory_filing_no")
            )
            or None,
            "regulatory_filing_ref": self._text(
                data.get("regulatory_filing_ref")
            )
            or None,
            "prepared_by": self._text(data.get("prepared_by")) or None,
            "prepared_date": self._date(
                data.get("prepared_date"), "配制日期"
            ),
            "created_at_ms": now_ms,
            "created_by": actor,
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
            "compliance_reviews": self.store.list_compliance_reviews(item_id),
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
            "owner": ("owner", "负责人"),
            "note": ("note", "备注"),
            "cas_no": ("cas_no", "CAS 号"),
            "spec": ("spec", "规格"),
            "prepared_by": ("prepared_by", "配制人"),
            "sds_revision": ("sds_revision", "SDS 修订日期/版本"),
            "catalog_source": ("catalog_source", "目录判定来源"),
            "catalog_version": ("catalog_version", "目录版本"),
            "catalog_entry_no": ("catalog_entry_no", "目录序号"),
            "dual_control_reason": (
                "dual_control_reason",
                "双人控制原因",
            ),
            "source_organization": ("source_organization", "来源单位"),
            "handover_document_no": (
                "handover_document_no",
                "交付凭证编号",
            ),
            "handover_document_ref": (
                "handover_document_ref",
                "交付凭证引用",
            ),
            "received_by": ("received_by", "接收人"),
            "accepted_by": ("accepted_by", "验收人"),
            "regulatory_filing_no": (
                "regulatory_filing_no",
                "许可/备案编号",
            ),
            "regulatory_filing_ref": (
                "regulatory_filing_ref",
                "许可/备案凭证引用",
            ),
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
        if "hazardous_status" in data:
            updates["hazardous_status"] = self._choice(
                data.get("hazardous_status"),
                self.HAZARDOUS_STATUSES,
                "危化品判定",
            )
        if "controlled_categories" in data:
            controlled_categories = self._control_categories(
                data.get("controlled_categories")
            )
            updates["controlled_categories_json"] = json.dumps(
                controlled_categories,
                ensure_ascii=False,
            )
            updates["is_controlled"] = (
                1
                if controlled_categories or data.get("is_controlled")
                else 0
            )
        for exposed, stored, label in (
            ("expiry_date", "expires_on", "有效期"),
            ("opened_date", "opened_on", "开封日期"),
            ("prepared_date", "prepared_date", "配制日期"),
        ):
            if exposed in data:
                updates[stored] = self._date(data.get(exposed), label)
        if "received_date" in data:
            received_date = self._date(
                data.get("received_date"), "接收日期"
            )
            updates["received_at_ms"] = (
                int(
                    datetime.combine(
                        date.fromisoformat(received_date),
                        datetime_time.min,
                    ).timestamp()
                    * 1000
                )
                if received_date
                else None
            )
        if "sds_url" in data:
            updates["sds_url"] = self._url(data.get("sds_url"), "SDS 链接")
        sds_evidence_changed = any(
            key in updates and updates[key] != current[key]
            for key in ("sds_url", "sds_revision")
        )
        actor = self._text(data.get("actor")) or current["created_by"]
        if data.get("sds_verified"):
            effective_sds = updates.get("sds_url", current["sds_url"])
            if not effective_sds:
                raise InventoryError("核验 SDS 前必须先登记 SDS 链接")
            updates["sds_verified_at_ms"] = self.clock_ms()
            updates["sds_verified_by"] = actor
        elif "sds_verified" in data or sds_evidence_changed:
            updates["sds_verified_at_ms"] = None
            updates["sds_verified_by"] = None
        regulatory_evidence_changed = any(
            key in updates and updates[key] != current[key]
            for key in (
                "cas_no",
                "hazardous_status",
                "catalog_source",
                "catalog_version",
                "catalog_entry_no",
            )
        )
        if data.get("regulatory_review_confirmed"):
            updates["regulatory_reviewed_at_ms"] = self.clock_ms()
            updates["regulatory_reviewed_by"] = actor
        elif (
            "regulatory_review_confirmed" in data
            or regulatory_evidence_changed
        ):
            updates["regulatory_reviewed_at_ms"] = None
            updates["regulatory_reviewed_by"] = None
        if "storage_group" in data:
            updates["storage_group"] = self._choice(
                data.get("storage_group"),
                self.STORAGE_GROUPS,
                "储存组",
            )
        if "ghs_pictograms" in data:
            updates["ghs_pictograms_json"] = json.dumps(
                self._list_choices(
                    data.get("ghs_pictograms"),
                    self.GHS_PICTOGRAMS,
                    "GHS 象形图",
                ),
                ensure_ascii=False,
            )
        if "dual_control_required" in data:
            updates["dual_control_required"] = (
                1 if data.get("dual_control_required") else 0
            )
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
                    "hazardous_status": "not_assessed",
                    "controlled_categories_json": "[]",
                    "is_controlled": 0,
                    "storage_location_id": None,
                    "storage_group": "unassessed",
                    "ghs_pictograms_json": "[]",
                    "sds_revision": None,
                    "sds_verified_at_ms": None,
                    "sds_verified_by": None,
                    "catalog_source": None,
                    "catalog_version": None,
                    "catalog_entry_no": None,
                    "regulatory_reviewed_at_ms": None,
                    "regulatory_reviewed_by": None,
                    "dual_control_required": 0,
                    "dual_control_reason": None,
                }
            )
        elif "location" in data or "storage_group" in data:
            location_code = self._text(
                data.get("location", current.get("location"))
            ).upper()
            storage_group = updates.get(
                "storage_group", current["storage_group"]
            )
            location = self._resolve_storage_location(
                location_code, storage_group, item_id=item_id
            )
            updates["location"] = location_code or None
            updates["storage_location_id"] = (
                location["id"] if location else None
            )
            if location and location["requires_dual_control"]:
                updates["dual_control_required"] = 1
                updates["dual_control_reason"] = (
                    updates.get("dual_control_reason")
                    or "库位要求双人控制"
                )
        effective_controls = (
            self._control_categories(data.get("controlled_categories"))
            if "controlled_categories" in data
            else current["controlled_categories"]
        )
        if self.DUAL_CONTROL_CATEGORIES.intersection(effective_controls):
            updates["dual_control_required"] = 1
            updates["dual_control_reason"] = (
                updates.get("dual_control_reason")
                or "特殊管制类别要求双人控制"
            )
        updates["updated_at_ms"] = self.clock_ms()
        try:
            if not data.get("compliance_review"):
                return self.store.update_item(item_id, updates)
            with self.store.transaction() as conn:
                updated = self.store.update_item(
                    item_id, updates, connection=conn
                )
                complete, reason = self._hazardous_data_complete(updated)
                result = "approved" if complete else "needs_correction"
                self.store.record_compliance_review(
                    item_id,
                    {
                        "reviewed_at_ms": self.clock_ms(),
                        "reviewed_by": actor,
                        "reviewed_by_user_id": data.get("actor_user_id"),
                        "result": result,
                        "checklist": {
                            "hazardous_status": updated[
                                "hazardous_status"
                            ],
                            "sds_verified": bool(
                                updated["sds_verified_at_ms"]
                            ),
                            "storage_group": updated["storage_group"],
                            "storage_location_id": updated[
                                "storage_location_id"
                            ],
                        },
                        "note": None if complete else reason,
                    },
                    release=complete,
                    connection=conn,
                )
                return self.store.get_item(item_id, connection=conn)
        except sqlite3.IntegrityError as exc:
            raise InventoryError("外部条码已被其他物品使用", 409) from exc

    def record_movement(self, item_id: int, data: dict) -> dict:
        action = self._text(data.get("action"))
        if action not in self.MOVEMENTS:
            raise InventoryError("库存操作类型无效")
        payload = self._movement_payload(item_id, action, data)
        try:
            with self.store.transaction() as conn:
                item = self.store.get_item(item_id, connection=conn)
                if item is None:
                    raise InventoryError("库存物品不存在", 404)
                self._require_operable_chemical(item, action)
                if (
                    item["dual_control_required"]
                    and action in self.DUAL_CONTROL_ACTIONS
                ):
                    raise InventoryError(
                        "该物品需要双人确认，请先提交待审批操作",
                        409,
                    )
                return self.store.record_movement(
                    item_id, payload, connection=conn
                )
        except InventoryError:
            raise
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc
        except ValueError as exc:
            status = 409 if "库存不足" in str(exc) else 400
            raise InventoryError(str(exc), status) from exc

    def _movement_payload(
        self, item_id: int, action: str, data: dict
    ) -> dict:
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
            # Approval identity is only added by the store's atomic approval
            # decision path. Never trust client-supplied approval fields.
            "approved_by": None,
            "approved_by_user_id": None,
            "approved_at_ms": None,
        }
        return payload

    def request_movement_approval(
        self, item_id: int, data: dict
    ) -> dict:
        item = self.store.get_item(item_id)
        if item is None:
            raise InventoryError("库存物品不存在", 404)
        action = self._text(data.get("action"))
        if action not in self.MOVEMENTS:
            raise InventoryError("库存操作类型无效")
        self._require_operable_chemical(item, action)
        if not (
            item["dual_control_required"]
            and action in self.DUAL_CONTROL_ACTIONS
        ):
            raise InventoryError("该操作不需要双人审批")
        requester_id = data.get("actor_user_id")
        if not isinstance(requester_id, int) or requester_id <= 0:
            raise InventoryError("双人审批必须使用真实登录账号")
        payload = self._movement_payload(item_id, action, data)
        return self.store.request_operation_approval(
            {
                "client_event_id": payload["client_event_id"],
                "item_id": item_id,
                "action": action,
                "request_payload": payload,
                "requested_at_ms": payload["effective_at_ms"],
                "requested_by": payload["actor"],
                "requested_by_user_id": requester_id,
            }
        )

    def list_movement_approvals(self, status: str = "pending") -> list[dict]:
        if status not in {"pending", "approved", "rejected", "cancelled"}:
            raise InventoryError("审批状态无效")
        return self.store.list_operation_approvals(status=status)

    def decide_movement_approval(
        self, approval_id: int, data: dict
    ) -> dict:
        approver_id = data.get("actor_user_id")
        if not isinstance(approver_id, int) or approver_id <= 0:
            raise InventoryError("双人审批必须使用真实登录账号")
        decision = self._text(
            data.get("decision"), required=True, field="审批结论"
        )
        if decision not in {"approve", "reject"}:
            raise InventoryError("审批结论无效")
        try:
            return self.store.decide_operation_approval(
                approval_id,
                approved=decision == "approve",
                decided_at_ms=self.clock_ms(),
                decided_by=self._text(
                    data.get("actor"), required=True, field="复核人"
                ),
                decided_by_user_id=approver_id,
                decision_note=self._text(data.get("note")) or None,
            )
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc
        except ValueError as exc:
            raise InventoryError(str(exc), 409) from exc

    def list_storage_locations(self) -> list[dict]:
        return self.store.list_storage_locations()

    def save_storage_location(
        self, data: dict, *, location_id: int | None = None
    ) -> dict:
        code = self._text(
            data.get("location_code"),
            required=location_id is None,
            field="库位编码",
        ).upper()
        if code and not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", code):
            raise InventoryError("库位编码只能使用字母、数字、- 和 _")
        location_type = self._choice(
            data.get("location_type") or "general",
            self.LOCATION_TYPES,
            "库位类型",
        )
        allowed = self._list_choices(
            data.get("allowed_storage_groups"),
            self.STORAGE_GROUPS - {"unassessed"},
            "允许储存组",
        )
        if location_type != "general" and not allowed:
            raise InventoryError("化学品库位必须明确至少一个允许储存组")
        for storage_group in allowed:
            conflicts = self.INCOMPATIBLE_STORAGE_GROUPS.get(
                storage_group, set()
            ).intersection(allowed)
            if conflicts:
                raise InventoryError(
                    "同一库位不能同时允许禁忌储存组："
                    f"{storage_group} / {sorted(conflicts)[0]}"
                )
        physical_controls = self._list_choices(
            data.get("physical_controls"),
            self.PHYSICAL_CONTROLS,
            "物理防护",
        )
        requires_dual = bool(data.get("requires_dual_control"))
        if requires_dual and "双人双锁" not in physical_controls:
            raise InventoryError(
                "双人控制库位必须勾选已落实“双人双锁”物理措施"
            )
        if location_id is not None:
            current_location = next(
                (
                    location
                    for location in self.store.list_storage_locations()
                    if location["id"] == location_id
                ),
                None,
            )
            if current_location is None:
                raise InventoryError("库存位置不存在", 404)
            current_is_waste = (
                current_location["location_type"] == "waste_storage"
            )
            new_is_waste = location_type == "waste_storage"
            if current_is_waste != new_is_waste:
                raise InventoryError(
                    "普通库位与危废暂存库位不能互相改类型，请新建库位",
                    409,
                )
            for item in self.store.list_active_items_at_location(
                location_id
            ):
                if item["storage_group"] not in allowed:
                    raise InventoryError(
                        "库位内仍有"
                        f"{item['material_name']}（{item['storage_group']}），"
                        "不能保存不允许该储存组的新配置",
                        409,
                    )
        payload = {
            "location_code": code,
            "display_name": self._text(
                data.get("display_name"),
                required=True,
                field="库位名称",
            ),
            "storage_condition": self._text(
                data.get("storage_condition")
            )
            or None,
            "location_type": location_type,
            "allowed_storage_groups": allowed,
            "requires_dual_control": requires_dual,
            "physical_controls": physical_controls,
            "compliance_note": self._text(
                data.get("compliance_note")
            )
            or None,
            "created_at_ms": self.clock_ms(),
            "created_by": self._text(
                data.get("actor"), required=True, field="创建人"
            ),
        }
        try:
            if location_id is None:
                return self.store.create_storage_location(payload)
            return self.store.update_storage_location(location_id, payload)
        except sqlite3.IntegrityError as exc:
            raise InventoryError("库位编码已存在", 409) from exc
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc

    def create_hazardous_waste(self, data: dict) -> dict:
        location_code = self._text(
            data.get("location_code"),
            required=True,
            field="危废暂存库位",
        ).upper()
        location = self.store.get_storage_location_by_code(location_code)
        if location is None or location["location_type"] != "waste_storage":
            raise InventoryError("危废必须进入已配置的危废暂存库位")
        quantity = self._number(data.get("quantity"), "初始数量")
        state = self._choice(
            data.get("physical_state"),
            self.WASTE_PHYSICAL_STATES,
            "物理形态",
        )
        characteristics = self._list_choices(
            data.get("hazard_characteristics"),
            self.WASTE_HAZARD_CHARACTERISTICS,
            "危险特性",
        )
        if not characteristics:
            raise InventoryError("至少选择一项危废危险特性")
        now_ms = self.clock_ms()
        payload = {
            "waste_code": self._text(data.get("waste_code")) or None,
            "waste_name": self._text(
                data.get("waste_name"), required=True, field="危废名称"
            ),
            "waste_category_code": self._text(
                data.get("waste_category_code"),
                required=True,
                field="国家危险废物类别/代码",
            ),
            "waste_category_name": self._text(
                data.get("waste_category_name")
            )
            or None,
            "physical_state": state,
            "hazard_characteristics": characteristics,
            "composition": self._text(
                data.get("composition"),
                required=True,
                field="主要成分",
            ),
            "quantity": quantity,
            "unit": self._text(
                data.get("unit"), required=True, field="单位"
            ),
            "package_type": self._text(
                data.get("package_type"), required=True, field="包装容器"
            ),
            "storage_location_id": location["id"],
            "source_experiment_id": self._positive_int(
                data.get("source_experiment_id"), "来源实验"
            ),
            "source_item_id": self._positive_int(
                data.get("source_item_id"), "来源物品"
            ),
            "started_at_ms": now_ms,
            "created_at_ms": now_ms,
            "created_by": self._text(
                data.get("actor"), required=True, field="登记人"
            ),
            "created_by_user_id": data.get("actor_user_id"),
            "note": self._text(data.get("note")) or None,
            "client_event_id": self._text(
                data.get("client_event_id"),
                required=True,
                field="操作编号",
            ),
        }
        try:
            return self.store.create_hazardous_waste(payload)
        except sqlite3.IntegrityError as exc:
            raise InventoryError("危废编号或操作编号已存在", 409) from exc

    def list_hazardous_waste(self, status: str = "") -> list[dict]:
        if status and status not in {
            "accumulating",
            "ready_for_transfer",
            "transferred",
        }:
            raise InventoryError("危废状态无效")
        return self.store.list_hazardous_waste(status)

    def get_hazardous_waste(self, waste_id: int) -> dict:
        result = self.store.get_hazardous_waste(waste_id)
        if result is None:
            raise InventoryError("危废容器不存在", 404)
        return result

    def update_hazardous_waste_state(
        self, waste_id: int, action: str, data: dict
    ) -> dict:
        now_ms = self.clock_ms()
        payload = {
            "client_event_id": self._text(
                data.get("client_event_id"),
                required=True,
                field="操作编号",
            ),
            "effective_at_ms": now_ms,
            "actor": self._text(
                data.get("actor"), required=True, field="操作人"
            ),
            "actor_user_id": data.get("actor_user_id"),
            "payload": {"note": self._text(data.get("note")) or None},
        }
        try:
            if action == "add":
                payload["quantity"] = self._number(
                    data.get("quantity"), "增加数量"
                )
                if payload["quantity"] <= 0:
                    raise InventoryError("增加数量必须大于 0")
                return self.store.add_hazardous_waste_quantity(
                    waste_id, payload
                )
            if action == "seal":
                return self.store.seal_hazardous_waste(waste_id, payload)
            if action == "complete_transfer":
                return self.store.complete_hazardous_waste_transfer(
                    waste_id, payload
                )
            raise InventoryError("危废操作类型无效")
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc
        except ValueError as exc:
            raise InventoryError(str(exc), 409) from exc

    def register_hazardous_waste_transfer(
        self, waste_id: int, data: dict
    ) -> dict:
        transfer_date = self._date(
            data.get("transfer_date"), "转移日期"
        )
        if not transfer_date:
            raise InventoryError("转移日期不能为空")
        now_ms = self.clock_ms()
        payload = {
            "client_event_id": self._text(
                data.get("client_event_id"),
                required=True,
                field="操作编号",
            ),
            "national_manifest_no": self._text(
                data.get("national_manifest_no"),
                required=True,
                field="国家系统电子联单编号",
            ),
            "national_system_ref": self._text(
                data.get("national_system_ref")
            )
            or None,
            "transfer_at_ms": int(
                datetime.combine(
                    date.fromisoformat(transfer_date), datetime_time.min
                ).timestamp()
                * 1000
            ),
            "transporter_name": self._text(
                data.get("transporter_name"),
                required=True,
                field="承运单位",
            ),
            "transporter_license_no": self._text(
                data.get("transporter_license_no"),
                required=True,
                field="承运资质编号",
            ),
            "vehicle_no": self._text(data.get("vehicle_no")) or None,
            "recipient_name": self._text(
                data.get("recipient_name"),
                required=True,
                field="接收/处置单位",
            ),
            "recipient_permit_no": self._text(
                data.get("recipient_permit_no"),
                required=True,
                field="危废经营许可证编号",
            ),
            "disposal_method": self._text(
                data.get("disposal_method"),
                required=True,
                field="利用/处置方式",
            ),
            "registered_at_ms": now_ms,
            "registered_by": self._text(
                data.get("actor"), required=True, field="登记人"
            ),
            "registered_by_user_id": data.get("actor_user_id"),
            "note": self._text(data.get("note")) or None,
        }
        try:
            return self.store.register_hazardous_waste_transfer(
                waste_id, payload
            )
        except sqlite3.IntegrityError as exc:
            raise InventoryError("国家电子联单编号已登记", 409) from exc
        except LookupError as exc:
            raise InventoryError(str(exc), 404) from exc
        except ValueError as exc:
            raise InventoryError(str(exc), 409) from exc

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
