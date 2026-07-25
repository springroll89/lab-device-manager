import json
from pathlib import Path

import pytest

from lab_device_manager.inventory.regulatory_catalog import (
    lookup_hazardous_catalog,
    normalize_cas,
)


def test_bundled_official_catalog_is_complete():
    catalog_path = (
        Path("lab_device_manager/inventory/data")
        / "hazardous_catalog_2015_2022.json"
    )
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert payload["entry_count"] == 2828
    assert len(payload["entries"]) == 2828
    assert len(payload["source_sha256"]) == 64


def test_official_catalog_matches_known_cas_and_returns_evidence():
    ethanol = lookup_hazardous_catalog("64-17-5")

    assert ethanol["matched"] is True
    assert ethanol["hazardous_status"] == "listed"
    assert ethanol["catalog_entries"]
    assert "GHS02" in ethanol["suggested_ghs_pictograms"]
    assert "flammable" in ethanol["suggested_storage_groups"]
    assert "2015" in ethanol["catalog_version"]


def test_catalog_miss_stays_pending_instead_of_claiming_non_hazardous():
    result = lookup_hazardous_catalog("7732-18-5")

    assert result["matched"] is False
    assert result["hazardous_status"] == "pending_review"
    assert "不等于非危险化学品" in result["warning"]


def test_cas_checksum_rejects_common_input_error():
    with pytest.raises(ValueError, match="校验位"):
        normalize_cas("64-17-6")
