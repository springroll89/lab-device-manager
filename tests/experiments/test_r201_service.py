import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from lab_device_manager.db.repository import Repository
from lab_device_manager.experiments.service import R201Error, R201Service


class Clock:
    def __init__(self, ms: int = 1_000_000):
        self.ms = ms

    def __call__(self) -> int:
        self.ms += 100
        return self.ms


CREATE = {
    "batch_id": "20260723-CEM-01",
    "membrane_system": "CEM",
    "recipe_no": "R-CEM-001",
    "recipe_version": "V1",
    "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
    "sop_version": "V0.2",
    "target_viscosity_min_mpas": 2.5,
    "target_viscosity_max_mpas": 10.0,
    "operator": "张三",
    "reviewer": "李四",
    "spec_snapshot": {
        "premix_rpm_min": 300,
        "premix_rpm_max": 500,
        "acid_rate_min_ml_min": 0.3,
        "acid_rate_max_ml_min": 1.0,
        "reaction_temp_min_c": 38,
        "reaction_temp_max_c": 42,
    },
}


VALID_RESULTS = {
    "R201-01": {"environment_temp_c": 25, "environment_humidity_rh": 50, "device_checks": ["HMS-C", "TYD02"]},
    "R201-02": {"glassware_items": [{"name": "两口烧瓶", "dry": True}]},
    "R201-03": {"materials": [{"name": "TEOS", "lot": "T-01", "actual": 10, "unit": "mL"}]},
    "R201-04": {"hcl_c1_mol_l": 12, "hcl_c2_mol_l": 0.1, "hcl_v1_ml": 0.833, "hcl_v2_ml": 100, "acid_into_water": True},
    "R201-10": {"appearance": "澄清", "actual_rpm": 400},
    "R201-20": {"ethanol_actual_ml": 20, "appearance": "澄清", "actual_rpm": 400},
    "R201-30": {"ice_bath_confirmed": True, "reaction_temp_c": 3, "syringe_spec": "10 mL", "target_volume_ml": 5, "target_rate_ml_min": 0.5, "line_purged": True},
    "R201-31": {"acc_volume_start": 100, "acc_volume_end": 105, "acc_volume_unit": "mL", "target_volume_ml": 5},
    "R201-32": {"balance_minutes": 7},
    "R201-40": {"condenser_confirmed": True, "moisture_protection_confirmed": True, "reached_temp_c": 40},
    "R201-60": {"room_temp_confirmed": True, "cooling_minutes": 20},
    "R201-70": {"filter_spec": "G4+0.45µm PTFE", "container_tare_g": 100, "container_gross_g": 150, "transfer_viscosity_mpas": 5.2, "appearance": "澄清", "label_confirmed": True},
}


def _service():
    repo = Repository(":memory:")
    return R201Service(repo, clock_ms=Clock()), repo


def _event(prefix: str, n: int) -> dict:
    return {
        "client_event_id": f"{prefix}-{n}",
        "occurred_at_client_ms": 1_000_000 + n,
        "client_clock_offset_ms": 0,
        "clock_sync_status": "trusted",
        "actor": "张三",
    }


def _capture(
    *,
    pump_volume=None,
    pump_rate=0.5,
    pump_target=5,
    stirrer_temp=40,
    stirrer_rpm=400,
    viscosity=None,
    torque=50,
):
    devices = []
    if pump_volume is not None:
        devices.append(
            {
                "device_id": 1,
                "name": "pump-1",
                "alias": "TYD02",
                "type": "tyd02",
                "age_ms": 0,
                "snapshot": {
                    "state": "running",
                    "work_mode": "仅注入",
                    "ts_ms": 1_000_000,
                    "acc_volume": pump_volume,
                    "acc_unit": "mL",
                    "metrics": {
                        "inject_rate": pump_rate,
                        "inject_rate_unit": "mL/min",
                        "target_volume": pump_target,
                        "target_unit": "mL",
                        "syringe_code": "10 mL",
                    },
                },
            }
        )
    devices.append(
        {
            "device_id": 2,
            "name": "stirrer-1",
            "alias": "HMS-C",
            "type": "stirrer",
            "age_ms": 0,
            "snapshot": {
                "state": "running",
                "ts_ms": 1_000_000,
                "temp_c": stirrer_temp,
                "metrics": {"speed": stirrer_rpm},
            },
        }
    )
    if viscosity is not None:
        devices.append(
            {
                "device_id": 3,
                "name": "viscometer-1",
                "alias": "粘度计",
                "type": "viscometer",
                "age_ms": 0,
                "snapshot": {
                    "state": "running",
                    "ts_ms": 1_000_000,
                    "temp_c": 25,
                    "metrics": {
                        "viscosity_mPas": viscosity,
                        "torque_pct": torque,
                        "rotor": "18",
                        "rpm": 60,
                        "data_verified": True,
                    },
                },
            }
        )
    return {
        "captured_at_server_ms": 1_000_100,
        "devices": devices,
    }


def test_capture_device_does_not_guess_between_multiple_stirrers():
    capture = {
        "devices": [
            {
                "device_id": 11,
                "type": "stirrer",
                "age_ms": 10,
                "snapshot": {"state": "running", "temp_c": 31},
            },
            {
                "device_id": 12,
                "type": "stirrer",
                "age_ms": 10,
                "snapshot": {"state": "running", "temp_c": 39},
            },
        ]
    }

    assert R201Service._capture_device(
        capture,
        "stirrer",
        "reaction_temp",
    ) is None

    capture["role_device_ids"] = {"reaction_temp": 12}
    selected = R201Service._capture_device(
        capture,
        "stirrer",
        "reaction_temp",
    )
    assert selected["device_id"] == 12


def _advance_to_aging(service: R201Service, experiment_id: int):
    steps = ["R201-01", "R201-02", "R201-03", "R201-04", "R201-10", "R201-20", "R201-30", "R201-31", "R201-32", "R201-40"]
    for i, step in enumerate(steps):
        exp = service.get_experiment(experiment_id)["experiment"]
        service.start_step(experiment_id, step, exp["row_version"], _event("start", i))
        exp = service.get_experiment(experiment_id)["experiment"]
        service.complete_step(
            experiment_id,
            step,
            exp["row_version"],
            VALID_RESULTS[step],
            _event("complete", i),
        )
    exp = service.get_experiment(experiment_id)["experiment"]
    service.start_step(experiment_id, "R201-50", exp["row_version"], _event("start-aging", 1))


