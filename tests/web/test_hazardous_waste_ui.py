import subprocess
from pathlib import Path

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


def test_hazardous_waste_page_exposes_complete_workflow():
    page = _client().get("/hazardous-waste")

    assert page.status_code == 200
    for marker in (
        b"openWasteForm",
        b"wasteStatusFilter",
        b"wasteRows",
        b"wasteFormDialog",
        b"wasteCategoryCode",
        b"wasteCharacteristics",
        b"wasteLocation",
        b"wasteDetailDialog",
        b"wasteActionDialog",
        b"manifestNo",
        b"transporterLicense",
        b"recipientPermit",
        b"hazardous-waste.js",
    ):
        assert marker in page.data


def test_hazardous_waste_script_is_valid_and_uses_protected_apis():
    script = Path(
        "lab_device_manager/web/static/hazardous-waste.js"
    )

    checked = subprocess.run(
        ["node", "--check", str(script)],
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 0, checked.stderr
    source = script.read_text(encoding="utf-8")
    assert "/api/inventory/hazardous-waste" in source
    assert "X-CSRF-Token" in source
    assert "national_manifest_no" in source
    assert "transporter_license_no" in source
    assert "recipient_permit_no" in source
