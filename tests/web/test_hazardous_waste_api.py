from lab_device_manager.db.repository import Repository
from lab_device_manager.web.app import create_app


class EmptyEngine:
    def latest(self):
        return {}

    def device_map(self):
        return {}


def _client():
    repo = Repository(":memory:")
    app = create_app(EmptyEngine(), repo, secret_key="test-secret")
    app.config.update(TESTING=True, AUTH_TEST_BYPASS=True)
    return app.test_client()


def test_hazardous_waste_requires_external_manifest_and_closes_loop():
    client = _client()
    location = client.post(
        "/api/storage-locations",
        json={
            "location_code": "WASTE-01",
            "display_name": "危废暂存柜 1",
            "location_type": "waste_storage",
            "allowed_storage_groups": ["general_chemical"],
            "physical_controls": ["防泄漏托盘", "通风"],
        },
    )
    created = client.post(
        "/api/inventory/hazardous-waste",
        json={
            "waste_name": "酸性无机废液",
            "waste_category_code": "HW49",
            "waste_category_name": "其他废物",
            "physical_state": "liquid",
            "hazard_characteristics": ["腐蚀性", "毒性"],
            "composition": "盐酸、水及无机盐",
            "quantity": 1.5,
            "unit": "L",
            "package_type": "5L HDPE 废液桶",
            "location_code": "WASTE-01",
            "client_event_id": "waste-create-1",
        },
    )
    assert location.status_code == 201
    assert created.status_code == 201
    waste = created.get_json()
    assert waste["waste_code"].startswith("HW-")

    added = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/events",
        json={
            "action": "add",
            "quantity": 0.5,
            "client_event_id": "waste-add-1",
        },
    )
    premature_transfer = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/transfers",
        json={
            "national_manifest_no": "20263101000001",
            "transfer_date": "2026-07-25",
            "transporter_name": "合规运输单位",
            "transporter_license_no": "TRANS-001",
            "recipient_name": "有资质处置单位",
            "recipient_permit_no": "WASTE-PERMIT-001",
            "disposal_method": "物化处理",
            "client_event_id": "waste-transfer-too-soon",
        },
    )
    sealed = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/events",
        json={"action": "seal", "client_event_id": "waste-seal-1"},
    )
    missing_manifest = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/transfers",
        json={
            "transfer_date": "2026-07-25",
            "transporter_name": "合规运输单位",
            "transporter_license_no": "TRANS-001",
            "recipient_name": "有资质处置单位",
            "recipient_permit_no": "WASTE-PERMIT-001",
            "disposal_method": "物化处理",
            "client_event_id": "waste-transfer-no-manifest",
        },
    )
    registered = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/transfers",
        json={
            "national_manifest_no": "20263101000001",
            "national_system_ref": "国家固废系统记录",
            "transfer_date": "2026-07-25",
            "transporter_name": "合规运输单位",
            "transporter_license_no": "TRANS-001",
            "vehicle_no": "沪A12345",
            "recipient_name": "有资质处置单位",
            "recipient_permit_no": "WASTE-PERMIT-001",
            "disposal_method": "物化处理",
            "client_event_id": "waste-transfer-1",
        },
    )
    completed = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/events",
        json={
            "action": "complete_transfer",
            "client_event_id": "waste-complete-1",
        },
    )
    repeated_seal = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/events",
        json={"action": "seal", "client_event_id": "waste-seal-1"},
    )
    repeated_transfer = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/transfers",
        json={
            "national_manifest_no": "20263101000001",
            "transfer_date": "2026-07-25",
            "transporter_name": "合规运输单位",
            "transporter_license_no": "TRANS-001",
            "recipient_name": "有资质处置单位",
            "recipient_permit_no": "WASTE-PERMIT-001",
            "disposal_method": "物化处理",
            "client_event_id": "waste-transfer-1",
        },
    )
    repeated_completion = client.post(
        f"/api/inventory/hazardous-waste/{waste['id']}/events",
        json={
            "action": "complete_transfer",
            "client_event_id": "waste-complete-1",
        },
    )

    assert added.status_code == 200
    assert added.get_json()["quantity"] == 2
    assert premature_transfer.status_code == 409
    assert sealed.get_json()["status"] == "ready_for_transfer"
    assert missing_manifest.status_code == 400
    assert registered.status_code == 201
    assert completed.status_code == 200
    assert repeated_seal.status_code == 200
    assert repeated_transfer.status_code == 201
    assert repeated_completion.status_code == 200
    detail = completed.get_json()
    exported = client.get("/api/inventory/hazardous-waste.csv")
    assert detail["status"] == "transferred"
    assert detail["transfers"][0]["national_manifest_no"] == (
        "20263101000001"
    )
    assert detail["transfers"][0]["recipient_permit_no"] == (
        "WASTE-PERMIT-001"
    )
    assert {
        event["event_type"] for event in detail["events"]
    } == {
        "created",
        "quantity_added",
        "sealed",
        "transfer_registered",
        "transfer_completed",
    }
    assert exported.status_code == 200
    export_text = exported.data.decode("utf-8-sig")
    assert "国家电子联单编号" in export_text
    assert "20263101000001" in export_text
    assert "TRANS-001" in export_text
    assert "WASTE-PERMIT-001" in export_text


def test_hazardous_waste_cannot_use_an_ordinary_storage_location():
    client = _client()
    client.post(
        "/api/storage-locations",
        json={
            "location_code": "CAB-01",
            "display_name": "普通试剂柜",
            "location_type": "chemical_cabinet",
            "allowed_storage_groups": ["general_chemical"],
            "physical_controls": ["通风"],
        },
    )

    response = client.post(
        "/api/inventory/hazardous-waste",
        json={
            "waste_name": "测试废液",
            "waste_category_code": "HW49",
            "physical_state": "liquid",
            "hazard_characteristics": ["腐蚀性"],
            "composition": "测试",
            "quantity": 1,
            "unit": "L",
            "package_type": "废液桶",
            "location_code": "CAB-01",
            "client_event_id": "waste-wrong-location",
        },
    )

    assert response.status_code == 400
    assert "危废暂存库位" in response.get_json()["error"]
