from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from datetime import date, datetime
from typing import Callable, Optional


MAIN_STEPS = (
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
    "R201-50",
    "R201-60",
    "R201-70",
)

NEXT_STEP = {
    step: MAIN_STEPS[index + 1] if index + 1 < len(MAIN_STEPS) else "R201-80"
    for index, step in enumerate(MAIN_STEPS)
}

STEP_LABELS = {
    "R201-01": "环境与设备确认",
    "R201-02": "器皿干燥确认",
    "R201-03": "物料确认",
    "R201-04": "酸水配制",
    "R201-10": "TEOS/功能硅烷预混",
    "R201-20": "加入乙醇搅拌",
    "R201-30": "冰浴与注射泵准备",
    "R201-31": "注射泵加酸水",
    "R201-32": "加完后平衡",
    "R201-40": "冷凝回流与升温",
    "R201-50": "40℃恒温陈化",
    "R201-60": "停止加热并降温",
    "R201-70": "过滤、出料与转序",
    "R201-80": "完成实验并锁定记录",
    "R201-90": "实验记录已完成",
}

TRUSTED_EVENT_TIME_SKEW_MS = 30_000

REQUIRED_RESULT_FIELDS = {
    "R201-01": ("environment_temp_c", "environment_humidity_rh", "device_checks"),
    "R201-02": ("glassware_items",),
    "R201-03": ("materials",),
    "R201-04": (
        "hcl_c1_mol_l",
        "hcl_c2_mol_l",
        "hcl_v1_ml",
        "hcl_v2_ml",
        "acid_into_water",
    ),
    "R201-10": ("appearance", "actual_rpm"),
    "R201-20": ("ethanol_actual_ml", "appearance", "actual_rpm"),
    "R201-30": (
        "ice_bath_confirmed",
        "reaction_temp_c",
        "syringe_spec",
        "target_volume_ml",
        "target_rate_ml_min",
        "line_purged",
    ),
    "R201-31": (
        "acc_volume_start",
        "acc_volume_end",
        "acc_volume_unit",
        "target_volume_ml",
    ),
    "R201-32": ("balance_minutes",),
    "R201-40": (
        "condenser_confirmed",
        "moisture_protection_confirmed",
        "reached_temp_c",
    ),
    "R201-50": ("endpoint_confirmed",),
    "R201-60": ("room_temp_confirmed", "cooling_minutes"),
    "R201-70": (
        "filter_spec",
        "container_tare_g",
        "container_gross_g",
        "transfer_viscosity_mpas",
        "appearance",
        "label_confirmed",
    ),
}

VISCOSITY_FIELDS = (
    "viscosity_mpas",
    "sample_temp_c",
    "reaction_temp_c",
    "rotor",
    "rpm",
    "torque_pct",
)

PROCESS_DEVICE_BINDINGS = {
    "tyd02": {"acid_pump": "acc_volume"},
    "stirrer": {
        "stirrer": "speed",
        "reaction_temp": "temp_c",
    },
    "viscometer": {"viscometer": "viscosity_mPas"},
    "whd46": {"environment": "temp_c"},
}


