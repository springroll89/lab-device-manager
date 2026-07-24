from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository


class InventoryImportError(ValueError):
    pass


_STATUS_MAP = {
    "in_stock": "available",
    "used_up": "empty",
    "to_dispose": "quarantined",
}
_ACTION_MAP = {
    "in": "received",
    "out": "issued",
    "adjust": "adjusted",
    "dispose": "disposed",
}


def _read_csv(path, required_fields: set[str]) -> list[dict]:
    source = Path(path)
    if not source.is_file():
        raise InventoryImportError(f"找不到导出文件：{source}")
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = sorted(required_fields - fields)
        if missing:
            raise InventoryImportError(
                f"{source.name} 缺少字段：{', '.join(missing)}"
            )
        return [
            {
                str(key).strip(): value.strip()
                if isinstance(value, str)
                else value
                for key, value in row.items()
            }
            for row in reader
        ]


def _text(value, field: str, *, required=False):
    result = str(value or "").strip()
    if required and not result:
        raise InventoryImportError(f"{field}不能为空")
    return result or None


def _number(value, field: str, *, optional=False):
    if value in (None, "") and optional:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise InventoryImportError(f"{field}必须是数字") from exc
    if not math.isfinite(result):
        raise InventoryImportError(f"{field}必须是有限数字")
    return result


def _boolean(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "是"}


def _hazards(value) -> list[str]:
    text = str(value or "").strip()
    if not text or text == "{}":
        return []
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InventoryImportError("危险性字段 JSON 格式无效") from exc
        if not isinstance(decoded, list):
            raise InventoryImportError("危险性字段必须是数组")
        return [str(item).strip() for item in decoded if str(item).strip()]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1]
        return [
            item.strip()
            for item in next(csv.reader([inner]))
            if item.strip()
        ]
    separator = "/" if "/" in text else ","
    return [item.strip() for item in text.split(separator) if item.strip()]


def _timestamp_ms(value, field: str, *, fallback=None) -> int:
    text = str(value or "").strip()
    if not text:
        if fallback is not None:
            return int(fallback)
        return int(time.time() * 1000)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryImportError(f"{field}时间格式无效：{text}") from exc
    return int(parsed.timestamp() * 1000)


def _normalize_items(rows: list[dict]) -> list[dict]:
    result = []
    ids = set()
    codes = set()
    for number, row in enumerate(rows, start=2):
        source_id = _text(row.get("id"), f"items.csv 第 {number} 行 id", required=True)
        code = _text(row.get("code"), f"items.csv 第 {number} 行 code", required=True)
        if source_id in ids:
            raise InventoryImportError(f"items.csv 重复 id：{source_id}")
        if code.upper() in codes:
            raise InventoryImportError(f"items.csv 重复编号：{code}")
        ids.add(source_id)
        codes.add(code.upper())
        category = _text(
            row.get("category"),
            f"items.csv 第 {number} 行 category",
            required=True,
        )
        if category not in {"chemical", "consumable", "office"}:
            raise InventoryImportError(
                f"items.csv 第 {number} 行类别无效：{category}"
            )
        status = _STATUS_MAP.get(str(row.get("status") or "").strip())
        if status is None:
            raise InventoryImportError(
                f"items.csv 第 {number} 行状态无效：{row.get('status')}"
            )
        quantity = _number(
            row.get("quantity"), f"items.csv 第 {number} 行 quantity"
        )
        if quantity < 0:
            raise InventoryImportError(
                f"items.csv 第 {number} 行库存不能为负数"
            )
        created_at_ms = _timestamp_ms(
            row.get("created_at"), f"items.csv 第 {number} 行 created_at"
        )
        updated_at_ms = _timestamp_ms(
            row.get("updated_at"),
            f"items.csv 第 {number} 行 updated_at",
            fallback=created_at_ms,
        )
        result.append(
            {
                "source_id": source_id,
                "container_code": code.upper(),
                "material_name": _text(
                    row.get("name"),
                    f"items.csv 第 {number} 行 name",
                    required=True,
                ),
                "category": category,
                "quantity_remaining": quantity,
                "unit": _text(
                    row.get("unit"),
                    f"items.csv 第 {number} 行 unit",
                    required=True,
                ),
                "location": _text(row.get("location"), "location"),
                "owner": _text(row.get("owner"), "owner"),
                "min_threshold": _number(
                    row.get("min_threshold"),
                    "min_threshold",
                    optional=True,
                ),
                "max_threshold": _number(
                    row.get("max_threshold"),
                    "max_threshold",
                    optional=True,
                ),
                "is_controlled": _boolean(row.get("is_controlled")),
                "status": status,
                "note": _text(row.get("note"), "note"),
                "cas_no": _text(row.get("cas_no"), "cas_no"),
                "spec": _text(row.get("spec"), "spec"),
                "hazards": _hazards(row.get("hazards")),
                "sds_url": _text(row.get("sds_url"), "sds_url"),
                "opened_on": _text(row.get("opened_date"), "opened_date"),
                "expires_on": _text(row.get("expiry_date"), "expiry_date"),
                "supplier_lot": _text(row.get("lot_no"), "lot_no"),
                "prepared_by": _text(row.get("prepared_by"), "prepared_by"),
                "prepared_date": _text(
                    row.get("prepared_date"), "prepared_date"
                ),
                "created_at_ms": created_at_ms,
                "updated_at_ms": updated_at_ms,
                "created_by": "旧库存系统",
                "created_by_user_id": None,
                "client_event_id": f"supabase-item:{source_id}",
                "initial_action": "imported",
                "initial_delta": 0,
                "initial_movement_at_ms": updated_at_ms,
                "imported_source": "supabase-inventory",
                "imported_id": source_id,
            }
        )
    return result


