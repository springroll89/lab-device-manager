import sqlite3
from pathlib import Path

import pytest

from lab_device_manager.db.repository import Repository


def _experiment_payload(batch_id: str = "20260723-CEM-01") -> dict:
    return {
        "batch_id": batch_id,
        "membrane_system": "CEM",
        "recipe_no": "R-CEM-001",
        "recipe_version": "V1",
        "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
        "sop_version": "V0.2",
        "target_viscosity_min_mpas": 2.5,
        "target_viscosity_max_mpas": 10.0,
        "operator": "张三",
        "reviewer": "李四",
        "spec_snapshot": {"premix_duration_min": 10},
    }


def test_repository_enables_foreign_keys_and_applies_r201_migration():
    repo = Repository(":memory:")
    assert repo._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    versions = {
        row[0] for row in repo._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchall()
    }
    assert "001_r201_core" in versions
    assert "002_r201_snapshot_hash" in versions
    assert "003_r201_binding_idempotency" in versions
    assert "004_traceability_mvp" in versions
    assert "007_parallel_traceability" in versions
    assert "008_inventory_module" in versions
    assert "009_inventory_ledger_backfill" in versions


def test_viscometer_lease_is_exclusive_and_reusable_after_release():
    repo = Repository(":memory:")
    first = repo.experiments.create_experiment(
        _experiment_payload(), now_ms=1000
    )
    second = repo.experiments.create_experiment(
        _experiment_payload("20260723-CEM-02"), now_ms=1100
    )
    device_id = repo.upsert_device(
        "viscometer-1", "viscometer", "共享粘度计"
    )

    lease = repo.experiments.claim_measurement_device(
        first["id"], device_id, "张三", None, 1200
    )
    assert lease["purpose"] == "measurement"
    with pytest.raises(RuntimeError, match=first["batch_id"]):
        repo.experiments.claim_measurement_device(
            second["id"], device_id, "李四", None, 1300
        )

    assert repo.experiments.release_measurement_device(
        first["id"], device_id, 1400
    ) == 1
    reused = repo.experiments.claim_measurement_device(
        second["id"], device_id, "李四", None, 1500
    )
    assert reused["experiment_id"] == second["id"]

    expiring = repo.experiments.claim_measurement_device(
        second["id"], device_id, "李四", None, 2000, lease_ms=10
    )
    assert expiring["status"] == "active"
    assert repo.experiments.list_device_reservations(
        now_ms=2011
    ) == []


