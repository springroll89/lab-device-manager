"""Isolated data set used to capture the weekly-showcase screenshots.

This server uses an in-memory SQLite database. It never reads or writes the
application's production database.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

from flask import Response
from lab_device_manager.db.repository import Repository
from lab_device_manager.experiments.service import R201Service
from lab_device_manager.instruments.base import StatusSnapshot
from lab_device_manager.runtime.types import DeviceConfig
from lab_device_manager.web.app import create_app
from lab_device_manager.web.trace_labels import trace_labels_html


class ShowcaseEngine:
    def __init__(self, latest: dict, device_map: dict):
        self._latest = latest
        self._device_map = device_map

    def latest(self):
        current_time = time.time()
        return {
            device_id: replace(snapshot, timestamp=current_time)
            for device_id, snapshot in self._latest.items()
        }

    def device_map(self):
        return dict(self._device_map)

    def reconnect_device(self, device_id: int, serial_port: str):
        return {"ok": True, "device_id": device_id, "port": serial_port}

    def disconnect_device(self, device_id: int):
        return {"ok": True, "device_id": device_id}


def event(event_id: str, actor: str = "张三") -> dict:
    return {
        "client_event_id": event_id,
        "occurred_at_client_ms": int(time.time() * 1000),
        "client_clock_offset_ms": 0,
        "clock_sync_status": "trusted",
        "actor": actor,
    }


STEP_RESULTS = {
    "R201-01": {
        "environment_temp_c": 24.8,
        "environment_humidity_rh": 51.2,
        "device_checks": [
            "HMS-C 搅拌器 1",
            "TYD02 注射泵",
            "WHD46 温湿度控制器",
        ],
    },
    "R201-02": {
        "glassware_items": [
            {"name": "两口烧瓶", "dry": True},
            {"name": "滴液漏斗", "dry": True},
        ]
    },
    "R201-03": {
        "materials": [
            {
                "name": "TEOS",
                "lot": "TEOS-260718",
                "actual": 10.0,
                "unit": "mL",
            },
            {
                "name": "MPTES",
                "lot": "MPTES-260705",
                "actual": 2.0,
                "unit": "mL",
            },
            {
                "name": "无水乙醇",
                "lot": "ETOH-260721",
                "actual": 20.0,
                "unit": "mL",
            },
        ]
    },
    "R201-04": {
        "hcl_c1_mol_l": 12,
        "hcl_c2_mol_l": 0.1,
        "hcl_v1_ml": 0.833,
        "hcl_v2_ml": 100,
        "acid_into_water": True,
    },
    "R201-10": {"appearance": "澄清透明", "actual_rpm": 400},
    "R201-20": {
        "ethanol_actual_ml": 20,
        "appearance": "澄清透明",
        "actual_rpm": 400,
    },
    "R201-30": {
        "ice_bath_confirmed": True,
        "reaction_temp_c": 3.2,
        "syringe_spec": "10 mL",
        "target_volume_ml": 5,
        "target_rate_ml_min": 0.5,
        "line_purged": True,
    },
    "R201-31": {
        "acc_volume_start": 118.6,
        "acc_volume_end": 123.6,
        "acc_volume_unit": "mL",
        "target_volume_ml": 5,
    },
    "R201-32": {"balance_minutes": 7},
    "R201-40": {
        "condenser_confirmed": True,
        "moisture_protection_confirmed": True,
        "reached_temp_c": 40,
    },
    "R201-60": {"room_temp_confirmed": True, "cooling_minutes": 22},
    "R201-70": {
        "filter_spec": "G4 + 0.45 µm PTFE",
        "container_tare_g": 100,
        "container_gross_g": 151.4,
        "transfer_viscosity_mpas": 5.3,
        "appearance": "澄清透明",
        "label_confirmed": True,
    },
}


def seed_devices(repo: Repository):
    now = time.time()
    latest = {}
    device_map = {}

    def register(config: DeviceConfig, snapshot: StatusSnapshot):
        device_id = repo.upsert_device(config.name, config.type, config.alias)
        device_map[device_id] = config
        latest[device_id] = snapshot
        return device_id

    pump_id = register(
        DeviceConfig(
            name="pump-1",
            type="tyd02",
            alias="TYD02 注射泵",
            serial_port="COM3",
        ),
        StatusSnapshot(
            timestamp=now,
            state="running",
            work_mode="仅注入",
            device_id="pump-1",
            acc_volume=123.6,
            acc_unit="mL",
            progress_pct=68.0,
            metrics={
                "inject_rate": 0.5,
                "inject_rate_unit": "mL/min",
                "target_volume": 5,
                "target_unit": "mL",
                "syringe_name": "10 mL",
                "repeat_count": 1,
                "force": "中",
            },
        ),
    )
    viscometer_id = register(
        DeviceConfig(
            name="viscometer-1",
            type="viscometer",
            alias="WHD46-33 粘度计",
            serial_port="COM4",
        ),
        StatusSnapshot(
            timestamp=now,
            state="running",
            work_mode="连续测量",
            device_id="viscometer-1",
            temp_c=25.1,
            metrics={
                "viscosity_mPas": 5.3,
                "torque_pct": 51.2,
                "rotor": "18",
                "rpm": 60,
                "shear_rate_1s": 72.0,
                "shear_stress_pa": 0.38,
            },
        ),
    )
    stirrer_ids = []
    for index, (temp_c, speed, set_temp) in enumerate(
        [
            (40.1, 401, 40),
            (24.9, 350, 25),
            (3.2, 400, 4),
            (25.4, 300, 25),
            (39.8, 402, 40),
        ],
        start=1,
    ):
        stirrer_ids.append(
            register(
                DeviceConfig(
                    name=f"stirrer-{index}",
                    type="stirrer",
                    alias=f"HMS-C 搅拌器 {index}",
                    serial_port=f"COM{index + 4}",
                    modbus_addr=index,
                ),
                StatusSnapshot(
                    timestamp=now,
                    state="running",
                    work_mode="恒温搅拌" if set_temp == 40 else "搅拌",
                    device_id=f"stirrer-{index}",
                    temp_c=temp_c,
                    metrics={
                        "speed": speed,
                        "set_speed": 400 if index != 4 else 300,
                        "set_temp": set_temp,
                    },
                ),
            )
        )
    sensor_id = register(
        DeviceConfig(
            name="sensor-1",
            type="whd46",
            alias="WHD46 温湿度控制器",
            serial_port="COM10",
        ),
        StatusSnapshot(
            timestamp=now,
            state="running",
            work_mode="三通道采集",
            device_id="sensor-1",
            temp_c=24.8,
            metrics={
                "avg_humid_rh": 51.2,
                "ch1_temp_c": 24.8,
                "ch1_humid_rh": 51.1,
                "ch2_temp_c": 24.9,
                "ch2_humid_rh": 51.3,
                "ch3_temp_c": 24.7,
                "ch3_humid_rh": 51.2,
                "channels": [
                    {"channel": 1, "temp": 24.8, "humid": 51.1},
                    {"channel": 2, "temp": 24.9, "humid": 51.3},
                    {"channel": 3, "temp": 24.7, "humid": 51.2},
                ],
            },
        ),
    )
    return latest, device_map, {
        "pump": pump_id,
        "viscometer": viscometer_id,
        "stirrers": stirrer_ids,
        "sensor": sensor_id,
    }


def seed_runs(repo: Repository, device_ids: dict):
    now_ms = int(time.time() * 1000)
    pump_id = device_ids["pump"]
    run_ids = []
    for run_index, actual_volume in enumerate((5.0, 4.98, 5.02), start=1):
        started_ms = now_ms - (run_index * 5_400_000)
        run_id = repo.open_run(
            pump_id,
            1,
            started_ms,
            {
                "operator": "张三",
                "project_tag": "CEM 湿化学合成",
                "experiment_tag": f"20260724-CEM-0{run_index}",
                "work_mode": "仅注入",
                "syringe_code": "10 mL",
                "target_volume": 5,
                "target_volume_unit": "mL",
                "inject_rate": 0.5,
                "force": "中",
            },
        )
        for sample_index in range(21):
            progress = sample_index / 20
            repo.add_sample(
                run_id,
                pump_id,
                started_ms + sample_index * 30_000,
                "running" if sample_index < 20 else "stopped",
                0.5 if sample_index < 20 else 0,
                actual_volume * progress,
                25.0 + sample_index * 0.01,
                json.dumps(
                    {
                        "inject_rate": 0.5,
                        "target_volume": 5,
                        "progress_pct": progress * 100,
                    },
                    ensure_ascii=False,
                ),
            )
        ended_ms = started_ms + 600_000
        repo.add_event(
            pump_id,
            run_id,
            started_ms,
            "run_started",
            "info",
            json.dumps({"source": "device"}, ensure_ascii=False),
        )
        repo.add_event(
            pump_id,
            run_id,
            ended_ms,
            "run_completed",
            "info",
            json.dumps({"actual_volume": actual_volume}, ensure_ascii=False),
        )
        repo.close_run(
            run_id,
            ended_ms,
            "completed",
            actual_volume,
            "mL",
            110 + run_index * 5,
            "mL",
            0,
        )
        repo.tag_run(
            run_id,
            "张三",
            "CEM 湿化学合成",
            f"20260724-CEM-0{run_index}",
            "设备自动记录",
        )
        run_ids.append(run_id)

    sensor_id = device_ids["sensor"]
    for sample_index in range(50):
        ts_ms = now_ms - (49 - sample_index) * 60_000
        channels = [
            {
                "channel": channel,
                "temp": 24.6 + channel * 0.1 + sample_index * 0.003,
                "humid": 50.8 + channel * 0.1 + sample_index * 0.004,
            }
            for channel in range(1, 4)
        ]
        repo.add_sample(
            None,
            sensor_id,
            ts_ms,
            "running",
            None,
            None,
            sum(item["temp"] for item in channels) / 3,
            json.dumps(
                {"channels": channels, "avg_humid_rh": 51.2},
                ensure_ascii=False,
            ),
        )
    return run_ids


def create_experiment(
    service: R201Service,
    batch_id: str,
    reviewer: str = "",
):
    return service.create_experiment(
        {
            "batch_id": batch_id,
            "membrane_system": "CEM",
            "recipe_no": "R-CEM-001",
            "recipe_version": "V1.2",
            "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
            "sop_version": "V0.2",
            "target_viscosity_min_mpas": 4.8,
            "target_viscosity_max_mpas": 5.8,
            "operator": "张三",
            "reviewer": reviewer,
            "spec_snapshot": {
                "premix_rpm_min": 300,
                "premix_rpm_max": 500,
                "acid_rate_min_ml_min": 0.3,
                "acid_rate_max_ml_min": 1.0,
                "reaction_temp_min_c": 38,
                "reaction_temp_max_c": 42,
            },
            "recipe_parameters": [
                {
                    "parameter_code": "TEOS_TARGET",
                    "display_name": "TEOS 理论量",
                    "target_value": 10,
                    "unit": "mL",
                    "source": "R-CEM-001/V1.2",
                },
                {
                    "parameter_code": "ETHANOL_TARGET",
                    "display_name": "乙醇理论量",
                    "target_value": 20,
                    "unit": "mL",
                    "source": "R-CEM-001/V1.2",
                },
            ],
        }
    )


def advance_to_aging(service: R201Service, experiment_id: int):
    steps = [
        "R201-01",
        "R201-02",
        "R201-03",
        "R201-04",
        "R201-10",
        "R201-20",
        "R201-30",
        "R201-31",
        "R201-32",
        "R201-40",
    ]
    for index, step_code in enumerate(steps, start=1):
        current = service.get_experiment(experiment_id)["experiment"]
        service.start_step(
            experiment_id,
            step_code,
            current["row_version"],
            event(f"{experiment_id}-{step_code}-start-{index}"),
        )
        current = service.get_experiment(experiment_id)["experiment"]
        service.complete_step(
            experiment_id,
            step_code,
            current["row_version"],
            STEP_RESULTS[step_code],
            event(f"{experiment_id}-{step_code}-complete-{index}"),
        )
    current = service.get_experiment(experiment_id)["experiment"]
    service.start_step(
        experiment_id,
        "R201-50",
        current["row_version"],
        event(f"{experiment_id}-R201-50-start"),
    )
    for index, viscosity in enumerate((5.0, 5.3), start=1):
        captured_at_ms = int(time.time() * 1000)
        service.record_viscosity(
            experiment_id,
            {
                **event(f"{experiment_id}-viscosity-{index}"),
                "device_capture": {
                    "captured_at_server_ms": captured_at_ms,
                    "role_device_ids": {
                        "viscometer": 2,
                        "reaction_temp": 3,
                    },
                    "devices": [
                        {
                            "device_id": 2,
                            "name": "viscometer-1",
                            "alias": "WHD46-33 粘度计",
                            "type": "viscometer",
                            "age_ms": 0,
                            "snapshot": {
                                "state": "running",
                                "ts_ms": captured_at_ms,
                                "temp_c": 25.1,
                                "metrics": {
                                    "viscosity_mPas": viscosity,
                                    "torque_pct": 50 + index * 0.6,
                                    "rotor": "18",
                                    "rpm": 60,
                                    "shear_rate_1s": 72,
                                    "shear_stress_pa": 0.38,
                                },
                            },
                        },
                        {
                            "device_id": 3,
                            "name": "stirrer-1",
                            "alias": "HMS-C 搅拌器 1",
                            "type": "stirrer",
                            "age_ms": 0,
                            "snapshot": {
                                "state": "running",
                                "ts_ms": captured_at_ms,
                                "temp_c": 40.1,
                                "metrics": {"speed": 401},
                            },
                        },
                    ],
                },
            },
        )


def seed_experiments(repo: Repository):
    service = R201Service(repo)
    location = service.create_storage_location(
        {
            "location_code": "FRIDGE-01-A2",
            "display_name": "1 号冰箱 · A2",
            "storage_condition": "4 ℃",
            "actor": "张三",
        }
    )

    active = create_experiment(service, "20260724-CEM-01")
    advance_to_aging(service, active["id"])
    items = service.create_trace_items(
        active["id"],
        {
            **event("active-trace-items"),
            "item_type": "intermediate",
            "display_name": "陈化中间溶胶",
            "container_count": 2,
            "quantity": 50,
            "unit": "mL",
        },
    )
    service.transition_trace_item(
        items[0]["id"],
        "store",
        {
            **event("active-trace-store"),
            "location_code": location["location_code"],
            "hold_hours": 24,
        },
    )
    traceability = service.get_traceability(active["id"])
    service.request_trace_labels(
        active["id"],
        {
            **event("active-trace-print"),
            "item_ids": [item["id"] for item in traceability["items"]],
            "reason": "initial",
            "copies": 1,
        },
    )
    deviation = service.open_deviation(
        active["id"],
        {
            **event("active-deviation"),
            "description": "注射泵通讯短时中断 3 秒",
            "immediate_action": "检查转换器接线后恢复采集",
            "opened_by": "张三",
            "severity": "warning",
        },
    )
    current = service.get_experiment(active["id"])["experiment"]
    service.resolve_deviation(
        active["id"],
        deviation["id"],
        {
            **event("active-deviation-resolved"),
            "reviewed_by": "张三",
            "impact_assessment": "累计加入量连续，未影响本批加酸结果",
            "cause": "转换器接头松动",
            "disposition": "continue",
            "row_version": current["row_version"],
        },
    )

    released = create_experiment(
        service,
        "20260723-CEM-02",
        reviewer="李四",
    )
    advance_to_aging(service, released["id"])
    current = service.get_experiment(released["id"])["experiment"]
    service.complete_step(
        released["id"],
        "R201-50",
        current["row_version"],
        {"endpoint_confirmed": True},
        event("released-R201-50-complete"),
    )
    for step_code in ("R201-60", "R201-70"):
        current = service.get_experiment(released["id"])["experiment"]
        service.start_step(
            released["id"],
            step_code,
            current["row_version"],
            event(f"released-{step_code}-start"),
        )
        current = service.get_experiment(released["id"])["experiment"]
        service.complete_step(
            released["id"],
            step_code,
            current["row_version"],
            STEP_RESULTS[step_code],
            event(f"released-{step_code}-complete"),
        )
    current = service.get_experiment(released["id"])["experiment"]
    submitted = service.submit(
        released["id"],
        current["row_version"],
        actor="张三",
        client_event_id="released-submit",
    )
    service.review(
        released["id"],
        submitted["row_version"],
        action="release",
        reviewer="李四",
        client_event_id="released-review",
        disposition="合格",
    )
    return {"active": active["id"], "released": released["id"]}


def build_app():
    repo = Repository(":memory:")
    latest, device_map, device_ids = seed_devices(repo)
    run_ids = seed_runs(repo, device_ids)
    experiment_ids = seed_experiments(repo)
    app = create_app(
        ShowcaseEngine(latest, device_map),
        repo,
        secret_key="showcase-only",
    )

    @app.get("/showcase/experiments/<int:experiment_id>")
    @app.get("/showcase/experiments/<int:experiment_id>/<section>")
    def showcase_experiment(
        experiment_id: int,
        section: str | None = None,
    ):
        page_path = (
            Path(__file__).parents[2]
            / "lab_device_manager"
            / "web"
            / "static"
            / "experiment.html"
        )
        html = page_path.read_text(encoding="utf-8")
        html = html.replace(
            '<details id="devicePanel" class="panel">',
            '<details id="devicePanel" class="panel" open>',
        )
        html = html.replace(
            '<details id="tracePanel" class="panel trace-panel">',
            '<details id="tracePanel" class="panel trace-panel" open>',
        )
        target = {
            "devices": "devicePanel",
            "trace": "tracePanel",
            "timeline": "timelinePanel",
        }.get(section or "")
        bootstrap = (
            "<script>"
            f"history.replaceState(null,'','/experiments/{experiment_id}');"
        )
        if target:
            bootstrap += (
                "setTimeout(()=>document.getElementById("
                f"'{target}')?.scrollIntoView({{block:'start'}}),1200);"
            )
        bootstrap += "</script>"
        html = html.replace(
            '<script src="/static/experiment.js"></script>',
            bootstrap + '<script src="/static/experiment.js"></script>',
        )
        return Response(html, mimetype="text/html")

    @app.get("/showcase/labels/<int:experiment_id>")
    def showcase_labels(experiment_id: int):
        service = R201Service(repo)
        traceability = service.get_traceability(experiment_id)
        experiment = service.get_experiment(experiment_id)["experiment"]
        html = trace_labels_html(
            traceability["items"],
            experiment,
            copies=1,
        )
        html = html.replace(
            "</style>",
            ".sheet{justify-content:start!important;justify-items:start!important}</style>",
        )
        return Response(html, mimetype="text/html")

    @app.get("/showcase/device/<int:device_id>")
    def showcase_device(device_id: int):
        page_path = (
            Path(__file__).parents[2]
            / "lab_device_manager"
            / "web"
            / "static"
            / "device.html"
        )
        html = page_path.read_text(encoding="utf-8")
        html = html.replace(
            "</head>",
            "<style>body{zoom:.5}</style></head>",
        )
        html = html.replace(
            '<script src="/static/device.js"></script>',
            (
                "<script>"
                f"history.replaceState(null,'','/device/{device_id}');"
                "</script>"
                '<script src="/static/device.js"></script>'
            ),
        )
        return Response(html, mimetype="text/html")

    app.config["SHOWCASE_IDS"] = {
        "devices": device_ids,
        "runs": run_ids,
        "experiments": experiment_ids,
    }
    return app


if __name__ == "__main__":
    build_app().run(host="127.0.0.1", port=7801, debug=False)
