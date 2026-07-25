from lab_device_manager.db.repository import Repository
from lab_device_manager.inventory.manifest_importer import (
    import_inventory_manifest,
)


def _manifest():
    return {
        "source": "warehouse-audio-test",
        "inventoried_at": "2026-07-25T10:08:06+08:00",
        "actor_username": "admin",
        "locations": [
            {
                "location_code": "CAB-TEST-S01",
                "display_name": "测试柜第1层",
                "storage_condition": "室温、密闭",
            }
        ],
        "items": [
            {
                "imported_id": "INV-001",
                "material_name": "盐酸",
                "quantity_remaining": None,
                "unit": "mL",
                "status": "available",
                "location": "CAB-TEST-S01",
                "supplier_lot": "LOT-1",
                "hazardous_status": "listed",
                "controlled_categories": ["易制毒第三类"],
                "hazards": ["腐蚀"],
            },
            {
                "imported_id": "INV-002",
                "material_name": "氯化钠",
                "quantity_remaining": 500,
                "unit": "g",
                "status": "available",
                "location": "CAB-TEST-S01",
                "supplier_lot": "LOT-2",
                "hazardous_status": "not_listed",
                "controlled_categories": [],
                "hazards": [],
            },
        ],
    }


def test_manifest_import_is_atomic_idempotent_and_preserves_unknown_balance():
    repo = Repository(":memory:")
    repo.accounts.ensure_initial_admin("test-hash")

    preview = import_inventory_manifest(repo, _manifest(), apply=False)
    assert repo.inventory.list_items() == []
    first = import_inventory_manifest(repo, _manifest(), apply=True)
    second = import_inventory_manifest(repo, _manifest(), apply=True)

    assert preview["planned_items"] == 2
    assert first["items_imported"] == 2
    assert first["locations_imported"] == 1
    assert second["items_imported"] == 0
    assert second["items_skipped"] == 2
    assert second["locations_skipped"] == 1
    hydrochloric = repo.inventory.get_item_by_import_identity(
        "warehouse-audio-test", "INV-001"
    )
    assert hydrochloric["quantity_remaining"] is None
    assert hydrochloric["created_by"] == "系统管理员"
    assert hydrochloric["controlled_categories"] == ["易制毒第三类"]
    assert hydrochloric["is_controlled"] is True
    assert len(repo.inventory.list_movements(item_id=hydrochloric["id"])) == 1