class R201Error(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[dict] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class R201Service:
    def __init__(
        self,
        repo,
        clock_ms: Optional[Callable[[], int]] = None,
    ):
        self.repo = repo
        self.store = repo.experiments
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def create_experiment(self, data: dict) -> dict:
        client_event_id = data.get("client_event_id")
        if client_event_id:
            existing_event = self.store.get_event_by_client_id(
                str(client_event_id)
            )
            if existing_event is not None:
                if (
                    existing_event["event_type"] != "experiment_created"
                    or existing_event["payload"].get("batch_id")
                    != str(data.get("batch_id", "")).upper()
                ):
                    raise R201Error(
                        "client_event_id already belongs to another operation",
                        409,
                    )
                existing = self.store.get_experiment(
                    existing_event["experiment_id"]
                )
                if existing is None:
                    raise R201Error(
                        "idempotent experiment result is unavailable", 409
                    )
                return existing
        required = (
            "batch_id",
            "membrane_system",
            "recipe_no",
            "recipe_version",
            "sop_code",
            "sop_version",
            "target_viscosity_min_mpas",
            "target_viscosity_max_mpas",
            "operator",
        )
        self._require_fields(data, required)
        system = str(data["membrane_system"]).upper()
        if system not in ("CEM", "AEM"):
            raise R201Error("membrane_system must be CEM or AEM")
        batch_id = str(data["batch_id"]).upper()
        if not re.fullmatch(r"\d{8}-(CEM|AEM)-[A-Z0-9]{2,}", batch_id):
            raise R201Error("batch_id must match YYYYMMDD-CEM-XX or YYYYMMDD-AEM-XX")
        if f"-{system}-" not in batch_id:
            raise R201Error("batch_id membrane system does not match membrane_system")
        low = self._number(data["target_viscosity_min_mpas"], "target viscosity minimum")
        high = self._number(data["target_viscosity_max_mpas"], "target viscosity maximum")
        if low <= 0 or high <= low:
            raise R201Error("target viscosity range is invalid")
        reviewer = str(data.get("reviewer") or "").strip()
        if reviewer and str(data["operator"]).strip() == reviewer:
            raise R201Error("operator and reviewer must be different")
        recipe_parameters = data.get("recipe_parameters") or []
        if not isinstance(recipe_parameters, list):
            raise R201Error("recipe_parameters must be a list")
        for parameter in recipe_parameters:
            self._require_fields(
                parameter,
                ("parameter_code", "display_name", "unit", "source"),
            )
        payload = dict(data)
        payload["batch_id"] = batch_id
        payload["membrane_system"] = system
        payload["reviewer"] = reviewer
        payload["target_viscosity_min_mpas"] = low
        payload["target_viscosity_max_mpas"] = high
        snapshot_bytes = json.dumps(
            {
                "recipe_parameters": recipe_parameters,
                "spec_snapshot": data.get("spec_snapshot") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["snapshot_sha256"] = hashlib.sha256(snapshot_bytes).hexdigest()
        payload["recipe_parameters"] = recipe_parameters
        event = self._event(
            {
                **data,
                "client_event_id": (
                    data.get("client_event_id") or f"create-{uuid.uuid4()}"
                ),
                "actor": data["operator"],
                "actor_user_id": data.get("operator_user_id"),
            },
            "experiment_created",
            {
                "batch_id": batch_id,
                "membrane_system": system,
                "validation_mode": "parallel_validation",
            },
        )
        created = self.store.create_experiment(
            payload,
            now_ms=event["received_at_server_ms"],
            event=event,
        )
        return created

    def list_experiments(self) -> list[dict]:
        return self.store.list_experiments()

    def experiment_revision(self, experiment_id: int) -> dict:
        try:
            return self.store.experiment_revision(experiment_id)
        except LookupError as exc:
            raise R201Error(str(exc), 404) from exc

    def create_material_container(self, data: dict) -> dict:
        self._require_fields(
            data,
            (
                "container_code",
                "material_name",
                "created_by",
                "client_event_id",
            ),
        )
        container_code = str(data["container_code"]).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{2,63}", container_code):
            raise R201Error("container_code format is invalid")
        quantity = data.get("quantity_remaining")
        if quantity not in (None, ""):
            quantity = self._number(quantity, "quantity_remaining")
            if quantity < 0:
                raise R201Error("quantity_remaining cannot be negative")
            if not str(data.get("unit") or "").strip():
                raise R201Error("有余量时必须填写单位")
        material_name = str(data["material_name"]).strip()
        if not material_name:
            raise R201Error("material_name is required")
        expires_on = str(data.get("expires_on") or "").strip() or None
        if expires_on:
            try:
                expiry_date = datetime.strptime(
                    expires_on, "%Y-%m-%d"
                ).date()
            except ValueError as exc:
                raise R201Error("有效期必须使用 YYYY-MM-DD 格式") from exc
            if expiry_date < date.today():
                raise R201Error("原材料有效期已过，不能登记为可用容器")
        payload = {
            **data,
            "container_code": container_code,
            "external_barcode": (
                str(data.get("external_barcode") or "").strip().upper()
                or None
            ),
            "material_name": material_name,
            "expires_on": expires_on,
            "quantity_remaining": quantity,
            "unit": str(data.get("unit") or "").strip() or None,
            "status": "available",
            "created_at_ms": self.clock_ms(),
        }
        try:
            return self.store.create_material_container(payload)
        except sqlite3.IntegrityError as exc:
            raise R201Error(
                "原材料容器编号或供应商条码已存在", 409
            ) from exc

    def list_material_containers(self) -> list[dict]:
        self.store.expire_material_containers(
            date.today().isoformat(), self.clock_ms()
        )
        return self.store.list_material_containers()

    def resolve_scan_code(self, code: str) -> dict:
        normalized = str(code or "").strip()
        if "/scan/" in normalized:
            normalized = normalized.rsplit("/scan/", 1)[1]
        normalized = normalized.removeprefix("PURICORE:")
        self.store.expire_material_containers(
            date.today().isoformat(), self.clock_ms()
        )
        material = self.store.get_material_container_by_code(
            normalized.upper()
        )
        if material is not None:
            return {"kind": "material_container", "material": material}
        return self.lookup_trace_code(normalized)

    def claim_viscometer(
        self, experiment_id: int, device_id: int, data: dict
    ) -> dict:
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        active = self.store.get_active_step(experiment_id)
        if (
            experiment["current_step_code"] != "R201-50"
            or active is None
            or active["step_code"] != "R201-50"
        ):
            raise R201Error("当前批次尚未进入粘度测量阶段", 409)
        try:
            return self.store.claim_measurement_device(
                experiment_id,
                device_id,
                str(data["actor"]),
                data.get("actor_user_id"),
                self.clock_ms(),
            )
        except RuntimeError as exc:
            raise R201Error(str(exc), 409) from exc

    def release_viscometer(
        self, experiment_id: int, device_id: int, data: dict
    ) -> dict:
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        released = self.store.release_measurement_device(
            experiment_id, device_id, self.clock_ms()
        )
        return {"released": bool(released)}

    def suggest_batch_id(self, membrane_system: str, date_text: str) -> str:
        system = str(membrane_system).upper().strip()
        if system not in ("CEM", "AEM"):
            raise R201Error("membrane_system must be CEM or AEM")
        try:
            datetime.strptime(str(date_text), "%Y%m%d")
        except ValueError as exc:
            raise R201Error("date must match YYYYMMDD") from exc
        prefix = f"{date_text}-{system}-"
        sequence = self.store.max_numeric_batch_sequence(prefix)
        return f"{prefix}{sequence + 1:02d}"

    def get_experiment(self, experiment_id: int) -> dict:
        experiment = self.store.get_experiment(experiment_id)
        if experiment is None:
            raise R201Error("experiment not found", 404)
        measurements = self.store.list_measurements(experiment_id)
        steps = self.store.list_steps(experiment_id)
        events = self.store.list_experiment_events(experiment_id)
        telemetry = self._telemetry_summary(experiment_id, events)
        return {
            "experiment": experiment,
            "steps": steps,
            "active_step": self.store.get_active_step(experiment_id),
            "events": events,
            "measurements": measurements,
            "materials": self.store.list_material_usages(experiment_id),
            "recipe_parameters": self.store.list_recipe_parameters(experiment_id),
            "data_sources": self.store.list_data_source_bindings(experiment_id),
            "deviations": self.store.list_deviations(experiment_id),
            "device_reservations": self.store.list_device_reservations(
                experiment_id
            ),
            "endpoint_ready": self._endpoint_ready(
                experiment, measurements, steps
            ),
            **telemetry,
            "step_labels": STEP_LABELS,
        }

    def get_traceability(self, experiment_id: int) -> dict:
        experiment = self._get(experiment_id)
        self.store.ensure_batch_trace_item(
            experiment_id,
            now_ms=experiment["created_at_ms"],
            actor=experiment["operator"],
        )
        return {
            "items": self.store.list_trace_items(experiment_id),
            "locations": self.store.list_storage_locations(),
            "print_jobs": self.store.list_label_print_jobs(experiment_id),
        }

    def create_storage_location(self, data: dict) -> dict:
        self._require_fields(
            data, ("location_code", "display_name", "actor")
        )
        location_code = str(data["location_code"]).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,31}", location_code):
            raise R201Error(
                "location_code must use 2-32 letters, numbers, - or _"
            )
        display_name = str(data["display_name"]).strip()
        actor = str(data["actor"]).strip()
        if not actor:
            raise R201Error("actor is required")
        return self.store.create_storage_location(
            {
                "location_code": location_code,
                "display_name": display_name,
                "storage_condition": (
                    str(data.get("storage_condition") or "").strip() or None
                ),
                "actor": actor,
                "created_at_ms": self.clock_ms(),
            }
        )

    def list_storage_locations(self) -> list[dict]:
        return self.store.list_storage_locations()

    def create_trace_items(
        self, experiment_id: int, data: dict
    ) -> list[dict]:
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        self._require_fields(data, ("item_type", "client_event_id"))
        item_type = str(data["item_type"]).strip()
        if item_type not in ("intermediate", "final_product"):
            raise R201Error(
                "item_type must be intermediate or final_product"
            )
        try:
            container_count = int(data.get("container_count", 1))
        except (TypeError, ValueError) as exc:
            raise R201Error("container_count must be an integer") from exc
        if not 1 <= container_count <= 20:
            raise R201Error("container_count must be between 1 and 20")
        quantity = data.get("quantity")
        if quantity not in (None, ""):
            quantity = self._number(quantity, "quantity")
            if quantity <= 0:
                raise R201Error("quantity must be positive")
        unit = str(data.get("unit") or "").strip() or None
        source_step = experiment["current_step_code"]
        if item_type == "intermediate":
            default_name = (
                f"{STEP_LABELS.get(source_step, source_step)}中间品"
            )
        else:
            default_name = (
                f"{experiment['membrane_system']} 功能膜成品"
            )
        display_name = (
            str(data.get("display_name") or "").strip() or default_name
        )
        event = self._event(
            data,
            "trace_items_created",
            {
                "item_type": item_type,
                "container_count": container_count,
                "source_step_code": source_step,
            },
        )
        try:
            return self.store.create_trace_items(
                experiment_id,
                {
                    "item_type": item_type,
                    "display_name": display_name,
                    "source_step_code": (
                        source_step if item_type == "intermediate" else None
                    ),
                    "container_count": container_count,
                    "quantity": quantity,
                    "unit": unit,
                    "creation_group_id": event["client_event_id"],
                    "actor": event["actor"],
                    "effective_at_ms": event["effective_at_ms"],
                },
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            raise R201Error(str(exc), 409) from exc

    def transition_trace_item(
        self, trace_item_id: int, action: str, data: dict
    ) -> dict:
        item = self.store.get_trace_item(trace_item_id)
        if item is None:
            raise R201Error("trace item not found", 404)
        experiment = self._get(item["experiment_id"])
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        if action not in ("store", "retrieve"):
            raise R201Error("unsupported trace item action")
        event = self._event(
            data,
            f"trace_item_{action}d",
            {"trace_item_id": trace_item_id},
        )
        location_code = None
        hold_until_ms = None
        if action == "store":
            self._require_fields(data, ("location_code",))
            location_code = str(data["location_code"]).strip().upper()
            hold_hours = data.get("hold_hours")
            if hold_hours not in (None, ""):
                hours = self._number(hold_hours, "hold_hours")
                if hours <= 0:
                    raise R201Error("hold_hours must be positive")
                hold_until_ms = (
                    event["effective_at_ms"] + int(hours * 3_600_000)
                )
        try:
            return self.store.transition_trace_item(
                trace_item_id,
                {
                    "action": action,
                    "location_code": location_code,
                    "hold_until_ms": hold_until_ms,
                    "client_event_id": event["client_event_id"],
                    "actor": event["actor"],
                    "effective_at_ms": event["effective_at_ms"],
                },
            )
        except LookupError as exc:
            raise R201Error(str(exc), 404) from exc
        except (RuntimeError, ValueError) as exc:
            raise R201Error(str(exc), 409) from exc

    def request_trace_labels(
        self, experiment_id: int, data: dict
    ) -> list[dict]:
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        self._require_fields(data, ("item_ids", "client_event_id"))
        item_ids = data["item_ids"]
        if not isinstance(item_ids, list) or not item_ids:
            raise R201Error("item_ids must contain at least one item")
        if len(item_ids) > 20:
            raise R201Error("cannot print more than 20 labels at once")
        reason = str(data.get("reason") or "initial").strip()
        if reason not in ("initial", "reprint"):
            raise R201Error("reason must be initial or reprint")
        try:
            copies = int(data.get("copies", 1))
        except (TypeError, ValueError) as exc:
            raise R201Error("copies must be an integer") from exc
        if not 1 <= copies <= 20:
            raise R201Error("copies must be between 1 and 20")
        requested_at_ms = self.clock_ms()
        jobs = []
        for index, raw_item_id in enumerate(item_ids, start=1):
            try:
                item_id = int(raw_item_id)
            except (TypeError, ValueError) as exc:
                raise R201Error("item_ids must be integers") from exc
            item = self.store.get_trace_item(item_id)
            if item is None or item["experiment_id"] != experiment_id:
                raise R201Error("trace item does not belong to experiment")
            jobs.append(
                self.store.request_trace_label_print(
                    item_id,
                    {
                        "client_event_id": (
                            f"{data['client_event_id']}:{index}"
                        ),
                        "reason": reason,
                        "copies": copies,
                        "actor": str(data["actor"]),
                        "requested_at_ms": requested_at_ms,
                    },
                )
            )
        return jobs

    def request_location_label(
        self, location_id: int, data: dict
    ) -> dict:
        self._require_fields(data, ("actor", "client_event_id"))
        reason = str(data.get("reason") or "initial")
        if reason not in ("initial", "reprint"):
            raise R201Error("reason must be initial or reprint")
        try:
            copies = int(data.get("copies", 1))
        except (TypeError, ValueError) as exc:
            raise R201Error("copies must be an integer") from exc
        if not 1 <= copies <= 20:
            raise R201Error("copies must be between 1 and 20")
        try:
            return self.store.request_location_label_print(
                location_id,
                {
                    "client_event_id": str(data["client_event_id"]),
                    "reason": reason,
                    "copies": copies,
                    "actor": str(data["actor"]),
                    "requested_at_ms": self.clock_ms(),
                },
            )
        except LookupError as exc:
            raise R201Error(str(exc), 404) from exc

    def lookup_trace_code(self, code: str) -> dict:
        normalized = str(code or "").strip().upper()
        if normalized.startswith("PURICORE:"):
            normalized = normalized.split(":", 1)[1]
        item = self.store.get_trace_item_by_code(normalized)
        if item is not None:
            return {"kind": "trace_item", "item": item}
        location = self.store.get_storage_location_by_code(normalized)
        if location is not None:
            return {"kind": "storage_location", "location": location}
        raise R201Error("trace code not found", 404)

    def evaluate_telemetry(self, experiment_id: int) -> dict:
        experiment = self._get(experiment_id)
        active = self.store.get_active_step(experiment_id)
        events = self.store.list_experiment_events(experiment_id)
        summary = self._telemetry_summary(experiment_id, events)
        with self.store.transaction() as conn:
            if (
                active is not None
                and active["step_code"] in ("R201-40", "R201-50")
                and summary["telemetry_gaps"]
            ):
                self._record_telemetry_gap_deviations(
                    experiment,
                    active,
                    summary["telemetry_gaps"],
                    connection=conn,
                )
                events = self.store.list_experiment_events(experiment_id)
                summary = self._telemetry_summary(experiment_id, events)
            if active is not None and active["step_code"] == "R201-40":
                spec = experiment["spec_snapshot"]
                required = spec.get("reach_temp_consecutive_samples")
                lower = spec.get("reaction_temp_min_c")
                upper = spec.get("reaction_temp_max_c")
                if (
                    required is not None
                    and lower is not None
                    and upper is not None
                    and summary["temperature_series"]
                ):
                    required_count = int(
                        self._number(
                            required, "reach_temp_consecutive_samples"
                        )
                    )
                    if required_count <= 0:
                        raise R201Error(
                            "reach_temp_consecutive_samples must be positive"
                        )
                    recent = summary["temperature_series"][-required_count:]
                    ready = len(recent) == required_count and all(
                        self._number(lower, "reaction_temp_min_c")
                        <= point["value"]
                        <= self._number(upper, "reaction_temp_max_c")
                        for point in recent
                    )
                    event_id = (
                        f"derived-reached-temperature-{experiment_id}-"
                        f"{active['id']}"
                    )
                    if (
                        ready
                        and self.store.get_event_by_client_id(
                            event_id, connection=conn
                        )
                        is None
                    ):
                        point = recent[-1]
                        self.store.add_experiment_event(
                            experiment_id,
                            {
                                "client_event_id": event_id,
                                "event_type": "reached_temperature",
                                "occurred_at_client_ms": None,
                                "received_at_server_ms": self.clock_ms(),
                                "client_clock_offset_ms": None,
                                "clock_sync_status": "server",
                                "effective_at_ms": point["ts_ms"],
                                "actor": "system",
                                "source_type": "derived",
                                "payload": {
                                    "sample_id": point["sample_id"],
                                    "value": point["value"],
                                    "metric_key": point["metric_key"],
                                    "device_id": point["device_id"],
                                    "range_min_c": float(lower),
                                    "range_max_c": float(upper),
                                    "consecutive_samples": required_count,
                                },
                            },
                            active["id"],
                            connection=conn,
                        )
                    return self._telemetry_summary(
                        experiment_id,
                        self.store.list_experiment_events(experiment_id),
                    )
            if (
                active is None
                or active["step_code"] != "R201-50"
                or not summary["temperature_series"]
            ):
                return summary
            interval_minutes = self._number(
                experiment["spec_snapshot"].get(
                    "aging_checkpoint_interval_min", 20
                ),
                "aging_checkpoint_interval_min",
            )
            if interval_minutes <= 0:
                raise R201Error(
                    "aging_checkpoint_interval_min must be positive"
                )
            interval_ms = int(interval_minutes * 60_000)
            start_ms = active["started_effective_at_ms"]
            latest_ms = summary["temperature_series"][-1]["ts_ms"]
            checkpoint_count = max(
                0, (latest_ms - start_ms) // interval_ms
            )
            for index in range(1, checkpoint_count + 1):
                due_ms = start_ms + index * interval_ms
                event_id = (
                    f"derived-aging-checkpoint-{experiment_id}-"
                    f"{active['id']}-{index}"
                )
                if (
                    self.store.get_event_by_client_id(
                        event_id, connection=conn
                    )
                    is not None
                ):
                    continue
                point = min(
                    summary["temperature_series"],
                    key=lambda item: abs(item["ts_ms"] - due_ms),
                )
                payload = {
                    "checkpoint_index": index,
                    "due_at_ms": due_ms,
                    "sample_id": point["sample_id"],
                    "sampled_at_ms": point["ts_ms"],
                    "metric_key": point["metric_key"],
                    "value": point["value"],
                    "device_id": point["device_id"],
                }
                self.store.add_experiment_event(
                    experiment_id,
                    {
                        "client_event_id": event_id,
                        "event_type": "aging_temperature_checkpoint",
                        "occurred_at_client_ms": None,
                        "received_at_server_ms": self.clock_ms(),
                        "client_clock_offset_ms": None,
                        "clock_sync_status": "server",
                        "effective_at_ms": due_ms,
                        "actor": "system",
                        "source_type": "derived",
                        "payload": payload,
                    },
                    active["id"],
                    connection=conn,
                )
            return self._telemetry_summary(
                experiment_id,
                self.store.list_experiment_events(experiment_id),
            )

    def start_step(
        self,
        experiment_id: int,
        step_code: str,
        expected_version: int,
        request_data: dict,
    ) -> dict:
        duplicate = self._duplicate_result(
            experiment_id, request_data, step_code, "step_started"
        )
        if duplicate is not None:
            return duplicate
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment,
            request_data.get("actor"),
            request_data.get("actor_user_id"),
        )
        self._check_version(experiment, expected_version)
        if step_code not in MAIN_STEPS:
            raise R201Error("step cannot be started directly")
        if experiment["current_step_code"] != step_code:
            raise R201Error(
                f"current step is {experiment['current_step_code']}, not {step_code}",
                409,
            )
        if experiment["status"] in ("pending_review", "released", "terminated"):
            raise R201Error(f"experiment status {experiment['status']} cannot start a step", 409)
        if self.store.get_active_step(experiment_id) is not None:
            raise R201Error("another step is already active", 409)
        event = self._event(request_data, "step_started", {"step_code": step_code})
        event["payload"]["device_capture"] = request_data.get(
            "device_capture"
        ) or {}
        try:
            updated, step = self.store.start_step(
                experiment_id,
                step_code,
                expected_version,
                event["effective_at_ms"],
                event["actor"],
                event,
            )
        except (RuntimeError, sqlite3.IntegrityError) as exc:
            raise R201Error(str(exc), 409) from exc
        return {"experiment": updated, "step": step}

    def preview_step_completion(
        self,
        experiment_id: int,
        step_code: str,
        expected_version: int,
        result: dict,
        request_data: dict,
    ) -> dict:
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment,
            request_data.get("actor"),
            request_data.get("actor_user_id"),
        )
        self._check_version(experiment, expected_version)
        if experiment["current_step_code"] != step_code:
            raise R201Error(
                f"当前步骤是 {experiment['current_step_code']}，不是 {step_code}",
                409,
            )
        active = self.store.get_active_step(experiment_id)
        if active is None or active["step_code"] != step_code:
            raise R201Error("请先开始当前步骤", 409)
        preview_event = self._event(
            request_data,
            "step_completion_previewed",
            {"step_code": step_code},
        )
        prepared = self._derive_step_result(
            experiment,
            active,
            step_code,
            dict(result or {}),
            request_data.get("device_capture") or {},
            preview_event["effective_at_ms"],
        )
        missing = [
            field
            for field in REQUIRED_RESULT_FIELDS[step_code]
            if field not in prepared
            or prepared[field] is None
            or prepared[field] == ""
        ]
        findings = []
        if not missing:
            self._validate_step_result(step_code, prepared)
            if step_code == "R201-31":
                self._calculate_dose_result(prepared)
            findings = self._rule_findings(
                experiment, step_code, prepared
            )
        return {
            "result": prepared,
            "missing_fields": missing,
            "findings": findings,
        }

    def complete_step(
        self,
        experiment_id: int,
        step_code: str,
        expected_version: int,
        result: dict,
        request_data: dict,
    ) -> dict:
        duplicate = self._duplicate_result(
            experiment_id, request_data, step_code, "step_completed"
        )
        if duplicate is not None:
            return duplicate
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment,
            request_data.get("actor"),
            request_data.get("actor_user_id"),
        )
        self._check_version(experiment, expected_version)
        if experiment["current_step_code"] != step_code:
            raise R201Error(
                f"current step is {experiment['current_step_code']}, not {step_code}",
                409,
            )
        active = self.store.get_active_step(experiment_id)
        if active is None or active["step_code"] != step_code:
            raise R201Error("step is not active", 409)
        event = self._event(
            request_data,
            "step_completed",
            {"step_code": step_code},
        )
        capture = request_data.get("device_capture") or {}
        result = self._derive_step_result(
            experiment,
            active,
            step_code,
            dict(result or {}),
            capture,
            event["effective_at_ms"],
        )
        self._require_fields(result, REQUIRED_RESULT_FIELDS[step_code])
        self._validate_step_result(step_code, result)
        if step_code == "R201-03":
            names = {
                str(item.get("name", "")).strip().upper()
                for item in result["materials"]
                if isinstance(item, dict)
            }
            if (
                experiment["membrane_system"] == "CEM"
                and "TMAPS" in names
            ):
                raise R201Error("CEM batch cannot use TMAPS")
            if (
                experiment["membrane_system"] == "AEM"
                and "MPTES" in names
            ):
                raise R201Error("AEM batch cannot use MPTES")
        if step_code == "R201-04":
            c1v1 = self._number(
                result["hcl_c1_mol_l"], "hcl_c1_mol_l"
            ) * self._number(result["hcl_v1_ml"], "hcl_v1_ml")
            c2v2 = self._number(
                result["hcl_c2_mol_l"], "hcl_c2_mol_l"
            ) * self._number(result["hcl_v2_ml"], "hcl_v2_ml")
            if c2v2 <= 0:
                raise R201Error("acid dilution target must be positive")
            result["acid_calculation_c1v1"] = c1v1
            result["acid_calculation_c2v2"] = c2v2
            result["acid_calculation_error_pct"] = (
                abs(c1v1 - c2v2) / c2v2 * 100
            )
            tolerance = experiment["spec_snapshot"].get(
                "acid_calculation_tolerance_pct"
            )
            if (
                tolerance is not None
                and result["acid_calculation_error_pct"]
                > self._number(
                    tolerance, "acid_calculation_tolerance_pct"
                )
            ):
                raise R201Error(
                    "acid dilution calculation exceeds batch tolerance"
                )
        if step_code == "R201-31":
            self._calculate_dose_result(result)
        if step_code == "R201-50":
            detail = self.get_experiment(experiment_id)
            if not detail["endpoint_ready"]:
                raise R201Error("viscosity endpoint is not stable and in target range", 409)
            if result["endpoint_confirmed"] is not True:
                raise R201Error("endpoint_confirmed must be true")
        if step_code == "R201-70":
            tare = self._number(
                result["container_tare_g"], "container_tare_g"
            )
            gross = self._number(
                result["container_gross_g"], "container_gross_g"
            )
            if gross <= tare:
                raise R201Error(
                    "container_gross_g must be greater than container_tare_g"
                )
            result["net_product_mass_g"] = gross - tare
        event["payload"]["result"] = result
        event["payload"]["device_capture"] = capture
        findings = self._rule_findings(experiment, step_code, result)
        confirmed_codes = {
            str(value)
            for value in request_data.get("confirmed_finding_codes") or []
        }
        if request_data.get("require_finding_confirmation"):
            unconfirmed = [
                finding
                for finding in findings
                if finding["rule_code"] not in confirmed_codes
            ]
            if unconfirmed:
                raise R201Error(
                    "检测到数据超出建议范围，请确认数据是否正确",
                    409,
                    {
                        "code": "FINDING_CONFIRMATION_REQUIRED",
                        "findings": unconfirmed,
                        "result": result,
                    },
                )
        materials = []
        if step_code == "R201-03":
            materials = self._normalize_materials(
                result["materials"], event["effective_at_ms"], event["actor"]
            )
        deviations = []
        for finding in findings:
            operator_confirmed = (
                finding["rule_code"] in confirmed_codes
                and not str(experiment.get("reviewer") or "").strip()
                and finding.get("severity") == "warning"
            )
            deviation_event = {
                **event,
                "client_event_id": (
                    f"{event['client_event_id']}:deviation:{finding['rule_code']}"
                ),
                "event_type": "deviation_opened",
                "received_at_server_ms": self.clock_ms(),
                "actor": "system",
                "source_type": "derived",
                "payload": {
                    "rule_code": finding["rule_code"],
                    "description": finding["description"],
                    "operator_confirmed": operator_confirmed,
                },
            }
            deviations.append(
                {
                    **finding,
                    "opened_at_ms": event["effective_at_ms"],
                    "opened_by": "system",
                    "status": "closed" if operator_confirmed else "open",
                    "impact_assessment": (
                        "操作员已核对自动采集数据，确认数据无误并继续实验"
                        if operator_confirmed
                        else None
                    ),
                    "disposition": (
                        "continue" if operator_confirmed else None
                    ),
                    "reviewed_by": None,
                    "reviewed_at_ms": (
                        event["effective_at_ms"]
                        if operator_confirmed
                        else None
                    ),
                    "event": deviation_event,
                }
            )
        try:
            updated, step, created_deviations = self.store.complete_step(
                experiment_id,
                step_code,
                expected_version,
                NEXT_STEP[step_code],
                result,
                event["effective_at_ms"],
                event["actor"],
                event,
                deviations=deviations,
                materials=materials,
            )
        except (RuntimeError, sqlite3.IntegrityError) as exc:
            raise R201Error(str(exc), 409) from exc
        return {
            "experiment": updated,
            "step": step,
            "deviations": created_deviations,
            "materials": materials,
        }

    def record_viscosity(self, experiment_id: int, data: dict) -> dict:
        data = dict(data or {})
        self._require_fields(data, ("client_event_id", "actor"))
        experiment = self._get(experiment_id)
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        duplicate = self._idempotent_event(
            experiment_id,
            data["client_event_id"],
            "viscosity_recorded",
        )
        if duplicate is not None:
            detail = self.get_experiment(experiment_id)
            measurement = next(
                (
                    item
                    for item in detail["measurements"]
                    if item["client_event_id"] == data["client_event_id"]
                ),
                None,
            )
            if measurement is None:
                raise R201Error(
                    "idempotent viscosity result is unavailable", 409
                )
            return {
                "experiment": detail["experiment"],
                "measurement": measurement,
                "endpoint_ready": detail["endpoint_ready"],
            }
        capture = data.get("device_capture") or {}
        viscometer = self._capture_device(
            capture,
            "viscometer",
            "viscometer",
        )
        stirrer = self._capture_device(
            capture,
            "stirrer",
            "reaction_temp",
        )
        provenance = {}

        def capture_field(field: str, device: Optional[dict], *keys):
            if data.get(field) not in (None, ""):
                return
            value, metric_key = self._capture_value(device, *keys)
            if value in (None, ""):
                return
            data[field] = value
            snapshot = device.get("snapshot") or {}
            provenance[field] = {
                "source_type": "device_confirmed",
                "device_id": device.get("device_id"),
                "device_name": device.get("alias") or device.get("name"),
                "metric_key": metric_key,
                "sample_id": device.get("sample_id"),
                "sampled_at_ms": snapshot.get("ts_ms"),
            }

        capture_field(
            "viscosity_mpas",
            viscometer,
            "viscosity_mPas",
            "viscosity_mpas",
        )
        capture_field(
            "sample_temp_c", viscometer, "temp_c", "sample_temp_c"
        )
        capture_field(
            "torque_pct", viscometer, "torque_pct"
        )
        capture_field(
            "shear_rate_1s", viscometer, "shear_rate_1s"
        )
        capture_field(
            "shear_stress_pa",
            viscometer,
            "shear_stress_Pa",
            "shear_stress_pa",
        )
        capture_field(
            "reaction_temp_c", stirrer, "temp_c"
        )
        capture_field("rotor", viscometer, "rotor")
        capture_field("rpm", viscometer, "rpm", "speed_rpm")

        active = self.store.get_active_step(experiment_id)
        previous = [
            item
            for item in self.store.list_measurements(
                experiment_id, "viscosity"
            )
            if active is not None
            and item["step_instance_id"] == active["id"]
        ]
        if previous:
            previous_values = previous[-1]["values"]
            for field in ("rotor", "rpm"):
                if data.get(field) in (None, "") and previous_values.get(
                    field
                ) not in (None, ""):
                    data[field] = previous_values[field]
                    provenance[field] = {
                        "source_type": "derived",
                        "formula": "reuse last confirmed viscometer setting",
                        "measurement_id": previous[-1]["id"],
                    }
        spec = experiment.get("spec_snapshot") or {}
        for field, key in (
            ("rotor", "viscometer_rotor"),
            ("rpm", "viscometer_rpm"),
        ):
            if data.get(field) in (None, "") and spec.get(key) not in (
                None,
                "",
            ):
                data[field] = spec[key]
                provenance[field] = {
                    "source_type": "derived",
                    "formula": f"batch specification {key}",
                }
        self._require_fields(data, VISCOSITY_FIELDS)
        if (
            data.get("sample_id") is None
            and viscometer is not None
            and viscometer.get("sample_id") is not None
        ):
            data["sample_id"] = viscometer["sample_id"]
        if (
            experiment["current_step_code"] != "R201-50"
            or active is None
            or active["step_code"] != "R201-50"
        ):
            raise R201Error("viscosity can only be recorded during active R201-50 aging", 409)
        viscosity = self._number(data["viscosity_mpas"], "viscosity_mpas")
        torque = self._number(data["torque_pct"], "torque_pct")
        if viscosity <= 0:
            raise R201Error("viscosity_mpas must be positive")
        valid = 10 <= torque <= 90
        invalid_reason = None if valid else "torque must be between 10% and 90%"
        event = self._event(
            data,
            "viscosity_recorded",
            {
                "viscosity_mpas": viscosity,
                "torque_pct": torque,
                "valid": valid,
            },
        )
        values = {key: data[key] for key in VISCOSITY_FIELDS}
        for optional in ("shear_rate_1s", "shear_stress_pa", "reaction_elapsed_min"):
            if optional in data:
                values[optional] = data[optional]
        if provenance:
            values["data_provenance"] = provenance
        measurement = self.store.add_measurement(
            experiment_id,
            {
                "client_event_id": data["client_event_id"],
                "measurement_type": "viscosity",
                "effective_at_ms": event["effective_at_ms"],
                "sample_id": data.get("sample_id"),
                "source_type": (
                    "device_confirmed"
                    if "viscosity_mpas" in provenance
                    else data.get("source_type", "manual")
                ),
                "valid": valid,
                "invalid_reason": invalid_reason,
                "values": values,
                "raw_payload_sha256": data.get("raw_payload_sha256"),
                "parser_version": data.get("parser_version"),
                "operator": data["actor"],
                "operator_user_id": data.get("actor_user_id"),
            },
            active["id"],
            event=event,
        )
        detail = self.get_experiment(experiment_id)
        return {
            "experiment": detail["experiment"],
            "measurement": measurement,
            "endpoint_ready": detail["endpoint_ready"],
        }

    def add_data_source(self, experiment_id: int, data: dict) -> dict:
        experiment = self._get(experiment_id)
        self._require_fields(
            data,
            (
                "client_event_id",
                "actor",
                "device_id",
                "device_role",
                "metric_key",
                "linked_at_ms",
                "link_method",
            ),
        )
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        duplicate = self._idempotent_event(
            experiment_id, data["client_event_id"], "data_source_bound"
        )
        if duplicate is not None:
            existing = self.store.get_data_source_binding_by_client_event(
                str(data["client_event_id"])
            )
            if existing is None:
                raise R201Error(
                    "idempotent data source result is unavailable", 409
                )
            return existing
        if data["link_method"] not in ("manual", "automatic"):
            raise R201Error("link_method must be manual or automatic")
        binding_data = dict(data)
        active = self.store.get_active_step(experiment_id)
        if (
            binding_data.get("step_instance_id") is None
            and active is not None
        ):
            binding_data["step_instance_id"] = active["id"]
        event = self._event(
            data,
            "data_source_bound",
            {
                "device_id": data["device_id"],
                "device_role": data["device_role"],
                "metric_key": data["metric_key"],
                "channel_selector": data.get("channel_selector"),
                "linked_at_ms": data["linked_at_ms"],
            },
        )
        try:
            return self.store.add_data_source_binding(
                experiment_id, binding_data, event=event
            )
        except sqlite3.IntegrityError as exc:
            raise R201Error("invalid device, run, or step binding") from exc

    def select_process_device(
        self,
        experiment_id: int,
        data: dict,
    ) -> dict:
        experiment = self._get(experiment_id)
        self._require_fields(
            data,
            (
                "client_event_id",
                "actor",
                "device_id",
                "device_type",
            ),
        )
        self._require_operator(
            experiment, data.get("actor"), data.get("actor_user_id")
        )
        if experiment["status"] in (
            "pending_review",
            "released",
            "terminated",
        ):
            raise R201Error(
                f"experiment status {experiment['status']} cannot change devices",
                409,
            )
        device_type = str(data["device_type"])
        role_metrics = PROCESS_DEVICE_BINDINGS.get(device_type)
        if role_metrics is None:
            raise R201Error("unsupported process device type")
        duplicate = self._idempotent_event(
            experiment_id,
            data["client_event_id"],
            "process_device_selected",
        )
        if duplicate is not None:
            roles = set(role_metrics)
            return {
                "bindings": [
                    item
                    for item in self.store.list_data_source_bindings(
                        experiment_id
                    )
                    if item["device_role"] in roles
                    and item["unlinked_at_ms"] is None
                ],
                "event": duplicate,
            }
        selected_at_ms = int(
            data.get("selected_at_ms") or self.clock_ms()
        )
        reservation = None
        if device_type != "viscometer":
            try:
                reservation = self.store.reserve_process_device(
                    experiment_id,
                    int(data["device_id"]),
                    device_type,
                    str(data["actor"]),
                    data.get("actor_user_id"),
                    selected_at_ms,
                )
            except RuntimeError as exc:
                raise R201Error(str(exc), 409) from exc
        active = self.store.get_active_step(experiment_id)
        event = self._event(
            data,
            "process_device_selected",
            {
                "device_id": int(data["device_id"]),
                "device_type": device_type,
                "roles": list(role_metrics),
                "selected_at_ms": selected_at_ms,
            },
        )
        try:
            bindings = self.store.replace_active_role_bindings(
                experiment_id,
                device_id=int(data["device_id"]),
                role_metrics=role_metrics,
                linked_at_ms=selected_at_ms,
                link_method="manual",
                client_event_id=str(data["client_event_id"]),
                step_instance_id=active["id"] if active else None,
                event=event,
            )
        except sqlite3.IntegrityError as exc:
            raise R201Error("invalid process device", 400) from exc
        return {
            "bindings": bindings,
            "event": event,
            "reservation": reservation,
        }

    def open_deviation(self, experiment_id: int, data: dict) -> dict:
        experiment = self._get(experiment_id)
        if experiment["status"] in (
            "pending_review",
            "released",
            "terminated",
        ):
            raise R201Error(
                f"experiment status {experiment['status']} cannot open a deviation",
                409,
            )
        self._require_fields(
            data, ("client_event_id", "description", "opened_by")
        )
        self._require_operator(
            experiment,
            data.get("opened_by"),
            data.get("opened_by_user_id"),
        )
        existing_event = self._idempotent_event(
            experiment_id,
            data["client_event_id"],
            "deviation_opened",
        )
        if existing_event is not None:
            deviation_no = existing_event["payload"].get("deviation_no")
            existing = (
                self.store.get_deviation_by_no(deviation_no)
                if deviation_no
                else None
            )
            if existing is None:
                raise R201Error("idempotent deviation result is unavailable", 409)
            return existing
        severity = data.get("severity", "warning")
        if severity not in ("warning", "critical"):
            raise R201Error("severity must be warning or critical")
        event = self._event(
            {
                **data,
                "actor": data["opened_by"],
                "actor_user_id": data.get("opened_by_user_id"),
            },
            "deviation_opened",
            {
                "description": data["description"],
                "severity": severity,
            },
        )
        return self.store.add_deviation(
            experiment_id,
            {
                **data,
                "severity": severity,
                "opened_at_ms": data.get(
                    "opened_at_ms", event["effective_at_ms"]
                ),
            },
            event,
        )

    def resolve_deviation(
        self, experiment_id: int, deviation_id: int, data: dict
    ) -> dict:
        experiment = self._get(experiment_id)
        self._require_fields(
            data,
            (
                "client_event_id",
                "reviewed_by",
                "impact_assessment",
                "disposition",
                "row_version",
            ),
        )
        existing_event = self._idempotent_event(
            experiment_id,
            data["client_event_id"],
            "deviation_resolved",
        )
        if existing_event is not None:
            existing_id = existing_event["payload"].get("deviation_id")
            existing = (
                self.store.get_deviation(existing_id)
                if existing_id is not None
                else None
            )
            if existing is None:
                raise R201Error(
                    "idempotent deviation resolution is unavailable", 409
                )
            return existing
        deviation = self.store.get_deviation(deviation_id)
        if deviation is None or deviation["experiment_id"] != experiment_id:
            raise R201Error("deviation not found", 404)
        if deviation["status"] == "closed":
            raise R201Error("deviation is already closed", 409)
        reviewed_by = str(data["reviewed_by"]).strip()
        reviewed_by_user_id = data.get("reviewed_by_user_id")
        assigned_reviewer = str(
            experiment.get("reviewer") or ""
        ).strip()
        if assigned_reviewer:
            if (
                experiment.get("operator_user_id") is not None
                and reviewed_by_user_id == experiment["operator_user_id"]
            ):
                raise R201Error("偏差处置人不能与操作员相同")
            if experiment.get("reviewer_user_id") is not None:
                if reviewed_by_user_id != experiment["reviewer_user_id"]:
                    raise R201Error("偏差处置人不是本批指定复核员")
            elif reviewed_by != assigned_reviewer:
                raise R201Error("偏差处置人不是本批指定复核员")
        else:
            self._require_operator(
                experiment,
                reviewed_by,
                reviewed_by_user_id,
            )
        if data["disposition"] not in ("continue", "rework", "terminate"):
            raise R201Error(
                "disposition must be continue, rework, or terminate"
            )
        expected_version = data["row_version"]
        self._check_version(experiment, expected_version)
        experiment_updates = None
        supersede_step_codes: tuple[str, ...] = ()
        if data["disposition"] == "rework":
            self._require_fields(data, ("rework_step_code",))
            rework_step = str(data["rework_step_code"])
            if rework_step not in MAIN_STEPS:
                raise R201Error("rework_step_code must be an R-201 main step")
            ordered_steps = (*MAIN_STEPS, "R201-80", "R201-90")
            current_index = ordered_steps.index(
                experiment["current_step_code"]
            )
            target_index = ordered_steps.index(rework_step)
            if target_index > current_index:
                raise R201Error("rework cannot jump forward")
            supersede_step_codes = tuple(
                MAIN_STEPS[target_index : min(current_index + 1, len(MAIN_STEPS))]
            )
            experiment_updates = {
                "status": "in_progress",
                "current_step_code": rework_step,
                "disposition": "偏差待评估",
                "completed_effective_at_ms": None,
            }
        elif data["disposition"] == "terminate":
            active = self.store.get_active_step(experiment_id)
            supersede_step_codes = (
                (active["step_code"],) if active is not None else ()
            )
            experiment_updates = {
                "status": "terminated",
                "disposition": "不合格",
                "completed_effective_at_ms": self.clock_ms(),
            }
        event = self._event(
            {
                **data,
                "actor": data["reviewed_by"],
                "actor_user_id": data.get("reviewed_by_user_id"),
            },
            "deviation_resolved",
            {
                "deviation_id": deviation_id,
                "deviation_no": deviation["deviation_no"],
                "disposition": data["disposition"],
                "rework_step_code": data.get("rework_step_code"),
            },
        )
        try:
            return self.store.resolve_deviation(
                experiment_id,
                deviation_id,
                data,
                event,
                expected_version=expected_version,
                experiment_updates=experiment_updates,
                supersede_step_codes=supersede_step_codes,
            )
        except (LookupError, RuntimeError) as exc:
            status_code = 404 if isinstance(exc, LookupError) else 409
            raise R201Error(str(exc), status_code) from exc

    def submit(
        self,
        experiment_id: int,
        expected_version: int,
        actor: str,
        client_event_id: str,
        actor_user_id: Optional[int] = None,
    ) -> dict:
        duplicate = self._idempotent_event(
            experiment_id, client_event_id, "experiment_submitted"
        )
        if duplicate is not None:
            return self._get(experiment_id)
        experiment = self._get(experiment_id)
        self._require_operator(experiment, actor, actor_user_id)
        self._check_version(experiment, expected_version)
        if experiment["current_step_code"] != "R201-80":
            raise R201Error("experiment is not ready to submit", 409)
        if self.store.get_active_step(experiment_id) is not None:
            raise R201Error("active step must be completed before submit", 409)
        if any(
            item["status"] != "closed"
            for item in self.store.list_deviations(experiment_id)
        ):
            raise R201Error(
                "仍有未处置的异常，请先在当前页面完成影响评估",
                409,
            )
        event = self._event(
            {
                "client_event_id": client_event_id,
                "actor": actor,
                "actor_user_id": actor_user_id,
            },
            "experiment_submitted",
            {},
        )
        has_reviewer = bool(str(experiment.get("reviewer") or "").strip())
        updates = {
            "status": "pending_review" if has_reviewer else "released",
            "current_step_code": "R201-90",
        }
        if not has_reviewer:
            updates["disposition"] = "实验记录已由操作员确认并锁定"
        try:
            return self.store.transition_experiment(
                experiment_id,
                expected_version,
                updates,
                event,
                require_no_open_deviations=True,
            )
        except RuntimeError as exc:
            raise R201Error(str(exc), 409) from exc

    def review(
        self,
        experiment_id: int,
        expected_version: int,
        action: str,
        reviewer: str,
        client_event_id: str,
        disposition: Optional[str] = None,
        reviewer_user_id: Optional[int] = None,
    ) -> dict:
        event_types = {
            "release": "experiment_released",
            "return": "experiment_returned",
            "terminate": "experiment_terminated",
        }
        if action not in event_types:
            raise R201Error("action must be release, return, or terminate")
        duplicate = self._idempotent_event(
            experiment_id, client_event_id, event_types[action]
        )
        if duplicate is not None:
            return self._get(experiment_id)
        experiment = self._get(experiment_id)
        self._check_version(experiment, expected_version)
        if experiment["status"] != "pending_review":
            raise R201Error("experiment is not pending review", 409)
        if (
            experiment.get("operator_user_id") is not None
            and reviewer_user_id == experiment["operator_user_id"]
        ):
            raise R201Error("reviewer must be different from operator")
        if experiment.get("reviewer_user_id") is not None:
            if reviewer_user_id != experiment["reviewer_user_id"]:
                raise R201Error("reviewer does not match the assigned reviewer")
        elif reviewer.strip() != experiment["reviewer"].strip():
            raise R201Error("reviewer does not match the assigned reviewer")
        if action == "release":
            if not disposition:
                raise R201Error("disposition is required for release")
            if any(
                item["status"] != "closed"
                for item in self.store.list_deviations(experiment_id)
            ):
                raise R201Error(
                    "仍有未处置的异常，不能发布实验记录",
                    409,
                )
            updates = {
                "status": "released",
                "disposition": disposition,
                "completed_effective_at_ms": self.clock_ms(),
            }
        elif action == "return":
            updates = {
                "status": "in_progress",
                "current_step_code": "R201-80",
                "disposition": "偏差待评估",
            }
        elif action == "terminate":
            updates = {
                "status": "terminated",
                "disposition": disposition or "不合格",
                "completed_effective_at_ms": self.clock_ms(),
            }
        event = self._event(
            {
                "client_event_id": client_event_id,
                "actor": reviewer,
                "actor_user_id": reviewer_user_id,
            },
            f"experiment_{action}d" if action != "return" else "experiment_returned",
            {"disposition": updates.get("disposition")},
        )
        try:
            return self.store.transition_experiment(
                experiment_id,
                expected_version,
                updates,
                event,
                require_no_open_deviations=(action == "release"),
            )
        except RuntimeError as exc:
            raise R201Error(str(exc), 409) from exc

    def _get(self, experiment_id: int) -> dict:
        experiment = self.store.get_experiment(experiment_id)
        if experiment is None:
            raise R201Error("experiment not found", 404)
        return experiment

    @staticmethod
    def _check_version(experiment: dict, expected_version: int):
        if experiment["row_version"] != expected_version:
            raise R201Error("row version conflict", 409)

    @staticmethod
    def _require_operator(
        experiment: dict, actor, actor_user_id: Optional[int] = None
    ):
        assigned_user_id = experiment.get("operator_user_id")
        if assigned_user_id is not None:
            if actor_user_id != assigned_user_id:
                raise R201Error("当前账号不是本批指定操作员", 403)
            return
        if str(actor or "").strip() != experiment["operator"].strip():
            raise R201Error("actor does not match the assigned operator")

    @staticmethod
    def _capture_device(
        capture: dict,
        device_type: str,
        role: Optional[str] = None,
    ) -> Optional[dict]:
        selected_device_id = (
            (capture or {}).get("role_device_ids", {}).get(role)
            if role
            else None
        )
        candidates = [
            item
            for item in (capture or {}).get("devices", [])
            if item.get("type") == device_type
            and (
                selected_device_id is None
                or str(item.get("device_id")) == str(selected_device_id)
            )
            and item.get("snapshot")
            and item["snapshot"].get("state") != "offline"
            and int(item.get("age_ms") or 0) <= 15_000
            and (
                device_type != "tyd02"
                or role != "acid_pump"
                or item["snapshot"].get("work_mode") == "仅注入"
            )
            and (
                device_type != "viscometer"
                or (
                    item["snapshot"].get("metrics") or {}
                ).get("data_verified") is True
            )
        ]
        if selected_device_id is not None:
            return candidates[0] if candidates else None
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _capture_value(device: Optional[dict], *keys):
        if not device:
            return None, None
        snapshot = device.get("snapshot") or {}
        metrics = snapshot.get("metrics") or {}
        for key in keys:
            value = snapshot.get(key)
            if value not in (None, ""):
                return value, key
            value = metrics.get(key)
            if value not in (None, ""):
                return value, key
        return None, None

    @staticmethod
    def _volume_ml(value, unit) -> Optional[float]:
        factors = {
            "nl": 0.000001,
            "μl": 0.001,
            "µl": 0.001,
            "ul": 0.001,
            "ml": 1.0,
            "l": 1000.0,
        }
        factor = factors.get(str(unit or "").strip().lower())
        if factor is None or value in (None, ""):
            return None
        try:
            return float(value) * factor
        except (TypeError, ValueError):
            return None

    @classmethod
    def _rate_ml_min(cls, value, unit) -> Optional[float]:
        normalized = str(unit or "").strip().lower().replace("／", "/")
        suffixes = ("/min", "/分钟")
        volume_unit = next(
            (
                normalized[: -len(suffix)]
                for suffix in suffixes
                if normalized.endswith(suffix)
            ),
            None,
        )
        return cls._volume_ml(value, volume_unit)

    def _derive_step_result(
        self,
        experiment: dict,
        active: dict,
        step_code: str,
        result: dict,
        capture: dict,
        completion_at_ms: int,
    ) -> dict:
        provenance = dict(result.get("data_provenance") or {})

        def add_device(field: str, value, device: Optional[dict], metric: str):
            if field in result and result[field] not in (None, ""):
                return
            if value in (None, "") or device is None:
                return
            result[field] = value
            snapshot = device.get("snapshot") or {}
            provenance[field] = {
                "source_type": "device_confirmed",
                "device_id": device.get("device_id"),
                "device_name": device.get("alias") or device.get("name"),
                "device_type": device.get("type"),
                "metric_key": metric,
                "sample_id": device.get("sample_id"),
                "sampled_at_ms": snapshot.get("ts_ms"),
                "captured_at_server_ms": capture.get(
                    "captured_at_server_ms"
                ),
            }

        def add_derived(field: str, value, formula: str):
            if field in result and result[field] not in (None, ""):
                return
            result[field] = value
            provenance[field] = {
                "source_type": "derived",
                "formula": formula,
                "interval_start_ms": active["started_effective_at_ms"],
                "interval_end_ms": completion_at_ms,
            }

        whd = self._capture_device(capture, "whd46", "environment")
        stirrer = self._capture_device(capture, "stirrer", "stirrer")
        pump = self._capture_device(capture, "tyd02", "acid_pump")
        viscometer = self._capture_device(
            capture,
            "viscometer",
            "viscometer",
        )

        environment_temp, environment_temp_key = self._capture_value(
            whd, "temp_c", "avg_temp_c"
        )
        environment_humidity, environment_humidity_key = self._capture_value(
            whd, "avg_humid_rh"
        )
        if environment_humidity is None and whd:
            metrics = (whd.get("snapshot") or {}).get("metrics") or {}
            humidities = [
                metrics.get("ch1_humid_rh"),
                metrics.get("ch2_humid_rh"),
                metrics.get("ch3_humid_rh"),
            ]
            humidities = [
                float(value)
                for value in humidities
                if value not in (None, "")
            ]
            if humidities:
                environment_humidity = sum(humidities) / len(humidities)
                environment_humidity_key = "ch1-3_humid_rh_mean"

        reaction_temp, reaction_temp_key = self._capture_value(
            stirrer, "temp_c"
        )
        speed_rpm, speed_key = self._capture_value(
            stirrer, "speed", "speed_rpm", "flow_rpm"
        )
        pump_volume, pump_volume_key = self._capture_value(
            pump, "acc_volume", "delivered_volume"
        )
        pump_unit, _ = self._capture_value(pump, "acc_unit")
        pump_rate, pump_rate_key = self._capture_value(
            pump, "inject_rate", "flow_rate"
        )
        pump_target, pump_target_key = self._capture_value(
            pump, "target_volume"
        )
        pump_target_unit, _ = self._capture_value(pump, "target_unit")
        pump_rate_unit, _ = self._capture_value(
            pump, "inject_rate_unit", "flow_rate_unit"
        )
        pump_volume_ml = self._volume_ml(pump_volume, pump_unit)
        pump_target_ml = self._volume_ml(
            pump_target, pump_target_unit
        )
        pump_rate_ml_min = self._rate_ml_min(
            pump_rate, pump_rate_unit
        )
        syringe_spec, syringe_key = self._capture_value(
            pump, "syringe_spec", "syringe_code"
        )
        viscosity, viscosity_key = self._capture_value(
            viscometer, "viscosity_mPas", "viscosity_mpas"
        )

        if step_code == "R201-01":
            add_device(
                "environment_temp_c",
                environment_temp,
                whd,
                environment_temp_key or "temp_c",
            )
            add_device(
                "environment_humidity_rh",
                environment_humidity,
                whd,
                environment_humidity_key or "avg_humid_rh",
            )
            if "device_checks" not in result and capture.get("devices"):
                result["device_checks"] = [
                    item.get("alias") or item.get("name")
                    for item in capture.get("devices", [])
                    if item.get("snapshot", {}).get("state") != "offline"
                ]
                provenance["device_checks"] = {
                    "source_type": "derived",
                    "formula": "online devices at step completion",
                }
        elif step_code in ("R201-10", "R201-20"):
            add_device(
                "actual_rpm", speed_rpm, stirrer, speed_key or "speed"
            )
        elif step_code == "R201-30":
            add_device(
                "reaction_temp_c",
                reaction_temp,
                stirrer,
                reaction_temp_key or "temp_c",
            )
            add_device(
                "target_volume_ml",
                pump_target_ml,
                pump,
                pump_target_key or "target_volume",
            )
            add_device(
                "target_rate_ml_min",
                pump_rate_ml_min,
                pump,
                pump_rate_key or "inject_rate",
            )
            add_device(
                "syringe_spec",
                str(syringe_spec) if syringe_spec is not None else None,
                pump,
                syringe_key or "syringe_code",
            )
        elif step_code == "R201-31":
            start_event = next(
                (
                    item
                    for item in reversed(
                        self.store.list_experiment_events(
                            experiment["id"]
                        )
                    )
                    if item["step_instance_id"] == active["id"]
                    and item["event_type"] == "step_started"
                ),
                None,
            )
            start_capture = (
                start_event["payload"].get("device_capture", {})
                if start_event
                else {}
            )
            start_pump = self._capture_device(
                start_capture,
                "tyd02",
                "acid_pump",
            )
            start_volume, start_volume_key = self._capture_value(
                start_pump, "acc_volume", "delivered_volume"
            )
            start_unit, _ = self._capture_value(start_pump, "acc_unit")
            start_volume_ml = self._volume_ml(start_volume, start_unit)
            add_device(
                "acc_volume_start",
                start_volume_ml,
                start_pump,
                start_volume_key or "acc_volume",
            )
            add_device(
                "acc_volume_end",
                pump_volume_ml,
                pump,
                pump_volume_key or "acc_volume",
            )
            add_device(
                "target_volume_ml",
                pump_target_ml,
                pump,
                pump_target_key or "target_volume",
            )
            if (
                "acc_volume_unit" not in result
                and start_volume_ml is not None
                and pump_volume_ml is not None
            ):
                result["acc_volume_unit"] = "mL"
                provenance["acc_volume_unit"] = {
                    "source_type": "device_confirmed",
                    "device_id": pump.get("device_id") if pump else None,
                    "metric_key": "acc_unit converted to mL",
                }
        elif step_code == "R201-32":
            add_derived(
                "balance_minutes",
                round(
                    max(
                        0,
                        completion_at_ms
                        - active["started_effective_at_ms"],
                    )
                    / 60_000,
                    2,
                ),
                "(step end timestamp - step start timestamp) / 60000",
            )
        elif step_code == "R201-40":
            add_device(
                "reached_temp_c",
                reaction_temp,
                stirrer,
                reaction_temp_key or "temp_c",
            )
        elif step_code == "R201-60":
            add_derived(
                "cooling_minutes",
                round(
                    max(
                        0,
                        completion_at_ms
                        - active["started_effective_at_ms"],
                    )
                    / 60_000,
                    2,
                ),
                "(step end timestamp - step start timestamp) / 60000",
            )
            if (
                "room_temp_confirmed" not in result
                and reaction_temp is not None
                and environment_temp is not None
            ):
                result["room_temp_confirmed"] = (
                    abs(float(reaction_temp) - float(environment_temp)) <= 2
                )
                provenance["room_temp_confirmed"] = {
                    "source_type": "derived",
                    "formula": (
                        "abs(reaction temperature - environment "
                        "temperature) <= 2°C"
                    ),
                }
        elif step_code == "R201-70":
            add_device(
                "transfer_viscosity_mpas",
                viscosity,
                viscometer,
                viscosity_key or "viscosity_mPas",
            )

        if provenance:
            result["data_provenance"] = provenance
        return result

    def _calculate_dose_result(self, result: dict):
        start = self._number(
            result["acc_volume_start"], "acc_volume_start"
        )
        end = self._number(result["acc_volume_end"], "acc_volume_end")
        if end < start:
            raise R201Error(
                "设备累计量出现倒退，请检查设备复位或记录异常"
            )
        if str(result["acc_volume_unit"]).lower() != "ml":
            raise R201Error("当前版本要求 TYD02 累计量单位为 mL")
        result["dose_delivered_ml"] = end - start
        result["dose_error_ml"] = (
            result["dose_delivered_ml"]
            - self._number(result["target_volume_ml"], "target_volume_ml")
        )

    def _event(self, data: dict, event_type: str, payload: dict) -> dict:
        self._require_fields(data, ("client_event_id", "actor"))
        received = self.clock_ms()
        occurred = data.get("occurred_at_client_ms")
        clock_status = data.get("clock_sync_status", "unknown")
        offset = data.get("client_clock_offset_ms")
        if clock_status not in ("trusted", "untrusted", "unknown", "server"):
            raise R201Error("clock_sync_status is invalid")
        if occurred is not None:
            occurred = self._integer(
                occurred, "occurred_at_client_ms"
            )
        if offset is not None:
            offset = self._integer(
                offset, "client_clock_offset_ms"
            )
        if occurred is not None and clock_status == "trusted":
            if offset is None:
                raise R201Error(
                    "client_clock_offset_ms is required for trusted clock"
                )
            corrected = occurred - offset
            earliest = received - TRUSTED_EVENT_TIME_SKEW_MS
            latest = received + TRUSTED_EVENT_TIME_SKEW_MS
            if earliest <= corrected <= latest:
                effective = corrected
            else:
                clock_status = "untrusted"
                effective = received
        else:
            effective = received
        return {
            "client_event_id": str(data["client_event_id"]),
            "event_type": event_type,
            "occurred_at_client_ms": occurred,
            "received_at_server_ms": received,
            "client_clock_offset_ms": offset,
            "clock_sync_status": clock_status,
            "effective_at_ms": effective,
            "actor": str(data["actor"]),
            "actor_user_id": data.get("actor_user_id"),
            "source_type": data.get("source_type", "manual"),
            "payload": payload,
        }

    def _duplicate_result(
        self,
        experiment_id: int,
        request_data: dict,
        step_code: str,
        expected_event_type: str,
    ) -> Optional[dict]:
        client_event_id = request_data.get("client_event_id")
        if not client_event_id:
            return None
        existing = self._idempotent_event(
            experiment_id, client_event_id, expected_event_type
        )
        if existing is None:
            return None
        detail = self.get_experiment(experiment_id)
        step = next(
            (
                item
                for item in reversed(detail["steps"])
                if item["step_code"] == step_code
            ),
            None,
        )
        return {"experiment": detail["experiment"], "step": step}

    def _idempotent_event(
        self,
        experiment_id: int,
        client_event_id,
        expected_event_type: str,
    ) -> Optional[dict]:
        existing = self.store.get_event_by_client_id(
            str(client_event_id or "")
        )
        if existing is None:
            return None
        if (
            existing["experiment_id"] != experiment_id
            or existing["event_type"] != expected_event_type
        ):
            raise R201Error(
                "client_event_id already belongs to another operation", 409
            )
        return existing

    def _endpoint_ready(
        self,
        experiment: dict,
        measurements: list[dict],
        steps: Optional[list[dict]] = None,
    ) -> bool:
        steps = steps if steps is not None else self.store.list_steps(
            experiment["id"]
        )
        aging_attempts = [
            step
            for step in steps
            if step["step_code"] == "R201-50"
            and step["status"] in ("active", "completed")
        ]
        if not aging_attempts:
            return False
        current_attempt_id = aging_attempts[-1]["id"]
        valid = [
            item
            for item in measurements
            if item["measurement_type"] == "viscosity"
            and item["valid"]
            and item["step_instance_id"] == current_attempt_id
        ]
        if len(valid) < 2:
            return False
        last_two = valid[-2:]
        values = [
            self._number(item["values"].get("viscosity_mpas"), "viscosity_mpas")
            for item in last_two
        ]
        low = experiment["target_viscosity_min_mpas"]
        high = experiment["target_viscosity_max_mpas"]
        return (
            all(low <= value <= high for value in values)
            and abs(values[1] - values[0]) <= 0.5
        )

    @staticmethod
    def _require_fields(data: dict, fields):
        for field in fields:
            if field not in data or data[field] is None or data[field] == "":
                raise R201Error(f"{field} is required")

    @staticmethod
    def _number(value, field: str) -> float:
        if isinstance(value, bool):
            raise R201Error(f"{field} must be numeric")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise R201Error(f"{field} must be numeric") from exc
        if not math.isfinite(number):
            raise R201Error(f"{field} must be finite")
        return number

    @staticmethod
    def _integer(value, field: str) -> int:
        if isinstance(value, bool):
            raise R201Error(f"{field} must be an integer")
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise R201Error(f"{field} must be an integer") from exc
        if isinstance(value, float) and not value.is_integer():
            raise R201Error(f"{field} must be an integer")
        if isinstance(value, str) and str(number) != value.strip():
            raise R201Error(f"{field} must be an integer")
        return number

    def _validate_step_result(self, step_code: str, result: dict):
        true_fields = {
            "R201-04": ("acid_into_water",),
            "R201-30": ("ice_bath_confirmed", "line_purged"),
            "R201-40": ("condenser_confirmed", "moisture_protection_confirmed"),
            "R201-60": ("room_temp_confirmed",),
            "R201-70": ("label_confirmed",),
        }
        confirmation_labels = {
            "acid_into_water": "请确认已按“酸入水”完成操作",
            "ice_bath_confirmed": "请确认冰浴已就位",
            "line_purged": "请确认注射泵管路已排尽气泡",
            "condenser_confirmed": "请确认冷凝回流检查通过",
            "moisture_protection_confirmed": "请确认防吸湿措施已就位",
            "room_temp_confirmed": "当前尚未确认降至室温，请继续降温或现场确认",
            "label_confirmed": "请确认容器标签已完成",
        }
        for field in true_fields.get(step_code, ()):
            if result.get(field) is not True:
                raise R201Error(
                    confirmation_labels.get(field, f"请确认：{field}")
                )
        if (
            step_code == "R201-32"
            and self._number(result["balance_minutes"], "balance_minutes") < 5
        ):
            raise R201Error("加酸后平衡时间尚不足 5 分钟，请继续等待")

    def _rule_findings(
        self, experiment: dict, step_code: str, result: dict
    ) -> list[dict]:
        spec = experiment.get("spec_snapshot") or {}
        findings = []

        def range_finding(
            rule_code: str,
            value,
            minimum_key: str,
            maximum_key: str,
            label: str,
            unit: str,
        ):
            if minimum_key not in spec or maximum_key not in spec:
                return
            actual = self._number(value, label)
            minimum = self._number(spec[minimum_key], minimum_key)
            maximum = self._number(spec[maximum_key], maximum_key)
            if minimum <= actual <= maximum:
                return
            findings.append(
                {
                    "rule_code": rule_code,
                    "severity": "warning",
                    "actual_value": f"{self._format_number(actual)} {unit}",
                    "standard_value": (
                        f"{self._format_number(minimum)}–"
                        f"{self._format_number(maximum)} {unit}"
                    ),
                    "description": f"{label}超出本批参数快照范围",
                    "immediate_action": "请记录立即措施并完成影响评估",
                }
            )

        if step_code == "R201-10":
            range_finding(
                "PREMIX_RPM_RANGE",
                result["actual_rpm"],
                "premix_rpm_min",
                "premix_rpm_max",
                "预混转速",
                "rpm",
            )
        elif step_code == "R201-20":
            range_finding(
                "ETHANOL_MIX_RPM_RANGE",
                result["actual_rpm"],
                "ethanol_mix_rpm_min",
                "ethanol_mix_rpm_max",
                "乙醇搅拌转速",
                "rpm",
            )
        elif step_code == "R201-30":
            range_finding(
                "ICE_BATH_TEMP_RANGE",
                result["reaction_temp_c"],
                "ice_bath_temp_min_c",
                "ice_bath_temp_max_c",
                "冰浴反应温度",
                "℃",
            )
            range_finding(
                "ACID_RATE_RANGE",
                result["target_rate_ml_min"],
                "acid_rate_min_ml_min",
                "acid_rate_max_ml_min",
                "设定加酸速率",
                "mL/min",
            )
        elif step_code == "R201-31":
            tolerance = spec.get("acid_volume_tolerance_ml")
            if tolerance is not None:
                error = abs(self._number(result["dose_error_ml"], "dose_error_ml"))
                limit = self._number(tolerance, "acid_volume_tolerance_ml")
                if error > limit:
                    findings.append(
                        {
                            "rule_code": "ACID_DOSE_TOLERANCE",
                            "severity": "critical",
                            "actual_value": (
                                f"{self._format_number(result['dose_delivered_ml'])} mL"
                            ),
                            "standard_value": (
                                f"{self._format_number(result['target_volume_ml'])}"
                                f" ± {self._format_number(limit)} mL"
                            ),
                            "description": "本次加酸量超出本批容差",
                            "immediate_action": "停止转序并记录处置",
                        }
                    )
        elif step_code == "R201-32":
            minutes = self._number(result["balance_minutes"], "balance_minutes")
            if minutes > 10:
                findings.append(
                    {
                        "rule_code": "POST_DOSING_BALANCE_MAX",
                        "severity": "warning",
                        "actual_value": f"{self._format_number(minutes)} min",
                        "standard_value": "5–10 min",
                        "description": "加酸后平衡时长超过通用上限",
                        "immediate_action": "请评估延长平衡对本批的影响",
                    }
                )
        elif step_code == "R201-40":
            range_finding(
                "REACTION_TEMP_RANGE",
                result["reached_temp_c"],
                "reaction_temp_min_c",
                "reaction_temp_max_c",
                "到温温度",
                "℃",
            )
        return findings

    def _normalize_materials(
        self, materials, added_at_ms: int, operator: str
    ) -> list[dict]:
        if not isinstance(materials, list) or not materials:
            raise R201Error("materials must contain at least one item")
        normalized = []
        for index, material in enumerate(materials, start=1):
            if not isinstance(material, dict):
                raise R201Error(f"materials[{index}] must be an object")
            self._require_fields(material, ("name", "actual", "unit"))
            actual = self._number(
                material["actual"], f"materials[{index}].actual"
            )
            if actual <= 0:
                raise R201Error(
                    f"materials[{index}].actual must be positive"
                )
            theoretical = material.get("theoretical")
            if theoretical not in (None, ""):
                theoretical = self._number(
                    theoretical, f"materials[{index}].theoretical"
                )
            normalized.append(
                {
                    "material_name": str(material["name"]).strip(),
                    "lot_no": str(material.get("lot") or "").strip(),
                    "expires_at": material.get("expires_at"),
                    "opened_at": material.get("opened_at"),
                    "theoretical_value": theoretical,
                    "actual_value": actual,
                    "unit": str(material["unit"]).strip(),
                    "appearance": material.get("appearance"),
                    "added_at_ms": material.get("added_at_ms", added_at_ms),
                    "operator": operator,
                    "reviewer": material.get("reviewer"),
                    "material_container_id": material.get(
                        "material_container_id"
                    ),
                    "container_code": (
                        str(material.get("container_code") or "").strip()
                        or None
                    ),
                }
            )
        return normalized

    def _telemetry_summary(
        self, experiment_id: int, events: list[dict]
    ) -> dict:
        experiment = self.store.get_experiment(experiment_id)
        rows = self.store.list_bound_samples(
            experiment_id, "reaction_temp", limit=None
        )
        series = []
        for row in rows:
            metric_key = row["metric_key"]
            if metric_key == "temp_c":
                value = row["temp_c"]
            elif metric_key == "flow_rate":
                value = row["flow_rate"]
            elif metric_key == "delivered_volume":
                value = row["delivered_volume"]
            else:
                value = row["metrics"].get(metric_key)
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            series.append(
                {
                    "binding_id": row["binding_id"],
                    "device_id": row["device_id"],
                    "sample_id": row["sample_id"],
                    "ts_ms": row["ts_ms"],
                    "metric_key": metric_key,
                    "channel_selector": row["channel_selector"],
                    "value": numeric_value,
                }
            )
        checkpoints = [
            {
                "event_id": event["id"],
                "effective_at_ms": event["effective_at_ms"],
                **event["payload"],
            }
            for event in events
            if event["event_type"] == "aging_temperature_checkpoint"
        ]
        reached_temperature = next(
            (
                event
                for event in reversed(events)
                if event["event_type"] == "reached_temperature"
            ),
            None,
        )
        gaps = []
        gap_threshold_ms = (
            (experiment.get("spec_snapshot") or {}).get(
                "telemetry_gap_threshold_ms"
            )
            if experiment
            else None
        )
        if gap_threshold_ms is not None:
            threshold = int(
                self._number(
                    gap_threshold_ms, "telemetry_gap_threshold_ms"
                )
            )
            if threshold <= 0:
                raise R201Error(
                    "telemetry_gap_threshold_ms must be positive"
                )
            for previous, current in zip(series, series[1:]):
                gap_ms = current["ts_ms"] - previous["ts_ms"]
                if gap_ms > threshold:
                    gaps.append(
                        {
                            "started_at_ms": previous["ts_ms"],
                            "ended_at_ms": current["ts_ms"],
                            "duration_ms": gap_ms,
                            "threshold_ms": threshold,
                        }
                    )
        if not self.store.list_data_source_bindings(experiment_id):
            integrity_status = "no_source"
        elif not series:
            integrity_status = "no_data"
        elif gap_threshold_ms is None:
            integrity_status = "threshold_not_configured"
        elif gaps:
            integrity_status = "gaps_detected"
        else:
            integrity_status = "complete"
        return {
            "temperature_series": series,
            "temperature_checkpoints": checkpoints,
            "reached_temperature": reached_temperature,
            "telemetry_gaps": gaps,
            "telemetry_integrity_status": integrity_status,
        }

    def _record_telemetry_gap_deviations(
        self,
        experiment: dict,
        active: dict,
        gaps: list[dict],
        connection=None,
    ):
        for gap in gaps:
            event_id = (
                f"derived-telemetry-gap-{experiment['id']}-"
                f"{active['id']}-{gap['started_at_ms']}-"
                f"{gap['ended_at_ms']}"
            )
            if (
                self.store.get_event_by_client_id(
                    event_id, connection=connection
                )
                is not None
            ):
                continue
            event = {
                "client_event_id": event_id,
                "event_type": "deviation_opened",
                "occurred_at_client_ms": None,
                "received_at_server_ms": self.clock_ms(),
                "client_clock_offset_ms": None,
                "clock_sync_status": "server",
                "effective_at_ms": gap["ended_at_ms"],
                "actor": "system",
                "source_type": "derived",
                "payload": {
                    "rule_code": "REACTION_TEMP_TELEMETRY_GAP",
                    "started_at_ms": gap["started_at_ms"],
                    "ended_at_ms": gap["ended_at_ms"],
                    "duration_ms": gap["duration_ms"],
                },
            }
            self.store.add_deviation(
                experiment["id"],
                {
                    "step_instance_id": active["id"],
                    "opened_at_ms": gap["ended_at_ms"],
                    "status": "open",
                    "severity": "warning",
                    "actual_value": f"{gap['duration_ms']} ms",
                    "standard_value": (
                        f"≤ {gap['threshold_ms']} ms"
                    ),
                    "description": "反应温度遥测出现数据缺口",
                    "immediate_action": "核对设备通讯并评估缺失区间",
                    "opened_by": "system",
                },
                event,
                connection=connection,
            )

    @staticmethod
    def _format_number(value) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
