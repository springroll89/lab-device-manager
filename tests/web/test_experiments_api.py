import json
import time
from io import BytesIO

from PIL import Image
import zxingcpp
from lab_device_manager.db.repository import Repository
from lab_device_manager.instruments.base import StatusSnapshot
from lab_device_manager.runtime.types import DeviceConfig
from lab_device_manager.web.app import create_app


class StaticEngine:
    def __init__(self, latest, dmap):
        self._latest = latest
        self._dmap = dmap

    def latest(self):
        return dict(self._latest)

    def device_map(self):
        return dict(self._dmap)


def _app():
    repo = Repository(":memory:")
    did = repo.upsert_device("pump-1", "tyd02", "注射泵")
    snap = StatusSnapshot(
        timestamp=time.time(),
        state="running",
        work_mode="仅注入",
        device_id="pump-1",
        acc_volume=105,
        acc_unit="mL",
        temp_c=25,
        metrics={"inject_rate": 0.5, "target_volume": 5, "target_unit": "mL"},
    )
    engine = StaticEngine(
        {did: snap},
        {did: DeviceConfig(name="pump-1", type="tyd02", alias="注射泵")},
    )
    app = create_app(engine, repo, secret_key="test-secret")
    app.config.update(TESTING=True, AUTH_TEST_BYPASS=True)
    return app, repo, did


def _multi_stirrer_app():
    repo = Repository(":memory:")
    latest = {}
    device_map = {}
    device_ids = []
    for index in range(1, 6):
        name = f"stirrer-{index}"
        alias = f"HMS-C 搅拌器 {index}"
        device_id = repo.upsert_device(name, "stirrer", alias)
        device_ids.append(device_id)
        latest[device_id] = StatusSnapshot(
            timestamp=time.time(),
            state="running",
            work_mode="stirring",
            device_id=name,
            temp_c=25 + index,
            metrics={"speed": 200 + index * 10},
        )
        device_map[device_id] = DeviceConfig(
            name=name,
            type="stirrer",
            alias=alias,
        )
    engine = StaticEngine(latest, device_map)
    app = create_app(engine, repo, secret_key="test-secret")
    app.config.update(TESTING=True, AUTH_TEST_BYPASS=True)
    return app, repo, device_ids


def _single_stirrer_app():
    repo = Repository(":memory:")
    device_id = repo.upsert_device(
        "stirrer-only", "stirrer", "HMS-C 唯一搅拌器"
    )
    snapshot = StatusSnapshot(
        timestamp=time.time(),
        state="running",
        work_mode="stirring",
        device_id="stirrer-only",
        temp_c=26,
        metrics={"speed": 300},
    )
    engine = StaticEngine(
        {device_id: snapshot},
        {
            device_id: DeviceConfig(
                name="stirrer-only",
                type="stirrer",
                alias="HMS-C 唯一搅拌器",
            )
        },
    )
    app = create_app(engine, repo, secret_key="test-secret")
    app.config.update(TESTING=True, AUTH_TEST_BYPASS=True)
    return app, repo, device_id


CREATE = {
    "batch_id": "20260723-AEM-01",
    "membrane_system": "AEM",
    "recipe_no": "R-AEM-001",
    "recipe_version": "V1",
    "sop_code": "SOP-SOL-GEL-CEM-AEM-01",
    "sop_version": "V0.2",
    "target_viscosity_min_mpas": 2.5,
    "target_viscosity_max_mpas": 10,
    "operator": "张三",
    "reviewer": "李四",
    "spec_snapshot": {},
}


