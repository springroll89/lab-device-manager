import io
import json

import pytest

from lab_device_manager.inventory import pubchem
from lab_device_manager.inventory.pubchem import lookup_chemical, map_hazards


def test_pubchem_hazard_text_maps_to_standard_chinese_labels():
    hazards = map_hazards(
        "Highly flammable liquid. Causes serious eye irritation. "
        "Toxic to aquatic life."
    )

    assert hazards == ["易燃", "有毒", "有害/刺激", "环境危害"]


def test_pubchem_lookup_builds_fields_from_nested_official_response(
    monkeypatch,
):
    responses = iter(
        [
            {"IdentifierList": {"CID": [702]}},
            {
                "PropertyTable": {
                    "Properties": [
                        {
                            "Title": "Ethanol",
                            "MolecularFormula": "C2H6O",
                            "MolecularWeight": "46.07",
                        }
                    ]
                }
            },
            {
                "Record": {
                    "Section": [
                        {
                            "Information": [
                                {
                                    "Value": {
                                        "StringWithMarkup": [
                                            {
                                                "String": (
                                                    "Highly flammable liquid "
                                                    "and causes eye irritation"
                                                )
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                }
            },
        ]
    )
    monkeypatch.setattr(pubchem, "_get_json", lambda _url: next(responses))

    result = lookup_chemical("64-17-5")

    assert result["cid"] == 702
    assert result["name"] == "Ethanol"
    assert result["spec"] == "C2H6O / 46.07 g/mol"
    assert result["hazards"] == ["易燃", "有害/刺激"]
    assert result["sds_url"].startswith("https://pubchem.ncbi.nlm.nih.gov/")


def test_pubchem_lookup_handles_missing_cas_cid_and_optional_ghs(
    monkeypatch,
):
    with pytest.raises(ValueError, match="CAS"):
        lookup_chemical("")

    monkeypatch.setattr(
        pubchem, "_get_json", lambda _url: {"IdentifierList": {"CID": []}}
    )
    with pytest.raises(ValueError, match="未找到"):
        lookup_chemical("not-found")

    responses = iter(
        [
            {"IdentifierList": {"CID": [1]}},
            {"PropertyTable": {"Properties": [{"Title": "Test"}]}},
            ValueError("no ghs"),
        ]
    )

    def fake_get(_url):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(pubchem, "_get_json", fake_get)
    result = lookup_chemical("1-00-0")
    assert result["spec"] == ""
    assert result["hazards"] == []


class _JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_pubchem_http_json_reader_maps_transport_and_json_errors(
    monkeypatch,
):
    monkeypatch.setattr(
        pubchem,
        "urlopen",
        lambda _request, timeout: _JsonResponse(
            json.dumps({"ok": True}).encode()
        ),
    )
    assert pubchem._get_json("https://example.test") == {"ok": True}

    monkeypatch.setattr(
        pubchem,
        "urlopen",
        lambda _request, timeout: _JsonResponse(b"not-json"),
    )
    with pytest.raises(ValueError, match="未找到"):
        pubchem._get_json("https://example.test")