def _normalize_logs(rows: list[dict], item_ids: set[str]) -> list[dict]:
    result = []
    ids = set()
    for number, row in enumerate(rows, start=2):
        source_id = _text(row.get("id"), f"logs.csv 第 {number} 行 id", required=True)
        item_id = _text(
            row.get("item_id"),
            f"logs.csv 第 {number} 行 item_id",
            required=True,
        )
        if source_id in ids:
            raise InventoryImportError(f"logs.csv 重复 id：{source_id}")
        if item_id not in item_ids:
            raise InventoryImportError(
                f"logs.csv 引用了不存在的物品：{item_id}"
            )
        action = _ACTION_MAP.get(str(row.get("action") or "").strip())
        if action is None:
            raise InventoryImportError(
                f"logs.csv 第 {number} 行操作类型无效：{row.get('action')}"
            )
        ids.add(source_id)
        result.append(
            {
                "source_id": source_id,
                "item_source_id": item_id,
                "client_event_id": f"supabase-log:{source_id}",
                "action": action,
                "delta": _number(row.get("delta"), "delta"),
                "quantity_after": _number(
                    row.get("quantity_after"), "quantity_after"
                ),
                "actor": _text(row.get("operator"), "operator")
                or "旧库存系统",
                "effective_at_ms": _timestamp_ms(
                    row.get("created_at"), "created_at"
                ),
                "note": _text(row.get("note"), "note"),
                "payload": {
                    "source": "supabase-inventory",
                    "source_action": row.get("action"),
                },
            }
        )
    return result


def import_supabase_exports(
    repo,
    items_path,
    logs_path=None,
    *,
    apply=False,
) -> dict:
    raw_items = _read_csv(
        items_path,
        {"id", "code", "name", "category", "quantity", "unit", "status"},
    )
    raw_logs = (
        _read_csv(
            logs_path,
            {
                "id",
                "item_id",
                "action",
                "delta",
                "quantity_after",
                "created_at",
            },
        )
        if logs_path
        else []
    )
    items = _normalize_items(raw_items)
    logs = _normalize_logs(
        raw_logs, {item["source_id"] for item in items}
    )
    report = {
        "mode": "apply" if apply else "dry-run",
        "planned_items": len(items),
        "planned_logs": len(logs),
        "items_imported": 0,
        "items_skipped": 0,
        "logs_imported": 0,
        "logs_skipped": 0,
    }
    if not apply:
        return report
    try:
        with repo.inventory.transaction() as conn:
            imported_items = {}
            for item in items:
                existing = repo.inventory.get_item_by_import_identity(
                    "supabase-inventory",
                    item["source_id"],
                    connection=conn,
                )
                if existing is not None:
                    if existing["container_code"] != item["container_code"]:
                        raise InventoryImportError(
                            f"旧物品 {item['source_id']} 的编号与已导入记录不一致"
                        )
                    imported_items[item["source_id"]] = existing
                    report["items_skipped"] += 1
                    continue
                imported = repo.inventory.create_item(
                    item, connection=conn
                )
                imported_items[item["source_id"]] = imported
                report["items_imported"] += 1
            for log in logs:
                item = imported_items[log["item_source_id"]]
                history = {
                    **log,
                    "unit": item["unit"],
                }
                inserted = repo.inventory.import_historical_movement(
                    item["id"], history, connection=conn
                )
                report[
                    "logs_imported" if inserted else "logs_skipped"
                ] += 1
    except InventoryImportError:
        raise
    except (LookupError, sqlite3.Error, ValueError) as exc:
        raise InventoryImportError(f"库存导入失败：{exc}") from exc
    return report


def _backup_database(db_path: str) -> str | None:
    source_path = Path(db_path)
    if not source_path.is_file() or source_path.stat().st_size == 0:
        return None
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-inventory-import-{int(time.time())}.bak"
    )
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return str(backup_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把旧 Supabase 库存 CSV 导入统一实验系统"
    )
    parser.add_argument("--items", required=True, help="items 表 CSV")
    parser.add_argument("--logs", help="logs 表 CSV")
    parser.add_argument("--config", help="主系统配置文件")
    parser.add_argument("--db", help="覆盖配置中的 SQLite 路径")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认写入；省略时仅做预检",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    db_path = args.db or config.db_path
    backup_path = _backup_database(db_path) if args.apply else None
    repo = Repository(db_path)
    try:
        report = import_supabase_exports(
            repo,
            args.items,
            args.logs,
            apply=args.apply,
        )
    finally:
        repo.close()
    if backup_path:
        report["backup"] = backup_path
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
