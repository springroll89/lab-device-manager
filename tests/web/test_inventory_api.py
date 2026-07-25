import csv
import io
import time

from werkzeug.security import generate_password_hash

from lab_device_manager.db.repository import Repository
from lab_device_manager.web.app import create_app


class EmptyEngine:
    def latest(self):
        return {}

    def device_map(self):
        return {}


def _app():
    repo = Repository(":memory:")
    app = create_app(EmptyEngine(), repo, secret_key="test-secret")
    app.config.update(TESTING=True, AUTH_TEST_BYPASS=True)
    return app, repo


def _create_payload(**overrides):
    body = {
        "name": "无水乙醇",
        "category": "chemical",
        "quantity": 10,
        "unit": "L",
        "location": "危化品柜 A1",
        "owner": "实验室",
        "min_threshold": 3,
        "max_threshold": 20,
        "is_controlled": True,
        "hazardous_status": "listed",
        "controlled_categories": ["易制毒第三类"],
        "cas_no": "64-17-5",
        "spec": "AR 99.7%",
        "hazards": ["易燃"],
        "sds_url": "https://example.test/sds/ethanol",
        "lot_no": "LOT-202607",
        "expiry_date": "2027-07-01",
        "client_event_id": f"create-{time.time_ns()}",
    }
    body.update(overrides)
    return body


def _create(client, **overrides):
    body = _create_payload(**overrides)
    response = client.post("/api/inventory/items", json=body)
    assert response.status_code == 201
    return response.get_json()


def test_inventory_page_and_auto_numbered_three_category_items():
    app, _ = _app()
    client = app.test_client()

    chemical = _create(client)
    consumable = _create(
        client,
        name="丁腈手套",
        category="consumable",
        unit="盒",
        cas_no=None,
        hazards=[],
        sds_url=None,
        lot_no="GLOVE-01",
    )
    office = _create(
        client,
        name="标签纸",
        category="office",
        unit="卷",
        cas_no=None,
        hazards=[],
        sds_url=None,
        lot_no="LABEL-01",
    )

    assert chemical["container_code"] == "PC-0001"
    assert consumable["container_code"] == "PC-0002"
    assert office["container_code"] == "PC-0003"
    legacy_materials = client.get("/api/material-containers").get_json()
    assert [row["id"] for row in legacy_materials] == [chemical["id"]]
    assert client.get("/inventory").status_code == 200
    assert client.get("/materials").status_code == 200


def test_inventory_filters_dashboard_detail_and_audit():
    app, _ = _app()
    client = app.test_client()
    item = _create(
        client,
        quantity=2,
        min_threshold=3,
        expiry_date="2026-07-30",
    )

    filtered = client.get(
        "/api/inventory/items",
        query_string={
            "search": "乙醇",
            "category": "chemical",
            "controlled": "true",
        },
    )
    summary = client.get(
        "/api/inventory/summary",
        query_string={"today": "2026-07-25"},
    )
    detail = client.get(f"/api/inventory/items/{item['id']}")
    audit = client.get("/api/inventory/movements")

    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.get_json()] == [item["id"]]
    assert summary.get_json()["low_stock"] == 1
    assert summary.get_json()["expiring"] == 1
    assert detail.get_json()["item"]["hazards"] == ["易燃"]
    assert detail.get_json()["item"]["hazardous_status"] == "listed"
    assert detail.get_json()["item"]["controlled_categories"] == [
        "易制毒第三类"
    ]
    assert audit.get_json()["movements"][0]["action"] == "registered"


def test_inventory_issue_adjust_and_csv_export():
    app, _ = _app()
    client = app.test_client()
    item = _create(client, quantity=5, unit="瓶")

    issued = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 2,
            "note": "常规领用",
            "client_event_id": "api-issue-1",
        },
    )
    adjusted = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "adjusted",
            "actual_quantity": 4,
            "note": "盘点修正",
            "client_event_id": "api-adjust-1",
        },
    )
    insufficient = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 10,
            "client_event_id": "api-issue-too-much",
        },
    )
    exported = client.get("/api/inventory/export.csv")

    assert issued.status_code == 200
    assert issued.get_json()["item"]["quantity_remaining"] == 3
    assert issued.get_json()["movement"]["actor"] == "测试管理员"
    assert adjusted.get_json()["item"]["quantity_remaining"] == 4
    assert insufficient.status_code == 409
    rows = list(csv.DictReader(io.StringIO(exported.data.decode("utf-8-sig"))))
    assert rows[0]["系统编号"] == item["container_code"]
    assert rows[0]["当前库存"] == "4.0"


def test_inventory_validates_category_units_dates_and_urls():
    app, _ = _app()
    client = app.test_client()

    invalid_category = client.post(
        "/api/inventory/items",
        json={
            "name": "错误类别",
            "category": "unknown",
            "quantity": 1,
            "unit": "个",
            "client_event_id": "bad-category",
        },
    )
    invalid_sds = client.post(
        "/api/inventory/items",
        json={
            "name": "危险链接",
            "category": "chemical",
            "quantity": 1,
            "unit": "瓶",
            "sds_url": "javascript:alert(1)",
            "client_event_id": "bad-sds",
        },
    )
    invalid_threshold = client.post(
        "/api/inventory/items",
        json={
            "name": "错误阈值",
            "category": "consumable",
            "quantity": 1,
            "unit": "盒",
            "min_threshold": 5,
            "max_threshold": 2,
            "client_event_id": "bad-threshold",
        },
    )

    assert invalid_category.status_code == 400
    assert invalid_sds.status_code == 400
    assert invalid_threshold.status_code == 400

    invalid_hazardous_status = client.post(
        "/api/inventory/items",
        json={
            **_create_payload(),
            "client_event_id": "invalid-hazardous-status",
            "hazardous_status": "definitely-dangerous",
        },
    )
    invalid_control_category = client.post(
        "/api/inventory/items",
        json={
            **_create_payload(),
            "client_event_id": "invalid-control-category",
            "controlled_categories": ["未定义类别"],
        },
    )

    assert invalid_hazardous_status.status_code == 400
    assert invalid_control_category.status_code == 400