def test_experiments_page_and_static_script_are_served():
    app, _, _ = _app()
    client = app.test_client()
    page = client.get("/experiments")
    assert page.status_code == 200
    assert b"experiment.js" in page.data
    assert b"scanner.js" in page.data
    assert b"cameraScanButton" in page.data
    script = client.get("/static/experiment.js")
    assert script.status_code == 200
    assert b"STEP_DRAFT_PREFIX" in script.data
    assert b"saveStepDraft(previousForm.dataset.stepCode, previousForm)" in script.data
    assert b"restoreStepDraft(step, form)" in script.data
    assert b"const form = event.currentTarget" in script.data
    assert b"const viscosityForm = event.currentTarget" in script.data
    assert b"viscosityForm.reset()" in script.data
    assert b"event.currentTarget.reset()" not in script.data
    assert b"completion-preview" in script.data
    assert b"deviceReadingIsFresh" in script.data
    assert "设备离线？人工补录".encode() in page.data
    assert "操作与数据时间轴".encode() in page.data
    assert b"MATERIAL_PRESETS" in script.data
    assert b"EVENT_LABELS" in script.data
    assert b"selectProcessDevice" in script.data
    assert "选择本批使用的".encode() in script.data
    assert b"capture.role_device_ids" in script.data
    assert b'createStatus' in page.data
    assert b"deleteExperiment" in script.data
    assert b"created_by_username" in script.data
    assert b"can_delete_experiments" in script.data


def test_experiment_detail_has_focused_tablet_execution_shell():
    app, _, _ = _app()
    client = app.test_client()

    page = client.get("/experiments")
    script = client.get("/static/experiment.js")
    theme = client.get("/static/puricore-theme.css")

    assert page.status_code == 200
    assert script.status_code == 200
    assert theme.status_code == 200
    for element_id in (
        b'executionHeaderSummary',
        b'executionHeaderBatch',
        b'executionHeaderStep',
        b'executionHeaderProgressValue',
        b'executionExit',
        b'executionAlerts',
    ):
        assert element_id in page.data

    assert b'experiment-detail-mode' in script.data
    assert b'step-primary-action' in script.data
    assert b'rememberDeviceSnapshots' in script.data
    assert b'renderExecutionAlerts' in script.data

    assert b'.page-experiment.experiment-detail-mode' in theme.data
    assert b'.execution-header-summary' in theme.data
    assert b'.step-primary-action' in theme.data
    assert b'.execution-timeline-panel' in theme.data
    assert b'tracePanel' in page.data
    assert b'intermediateTraceButton' in page.data
    assert b'retrieveTraceButton' in page.data
    assert b'init().catch' in script.data
    assert b"bindingForm" not in page.data
    assert b'input id="operator"' not in page.data
    assert b'input id="reviewer"' not in page.data


def test_create_list_and_get_experiment_api():
    app, _, _ = _app()
    client = app.test_client()
    created = client.post("/api/experiments", json=CREATE)
    assert created.status_code == 201
    data = created.get_json()
    assert data["batch_id"] == "20260723-AEM-01"
    assert data["downstream_route_variant"] == "AEM_WITHOUT_F801"
    assert client.get("/api/experiments").get_json()[0]["id"] == data["id"]
    detail = client.get(f"/api/experiments/{data['id']}").get_json()
    assert detail["experiment"]["batch_id"] == "20260723-AEM-01"
    assert detail["available_devices"][0]["type"] == "tyd02"


def test_next_batch_id_and_session_operator_api():
    app, _, _ = _app()
    client = app.test_client()

    session_data = client.get("/api/session").get_json()
    assert session_data["authenticated"] is True
    assert session_data["operator"] == "测试管理员"
    assert session_data["role"] == "super_admin"
    assert session_data["can_manage_accounts"] is True
    first = client.get(
        "/api/experiments/next-batch-id"
        "?membrane_system=AEM&date=20260723"
    )
    assert first.status_code == 200
    assert first.get_json()["batch_id"] == "20260723-AEM-01"

    assert client.post("/api/experiments", json=CREATE).status_code == 201
    second = client.get(
        "/api/experiments/next-batch-id"
        "?membrane_system=AEM&date=20260723"
    )
    assert second.get_json()["batch_id"] == "20260723-AEM-02"


