from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from lab_device_manager.config import load_config
from lab_device_manager.db.repository import Repository
from lab_device_manager.inventory.service import InventoryService


class InventoryManifestError(ValueError):
    pass


def _required_text(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InventoryManifestError(f"{field}不能为空")
    return text


def _optional_number(value, field: str):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InventoryManifestError(f"{field}必须是数字或 null") from exc
    if not math.isfinite(number) or number < 0:
        raise InventoryManifestError(f"{field}不能为负数或非有限值")
    return number


def _text_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InventoryManifestError(f"{field}必须是非空文本列表")
    return list(dict.fromkeys(item.strip() for item in value))


def _load_manifest(path: str | Path) -> dict:
    source_path = Path(path)
    if not source_path.is_file():
        raise InventoryManifestError(f"找不到导入清单：{source_path}")
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InventoryManifestError("导入清单不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise InventoryManifestError("导入清单顶层必须是对象")
    return data


def _validate_manifest(repo, manifest: dict) -> tuple[dict, list[dict], list[dict]]:
    source = _required_text(manifest.get("source"), "source")
    actor_username = _required_text(
        manifest.get("actor_username"), "actor_username"
    )
    actor = repo.accounts.get_auth_user_by_username(actor_username)
    if actor is None or not actor["is_active"]:
        raise InventoryManifestError(
            f"盘点账号不存在或已停用：{actor_username}"
        )
    inventoried_at = _required_text(
        manifest.get("inventoried_at"), "inventoried_at"
    )
    try:
        inventoried_at_ms = int(
            datetime.fromisoformat(inventoried_at).timestamp() * 1000
        )
    except ValueError as exc:
        raise InventoryManifestError(
            "inventoried_at 必须是带日期和时间的 ISO 格式"
        ) from exc

    raw_locations = manifest.get("locations")
    raw_items = manifest.get("items")
    if not isinstance(raw_locations, list) or not isinstance(raw_items, list):
        raise InventoryManifestError("locations 和 items 必须是数组")

    locations = []
    location_codes = set()
    for index, raw in enumerate(raw_locations, start=1):
        if not isinstance(raw, dict):
            raise InventoryManifestError(f"第 {index} 个库位必须是对象")
        code = _required_text(
            raw.get("location_code"), f"第 {index} 个库位编号"
        ).upper()
        if code in location_codes:
            raise InventoryManifestError(f"库位编号重复：{code}")
        location_codes.add(code)
        locations.append(
            {
                "location_code": code,
                "display_name": _required_text(
                    raw.get("display_name"), f"库位 {code} 名称"
                ),
                "storage_condition": (
                    str(raw.get("storage_condition") or "").strip() or None
                ),
            }
        )

    items = []
    imported_ids = set()
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise InventoryManifestError(f"第 {index} 个物品必须是对象")
        imported_id = _required_text(
            raw.get("imported_id"), f"第 {index} 个物品来源编号"
        )
        if imported_id in imported_ids:
            raise InventoryManifestError(
                f"物品来源编号重复：{imported_id}"
            )
        imported_ids.add(imported_id)
        location = _required_text(
            raw.get("location"), f"物品 {imported_id} 库位"
        )
        if location != "待确认" and location not in location_codes:
            raise InventoryManifestError(
                f"物品 {imported_id} 引用了未定义库位：{location}"
            )
        status = _required_text(
            raw.get("status"), f"物品 {imported_id} 状态"
        )
        if status not in InventoryService.STATUSES:
            raise InventoryManifestError(
                f"物品 {imported_id} 状态无效：{status}"
            )
        hazardous_status = _required_text(
            raw.get("hazardous_status"),
            f"物品 {imported_id} 危化判定",
        )
        if hazardous_status not in InventoryService.HAZARDOUS_STATUSES:
            raise InventoryManifestError(
                f"物品 {imported_id} 危化判定无效"
            )
        controlled_categories = _text_list(
            raw.get("controlled_categories"),
            f"物品 {imported_id} 特殊管制",
        )
        if any(
            category not in InventoryService.CONTROL_CATEGORIES
            for category in controlled_categories
        ):
            raise InventoryManifestError(
                f"物品 {imported_id} 特殊管制类别无效"
            )
        quantity = _optional_number(
            raw.get("quantity_remaining"),
            f"物品 {imported_id} 当前余量",
        )
        item = dict(raw)
        item.update(
            {
                "imported_id": imported_id,
                "material_name": _required_text(
                    raw.get("material_name"),
                    f"物品 {imported_id} 名称",
                ),
                "quantity_remaining": quantity,
                "unit": _required_text(
                    raw.get("unit"), f"物品 {imported_id} 单位"
                ),
                "location": location,
                "status": status,
                "hazardous_status": hazardous_status,
                "controlled_categories": controlled_categories,
                "hazards": _text_list(
                    raw.get("hazards"), f"物品 {imported_id} 危险性"
                ),
                "source": source,
                "inventoried_at_ms": inventoried_at_ms,
            }
        )
        items.append(item)
    return actor, locations, items


def import_inventory_manifest(repo, manifest: dict, *, apply=False) -> dict:
    actor, locations, items = _validate_manifest(repo, manifest)
    source = str(manifest["source"]).strip()
    report = {
        "mode": "apply" if apply else "dry-run",
        "source": source,
        "actor": actor["display_name"],
        "planned_locations": len(locations),
        "planned_items": len(items),
        "locations_imported": 0,
        "locations_skipped": 0,
        "items_imported": 0,
        "items_skipped": 0,
    }
    if not apply:
        return report
    try:
        with repo.inventory.transaction() as conn:
            for location in locations:
                existing = conn.execute(
                    """SELECT * FROM storage_location
                       WHERE location_code=?""",
                    (location["location_code"],),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["display_name"] != location["display_name"]
                        or existing["storage_condition"]
                        != location["storage_condition"]
                    ):
                        raise InventoryManifestError(
                            f"库位 {location['location_code']} 与现有定义不一致"
                        )
                    report["locations_skipped"] += 1
                    continue
                conn.execute(
                    """INSERT INTO storage_location(
                         location_code, display_name, storage_condition,
                         active, created_at_ms, created_by)
                       VALUES(?,?,?,1,?,?)""",
                    (
                        location["location_code"],
                        location["display_name"],
                        location["storage_condition"],
                        items[0]["inventoried_at_ms"] if items else int(time.time() * 1000),
                        actor["display_name"],
                    ),
                )
                report["locations_imported"] += 1

            for item in items:
                existing = repo.inventory.get_item_by_import_identity(
                    source,
                    item["imported_id"],
                    connection=conn,
                )
                if existing is not None:
                    if (
                        existing["material_name"] != item["material_name"]
                        or existing.get("supplier_lot")
                        != item.get("supplier_lot")
                    ):
                        raise InventoryManifestError(
                            f"{item['imported_id']} 与现有导入记录不一致"
                        )
                    report["items_skipped"] += 1
                    continue
                payload = {
                    **item,
                    "category": "chemical",
                    "created_at_ms": item["inventoried_at_ms"],
                    "updated_at_ms": item["inventoried_at_ms"],
                    "created_by": actor["display_name"],
                    "created_by_user_id": actor["id"],
                    "is_controlled": bool(
                        item["controlled_categories"]
                        or item.get("is_controlled")
                    ),
                    "client_event_id": (
                        f"manifest:{source}:{item['imported_id']}"
                    ),
                    "initial_action": "imported",
                    "initial_delta": 0,
                    "initial_movement_at_ms": item["inventoried_at_ms"],
                    "imported_source": source,
                    "imported_id": item["imported_id"],
                }
                repo.inventory.create_item(payload, connection=conn)
                report["items_imported"] += 1
    except InventoryManifestError:
        raise
    except (LookupError, sqlite3.Error, ValueError) as exc:
        raise InventoryManifestError(f"库存清单导入失败：{exc}") from exc
    return report


def _backup_database(db_path: str) -> str | None:
    source_path = Path(db_path)
    if not source_path.is_file() or source_path.stat().st_size == 0:
        return None
    backup_path = source_path.with_name(
        f"{source_path.name}.pre-manifest-import-"
        f"{int(time.time() * 1000)}.bak"
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
        description="校验并导入 PURICORE 瓶级库存 JSON 清单"
    )
    parser.add_argument("--manifest", required=True, help="瓶级库存 JSON")
    parser.add_argument("--config", help="主系统配置文件")
    parser.add_argument("--db", help="覆盖配置中的 SQLite 路径")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="确认写入；省略时仅做预检",
    )
    args = parser.parse_args(argv)
    manifest = _load_manifest(args.manifest)
    config = load_config(args.config)
    db_path = args.db or config.db_path
    backup_path = _backup_database(db_path) if args.apply else None
    repo = Repository(db_path)
    try:
        report = import_inventory_manifest(
            repo, manifest, apply=args.apply
        )
        if repo.last_migration_backup:
            report["migration_backup"] = repo.last_migration_backup
    finally:
        repo.close()
    if backup_path:
        report["backup"] = backup_path
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
