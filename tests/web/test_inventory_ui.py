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


def test_inventory_page_exposes_complete_inventory_workflow():
    page = _client().get("/inventory")

    assert page.status_code == 200
    for marker in (
        b"inventorySummaryGrid",
        b"inventoryFilters",
        b"inventoryRows",
        b"openInventoryScanner",
        b"openItemForm",
        b"itemFormDialog",
        b"inventoryDetailDialog",
        b"inventoryOperationDialog",
        b"inventoryAudit",
        b"inventoryAuditPrevious",
        b"inventoryAuditPage",
        b"inventoryAuditNext",
        b"inventory.js",
    ):
        assert marker in page.data


def test_inventory_script_is_valid_and_uses_unified_apis():
    script = Path(
        "lab_device_manager/web/static/inventory.js"
    )

    checked = subprocess.run(
        ["node", "--check", str(script)],
        capture_output=True,
        text=True,
    )

    assert checked.returncode == 0, checked.stderr
    source = script.read_text(encoding="utf-8")
    assert "/api/inventory/items" in source
    assert "/api/inventory/summary" in source
    assert "/api/inventory/movements" in source
    assert "PuricoreScanner.open" in source
    assert "received" in source
    assert "issued" in source
    assert "adjusted" in source
    assert "quarantined" in source
    assert "disposed" in source