def test_create_experiment_sets_route_and_initial_step():
    service, _ = _service()
    created = service.create_experiment(CREATE)
    assert created["batch_id"] == "20260723-CEM-01"
    assert created["current_step_code"] == "R201-01"
    assert created["next_step"] == "C-320R"
    assert created["downstream_route_variant"] == "CEM_WITH_F801"
    traceability = service.get_traceability(created["id"])
    assert traceability["items"][0]["item_code"] == created["batch_id"]
    assert traceability["items"][0]["item_type"] == "batch"


def test_traceability_service_creates_stores_retrieves_and_finishes_items():
    service, _ = _service()
    created = service.create_experiment(CREATE)
    location = service.create_storage_location(
        {
            "location_code": "FRIDGE-01-A2",
            "display_name": "冰箱01 · A2",
            "storage_condition": "4℃",
            "actor": "张三",
        }
    )
    intermediates = service.create_trace_items(
        created["id"],
        {
            **_event("trace-create", 1),
            "item_type": "intermediate",
            "display_name": "湿化学中间溶胶",
            "container_count": 2,
            "quantity": 50,
            "unit": "mL",
        },
    )
    assert intermediates[0]["item_code"].endswith("-IP01-01")
    assert intermediates[1]["item_code"].endswith("-IP01-02")

    stored = service.transition_trace_item(
        intermediates[0]["id"],
        "store",
        {
            **_event("trace-store", 1),
            "location_code": location["location_code"],
            "hold_hours": 24,
        },
    )
    assert stored["status"] == "stored"
    assert stored["hold_until_ms"] > stored["updated_at_ms"]

    retrieved = service.transition_trace_item(
        intermediates[0]["id"],
        "retrieve",
        _event("trace-retrieve", 1),
    )
    assert retrieved["status"] == "active"

    final_items = service.create_trace_items(
        created["id"],
        {
            **_event("trace-final", 1),
            "item_type": "final_product",
            "container_count": 1,
        },
    )
    assert final_items[0]["item_code"].endswith("-FP-01")
    assert final_items[0]["parents"][0]["item_type"] == "intermediate"


def test_traceability_rejects_unknown_location_and_wrong_operator():
    service, _ = _service()
    created = service.create_experiment(CREATE)
    item = service.create_trace_items(
        created["id"],
        {
            **_event("trace-create", 1),
            "item_type": "intermediate",
            "container_count": 1,
        },
    )[0]

    with pytest.raises(R201Error, match="storage location"):
        service.transition_trace_item(
            item["id"],
            "store",
            {**_event("trace-store", 1), "location_code": "UNKNOWN"},
        )
    with pytest.raises(R201Error, match="operator"):
        service.transition_trace_item(
            item["id"],
            "retrieve",
            {**_event("trace-retrieve", 1), "actor": "李四"},
        )


def test_create_allows_blank_reviewer_and_suggests_next_daily_batch_id():
    service, _ = _service()

    assert service.suggest_batch_id("CEM", "20260723") == "20260723-CEM-01"
    created = service.create_experiment({**CREATE, "reviewer": ""})

    assert created["reviewer"] == ""
    assert service.suggest_batch_id("CEM", "20260723") == "20260723-CEM-02"
    assert service.suggest_batch_id("AEM", "20260723") == "20260723-AEM-01"

    service.create_experiment(
        {**CREATE, "batch_id": "20260723-CEM-ARCHIVE"}
    )
    service.create_experiment({**CREATE, "batch_id": "20260723-CEM-100"})
    assert service.suggest_batch_id("CEM", "20260723") == "20260723-CEM-101"


def test_material_lot_can_be_left_blank_for_fast_tablet_confirmation():
    service, _ = _service()
    experiment = service.create_experiment(CREATE)
    started = service.start_step(
        experiment["id"],
        "R201-01",
        experiment["row_version"],
        _event("start-blank-lot", 1),
    )
    service.complete_step(
        experiment["id"],
        "R201-01",
        started["experiment"]["row_version"],
        VALID_RESULTS["R201-01"],
        _event("complete-blank-lot", 1),
    )
    experiment = service.get_experiment(experiment["id"])["experiment"]
    started = service.start_step(
        experiment["id"],
        "R201-02",
        experiment["row_version"],
        _event("start-blank-lot", 2),
    )
    service.complete_step(
        experiment["id"],
        "R201-02",
        started["experiment"]["row_version"],
        VALID_RESULTS["R201-02"],
        _event("complete-blank-lot", 2),
    )
    experiment = service.get_experiment(experiment["id"])["experiment"]
    started = service.start_step(
        experiment["id"],
        "R201-03",
        experiment["row_version"],
        _event("start-blank-lot", 3),
    )

    detail = service.complete_step(
        experiment["id"],
        "R201-03",
        started["experiment"]["row_version"],
        {
            "materials": [
                {"name": "TEOS", "lot": "", "actual": 10, "unit": "mL"}
            ]
        },
        _event("complete-blank-lot", 3),
    )

    assert detail["materials"][0]["lot_no"] == ""


def test_scanned_material_container_is_decremented_and_audited():
    service, repo = _service()
    experiment = service.create_experiment(CREATE)
    container = service.create_material_container(
        {
            "container_code": "RM-TEOS-0001",
            "external_barcode": "6901234567890",
            "material_name": "TEOS",
            "supplier_lot": "SUP-01",
            "quantity_remaining": 20,
            "unit": "mL",
            "created_by": "张三",
            "client_event_id": "register-material-1",
        }
    )
    for index, step in enumerate(("R201-01", "R201-02"), start=1):
        current = service.get_experiment(
            experiment["id"]
        )["experiment"]
        started = service.start_step(
            experiment["id"],
            step,
            current["row_version"],
            _event("start-material", index),
        )
        service.complete_step(
            experiment["id"],
            step,
            started["experiment"]["row_version"],
            VALID_RESULTS[step],
            _event("complete-material", index),
        )
    current = service.get_experiment(experiment["id"])["experiment"]
    started = service.start_step(
        experiment["id"],
        "R201-03",
        current["row_version"],
        _event("start-material", 3),
    )
    service.complete_step(
        experiment["id"],
        "R201-03",
        started["experiment"]["row_version"],
        {
            "materials": [
                {
                    "name": "TEOS",
                    "lot": "SUP-01",
                    "actual": 7.5,
                    "unit": "mL",
                    "material_container_id": container["id"],
                    "container_code": container["container_code"],
                }
            ]
        },
        _event("complete-material", 3),
    )

    updated = repo.experiments.get_material_container_by_code(
        "RM-TEOS-0001"
    )
    events = repo.experiments.list_material_container_events(
        container["id"]
    )

    assert updated["quantity_remaining"] == 12.5
    assert updated["opened_on"] is not None
    assert [row["event_type"] for row in events] == [
        "registered",
        "opened",
        "used",
    ]
    assert events[-1]["experiment_id"] == experiment["id"]
    assert events[-1]["quantity"] == 7.5


