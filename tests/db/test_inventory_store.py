import pytest

from lab_device_manager.db.repository import Repository


def _item(**overrides):
    data = {
        "container_code": "PC-0001",
        "material_name": "无水乙醇",
        "category": "chemical",
        "quantity_remaining": 10,
        "unit": "L",
        "min_threshold": 3,
        "max_threshold": 20,
        "is_controlled": False,
        "status": "available",
        "created_at_ms": 1000,
        "created_by": "测试主管",
        "created_by_user_id": None,
        "client_event_id": "inventory-register-1",
    }
    data.update(overrides)
    return data


def test_inventory_movement_is_atomic_idempotent_and_never_negative():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(_item())

    issued = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "issue-1",
            "action": "issued",
            "quantity": 3,
            "unit": "L",
            "actor": "测试操作员",
            "actor_user_id": None,
            "effective_at_ms": 2000,
            "note": "日常领用",
        },
    )
    retry = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "issue-1",
            "action": "issued",
            "quantity": 3,
            "unit": "L",
            "actor": "测试操作员",
            "actor_user_id": None,
            "effective_at_ms": 2100,
        },
    )

    assert issued["movement"]["delta"] == -3
    assert issued["item"]["quantity_remaining"] == 7
    assert retry["movement"]["id"] == issued["movement"]["id"]
    assert retry["item"]["quantity_remaining"] == 7

    with pytest.raises(ValueError, match="库存不足"):
        repo.inventory.record_movement(
            item["id"],
            {
                "client_event_id": "issue-too-much",
                "action": "issued",
                "quantity": 8,
                "unit": "L",
                "actor": "测试操作员",
                "actor_user_id": None,
                "effective_at_ms": 2200,
            },
        )

    current = repo.inventory.get_item(item["id"])
    movements = repo.inventory.list_movements(item_id=item["id"])
    assert current["quantity_remaining"] == 7
    assert [row["client_event_id"] for row in movements] == [
        "issue-1",
        "inventory-register-1",
    ]


def test_inventory_adjustment_and_experiment_usage_share_one_ledger():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(
        _item(container_code="PC-0002", client_event_id="register-2")
    )
    experiment = repo.experiments.create_experiment(
        {
            "batch_id": "20260725-CEM-20",
            "membrane_system": "CEM",
            "recipe_no": "R-CEM-001",
            "recipe_version": "V1",
            "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
            "sop_version": "V0.2",
            "target_viscosity_min_mpas": 2.5,
            "target_viscosity_max_mpas": 10,
            "operator": "实验员01",
            "reviewer": "",
            "spec_snapshot": {},
        },
        now_ms=1100,
    )

    adjusted = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "adjust-1",
            "action": "adjusted",
            "actual_quantity": 12,
            "unit": "L",
            "actor": "测试主管",
            "actor_user_id": None,
            "effective_at_ms": 1500,
            "note": "月度盘点",
        },
    )
    used = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "experiment-use-1",
            "action": "experiment_used",
            "quantity": 2,
            "unit": "L",
            "experiment_id": experiment["id"],
            "actor": "实验员01",
            "actor_user_id": None,
            "effective_at_ms": 2000,
        },
    )

    assert adjusted["movement"]["delta"] == 2
    assert used["item"]["quantity_remaining"] == 10
    assert used["movement"]["batch_id"] == "20260725-CEM-20"
    assert repo.inventory.get_summary()["total_items"] == 1


def test_inventory_summary_keeps_alert_details_hazards_and_last_adjustment():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(
        _item(
            quantity_remaining=2,
            hazards=["易燃", "有害/刺激"],
            hazardous_status="listed",
            controlled_categories=["易制毒第三类"],
            is_controlled=True,
            expires_on="2024-08-20",
        )
    )
    repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "summary-adjust-1",
            "action": "adjusted",
            "actual_quantity": 2,
            "unit": "L",
            "actor": "测试主管",
            "actor_user_id": None,
            "effective_at_ms": 1_722_470_400_000,
        },
    )

    summary = repo.inventory.get_summary("2024-08-10")

    assert [row["id"] for row in summary["low_stock_items"]] == [item["id"]]
    assert [row["id"] for row in summary["expiring_items"]] == [item["id"]]
    assert summary["hazard_counts"] == {"易燃": 1, "有害/刺激": 1}
    assert summary["hazardous"] == 1
    assert summary["hazardous_status_counts"] == {"listed": 1}
    assert summary["control_category_counts"] == {"易制毒第三类": 1}
    assert summary["last_adjust_at_ms"] == 1_722_470_400_000
    assert summary["days_since_last_adjust"] == 9


