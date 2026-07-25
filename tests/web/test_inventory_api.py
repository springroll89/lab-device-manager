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
        "location": "CAB-FLAM-01",
        "owner": "实验室",
        "min_threshold": 3,
        "max_threshold": 20,
        "is_controlled": True,
        "hazardous_status": "listed",
        "controlled_categories": ["内部受控"],
        "cas_no": "64-17-5",
        "spec": "AR 99.7%",
        "hazards": ["易燃"],
        "ghs_pictograms": ["GHS02"],
        "storage_group": "flammable",
        "sds_url": "https://example.test/sds/ethanol",
        "sds_revision": "2026-01-01",
        "sds_verified": True,
        "catalog_source": "危险化学品目录",
        "catalog_version": "2015版（含2022调整）",
        "catalog_entry_no": "2568",
        "regulatory_review_confirmed": True,
        "source_organization": "合作大学",
        "handover_document_no": "HD-202607-001",
        "received_date": "2026-07-25",
        "received_by": "接收人甲",
        "accepted_by": "验收人乙",
        "lot_no": "LOT-202607",
        "expiry_date": "2027-07-01",
        "client_event_id": f"create-{time.time_ns()}",
    }
    body.update(overrides)
    return body


def _create(client, **overrides):
    if not client.get("/api/storage-locations").get_json():
        location = client.post(
            "/api/storage-locations",
            json={
                "location_code": "CAB-FLAM-01",
                "display_name": "易燃液体柜 1",
                "location_type": "flammable_cabinet",
                "allowed_storage_groups": ["flammable"],
                "physical_controls": ["通风", "防泄漏托盘", "防火"],
            },
        )
        assert location.status_code == 201
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
    assert detail.get_json()["item"]["controlled_categories"] == ["内部受控"]
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
    assert rows[0]["来源单位"] == "合作大学"
    assert rows[0]["交付/领用凭证编号"] == "HD-202607-001"
    assert rows[0]["SDS核验人"] == "测试管理员"
    assert "来源方许可/备案编号" in rows[0]


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

    location = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-FLAM-02",
            "display_name": "易燃液体柜 2",
            "location_type": "flammable_cabinet",
            "allowed_storage_groups": ["flammable"],
            "physical_controls": ["通风", "防泄漏托盘", "防火"],
        },
    )
    assert location.status_code == 201
    response = client.patch(
        f"/api/inventory/items/{item['id']}",
        json={
            "name": "无水乙醇（新供应商）",
            "location": "CAB-FLAM-02",
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
    assert updated["location"] == "CAB-FLAM-02"
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
    assert "第二确认人".encode() in audit_csv.data
    assert "实验领用".encode() in audit_csv.data
    assert disposal_csv.status_code == 200
    disposal_text = disposal_csv.data.decode("utf-8-sig")
    assert "名称,危险性,主要成分,负责人,数量,单位,位置" in disposal_text
    assert "'=酸性废液" in disposal_text


def test_hazardous_item_is_blocked_until_sds_location_and_review_are_ready():
    app, _ = _app()
    client = app.test_client()
    item = _create(
        client,
        sds_verified=False,
        location=None,
        storage_group="unassessed",
    )

    issued = client.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "client_event_id": "blocked-incomplete",
        },
    )

    assert issued.status_code == 409
    assert "合规资料不完整" in issued.get_json()["error"]


def test_changed_sds_or_catalog_evidence_invalidates_old_review():
    app, _ = _app()
    client = app.test_client()
    listed = _create(client)

    changed_sds = client.patch(
        f"/api/inventory/items/{listed['id']}",
        json={
            "sds_url": "https://example.test/sds/ethanol-revised",
            "sds_revision": "2026-07-25",
            "sds_verified": False,
        },
    )
    listed_issue = client.post(
        f"/api/inventory/items/{listed['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "client_event_id": "changed-sds-issue",
        },
    )

    not_listed = _create(
        client,
        name="目录外测试化学品",
        hazardous_status="not_listed",
        controlled_categories=[],
        is_controlled=False,
        cas_no="7732-18-5",
        client_event_id="create-not-listed-reviewed",
    )
    changed_cas = client.patch(
        f"/api/inventory/items/{not_listed['id']}",
        json={
            "cas_no": "7440-44-0",
            "regulatory_review_confirmed": False,
        },
    )
    not_listed_issue = client.post(
        f"/api/inventory/items/{not_listed['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "client_event_id": "changed-cas-issue",
        },
    )

    assert changed_sds.status_code == 200
    assert changed_sds.get_json()["sds_verified_at_ms"] is None
    assert listed_issue.status_code == 409
    assert "SDS" in listed_issue.get_json()["error"]
    assert changed_cas.status_code == 200
    assert changed_cas.get_json()["regulatory_reviewed_at_ms"] is None
    assert not_listed_issue.status_code == 409
    assert "法规复核" in not_listed_issue.get_json()["error"]


def test_storage_location_rejects_incompatible_or_unapproved_groups():
    app, _ = _app()
    client = app.test_client()

    incompatible = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-MIXED-01",
            "display_name": "错误混存柜",
            "location_type": "chemical_cabinet",
            "allowed_storage_groups": ["flammable", "oxidizer"],
            "physical_controls": ["通风"],
        },
    )
    acid_only = client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-ACID-01",
            "display_name": "酸柜",
            "location_type": "acid_alkali_cabinet",
            "allowed_storage_groups": ["acid"],
            "physical_controls": ["通风", "防泄漏托盘"],
        },
    )
    wrong_item = client.post(
        "/api/inventory/items",
        json={
            **_create_payload(),
            "client_event_id": "wrong-location-group",
            "location": "CAB-ACID-01",
            "storage_group": "flammable",
        },
    )

    assert incompatible.status_code == 400
    assert "禁忌储存组" in incompatible.get_json()["error"]
    assert acid_only.status_code == 201
    assert wrong_item.status_code == 400
    assert "未允许存放" in wrong_item.get_json()["error"]


