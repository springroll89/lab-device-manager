import json
from pathlib import Path

from lab_device_manager.db.repository import Repository
from lab_device_manager.experiments.service import R201Service
from lab_device_manager.web.app import _build_experiment_pdf


FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "r201_four_devices.json"
)


def test_four_device_fixture_supports_run_and_runless_bindings_and_report():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    repo = Repository(":memory:")
    service = R201Service(repo, clock_ms=lambda: 1_000_000)
    experiment = service.create_experiment(
        {
            "batch_id": "20260723-CEM-FX01",
            "membrane_system": "CEM",
            "recipe_no": "R-CEM-FIXTURE",
            "recipe_version": "V1",
            "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
            "sop_version": "V0.2",
            "target_viscosity_min_mpas": 2.5,
            "target_viscosity_max_mpas": 10.0,
            "operator": "fixture-operator",
            "reviewer": "fixture-reviewer",
            "spec_snapshot": {
                "reaction_temp_min_c": 38,
                "reaction_temp_max_c": 42,
                "aging_checkpoint_interval_min": 20,
                "telemetry_gap_threshold_ms": 1_500_000
            },
        }
    )
    device_ids = {}
    pump_run_id = None
    for index, item in enumerate(fixture["devices"]):
        device_id = repo.upsert_device(
            item["name"], item["type"], item["alias"]
        )
        device_ids[item["key"]] = device_id
        run_id = None
        if item["key"] == "acid_pump":
            pump_run_id = repo.open_run(
                device_id, 1, 1_000_000, item["setpoints"]
            )
            run_id = pump_run_id
        service.add_data_source(
            experiment["id"],
            {
                "client_event_id": f"fixture-bind-{index}",
                "occurred_at_client_ms": 1_000_000,
                "client_clock_offset_ms": 0,
                "clock_sync_status": "trusted",
                "actor": "fixture-operator",
                "device_id": device_id,
                "run_id": run_id,
                "device_role": item["role"],
                "metric_key": item["metric_key"],
                "channel_selector": item["channel_selector"],
                "linked_at_ms": 1_000_000,
                "link_method": "manual",
            },
        )
    for item in fixture["temperature_samples"]:
        repo.add_sample(
            None,
            device_ids["reaction_temp"],
            1_000_000 + item["offset_min"] * 60_000,
            "running",
            None,
            None,
            item["top_level_temp_c"],
            json.dumps(item["metrics"]),
        )

    detail = service.get_experiment(experiment["id"])

    assert len(detail["data_sources"]) == 4
    assert any(item["run_id"] == pump_run_id for item in detail["data_sources"])
    assert any(item["run_id"] is None for item in detail["data_sources"])
    assert [item["value"] for item in detail["temperature_series"]] == [
        39.8,
        40.1,
        40.3,
    ]
    assert detail["telemetry_gaps"] == []
    pdf = _build_experiment_pdf(detail)
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page") >= 2