def test_traceability_api_prints_and_tracks_intermediate_lifecycle():
    app, _, _ = _app()
    client = app.test_client()
    exp = client.post("/api/experiments", json=CREATE).get_json()

    location = client.post(
        "/api/storage-locations",
        json={
            "location_code": "FRIDGE-01-A2",
            "display_name": "冰箱01 · A2",
            "storage_condition": "4℃",
            "actor": "张三",
        },
    )
    assert location.status_code == 201

    created = client.post(
        f"/api/experiments/{exp['id']}/trace-items",
        json={
            "item_type": "intermediate",
            "display_name": "湿化学中间溶胶",
            "container_count": 1,
            "actor": "张三",
            "client_event_id": "web-trace-create-1",
        },
    )
    assert created.status_code == 201
    item = created.get_json()[0]
    assert item["item_code"].endswith("-IP01-01")

    stored = client.post(
        f"/api/trace-items/{item['id']}/store",
        json={
            "location_code": "FRIDGE-01-A2",
            "hold_hours": 24,
            "actor": "张三",
            "client_event_id": "web-trace-store-1",
        },
    )
    assert stored.status_code == 200
    assert stored.get_json()["status"] == "stored"

    retrieved = client.post(
        f"/api/trace-items/{item['id']}/retrieve",
        json={
            "actor": "张三",
            "client_event_id": "web-trace-retrieve-1",
        },
    )
    assert retrieved.status_code == 200
    assert retrieved.get_json()["status"] == "active"

    print_job = client.post(
        f"/api/experiments/{exp['id']}/trace-labels",
        json={
            "item_ids": [item["id"]],
            "reason": "initial",
            "copies": 1,
            "actor": "张三",
            "client_event_id": "web-trace-print-1",
        },
    )
    assert print_job.status_code == 201
    print_url = print_job.get_json()["print_url"]
    label = client.get(print_url)
    assert label.status_code == 200
    assert item["item_code"].encode() in label.data
    assert "打印标签".encode() in label.data
    assert "实验人".encode() in label.data

    qr = client.get(
        f"/api/trace/qr.png?code={item['item_code']}"
    )
    decoded = zxingcpp.read_barcodes(Image.open(BytesIO(qr.data)))
    assert qr.status_code == 200
    assert qr.mimetype == "image/png"
    assert qr.headers["X-QR-Code"] == item["item_code"]
    assert "PURICORE实验实物" in decoded[0].text
    assert f"编号：{item['item_code']}" in decoded[0].text
    assert "实验人：" in decoded[0].text

    traceability = client.get(
        f"/api/experiments/{exp['id']}/traceability"
    ).get_json()
    assert traceability["items"][1]["events"][-1]["event_type"] == (
        "label_print_requested"
    )
    assert len(traceability["print_jobs"]) == 1


def test_storage_location_label_and_qr_are_printable():
    app, _, _ = _app()
    client = app.test_client()
    location = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CABINET-02-B05",
            "display_name": "样品柜02 · B05",
            "storage_condition": "室温避光",
            "actor": "张三",
        },
    ).get_json()
    printed = client.post(
        f"/api/storage-locations/{location['id']}/print",
        json={
            "actor": "张三",
            "client_event_id": "location-print-1",
            "copies": 1,
        },
    )
    assert printed.status_code == 201
    label = client.get(printed.get_json()["print_url"])
    assert b"CABINET-02-B05" in label.data
    assert b"height:48mm" in label.data
    assert b"size:60mm 48mm" in label.data
    assert b"min-height:8mm" in label.data

    qr = client.get("/api/trace/qr.png?code=CABINET-02-B05")
    decoded = zxingcpp.read_barcodes(Image.open(BytesIO(qr.data)))
    assert qr.status_code == 200
    assert qr.mimetype == "image/png"
    assert "PURICORE存储位置" in decoded[0].text
    assert "编号：CABINET-02-B05" in decoded[0].text


def test_material_container_can_be_registered_and_resolved_by_scan():
    app, _, _ = _app()
    client = app.test_client()
    created = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-TEOS-0001",
            "external_barcode": "6901234567890",
            "material_name": "TEOS",
            "supplier": "测试供应商",
            "supplier_lot": "LOT-202607",
            "expires_on": "2027-07-01",
            "quantity_remaining": 500,
            "unit": "mL",
        },
    )
    resolved = client.get(
        "/api/scan/resolve?code=6901234567890"
    )

    assert created.status_code == 201
    assert resolved.status_code == 200
    assert resolved.get_json()["kind"] == "material_container"
    assert resolved.get_json()["material"]["material_name"] == "TEOS"