def test_storage_location_edit_cannot_invalidate_items_already_inside():
    app, _ = _app()
    client = app.test_client()
    item = _create(client)
    location = next(
        row
        for row in client.get("/api/storage-locations").get_json()
        if row["location_code"] == "CAB-FLAM-01"
    )
    invalid_edit = client.patch(
        f"/api/storage-locations/{location['id']}",
        json={
            "display_name": "被错误修改的柜",
            "location_type": "acid_alkali_cabinet",
            "allowed_storage_groups": ["acid"],
            "physical_controls": ["通风", "防泄漏托盘"],
        },
    )
    valid_edit = client.patch(
        f"/api/storage-locations/{location['id']}",
        json={
            "display_name": "易燃液体柜（已复核）",
            "location_type": "flammable_cabinet",
            "allowed_storage_groups": ["flammable"],
            "physical_controls": ["通风", "防泄漏托盘", "防火"],
            "compliance_note": "现场复核完成",
        },
    )

    assert item["storage_group"] == "flammable"
    assert invalid_edit.status_code == 409
    assert "仍有" in invalid_edit.get_json()["error"]
    assert valid_edit.status_code == 200
    assert valid_edit.get_json()["display_name"] == "易燃液体柜（已复核）"


def test_dual_control_requires_a_different_authorized_account():
    repo = Repository(":memory:")
    app = create_app(EmptyEngine(), repo, secret_key="test-secret")
    app.config.update(TESTING=True)
    users = []
    for username, display_name, role in (
        ("requester01", "领用人甲", "supervisor"),
        ("reviewer01", "复核人乙", "supervisor"),
    ):
        user = repo.accounts.create_user(
            username=username,
            display_name=display_name,
            password_hash=generate_password_hash("Temporary123"),
            role=role,
            created_by=1,
        )
        repo.accounts.update_password(
            user_id=user["id"],
            password_hash=generate_password_hash("Changed123"),
            actor_user_id=user["id"],
            must_change_password=False,
            action="password_changed",
        )
        users.append(repo.accounts.get_user_by_id(user["id"]))
    location = repo.inventory.create_storage_location(
        {
            "location_code": "CAB-TOXIC-01",
            "display_name": "剧毒品双锁柜",
            "storage_condition": None,
            "created_at_ms": 1000,
            "created_by": "系统管理员",
            "location_type": "controlled_cabinet",
            "allowed_storage_groups": ["toxic"],
            "requires_dual_control": True,
            "physical_controls": ["双人双锁", "视频监控"],
            "compliance_note": "测试库位",
        }
    )
    item = repo.inventory.create_item(
        {
            "container_code": "PC-DUAL-01",
            "material_name": "受双人控制试剂",
            "category": "chemical",
            "quantity_remaining": 5,
            "unit": "g",
            "status": "available",
            "location": location["location_code"],
            "storage_location_id": location["id"],
            "storage_group": "toxic",
            "hazards": ["有毒"],
            "ghs_pictograms": ["GHS06"],
            "hazardous_status": "listed",
            "controlled_categories": ["剧毒"],
            "sds_url": "https://example.test/sds/toxic",
            "sds_verified_at_ms": 1000,
            "sds_verified_by": "系统管理员",
            "source_organization": "合作大学",
            "handover_document_no": "HD-DUAL-001",
            "dual_control_required": True,
            "created_at_ms": 1000,
            "created_by": "系统管理员",
            "created_by_user_id": 1,
            "client_event_id": "dual-register",
        }
    )
    requester = app.test_client()
    reviewer = app.test_client()
    with requester.session_transaction() as session:
        session["user_id"] = users[0]["id"]
        session["csrf_token"] = "requester-csrf"
    with reviewer.session_transaction() as session:
        session["user_id"] = users[1]["id"]
        session["csrf_token"] = "reviewer-csrf"

    requested = requester.post(
        f"/api/inventory/items/{item['id']}/movement-approvals",
        json={
            "action": "issued",
            "quantity": 2,
            "client_event_id": "dual-issue-1",
        },
        headers={"X-CSRF-Token": "requester-csrf"},
    )
    approval_id = requested.get_json()["approval"]["id"]
    same_person = requester.post(
        f"/api/inventory/movement-approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers={"X-CSRF-Token": "requester-csrf"},
    )
    forged = requester.post(
        f"/api/inventory/items/{item['id']}/movements",
        json={
            "action": "issued",
            "quantity": 1,
            "client_event_id": "forged-dual-issue",
            "approved_by": "伪造复核人",
            "approved_by_user_id": users[1]["id"],
            "approved_at_ms": 2000,
        },
        headers={"X-CSRF-Token": "requester-csrf"},
    )
    approved = reviewer.post(
        f"/api/inventory/movement-approvals/{approval_id}/decision",
        json={"decision": "approve", "note": "账物核对无误"},
        headers={"X-CSRF-Token": "reviewer-csrf"},
    )

    assert requested.status_code == 202
    assert same_person.status_code == 409
    assert "两个不同账号" in same_person.get_json()["error"]
    assert forged.status_code == 409
    assert "先提交待审批操作" in forged.get_json()["error"]
    assert approved.status_code == 200
    assert approved.get_json()["item"]["quantity_remaining"] == 3
    assert approved.get_json()["movement"]["actor"] == "领用人甲"
    assert approved.get_json()["movement"]["approved_by"] == "复核人乙"