def test_existing_database_is_backed_up_once_before_pending_migrations(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE legacy_marker(value TEXT)")
    connection.execute("INSERT INTO legacy_marker(value) VALUES('preserved')")
    connection.commit()
    connection.close()

    repo = Repository(str(database))

    assert repo.last_migration_backup is not None
    backup = Path(repo.last_migration_backup)
    assert backup.exists()
    backup_connection = sqlite3.connect(backup)
    assert (
        backup_connection.execute(
            "SELECT value FROM legacy_marker"
        ).fetchone()[0]
        == "preserved"
    )
    backup_connection.close()
    repo.close()

    reopened = Repository(str(database))
    assert reopened.last_migration_backup is None
    reopened.close()


def test_experiment_store_creates_and_lists_experiment():
    repo = Repository(":memory:")
    created = repo.experiments.create_experiment(_experiment_payload(), now_ms=1000)
    assert created["batch_id"] == "20260723-CEM-01"
    assert created["current_step_code"] == "R201-01"
    assert created["validation_mode"] == "parallel_validation"
    assert repo.experiments.get_experiment(created["id"])["operator"] == "张三"
    assert [row["id"] for row in repo.experiments.list_experiments()] == [created["id"]]
    trace_items = repo.experiments.list_trace_items(created["id"])
    assert len(trace_items) == 1
    assert trace_items[0]["item_code"] == created["batch_id"]


def test_experiment_batch_id_is_unique():
    repo = Repository(":memory:")
    repo.experiments.create_experiment(_experiment_payload(), now_ms=1000)
    with pytest.raises(sqlite3.IntegrityError):
        repo.experiments.create_experiment(_experiment_payload(), now_ms=2000)


def test_experiment_event_is_idempotent_by_client_event_id():
    repo = Repository(":memory:")
    exp = repo.experiments.create_experiment(_experiment_payload(), now_ms=1000)
    data = {
        "client_event_id": "evt-001",
        "event_type": "experiment_created",
        "occurred_at_client_ms": 900,
        "received_at_server_ms": 1000,
        "effective_at_ms": 900,
        "client_clock_offset_ms": 0,
        "clock_sync_status": "trusted",
        "actor": "张三",
        "source_type": "manual",
        "payload": {"hello": "world"},
    }
    first = repo.experiments.add_experiment_event(exp["id"], data)
    second = repo.experiments.add_experiment_event(exp["id"], data)
    assert second["id"] == first["id"]
    assert len(repo.experiments.list_experiment_events(exp["id"])) == 1


def test_data_source_binding_supports_runless_channel_metric():
    repo = Repository(":memory:")
    exp = repo.experiments.create_experiment(_experiment_payload(), now_ms=1000)
    did = repo.upsert_device("whd-1", "whd46", "温湿度")
    binding = repo.experiments.add_data_source_binding(
        exp["id"],
        {
            "device_id": did,
            "run_id": None,
            "device_role": "reaction_temp",
            "metric_key": "ch2_temp_c",
            "channel_selector": "2",
            "linked_at_ms": 1000,
            "link_method": "manual",
        },
    )
    assert binding["run_id"] is None
    assert binding["metric_key"] == "ch2_temp_c"
    assert repo.experiments.list_data_source_bindings(exp["id"])[0]["device_role"] == "reaction_temp"


def test_foreign_key_rejects_binding_to_missing_device():
    repo = Repository(":memory:")
    exp = repo.experiments.create_experiment(_experiment_payload(), now_ms=1000)
    with pytest.raises(sqlite3.IntegrityError):
        repo.experiments.add_data_source_binding(
            exp["id"],
            {
                "device_id": 999,
                "device_role": "reaction_temp",
                "metric_key": "temp_c",
                "linked_at_ms": 1000,
                "link_method": "manual",
            },
        )


def test_trace_items_keep_container_identity_and_parentage():
    repo = Repository(":memory:")
    exp = repo.experiments.create_experiment(
        _experiment_payload(), now_ms=1000
    )
    batch = repo.experiments.ensure_batch_trace_item(
        exp["id"], now_ms=1000, actor="张三"
    )

    created = repo.experiments.create_trace_items(
        exp["id"],
        {
            "item_type": "intermediate",
            "display_name": "加酸后溶胶",
            "source_step_code": "R201-32",
            "container_count": 2,
            "quantity": 50,
            "unit": "mL",
            "creation_group_id": "trace-create-1",
            "actor": "张三",
            "effective_at_ms": 1100,
        },
    )

    assert [item["item_code"] for item in created] == [
        "20260723-CEM-01-IP32-01",
        "20260723-CEM-01-IP32-02",
    ]
    assert created[0]["parents"][0]["item_code"] == batch["item_code"]
    retry = repo.experiments.create_trace_items(
        exp["id"],
        {
            "item_type": "intermediate",
            "display_name": "加酸后溶胶",
            "source_step_code": "R201-32",
            "container_count": 2,
            "creation_group_id": "trace-create-1",
            "actor": "张三",
            "effective_at_ms": 1200,
        },
    )
    assert [item["id"] for item in retry] == [item["id"] for item in created]


def test_trace_item_store_retrieve_and_label_jobs_are_audited():
    repo = Repository(":memory:")
    exp = repo.experiments.create_experiment(
        _experiment_payload(), now_ms=1000
    )
    repo.experiments.ensure_batch_trace_item(
        exp["id"], now_ms=1000, actor="张三"
    )
    location = repo.experiments.create_storage_location(
        {
            "location_code": "FRIDGE-01-A2",
            "display_name": "冰箱01 · A2",
            "storage_condition": "4℃",
            "actor": "张三",
            "created_at_ms": 1050,
        }
    )
    item = repo.experiments.create_trace_items(
        exp["id"],
        {
            "item_type": "intermediate",
            "display_name": "待续实验溶胶",
            "source_step_code": "R201-50",
            "container_count": 1,
            "creation_group_id": "trace-create-2",
            "actor": "张三",
            "effective_at_ms": 1100,
        },
    )[0]

    stored = repo.experiments.transition_trace_item(
        item["id"],
        {
            "action": "store",
            "location_code": location["location_code"],
            "hold_until_ms": 86_400_000,
            "client_event_id": "trace-store-1",
            "actor": "张三",
            "effective_at_ms": 1200,
        },
    )
    assert stored["status"] == "stored"
    assert stored["storage_location_code"] == "FRIDGE-01-A2"

    retrieved = repo.experiments.transition_trace_item(
        item["id"],
        {
            "action": "retrieve",
            "client_event_id": "trace-retrieve-1",
            "actor": "张三",
            "effective_at_ms": 1300,
        },
    )
    assert retrieved["status"] == "active"
    assert retrieved["storage_location_code"] is None

    first = repo.experiments.request_trace_label_print(
        item["id"],
        {
            "client_event_id": "trace-print-1",
            "reason": "initial",
            "copies": 1,
            "actor": "张三",
            "requested_at_ms": 1400,
        },
    )
    retry = repo.experiments.request_trace_label_print(
        item["id"],
        {
            "client_event_id": "trace-print-1",
            "reason": "initial",
            "copies": 1,
            "actor": "张三",
            "requested_at_ms": 1500,
        },
    )
    assert retry["id"] == first["id"]
    assert len(repo.experiments.list_label_print_jobs(exp["id"])) == 1
    events = repo.experiments.list_trace_events(item["id"])
    assert [event["event_type"] for event in events][-3:] == [
        "stored",
        "retrieved",
        "label_print_requested",
    ]