def test_material_container_legacy_endpoint_keeps_compliance_fields():
    app, _, _ = _app()
    client = app.test_client()

    created = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-HCL-0001",
            "material_name": "盐酸",
            "quantity_remaining": 500,
            "unit": "mL",
            "hazardous_status": "listed",
            "storage_group": "acid",
            "sds_url": "https://example.invalid/hcl-sds",
            "sds_verified": True,
            "controlled_categories": ["易制毒第三类"],
            "dual_control_required": True,
            "dual_control_reason": "单位内部加严",
        },
    )

    assert created.status_code == 201
    body = created.get_json()
    assert body["hazardous_status"] == "listed"
    assert body["storage_group"] == "acid"
    assert body["controlled_categories"] == ["易制毒第三类"]
    assert body["dual_control_required"] is True


def test_material_management_page_and_offline_qr_are_available():
    app, _, _ = _app()
    client = app.test_client()
    page = client.get("/materials")
    assert page.status_code == 200
    assert b"inventory.js" in page.data

    created = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-ETOH-0001",
            "material_name": "乙醇",
            "quantity_remaining": 1000,
            "unit": "mL",
        },
    ).get_json()
    qr = client.get(
        f"/api/trace/qr.png?code={created['container_code']}"
    )
    label = client.get(
        f"/api/material-containers/{created['id']}/label"
    )
    detail = client.get(
        f"/api/material-containers/{created['id']}"
    )

    assert qr.status_code == 200
    decoded = zxingcpp.read_barcodes(Image.open(BytesIO(qr.data)))
    assert qr.headers["X-QR-Code"] == "RM-ETOH-0001"
    assert "PURICORE原材料" in decoded[0].text
    assert "编号：RM-ETOH-0001" in decoded[0].text
    assert "实验人：" in decoded[0].text
    assert label.status_code == 200
    assert "实验人".encode() in label.data
    assert "原材料标签".encode() in label.data
    assert detail.status_code == 200
    assert detail.get_json()["events"][0]["event_type"] == "registered"
    redirect_response = client.get(
        f"/scan/{created['container_code']}",
        follow_redirects=False,
    )
    assert redirect_response.headers["Location"] == (
        f"/inventory?scan={created['container_code']}"
    )


def test_material_and_location_labels_can_be_printed_in_batches():
    app, _, _ = _app()
    client = app.test_client()
    first = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-BATCH-0001",
            "material_name": "批量标签原料一",
            "quantity_remaining": 100,
            "unit": "g",
        },
    ).get_json()
    second = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-BATCH-0002",
            "material_name": "批量标签原料二",
            "quantity_remaining": 200,
            "unit": "g",
        },
    ).get_json()
    materials = client.get(
        "/api/material-containers/labels",
        query_string={"ids": f"{first['id']},{second['id']}"},
    )
    location_one = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-01-U-S01",
            "display_name": "上柜第1层",
            "actor": "张三",
        },
    ).get_json()
    location_two = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-01-U-S02",
            "display_name": "上柜第2层",
            "actor": "张三",
        },
    ).get_json()
    locations = client.get(
        "/api/storage-locations/labels",
        query_string={
            "ids": f"{location_one['id']},{location_two['id']}"
        },
    )

    assert materials.status_code == 200
    assert b"RM-BATCH-0001" in materials.data
    assert b"RM-BATCH-0002" in materials.data
    assert locations.status_code == 200
    assert b"CAB-01-U-S01" in locations.data
    assert b"CAB-01-U-S02" in locations.data