def test_inventory_movements_filter_by_action_item_and_date_range():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(_item(created_at_ms=1000))
    repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "range-receive",
            "action": "received",
            "quantity": 1,
            "unit": "L",
            "actor": "测试主管",
            "effective_at_ms": 2_000,
        },
    )
    repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "range-issue",
            "action": "issued",
            "quantity": 1,
            "unit": "L",
            "actor": "测试操作员",
            "effective_at_ms": 3_000,
        },
    )

    rows = repo.inventory.list_movements(
        item_id=item["id"],
        action="received",
        from_ms=1_500,
        to_ms=2_500,
    )

    assert [row["client_event_id"] for row in rows] == ["range-receive"]
    assert repo.inventory.count_movements(
        item_id=item["id"],
        action="received",
        from_ms=1_500,
        to_ms=2_500,
    ) == 1


def test_expired_chemical_is_derived_filtered_and_cannot_be_issued():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(
        _item(
            container_code="PC-0099",
            client_event_id="register-expired",
            expires_on="2020-01-01",
        )
    )

    assert item["status"] == "expired"
    assert [
        row["id"] for row in repo.inventory.list_items(status="expired")
    ] == [item["id"]]
    assert repo.inventory.list_items(status="available") == []

    with pytest.raises(ValueError, match="不可领用"):
        repo.inventory.record_movement(
            item["id"],
            {
                "client_event_id": "issue-expired",
                "action": "issued",
                "quantity": 1,
                "unit": "L",
                "actor": "测试操作员",
                "effective_at_ms": 2_000,
            },
        )

    adjusted = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "adjust-expired",
            "action": "adjusted",
            "actual_quantity": 8,
            "unit": "L",
            "actor": "测试主管",
            "effective_at_ms": 3_000,
        },
    )
    assert adjusted["item"]["status"] == "expired"
    assert [
        row["id"] for row in repo.inventory.list_items(status="expired")
    ] == [item["id"]]


def test_quarantine_survives_adjustment_and_disposed_item_cannot_revive():
    repo = Repository(":memory:")
    item = repo.inventory.create_item(
        _item(
            container_code="PC-0101",
            client_event_id="register-restricted",
        )
    )
    quarantined = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "quarantine-restricted",
            "action": "quarantined",
            "actor": "测试主管",
            "effective_at_ms": 2_000,
        },
    )
    adjusted = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "adjust-quarantined",
            "action": "adjusted",
            "actual_quantity": 8,
            "unit": "L",
            "actor": "测试主管",
            "effective_at_ms": 3_000,
        },
    )
    disposed = repo.inventory.record_movement(
        item["id"],
        {
            "client_event_id": "dispose-restricted",
            "action": "disposed",
            "actor": "测试主管",
            "effective_at_ms": 4_000,
        },
    )

    assert quarantined["item"]["status"] == "quarantined"
    assert adjusted["item"]["status"] == "quarantined"
    assert disposed["item"]["status"] == "disposed"
    for operation in (
        {
            "client_event_id": "receive-disposed",
            "action": "received",
            "quantity": 1,
        },
        {
            "client_event_id": "adjust-disposed",
            "action": "adjusted",
            "actual_quantity": 1,
        },
    ):
        with pytest.raises(ValueError, match="已处置"):
            repo.inventory.record_movement(
                item["id"],
                {
                    **operation,
                    "unit": "L",
                    "actor": "测试主管",
                    "effective_at_ms": 5_000,
                },
            )
