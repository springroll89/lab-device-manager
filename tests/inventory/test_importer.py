import csv

import pytest

from lab_device_manager.db.repository import Repository
from lab_device_manager.inventory.importer import (
    InventoryImportError,
    import_supabase_exports,
)
from lab_device_manager.inventory import importer


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _exports(tmp_path):
    items_path = tmp_path / "items.csv"
    logs_path = tmp_path / "logs.csv"
    _write_csv(
        items_path,
        [
            {
                "id": "item-chemical",
                "code": "PC-0042",
                "name": "无水乙醇",
                "category": "chemical",
                "quantity": "2.5",
                "unit": "L",
                "location": "危化品柜 A1",
                "owner": "王工",
                "min_threshold": "3",
                "max_threshold": "10",
                "is_controlled": "true",
                "status": "in_stock",
                "note": "",
                "cas_no": "64-17-5",
                "spec": "AR",
                "hazards": '{"易燃","有害/刺激"}',
                "sds_url": "https://example.test/sds",
                "opened_date": "2026-07-01",
                "expiry_date": "2027-07-01",
                "lot_no": "LOT-42",
                "prepared_by": "",
                "prepared_date": "",
                "created_at": "2026-07-01T08:00:00+08:00",
                "updated_at": "2026-07-25T08:00:00+08:00",
            },
            {
                "id": "item-office",
                "code": "PC-0043",
                "name": "标签纸",
                "category": "office",
                "quantity": "0",
                "unit": "卷",
                "location": "办公柜",
                "owner": "",
                "min_threshold": "",
                "max_threshold": "",
                "is_controlled": "false",
                "status": "used_up",
                "note": "",
                "cas_no": "",
                "spec": "",
                "hazards": "{}",
                "sds_url": "",
                "opened_date": "",
                "expiry_date": "",
                "lot_no": "",
                "prepared_by": "",
                "prepared_date": "",
                "created_at": "2026-07-02T08:00:00+08:00",
                "updated_at": "2026-07-24T08:00:00+08:00",
            },
        ],
    )
    _write_csv(
        logs_path,
        [
            {
                "id": "log-in",
                "item_id": "item-chemical",
                "action": "in",
                "delta": "5",
                "quantity_after": "5",
                "operator": "王工",
                "note": "首次入库",
                "created_at": "2026-07-01T08:00:00+08:00",
            },
            {
                "id": "log-out",
                "item_id": "item-chemical",
                "action": "out",
                "delta": "-2.5",
                "quantity_after": "2.5",
                "operator": "李工",
                "note": "领用",
                "created_at": "2026-07-20T08:00:00+08:00",
            },
        ],
    )
    return items_path, logs_path


def test_supabase_inventory_import_is_dry_run_by_default_and_idempotent(
    tmp_path,
):
    repo = Repository(":memory:")
    items_path, logs_path = _exports(tmp_path)

    preview = import_supabase_exports(repo, items_path, logs_path)

    assert preview["planned_items"] == 2
    assert preview["planned_logs"] == 2
    assert repo.inventory.list_items() == []

    applied = import_supabase_exports(
        repo, items_path, logs_path, apply=True
    )
    repeated = import_supabase_exports(
        repo, items_path, logs_path, apply=True
    )

    assert applied["items_imported"] == 2
    assert applied["logs_imported"] == 2
    assert repeated["items_imported"] == 0
    assert repeated["items_skipped"] == 2
    assert repeated["logs_imported"] == 0
    assert repeated["logs_skipped"] == 2
    chemical = repo.inventory.get_item_by_code("PC-0042")
    office = repo.inventory.get_item_by_code("PC-0043")
    assert chemical["quantity_remaining"] == 2.5
    assert chemical["status"] == "available"
    assert chemical["hazards"] == ["易燃", "有害/刺激"]
    assert office["status"] == "empty"
    movements = repo.inventory.list_movements(item_id=chemical["id"])
    assert {row["action"] for row in movements} == {
        "imported",
        "received",
        "issued",
    }
    assert next(
        row for row in movements if row["action"] == "issued"
    )["quantity_after"] == 2.5


def test_supabase_import_rolls_back_when_a_log_references_unknown_item(
    tmp_path,
):
    repo = Repository(":memory:")
    items_path, logs_path = _exports(tmp_path)
    rows = list(csv.DictReader(logs_path.open(encoding="utf-8")))
    rows[0]["item_id"] = "missing-item"
    _write_csv(logs_path, rows)

    with pytest.raises(InventoryImportError, match="missing-item"):
        import_supabase_exports(
            repo, items_path, logs_path, apply=True
        )

    assert repo.inventory.list_items() == []


def test_supabase_import_rejects_missing_files_fields_and_invalid_values(
    tmp_path,
):
    repo = Repository(":memory:")
    missing = tmp_path / "missing.csv"
    with pytest.raises(InventoryImportError, match="找不到"):
        import_supabase_exports(repo, missing)

    incomplete = tmp_path / "incomplete.csv"
    _write_csv(incomplete, [{"id": "one", "code": "PC-1"}])
    with pytest.raises(InventoryImportError, match="缺少字段"):
        import_supabase_exports(repo, incomplete)

    items_path, logs_path = _exports(tmp_path)
    rows = list(csv.DictReader(items_path.open(encoding="utf-8")))
    rows[0]["hazards"] = "[bad-json"
    _write_csv(items_path, rows)
    with pytest.raises(InventoryImportError, match="JSON"):
        import_supabase_exports(repo, items_path, logs_path)

    rows[0]["hazards"] = "[]"
    rows[0]["quantity"] = "-1"
    _write_csv(items_path, rows)
    with pytest.raises(InventoryImportError, match="不能为负数"):
        import_supabase_exports(repo, items_path, logs_path)


def test_inventory_import_cli_creates_backup_before_apply(
    tmp_path,
    capsys,
):
    items_path, logs_path = _exports(tmp_path)
    db_path = tmp_path / "inventory.db"
    Repository(str(db_path)).close()

    assert importer.main(
        [
            "--items",
            str(items_path),
            "--logs",
            str(logs_path),
            "--db",
            str(db_path),
        ]
    ) == 0
    preview = capsys.readouterr().out
    assert '"mode": "dry-run"' in preview

    assert importer.main(
        [
            "--items",
            str(items_path),
            "--logs",
            str(logs_path),
            "--db",
            str(db_path),
            "--apply",
        ]
    ) == 0
    applied = capsys.readouterr().out
    backups = list(tmp_path.glob("inventory.db.pre-inventory-import-*.bak"))
    assert '"mode": "apply"' in applied
    assert len(backups) == 1
    repo = Repository(str(db_path))
    try:
        assert repo.inventory.get_item_by_code("PC-0042") is not None
    finally:
        repo.close()