def test_camera_frame_can_be_decoded_on_server_for_ios_fallback():
    app, _, _ = _app()
    client = app.test_client()
    barcode = zxingcpp.create_barcode(
        "https://lab.puricore.example/scan/RM-TEOS-0001",
        zxingcpp.BarcodeFormat.QRCode,
    )
    image = Image.fromarray(barcode.to_image(scale=5))
    payload = BytesIO()
    image.save(payload, format="PNG")
    payload.seek(0)

    response = client.post(
        "/api/scan/decode",
        data={"image": (payload, "camera-frame.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["code"].endswith("/scan/RM-TEOS-0001")
    assert response.get_json()["format"] == "QR Code"


def test_offline_qr_text_resolves_by_embedded_identity():
    app, _, _ = _app()
    client = app.test_client()
    created = client.post(
        "/api/material-containers",
        json={
            "container_code": "RM-TEXT-0001",
            "material_name": "离线文本原料",
            "quantity_remaining": 50,
            "unit": "mL",
        },
    ).get_json()
    payload = "\n".join(
        [
            "PURICORE原材料",
            "类型：原材料",
            "名称：离线文本原料",
            f"编号：{created['container_code']}",
        ]
    )

    response = client.get("/api/scan/resolve", query_string={"code": payload})

    assert response.status_code == 200
    assert response.get_json()["kind"] == "material_container"
    assert response.get_json()["material"]["container_code"] == (
        "RM-TEXT-0001"
    )


def test_camera_decode_rejects_missing_invalid_and_barcode_free_images():
    app, _, _ = _app()
    client = app.test_client()
    missing = client.post("/api/scan/decode")
    invalid = client.post(
        "/api/scan/decode",
        data={"image": (BytesIO(b"not-an-image"), "bad.jpg")},
        content_type="multipart/form-data",
    )
    blank_data = BytesIO()
    Image.new("RGB", (200, 200), "white").save(
        blank_data, format="PNG"
    )
    blank_data.seek(0)
    blank = client.post(
        "/api/scan/decode",
        data={"image": (blank_data, "blank.png")},
        content_type="multipart/form-data",
    )

    assert missing.status_code == 400
    assert invalid.status_code == 400
    assert blank.status_code == 422


def test_scan_url_redirects_to_the_relevant_batch():
    app, _, _ = _app()
    client = app.test_client()
    experiment = client.post(
        "/api/experiments",
        json={**CREATE, "batch_id": "20260724-AEM-88"},
    ).get_json()

    response = client.get(
        "/scan/20260724-AEM-88",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/experiments/{experiment['id']}?scan=20260724-AEM-88"
    )


def test_create_experiment_validates_input_and_duplicate_batch():
    app, _, _ = _app()
    client = app.test_client()
    bad = client.post("/api/experiments", json={**CREATE, "membrane_system": "X"})
    assert bad.status_code == 400
    assert "membrane_system" in bad.get_json()["error"]
    assert client.post("/api/experiments", json=CREATE).status_code == 201
    duplicate = client.post("/api/experiments", json=CREATE)
    assert duplicate.status_code == 409


def test_create_experiment_retry_with_same_client_event_is_idempotent():
    app, repo, _ = _app()
    client = app.test_client()
    payload = {
        **CREATE,
        "client_event_id": "create-offline-retry-1",
        "occurred_at_client_ms": 1_000,
        "client_clock_offset_ms": 0,
        "clock_sync_status": "trusted",
    }

    first = client.post("/api/experiments", json=payload)
    second = client.post("/api/experiments", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.get_json()["id"] == first.get_json()["id"]
    assert len(repo.experiments.list_experiment_events(first.get_json()["id"])) == 1


def test_step_api_is_idempotent_and_checks_version():
    app, _, _ = _app()
    client = app.test_client()
    exp = client.post("/api/experiments", json=CREATE).get_json()
    body = {
        "row_version": exp["row_version"],
        "client_event_id": "start-1",
        "occurred_at_client_ms": 1000,
        "clock_sync_status": "trusted",
        "client_clock_offset_ms": 0,
        "actor": "张三",
    }
    first = client.post(f"/api/experiments/{exp['id']}/steps/R201-01/start", json=body)
    assert first.status_code == 200
    second = client.post(f"/api/experiments/{exp['id']}/steps/R201-01/start", json=body)
    assert second.status_code == 200
    assert second.get_json()["experiment"]["row_version"] == first.get_json()["experiment"]["row_version"]
    stale = client.post(
        f"/api/experiments/{exp['id']}/steps/R201-01/complete",
        json={
            **body,
            "client_event_id": "complete-1",
            "result": {"environment_temp_c": 25, "environment_humidity_rh": 50, "device_checks": ["pump"]},
        },
    )
    assert stale.status_code == 409


def test_data_source_binding_and_sse_snapshot():
    app, _, did = _app()
    client = app.test_client()
    exp = client.post("/api/experiments", json=CREATE).get_json()
    binding_payload = {
        "client_event_id": "bind-source-1",
        "occurred_at_client_ms": 1000,
        "client_clock_offset_ms": 0,
        "clock_sync_status": "trusted",
        "actor": "张三",
        "device_id": did,
        "device_role": "acid_pump",
        "metric_key": "acc_volume",
        "channel_selector": "1",
        "linked_at_ms": 1000,
        "link_method": "manual",
    }
    response = client.post(
        f"/api/experiments/{exp['id']}/data-sources",
        json=binding_payload,
    )
    assert response.status_code == 201
    repeated = client.post(
        f"/api/experiments/{exp['id']}/data-sources",
        json=binding_payload,
    )
    assert repeated.status_code == 201
    assert repeated.get_json()["id"] == response.get_json()["id"]
    detail = client.get(f"/api/experiments/{exp['id']}").get_json()
    assert len(detail["data_sources"]) == 1
    assert detail["data_sources"][0]["device_role"] == "acid_pump"
    assert detail["process_status"]["acid_pump"]["latest"]["acc_volume"] == 105
    assert (
        detail["process_status"]["acid_pump"]["latest"]["metrics"]["inject_rate"]
        == 0.5
    )
    assert client.post(
        f"/api/experiments/{exp['id']}/evaluate-telemetry"
    ).status_code == 200
    stream = client.get(
        f"/api/experiments/{exp['id']}/stream?once=1"
    )
    assert stream.status_code == 200
    assert stream.content_type.startswith("text/event-stream")
    body = stream.data.decode()
    assert "event: snapshot" in body
    assert '"acc_volume": 105' in body
    assert '"telemetry_integrity_status":' in body
    assert "temperature_series" not in body
    script = client.get("/static/experiment.js").data
    assert b"setInterval(connectLive" not in script
    assert b"setInterval(syncRevision" not in script
    assert b"appendLiveTemperature" in script


def test_experiment_revision_changes_after_another_client_writes():
    app, _, _ = _app()
    creator = app.test_client()
    observer = app.test_client()
    exp = creator.post("/api/experiments", json=CREATE).get_json()
    before = observer.get(
        f"/api/experiments/{exp['id']}/revision"
    ).get_json()

    response = creator.post(
        f"/api/experiments/{exp['id']}/steps/R201-01/start",
        json={
            "row_version": exp["row_version"],
            "client_event_id": "cross-client-start",
        },
    )
    after = observer.get(
        f"/api/experiments/{exp['id']}/revision"
    ).get_json()

    assert response.status_code == 200
    assert after["revision"] != before["revision"]
    assert after["row_version"] == 1


def test_same_device_cannot_be_reserved_by_two_active_batches():
    app, _, device_ids = _multi_stirrer_app()
    client = app.test_client()
    first = client.post(
        "/api/experiments", json=CREATE
    ).get_json()
    second = client.post(
        "/api/experiments",
        json={**CREATE, "batch_id": "20260723-AEM-02"},
    ).get_json()
    device_id = device_ids[0]

    claimed = client.post(
        f"/api/experiments/{first['id']}/device-bindings",
        json={"client_event_id": "reserve-first", "device_id": device_id},
    )
    conflict = client.post(
        f"/api/experiments/{second['id']}/device-bindings",
        json={"client_event_id": "reserve-second", "device_id": device_id},
    )

    assert claimed.status_code == 200
    assert conflict.status_code == 409
    assert first["batch_id"] in conflict.get_json()["error"]


def test_automatic_binding_reserves_unique_stirrer_and_never_double_assigns():
    app, repo, device_id = _single_stirrer_app()
    client = app.test_client()
    first = client.post("/api/experiments", json=CREATE).get_json()
    second = client.post(
        "/api/experiments",
        json={**CREATE, "batch_id": "20260723-AEM-02"},
    ).get_json()
    repo._conn.execute(
        """UPDATE experiment SET current_step_code='R201-10'
           WHERE id IN (?,?)""",
        (first["id"], second["id"]),
    )
    repo._conn.commit()

    for experiment in (first, second):
        started = client.post(
            f"/api/experiments/{experiment['id']}/steps/R201-10/start",
            json={
                "row_version": experiment["row_version"],
                "client_event_id": f"start-{experiment['id']}",
            },
        )
        assert started.status_code == 200

    first_detail = client.get(
        f"/api/experiments/{first['id']}"
    ).get_json()
    second_detail = client.get(
        f"/api/experiments/{second['id']}"
    ).get_json()
    workbench = client.get("/api/workbench").get_json()

    assert first_detail["process_status"]["stirrer"]["bound"] is True
    assert (
        first_detail["process_status"]["stirrer"]["device"]["id"]
        == device_id
    )
    assert second_detail["process_status"]["stirrer"]["bound"] is False
    assert workbench["active_count"] == 2
    assert len(workbench["reservations"]) == 1
    assert workbench["reservations"][0]["experiment_id"] == first["id"]


def test_experiment_detail_get_does_not_create_bindings_or_reservations():
    app, repo, _ = _single_stirrer_app()
    client = app.test_client()
    experiment = client.post("/api/experiments", json=CREATE).get_json()
    repo._conn.execute(
        "UPDATE experiment SET current_step_code='R201-10' WHERE id=?",
        (experiment["id"],),
    )
    repo._conn.commit()

    response = client.get(f"/api/experiments/{experiment['id']}")

    assert response.status_code == 200
    assert repo.experiments.list_data_source_bindings(experiment["id"]) == []
    assert repo.experiments.list_device_reservations(experiment["id"]) == []


def test_multiple_stirrers_are_visible_and_batch_selection_is_explicit():
    app, repo, device_ids = _multi_stirrer_app()
    client = app.test_client()
    experiment = client.post("/api/experiments", json=CREATE).get_json()

    initial = client.get(
        f"/api/experiments/{experiment['id']}"
    ).get_json()
    stirrers = [
        item
        for item in initial["available_devices"]
        if item["type"] == "stirrer"
    ]
    assert len(stirrers) == 5
    assert initial["process_status"]["stirrer"]["bound"] is False
    assert initial["process_status"]["reaction_temp"]["bound"] is False

    selected_id = device_ids[3]
    response = client.post(
        f"/api/experiments/{experiment['id']}/device-bindings",
        json={
            "client_event_id": "select-stirrer-1",
            "occurred_at_client_ms": 1_000,
            "clock_sync_status": "trusted",
            "client_clock_offset_ms": 0,
            "selected_at_ms": 1_000,
            "actor": "张三",
            "device_id": selected_id,
        },
    )
    assert response.status_code == 200
    selected = response.get_json()["detail"]
    assert selected["process_status"]["stirrer"]["device"]["id"] == selected_id
    assert (
        selected["process_status"]["reaction_temp"]["device"]["id"]
        == selected_id
    )
    active = [
        item
        for item in selected["data_sources"]
        if item["unlinked_at_ms"] is None
    ]
    assert {item["device_role"] for item in active} == {
        "stirrer",
        "reaction_temp",
    }
    assert {item["device_id"] for item in active} == {selected_id}

    replacement_id = device_ids[1]
    replaced = client.post(
        f"/api/experiments/{experiment['id']}/device-bindings",
        json={
            "client_event_id": "select-stirrer-2",
            "occurred_at_client_ms": 2_000,
            "clock_sync_status": "trusted",
            "client_clock_offset_ms": 0,
            "selected_at_ms": 2_000,
            "actor": "张三",
            "device_id": replacement_id,
        },
    )
    assert replaced.status_code == 200
    history = repo.experiments.list_data_source_bindings(
        experiment["id"]
    )
    assert len(history) == 4
    assert sum(item["unlinked_at_ms"] is None for item in history) == 2
    assert {
        item["device_id"]
        for item in history
        if item["unlinked_at_ms"] is None
    } == {replacement_id}


def test_experiment_report_pdf():
    app, _, _ = _app()
    client = app.test_client()
    exp = client.post("/api/experiments", json=CREATE).get_json()
    report = client.get(f"/api/experiments/{exp['id']}/report.pdf")
    assert report.status_code == 200
    assert report.content_type == "application/pdf"
    assert report.data[:4] == b"%PDF"


def test_missing_experiment_returns_404():
    app, _, _ = _app()
    client = app.test_client()
    assert client.get("/api/experiments/999").status_code == 404
    assert client.get("/experiments/999").status_code == 200