def test_inventory_item_can_be_edited_without_changing_identity_or_balance():
    app, _ = _app()
    client = app.test_client()
    item = _create(client, quantity=8)

    response = client.patch(
        f"/api/inventory/items/{item['id']}",
        json={
            "name": "无水乙醇（新供应商）",
            "location": "危化品柜 B2",
            "min_threshold": 2,
            "max_threshold": 12,
            "hazards": ["易燃", "有害/刺激"],
            "hazardous_status": "pending_review",
            "controlled_categories": ["内部受控"],
        },
    )

    assert response.status_code == 200
    updated = response.get_json()
    assert updated["container_code"] == item["container_code"]
    assert updated["quantity_remaining"] == 8
    assert updated["material_name"] == "无水乙醇（新供应商）"
    assert updated["location"] == "危化品柜 B2"
    assert updated["hazards"] == ["易燃", "有害/刺激"]
    assert updated["hazardous_status"] == "pending_review"
    assert updated["controlled_categories"] == ["内部受控"]
    assert updated["is_controlled"] is True


def test_operator_can_view_and_issue_but_cannot_manage_inventory():
    repo = Repository(":memory:")
    app = create_app(EmptyEngine(), repo, secret_key="test-secret")
    app.config.update(TESTING=True)
    operator = repo.accounts.create_user(
        username="inventory01",
        display_name="库存操作员",
        password_hash=generate_password_hash("Inventory123"),
        role="operator",
        created_by=1,
    )
    repo.accounts.update_password(
        user_id=operator["id"],
        password_hash=generate_password_hash("Inventory456"),
        actor_user_id=operator["id"],
        must_change_password=False,
        action="password_changed",
    )
    item = repo.inventory.create_item(
        {
            "container_code": "PC-0100",
            "material_name": "实验耗材",
            "category": "consumable",
            "quantity_remaining": 5,
            "unit": "盒",
            "status": "available",
            "created_at_ms": 1000,
            "created_by": "系统管理员",
            "created_by_user_id": 1,
            "client_event_id": "role-register-1",
        }
    )
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = operator["id"]
        session["csrf_token"] = "inventory-csrf"

    assert client.get("/inventory").status_code == 200
    assert client.get("/api/inventory/items").status_code == 200
    forbidden_create = client.post(
        "/api/inventory/items",
        json={
            "name": "不允许创建",
            "category": "office",
            "quantity": 1,
            "unit": "个",
            "client_event_id": "operator-create",
        },
        headers={"X-CSRF-Token": "inventory-csrf"},
    )
    forbidden_adjust = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "adjusted",
            "actual_quantity": 4,
            "client_event_id": "operator-adjust",
        },
        headers={"X-CSRF-Token": "inventory-csrf"},
    )
    issued = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "client_event_id": "operator-issue",
        },
        headers={"X-CSRF-Token": "inventory-csrf"},
    )

    assert forbidden_create.status_code == 403
    assert forbidden_adjust.status_code == 403
    assert issued.status_code == 200
    assert issued.get_json()["movement"]["actor"] == "库存操作员"
    assert issued.get_json()["item"]["quantity_remaining"] == 4


def test_inventory_mutations_require_csrf_outside_test_bypass():
    repo = Repository(":memory:")
    app = create_app(EmptyEngine(), repo, secret_key="test-secret")
    app.config.update(TESTING=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["csrf_token"] = "expected-token"
    repo.accounts.update_password(
        user_id=1,
        password_hash=generate_password_hash("Admin1234"),
        actor_user_id=1,
        must_change_password=False,
        action="password_changed",
    )

    response = client.post(
        "/api/inventory/items",
        json={
            "name": "缺少令牌",
            "category": "office",
            "quantity": 1,
            "unit": "个",
            "client_event_id": "no-csrf",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "invalid_csrf_token"


def test_inventory_audit_and_disposal_exports_preserve_operational_records():
    app, _ = _app()
    client = app.test_client()
    item = _create(
        client,
        name="=酸性废液",
        quantity=3,
        unit="L",
        hazards=["腐蚀"],
        note="盐酸体系",
        owner="王工",
    )
    client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "note": "实验领用",
            "client_event_id": "export-issue",
        },
    )
    client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "quarantined",
            "note": "等待危废处置",
            "client_event_id": "export-quarantine",
        },
    )

    filtered = client.get(
        "/api/inventory/movements",
        query_string={"action": "issued"},
    ).get_json()
    audit_csv = client.get(
        "/api/inventory/movements.csv",
        query_string={"action": "issued"},
    )
    disposal_csv = client.get("/api/inventory/disposal.csv")

    assert filtered["total"] == 1
    assert filtered["movements"][0]["client_event_id"] == "export-issue"
    assert audit_csv.status_code == 200
    assert "操作类型".encode() in audit_csv.data
    assert "实验领用".encode() in audit_csv.data
    assert disposal_csv.status_code == 200
    disposal_text = disposal_csv.data.decode("utf-8-sig")
    assert "名称,危险性,主要成分,负责人,数量,单位,位置" in disposal_text
    assert "'=酸性废液" in disposal_text