def test_expired_material_container_cannot_be_registered():
    service, _ = _service()
    with pytest.raises(R201Error, match="有效期"):
        service.create_material_container(
            {
                "container_code": "RM-OLD-0001",
                "material_name": "过期原料",
                "expires_on": "2020-01-01",
                "quantity_remaining": 10,
                "unit": "mL",
                "created_by": "张三",
                "client_event_id": "register-old-material",
            }
        )


def test_process_devices_are_released_when_their_physical_stage_finishes():
    service, repo = _service()
    experiment = service.create_experiment(CREATE)
    pump_id = repo.upsert_device("pump-1", "tyd02", "注射泵")
    repo.experiments.reserve_process_device(
        experiment["id"],
        pump_id,
        "tyd02",
        "张三",
        None,
        1000,
    )
    service.add_data_source(
        experiment["id"],
        {
            **_event("bind-release", 1),
            "device_id": pump_id,
            "device_role": "acid_pump",
            "metric_key": "acc_volume",
            "linked_at_ms": 1000,
            "link_method": "manual",
        },
    )
    steps = (
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
        "R201-31",
    )
    for index, step in enumerate(steps, start=1):
        current = service.get_experiment(
            experiment["id"]
        )["experiment"]
        started = service.start_step(
            experiment["id"],
            step,
            current["row_version"],
            _event("start-release", index),
        )
        completed = service.complete_step(
            experiment["id"],
            step,
            started["experiment"]["row_version"],
            VALID_RESULTS[step],
            _event("complete-release", index),
        )

    assert completed["experiment"]["current_step_code"] == "R201-32"
    assert repo.experiments.list_device_reservations(
        experiment["id"]
    ) == []
    bindings = repo.experiments.list_data_source_bindings(
        experiment["id"]
    )
    assert bindings[0]["unlinked_at_ms"] is not None


def test_create_rejects_invalid_membrane_system_and_target_range():
    service, _ = _service()
    with pytest.raises(R201Error, match="membrane_system"):
        service.create_experiment({**CREATE, "membrane_system": "OTHER"})
    with pytest.raises(R201Error, match="target viscosity"):
        service.create_experiment({
            **CREATE,
            "batch_id": "20260723-CEM-02",
            "target_viscosity_min_mpas": 10,
            "target_viscosity_max_mpas": 2,
        })


def test_step_transitions_are_sequential_and_require_row_version():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    with pytest.raises(R201Error, match="current step"):
        service.start_step(exp["id"], "R201-10", exp["row_version"], _event("wrong", 1))
    started = service.start_step(exp["id"], "R201-01", exp["row_version"], _event("start", 1))
    assert started["experiment"]["status"] == "in_progress"
    with pytest.raises(R201Error, match="row version"):
        service.complete_step(exp["id"], "R201-01", exp["row_version"], VALID_RESULTS["R201-01"], _event("complete", 1))


def test_reusing_idempotency_key_for_another_operation_is_rejected():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    started = service.start_step(
        exp["id"],
        "R201-01",
        exp["row_version"],
        _event("same-key", 1),
    )

    with pytest.raises(R201Error, match="another operation"):
        service.complete_step(
            exp["id"],
            "R201-01",
            started["experiment"]["row_version"],
            VALID_RESULTS["R201-01"],
            _event("same-key", 1),
        )


def test_start_step_maps_active_step_unique_race_to_conflict(monkeypatch):
    service, _ = _service()
    exp = service.create_experiment(CREATE)

    def raise_unique_race(*_args, **_kwargs):
        raise sqlite3.IntegrityError("idx_step_one_active")

    monkeypatch.setattr(service.store, "start_step", raise_unique_race)
    with pytest.raises(R201Error) as caught:
        service.start_step(
            exp["id"],
            "R201-01",
            exp["row_version"],
            _event("raced-start", 1),
        )
    assert caught.value.status_code == 409


def test_trusted_client_time_is_corrected_and_keeps_audit_evidence():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    request = {
        "client_event_id": "clock-evidence-1",
        "occurred_at_client_ms": 1_250_000,
        "client_clock_offset_ms": 250_000,
        "clock_sync_status": "trusted",
        "actor": "张三",
    }

    service.start_step(
        exp["id"], "R201-01", exp["row_version"], request
    )

    event = next(
        item
        for item in service.get_experiment(exp["id"])["events"]
        if item["client_event_id"] == "clock-evidence-1"
    )
    assert event["occurred_at_client_ms"] == 1_250_000
    assert event["received_at_server_ms"] > 1_000_000
    assert event["client_clock_offset_ms"] == 250_000
    assert event["clock_sync_status"] == "trusted"
    assert event["effective_at_ms"] == 1_000_000
    assert event["actor"] == "张三"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_numeric_fields_reject_non_finite_values_and_booleans(value):
    service, _ = _service()
    with pytest.raises(R201Error, match="must be"):
        service.create_experiment(
            {
                **CREATE,
                "target_viscosity_min_mpas": value,
            }
        )


def test_event_rejects_non_numeric_client_timestamp():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    with pytest.raises(
        R201Error, match="occurred_at_client_ms must be an integer"
    ):
        service.start_step(
            exp["id"],
            "R201-01",
            exp["row_version"],
            {
                **_event("bad-clock", 1),
                "occurred_at_client_ms": "not-a-time",
            },
        )


def test_trusted_time_outside_30_second_window_uses_server_time():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    service.start_step(
        exp["id"],
        "R201-01",
        exp["row_version"],
        {
            **_event("old-clock", 1),
            "occurred_at_client_ms": 900_000,
        },
    )
    event = next(
        item
        for item in service.get_experiment(exp["id"])["events"]
        if item["client_event_id"] == "old-clock-1"
    )
    assert event["clock_sync_status"] == "untrusted"
    assert event["effective_at_ms"] == event["received_at_server_ms"]


def test_step_completion_validates_required_fields():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    started = service.start_step(exp["id"], "R201-01", exp["row_version"], _event("start", 1))
    with pytest.raises(R201Error, match="device_checks"):
        service.complete_step(
            exp["id"],
            "R201-01",
            started["experiment"]["row_version"],
            {"environment_temp_c": 25, "environment_humidity_rh": 50},
            _event("complete", 1),
        )


def test_acid_dose_uses_lifetime_counter_delta():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    steps = ["R201-01", "R201-02", "R201-03", "R201-04", "R201-10", "R201-20", "R201-30"]
    for i, step in enumerate(steps):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(exp["id"], step, state["row_version"], _event("s", i))
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(exp["id"], step, state["row_version"], VALID_RESULTS[step], _event("c", i))
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(exp["id"], "R201-31", state["row_version"], _event("s-dose", 1))
    state = service.get_experiment(exp["id"])["experiment"]
    result = service.complete_step(exp["id"], "R201-31", state["row_version"], VALID_RESULTS["R201-31"], _event("c-dose", 1))
    assert result["step"]["result"]["dose_delivered_ml"] == pytest.approx(5.0)


def test_viscosity_is_repeatable_child_task_and_controls_endpoint():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    first = service.record_viscosity(
        exp["id"],
        {
            **_event("visc", 1),
            "viscosity_mpas": 5.0,
            "sample_temp_c": 25,
            "reaction_temp_c": 40,
            "rotor": "18",
            "rpm": 60,
            "torque_pct": 50,
        },
    )
    assert first["measurement"]["valid"] is True
    assert first["endpoint_ready"] is False
    assert first["experiment"]["current_step_code"] == "R201-50"
    second = service.record_viscosity(
        exp["id"],
        {
            **_event("visc", 2),
            "viscosity_mpas": 5.3,
            "sample_temp_c": 25,
            "reaction_temp_c": 40,
            "rotor": "18",
            "rpm": 60,
            "torque_pct": 50,
        },
    )
    assert second["endpoint_ready"] is True
    state = service.get_experiment(exp["id"])["experiment"]
    completed = service.complete_step(
        exp["id"], "R201-50", state["row_version"], {"endpoint_confirmed": True}, _event("complete-aging", 1)
    )
    assert completed["experiment"]["current_step_code"] == "R201-60"


def test_device_markers_derive_pump_delta_and_keep_provenance():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    steps = [
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
    ]
    for index, step in enumerate(steps):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("auto-pre-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("auto-pre-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"],
        "R201-31",
        state["row_version"],
        {
            **_event("auto-dose-start", 1),
            "device_capture": _capture(pump_volume=100),
        },
    )
    state = service.get_experiment(exp["id"])["experiment"]
    request_data = {
        **_event("auto-dose-end", 1),
        "device_capture": _capture(pump_volume=105),
    }
    preview = service.preview_step_completion(
        exp["id"], "R201-31", state["row_version"], {}, request_data
    )
    assert preview["missing_fields"] == []
    assert preview["result"]["acc_volume_start"] == 100
    assert preview["result"]["acc_volume_end"] == 105
    assert preview["result"]["dose_delivered_ml"] == 5
    completed = service.complete_step(
        exp["id"], "R201-31", state["row_version"], {}, request_data
    )
    result = completed["step"]["result"]
    assert result["dose_delivered_ml"] == 5
    assert (
        result["data_provenance"]["acc_volume_end"]["source_type"]
        == "device_confirmed"
    )


def test_out_of_range_preview_requires_confirmation_and_closes_warning_without_reviewer():
    clock = Clock()
    repo = Repository(":memory:")
    service = R201Service(repo, clock_ms=clock)
    exp = service.create_experiment({**CREATE, "reviewer": ""})
    steps = [
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
        "R201-31",
    ]
    for index, step in enumerate(steps):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("warning-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("warning-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    clock.ms = 1_999_900
    start_data = {
        **_event("balance-start", 1),
        "occurred_at_client_ms": 2_000_000,
    }
    service.start_step(
        exp["id"], "R201-32", state["row_version"], start_data
    )
    state = service.get_experiment(exp["id"])["experiment"]
    clock.ms = 2_659_900
    finish_data = {
        **_event("balance-finish", 1),
        "occurred_at_client_ms": 2_660_000,
        "require_finding_confirmation": True,
    }
    preview = service.preview_step_completion(
        exp["id"], "R201-32", state["row_version"], {}, finish_data
    )
    assert preview["result"]["balance_minutes"] == 11
    assert preview["findings"][0]["rule_code"] == "POST_DOSING_BALANCE_MAX"
    with pytest.raises(R201Error, match="请确认数据是否正确"):
        service.complete_step(
            exp["id"], "R201-32", state["row_version"], {}, finish_data
        )
    completed = service.complete_step(
        exp["id"],
        "R201-32",
        state["row_version"],
        {},
        {
            **finish_data,
            "client_event_id": "balance-confirmed",
            "confirmed_finding_codes": ["POST_DOSING_BALANCE_MAX"],
        },
    )
    assert completed["deviations"][0]["status"] == "closed"
    assert completed["deviations"][0]["reviewed_by"] is None


def test_viscosity_button_can_capture_complete_device_reading():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    reading = service.record_viscosity(
        exp["id"],
        {
            **_event("device-viscosity", 1),
            "device_capture": _capture(viscosity=5.4),
        },
    )
    assert reading["measurement"]["source_type"] == "device_confirmed"
    assert reading["measurement"]["values"]["viscosity_mpas"] == 5.4
    assert reading["measurement"]["values"]["reaction_temp_c"] == 40
    assert (
        reading["measurement"]["values"]["data_provenance"][
            "viscosity_mpas"
        ]["device_name"]
        == "粘度计"
    )


def test_unverified_viscometer_reading_cannot_be_device_confirmed():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    capture = _capture(viscosity=5.4)
    capture["devices"][-1]["snapshot"]["metrics"]["data_verified"] = False

    with pytest.raises(R201Error, match="viscosity_mpas is required"):
        service.record_viscosity(
            exp["id"],
            {
                **_event("unverified-device-viscosity", 1),
                "device_capture": capture,
            },
        )


def test_account_id_not_duplicate_display_name_controls_operator_access():
    service, repo = _service()
    repo._conn.executemany(
        """INSERT INTO user_account(
             username, display_name, password_hash, role, is_active,
             must_change_password, created_at_ms, updated_at_ms)
           VALUES(?, '同名操作员', 'x', 'operator', 1, 0, 1, 1)""",
        [("operator-a",), ("operator-b",)],
    )
    repo._conn.commit()
    exp = service.create_experiment(
        {
            **CREATE,
            "operator": "同名操作员",
            "operator_user_id": 1,
            "reviewer": "",
        }
    )

    with pytest.raises(R201Error, match="当前账号不是本批指定操作员"):
        service.start_step(
            exp["id"],
            "R201-01",
            exp["row_version"],
            {
                **_event("same-name-other-account", 1),
                "actor": "同名操作员",
                "actor_user_id": 2,
            },
        )


def test_tyd_units_are_converted_to_ml_and_wrong_mode_is_not_captured():
    capture = _capture(pump_volume=5000, pump_rate=1000, pump_target=5000)
    pump = capture["devices"][0]
    pump["snapshot"]["acc_unit"] = "uL"
    pump["snapshot"]["metrics"]["target_unit"] = "uL"
    pump["snapshot"]["metrics"]["inject_rate_unit"] = "uL/min"

    assert R201Service._volume_ml(5000, "uL") == pytest.approx(5)
    assert R201Service._rate_ml_min(1000, "uL/min") == pytest.approx(1)
    assert (
        R201Service._capture_device(capture, "tyd02", "acid_pump")
        is pump
    )
    pump["snapshot"]["work_mode"] = "仅抽取"
    assert (
        R201Service._capture_device(capture, "tyd02", "acid_pump")
        is None
    )


def test_r201_30_preview_applies_tyd_unit_conversion():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    for index, step in enumerate(
        ("R201-01", "R201-02", "R201-03", "R201-04", "R201-10", "R201-20")
    ):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("unit-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("unit-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"], "R201-30", state["row_version"], _event("unit-r30", 1)
    )
    state = service.get_experiment(exp["id"])["experiment"]
    capture = _capture(
        pump_volume=5000,
        pump_rate=1000,
        pump_target=5000,
        stirrer_temp=3,
    )
    pump = capture["devices"][0]["snapshot"]
    pump["acc_unit"] = "uL"
    pump["metrics"]["target_unit"] = "uL"
    pump["metrics"]["inject_rate_unit"] = "uL/min"
    preview = service.preview_step_completion(
        exp["id"],
        "R201-30",
        state["row_version"],
        {"ice_bath_confirmed": True, "line_purged": True},
        {
            **_event("unit-r30-preview", 1),
            "device_capture": capture,
        },
    )

    assert preview["missing_fields"] == []
    assert preview["result"]["target_volume_ml"] == pytest.approx(5)
    assert preview["result"]["target_rate_ml_min"] == pytest.approx(1)


def test_rework_invalidates_previous_viscosity_endpoint():
    service, _ = _service()
    exp = service.create_experiment({**CREATE, "reviewer": ""})
    _advance_to_aging(service, exp["id"])
    for index, value in enumerate((5.0, 5.2), start=1):
        service.record_viscosity(
            exp["id"],
            {
                **_event("visc-before-rework", index),
                "viscosity_mpas": value,
                "sample_temp_c": 25,
                "reaction_temp_c": 40,
                "rotor": "18",
                "rpm": 60,
                "torque_pct": 50,
            },
        )
    assert service.get_experiment(exp["id"])["endpoint_ready"] is True
    deviation = service.open_deviation(
        exp["id"],
        {
            **_event("visc-rework-deviation", 1),
            "description": "粘度阶段需要返工",
            "opened_by": "张三",
        },
    )
    state = service.get_experiment(exp["id"])["experiment"]
    service.resolve_deviation(
        exp["id"],
        deviation["id"],
        {
            **_event("visc-rework-resolve", 1),
            "reviewed_by": "张三",
            "impact_assessment": "重新老化并测量",
            "disposition": "rework",
            "rework_step_code": "R201-50",
            "row_version": state["row_version"],
        },
    )

    assert service.get_experiment(exp["id"])["endpoint_ready"] is False


def test_invalid_torque_does_not_count_toward_endpoint():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    result = service.record_viscosity(
        exp["id"],
        {
            **_event("visc", 1),
            "viscosity_mpas": 5.0,
            "sample_temp_c": 25,
            "reaction_temp_c": 40,
            "rotor": "18",
            "rpm": 60,
            "torque_pct": 5,
        },
    )
    assert result["measurement"]["valid"] is False
    assert result["endpoint_ready"] is False


def test_aging_cannot_complete_before_stable_endpoint():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    state = service.get_experiment(exp["id"])["experiment"]
    with pytest.raises(R201Error, match="viscosity endpoint"):
        service.complete_step(
            exp["id"], "R201-50", state["row_version"], {"endpoint_confirmed": True}, _event("complete-aging", 1)
        )


def test_submit_and_parallel_validation_release():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    for n, viscosity in enumerate((5.0, 5.2), start=1):
        service.record_viscosity(
            exp["id"],
            {
                **_event("visc", n),
                "viscosity_mpas": viscosity,
                "sample_temp_c": 25,
                "reaction_temp_c": 40,
                "rotor": "18",
                "rpm": 60,
                "torque_pct": 50,
            },
        )
    for i, step in enumerate(("R201-50", "R201-60", "R201-70")):
        state = service.get_experiment(exp["id"])["experiment"]
        if step != "R201-50":
            service.start_step(exp["id"], step, state["row_version"], _event("s-final", i))
            state = service.get_experiment(exp["id"])["experiment"]
        payload = {"endpoint_confirmed": True} if step == "R201-50" else VALID_RESULTS[step]
        service.complete_step(exp["id"], step, state["row_version"], payload, _event("c-final", i))
    state = service.get_experiment(exp["id"])["experiment"]
    deviation = service.open_deviation(
        exp["id"],
        {
            **_event("submit-deviation", 1),
            "description": "提交前偏差闭环测试",
            "opened_by": "张三",
            "severity": "warning",
        },
    )
    with pytest.raises(R201Error, match="未处置的异常"):
        service.submit(
            exp["id"],
            state["row_version"],
            actor="张三",
            client_event_id="submit-blocked",
        )
    resolved = service.resolve_deviation(
        exp["id"],
        deviation["id"],
        {
            **_event("resolve-deviation", 1),
            "reviewed_by": "李四",
                "impact_assessment": "不影响本批关键质量属性",
                "disposition": "continue",
                "cause": "测试偏差",
                "row_version": state["row_version"],
            },
        )
    repeated = service.resolve_deviation(
        exp["id"],
        deviation["id"],
        {
            **_event("resolve-deviation", 1),
            "reviewed_by": "李四",
                "impact_assessment": "不影响本批关键质量属性",
                "disposition": "continue",
                "cause": "测试偏差",
                "row_version": state["row_version"],
            },
        )
    assert resolved["status"] == "closed"
    assert repeated["id"] == resolved["id"]
    submitted = service.submit(exp["id"], state["row_version"], actor="张三", client_event_id="submit-1")
    assert submitted["status"] == "pending_review"
    released = service.review(
        exp["id"],
        submitted["row_version"],
        action="release",
        reviewer="李四",
        client_event_id="release-1",
        disposition="合格",
    )
    assert released["status"] == "released"
    assert released["validation_mode"] == "parallel_validation"


def test_snapshot_rule_violation_creates_audited_deviation_candidate():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    for index, step in enumerate(("R201-01", "R201-02", "R201-03", "R201-04")):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("rule-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("rule-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"], "R201-10", state["row_version"], _event("rule-start", 10)
    )
    state = service.get_experiment(exp["id"])["experiment"]

    result = service.complete_step(
        exp["id"],
        "R201-10",
        state["row_version"],
        {"appearance": "澄清", "actual_rpm": 650},
        _event("rule-complete", 10),
    )

    assert result["deviations"][0]["actual_value"] == "650 rpm"
    assert result["deviations"][0]["standard_value"] == "300–500 rpm"
    detail = service.get_experiment(exp["id"])
    assert len(detail["deviations"]) == 1
    assert [event["event_type"] for event in detail["events"]].count(
        "deviation_opened"
    ) == 1


def test_post_dosing_balance_below_minimum_is_blocked():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    steps = (
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
        "R201-31",
    )
    for index, step in enumerate(steps):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("balance-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("balance-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"], "R201-32", state["row_version"], _event("balance-start", 32)
    )
    state = service.get_experiment(exp["id"])["experiment"]

    with pytest.raises(R201Error, match="尚不足 5 分钟"):
        service.complete_step(
            exp["id"],
            "R201-32",
            state["row_version"],
            {"balance_minutes": 4.9},
            _event("balance-complete", 32),
        )


def test_manual_deviation_is_idempotent_and_audited():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    request = {
        **_event("manual-deviation", 1),
        "description": "观察到短时通讯中断",
        "immediate_action": "检查接线",
        "opened_by": "张三",
        "severity": "warning",
    }

    first = service.open_deviation(exp["id"], request)
    second = service.open_deviation(exp["id"], request)

    assert second["id"] == first["id"]
    detail = service.get_experiment(exp["id"])
    assert len(detail["deviations"]) == 1
    assert [event["event_type"] for event in detail["events"]].count(
        "deviation_opened"
    ) == 1


def test_concurrent_manual_deviations_get_unique_sequential_numbers():
    service, _ = _service()
    exp = service.create_experiment(CREATE)

    def open_one(index):
        return service.open_deviation(
            exp["id"],
            {
                **_event("parallel-deviation", index),
                "description": f"并发偏差 {index}",
                "opened_by": "张三",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(open_one, (1, 2)))

    assert sorted(item["deviation_no"] for item in created) == [
        "20260723-CEM-01-DEV-01",
        "20260723-CEM-01-DEV-02",
    ]


def test_continue_deviation_requires_current_row_version():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    deviation = service.open_deviation(
        exp["id"],
        {
            **_event("versioned-deviation", 1),
            "description": "版本冲突验证",
            "opened_by": "张三",
        },
    )
    with pytest.raises(R201Error, match="row version conflict"):
        service.resolve_deviation(
            exp["id"],
            deviation["id"],
            {
                **_event("versioned-resolve", 1),
                "reviewed_by": "李四",
                "impact_assessment": "不影响",
                "disposition": "continue",
                "row_version": exp["row_version"] + 1,
            },
        )


def test_pending_review_blocks_deviations_and_device_changes():
    service, repo = _service()
    exp = service.create_experiment(CREATE)
    device_id = repo.upsert_device("stirrer-1", "stirrer")
    repo._conn.execute(
        "UPDATE experiment SET status='pending_review' WHERE id=?",
        (exp["id"],),
    )
    repo._conn.commit()

    with pytest.raises(R201Error, match="cannot open a deviation"):
        service.open_deviation(
            exp["id"],
            {
                **_event("late-deviation", 1),
                "description": "复核中新增",
                "opened_by": "张三",
            },
        )
    with pytest.raises(R201Error, match="cannot change devices"):
        service.select_process_device(
            exp["id"],
            {
                **_event("late-device", 1),
                "device_id": device_id,
                "device_type": "stirrer",
            },
        )


def test_release_rechecks_that_all_deviations_are_closed():
    service, repo = _service()
    exp = service.create_experiment(CREATE)
    service.open_deviation(
        exp["id"],
        {
            **_event("review-open-deviation", 1),
            "description": "发布前仍未闭环",
            "opened_by": "张三",
        },
    )
    repo._conn.execute(
        """UPDATE experiment
           SET status='pending_review', current_step_code='R201-90'
           WHERE id=?""",
        (exp["id"],),
    )
    repo._conn.commit()

    with pytest.raises(R201Error, match="不能发布"):
        service.review(
            exp["id"],
            exp["row_version"],
            action="release",
            reviewer="李四",
            client_event_id="blocked-release",
            disposition="合格",
        )


def test_deviation_rework_supersedes_steps_and_increments_attempt():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    for index, step in enumerate(("R201-01", "R201-02")):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("rework-start", index)
        )
        if step == "R201-01":
            state = service.get_experiment(exp["id"])["experiment"]
            service.complete_step(
                exp["id"],
                step,
                state["row_version"],
                VALID_RESULTS[step],
                _event("rework-complete", index),
            )
    deviation = service.open_deviation(
        exp["id"],
        {
            **_event("rework-deviation", 1),
            "description": "器皿确认后需返工",
            "opened_by": "张三",
        },
    )
    state = service.get_experiment(exp["id"])["experiment"]

    service.resolve_deviation(
        exp["id"],
        deviation["id"],
        {
            **_event("rework-resolve", 1),
            "reviewed_by": "李四",
            "impact_assessment": "从环境确认重新执行",
            "disposition": "rework",
            "rework_step_code": "R201-01",
            "row_version": state["row_version"],
        },
    )

    detail = service.get_experiment(exp["id"])
    assert detail["experiment"]["current_step_code"] == "R201-01"
    assert detail["active_step"] is None
    assert [step["status"] for step in detail["steps"]] == [
        "superseded",
        "superseded",
    ]
    restarted = service.start_step(
        exp["id"],
        "R201-01",
        detail["experiment"]["row_version"],
        _event("rework-restart", 1),
    )
    assert restarted["step"]["attempt_no"] == 2


def test_deviation_terminate_closes_active_step_and_batch():
    service, _ = _service()
    exp = service.create_experiment(CREATE)
    service.start_step(
        exp["id"], "R201-01", exp["row_version"], _event("terminate-start", 1)
    )
    deviation = service.open_deviation(
        exp["id"],
        {
            **_event("terminate-deviation", 1),
            "description": "重大设备故障",
            "opened_by": "张三",
            "severity": "critical",
        },
    )
    state = service.get_experiment(exp["id"])["experiment"]

    service.resolve_deviation(
        exp["id"],
        deviation["id"],
        {
            **_event("terminate-resolve", 1),
            "reviewed_by": "李四",
            "impact_assessment": "本批终止",
            "disposition": "terminate",
            "row_version": state["row_version"],
        },
    )

    detail = service.get_experiment(exp["id"])
    assert detail["experiment"]["status"] == "terminated"
    assert detail["experiment"]["disposition"] == "不合格"
    assert detail["active_step"] is None
    assert detail["steps"][0]["status"] == "superseded"


def test_recipe_snapshot_hash_and_material_usage_are_persisted():
    service, _ = _service()
    recipe_parameters = [
        {
            "parameter_code": "TEOS_TARGET",
            "display_name": "TEOS 理论量",
            "target_value": 10.0,
            "actual_value": None,
            "unit": "mL",
            "formula": "approved recipe",
            "inputs": {"density_g_ml": 0.933},
            "source": "R-CEM-001/V1",
        }
    ]
    create = {**CREATE, "recipe_parameters": recipe_parameters}

    exp = service.create_experiment(create)

    expected_snapshot = json.dumps(
        {
            "recipe_parameters": recipe_parameters,
            "spec_snapshot": CREATE["spec_snapshot"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert exp["snapshot_sha256"] == hashlib.sha256(expected_snapshot).hexdigest()
    detail = service.get_experiment(exp["id"])
    assert detail["recipe_parameters"][0]["parameter_code"] == "TEOS_TARGET"

    for index, step in enumerate(("R201-01", "R201-02")):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("material-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("material-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"], "R201-03", state["row_version"], _event("material-start", 3)
    )
    state = service.get_experiment(exp["id"])["experiment"]
    service.complete_step(
        exp["id"],
        "R201-03",
        state["row_version"],
        {
            "materials": [
                {
                    "name": "TEOS",
                    "lot": "T-01",
                    "expires_at": "2027-01-01",
                    "theoretical": 10.0,
                    "actual": 9.98,
                    "unit": "mL",
                    "appearance": "澄清",
                }
            ]
        },
        _event("material-complete", 3),
    )

    material = service.get_experiment(exp["id"])["materials"][0]
    assert material["material_name"] == "TEOS"
    assert material["lot_no"] == "T-01"
    assert material["actual_value"] == pytest.approx(9.98)


def test_bound_temperature_channel_drives_aging_checkpoints_idempotently():
    service, repo = _service()
    exp = service.create_experiment(CREATE)
    _advance_to_aging(service, exp["id"])
    active = service.get_experiment(exp["id"])["active_step"]
    device_id = repo.upsert_device("whd-1", "whd46", "反应温度")
    service.add_data_source(
        exp["id"],
        {
            **_event("bind-aging-temp", 1),
            "device_id": device_id,
            "step_instance_id": active["id"],
            "device_role": "reaction_temp",
            "metric_key": "ch2_temp_c",
            "channel_selector": "2",
            "linked_at_ms": active["started_effective_at_ms"],
            "link_method": "manual",
        },
    )
    start = active["started_effective_at_ms"]
    for index, (minutes, ch2_temp) in enumerate(
        ((0, 39.8), (20, 40.1), (40, 40.3))
    ):
        repo.add_sample(
            run_id=None,
            device_id=device_id,
            ts_ms=start + minutes * 60_000,
            state="running",
            flow_rate=None,
            delivered_volume=None,
            temp_c=99.9,
            metrics_json=json.dumps(
                {
                    "ch1_temp_c": 20.0,
                    "ch2_temp_c": ch2_temp,
                    "ch3_temp_c": 80.0,
                }
            ),
        )

    first = service.evaluate_telemetry(exp["id"])
    second = service.evaluate_telemetry(exp["id"])

    assert [point["value"] for point in first["temperature_series"]] == [
        39.8,
        40.1,
        40.3,
    ]
    assert len(first["temperature_checkpoints"]) == 2
    assert len(second["temperature_checkpoints"]) == 2
    detail = service.get_experiment(exp["id"])
    checkpoint_events = [
        event
        for event in detail["events"]
        if event["event_type"] == "aging_temperature_checkpoint"
    ]
    assert len(checkpoint_events) == 2
    assert checkpoint_events[0]["payload"]["metric_key"] == "ch2_temp_c"


def test_heat_ramp_generates_reached_temperature_milestone_from_bound_source():
    service, repo = _service()
    exp = service.create_experiment(
        {
            **CREATE,
            "spec_snapshot": {
                **CREATE["spec_snapshot"],
                "reach_temp_consecutive_samples": 3,
            },
        }
    )
    steps = (
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
        "R201-31",
        "R201-32",
    )
    for index, step in enumerate(steps):
        state = service.get_experiment(exp["id"])["experiment"]
        service.start_step(
            exp["id"], step, state["row_version"], _event("heat-start", index)
        )
        state = service.get_experiment(exp["id"])["experiment"]
        service.complete_step(
            exp["id"],
            step,
            state["row_version"],
            VALID_RESULTS[step],
            _event("heat-complete", index),
        )
    state = service.get_experiment(exp["id"])["experiment"]
    service.start_step(
        exp["id"], "R201-40", state["row_version"], _event("heat-start", 40)
    )
    active = service.get_experiment(exp["id"])["active_step"]
    device_id = repo.upsert_device("whd-heat", "whd46", "反应温度")
    service.add_data_source(
        exp["id"],
        {
            **_event("bind-heat-temp", 1),
            "device_id": device_id,
            "step_instance_id": active["id"],
            "device_role": "reaction_temp",
            "metric_key": "ch1_temp_c",
            "channel_selector": "1",
            "linked_at_ms": active["started_effective_at_ms"],
            "link_method": "manual",
        },
    )
    for index, value in enumerate((37.5, 39.0, 40.0, 41.0)):
        repo.add_sample(
            None,
            device_id,
            active["started_effective_at_ms"] + index * 1_000,
            "running",
            None,
            None,
            99.9,
            json.dumps({"ch1_temp_c": value}),
        )

    first = service.evaluate_telemetry(exp["id"])
    second = service.evaluate_telemetry(exp["id"])

    assert first["reached_temperature"]["payload"]["value"] == 41.0
    assert second["reached_temperature"]["id"] == first["reached_temperature"]["id"]
    events = service.get_experiment(exp["id"])["events"]
    assert [event["event_type"] for event in events].count(
        "reached_temperature"
    ) == 1


def test_telemetry_gap_generates_one_idempotent_deviation_candidate():
    service, repo = _service()
    exp = service.create_experiment(
        {
            **CREATE,
            "spec_snapshot": {
                **CREATE["spec_snapshot"],
                "telemetry_gap_threshold_ms": 300_000,
            },
        }
    )
    _advance_to_aging(service, exp["id"])
    active = service.get_experiment(exp["id"])["active_step"]
    device_id = repo.upsert_device("whd-gap", "whd46", "反应温度")
    service.add_data_source(
        exp["id"],
        {
            **_event("bind-gap-temp", 1),
            "device_id": device_id,
            "step_instance_id": active["id"],
            "device_role": "reaction_temp",
            "metric_key": "ch2_temp_c",
            "channel_selector": "2",
            "linked_at_ms": active["started_effective_at_ms"],
            "link_method": "manual",
        },
    )
    for minutes in (0, 20):
        repo.add_sample(
            None,
            device_id,
            active["started_effective_at_ms"] + minutes * 60_000,
            "running",
            None,
            None,
            40.0,
            json.dumps({"ch2_temp_c": 40.0}),
        )

    service.evaluate_telemetry(exp["id"])
    service.evaluate_telemetry(exp["id"])

    detail = service.get_experiment(exp["id"])
    assert len(detail["telemetry_gaps"]) == 1
    assert len(detail["deviations"]) == 1
    assert detail["deviations"][0]["description"] == "反应温度遥测出现数据缺口"
    assert [event["event_type"] for event in detail["events"]].count(
        "deviation_opened"
    ) == 1


def test_telemetry_integrity_uses_samples_older_than_latest_thousand():
    service, repo = _service()
    exp = service.create_experiment(
        {
            **CREATE,
            "spec_snapshot": {
                **CREATE["spec_snapshot"],
                "telemetry_gap_threshold_ms": 300_000,
            },
        }
    )
    _advance_to_aging(service, exp["id"])
    active = service.get_experiment(exp["id"])["active_step"]
    device_id = repo.upsert_device("whd-long-run", "whd46", "反应温度")
    service.add_data_source(
        exp["id"],
        {
            **_event("bind-long-temp", 1),
            "device_id": device_id,
            "step_instance_id": active["id"],
            "device_role": "reaction_temp",
            "metric_key": "ch2_temp_c",
            "channel_selector": "2",
            "linked_at_ms": active["started_effective_at_ms"],
            "link_method": "manual",
        },
    )
    start = active["started_effective_at_ms"]
    timestamps = [start, start + 400_000]
    timestamps.extend(start + 400_000 + i * 1_000 for i in range(1, 1102))
    for ts_ms in timestamps:
        repo.add_sample(
            None,
            device_id,
            ts_ms,
            "running",
            None,
            None,
            40.0,
            json.dumps({"ch2_temp_c": 40.0}),
        )

    detail = service.get_experiment(exp["id"])

    assert len(detail["temperature_series"]) == len(timestamps)
    assert len(detail["telemetry_gaps"]) == 1


def test_telemetry_derived_writes_roll_back_together(monkeypatch):
    service, repo = _service()
    exp = service.create_experiment(
        {
            **CREATE,
            "spec_snapshot": {
                **CREATE["spec_snapshot"],
                "telemetry_gap_threshold_ms": 300_000,
            },
        }
    )
    _advance_to_aging(service, exp["id"])
    active = service.get_experiment(exp["id"])["active_step"]
    device_id = repo.upsert_device(
        "whd-rollback", "whd46", "反应温度"
    )
    service.add_data_source(
        exp["id"],
        {
            **_event("bind-rollback-temp", 1),
            "device_id": device_id,
            "step_instance_id": active["id"],
            "device_role": "reaction_temp",
            "metric_key": "ch2_temp_c",
            "channel_selector": "2",
            "linked_at_ms": active["started_effective_at_ms"],
            "link_method": "manual",
        },
    )
    for minutes in (0, 20):
        repo.add_sample(
            None,
            device_id,
            active["started_effective_at_ms"] + minutes * 60_000,
            "running",
            None,
            None,
            40.0,
            json.dumps({"ch2_temp_c": 40.0}),
        )

    original_add_event = service.store.add_experiment_event

    def fail_checkpoint(*args, **kwargs):
        data = args[1]
        if data["event_type"] == "aging_temperature_checkpoint":
            raise RuntimeError("checkpoint write failed")
        return original_add_event(*args, **kwargs)

    monkeypatch.setattr(
        service.store, "add_experiment_event", fail_checkpoint
    )

    with pytest.raises(RuntimeError, match="checkpoint write failed"):
        service.evaluate_telemetry(exp["id"])

    detail = service.get_experiment(exp["id"])
    assert detail["deviations"] == []
    assert [event["event_type"] for event in detail["events"]].count(
        "deviation_opened"
    ) == 0
    assert [event["event_type"] for event in detail["events"]].count(
        "aging_temperature_checkpoint"
    ) == 0
