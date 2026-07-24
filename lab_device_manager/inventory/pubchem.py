from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov"
_HAZARD_RULES = [
    ("易燃", (r"flammable", r"pyrophoric", r"self-heating")),
    ("腐蚀", (r"corros", r"skin burns", r"eye damage")),
    ("有毒", (r"toxic", r"fatal", r"poison", r"acute toxicity")),
    ("氧化", (r"oxid",)),
    ("易爆", (r"explos",)),
    ("反应性", (r"reactive", r"water-reactive", r"self-reactive")),
    (
        "有害/刺激",
        (r"harmful", r"irrit", r"sensiti[sz]ation", r"skin.*eye", r"carcin"),
    ),
    ("环境危害", (r"aquatic", r"environment", r"ecotoxic")),
]


def map_hazards(text: str) -> list[str]:
    return [
        label
        for label, patterns in _HAZARD_RULES
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
    ]


def _get_json(url: str) -> dict:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "PuricoreLab/1"},
    )
    try:
        with urlopen(request, timeout=8) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("PubChem 未找到匹配信息") from exc


def _collect_strings(node, output=None) -> list[str]:
    output = output if output is not None else []
    if not isinstance(node, dict):
        return output
    for information in node.get("Information") or []:
        values = (information.get("Value") or {}).get("StringWithMarkup") or []
        for value in values:
            text = value.get("String")
            if text:
                output.append(str(text))
    for section in node.get("Section") or []:
        _collect_strings(section, output)
    return output


def lookup_chemical(cas_no: str) -> dict:
    cas = str(cas_no or "").strip()
    if not cas:
        raise ValueError("请先输入 CAS 号")
    cid_data = _get_json(
        f"{PUBCHEM_BASE}/rest/pug/compound/name/{quote(cas)}/cids/JSON"
    )
    cids = (cid_data.get("IdentifierList") or {}).get("CID") or []
    if not cids:
        raise ValueError("PubChem 未找到匹配信息")
    cid = cids[0]
    properties = _get_json(
        f"{PUBCHEM_BASE}/rest/pug/compound/cid/{cid}/property/"
        "Title,MolecularFormula,MolecularWeight/JSON"
    )
    rows = (properties.get("PropertyTable") or {}).get("Properties") or []
    values = rows[0] if rows else {}
    try:
        ghs = _get_json(
            f"{PUBCHEM_BASE}/rest/pug_view/data/compound/{cid}/JSON"
            "?heading=GHS+Classification"
        )
    except ValueError:
        ghs = {}
    formula = values.get("MolecularFormula")
    weight = values.get("MolecularWeight")
    spec = " / ".join(
        value
        for value in (
            str(formula or ""),
            f"{weight} g/mol" if weight else "",
        )
        if value
    )
    return {
        "cid": cid,
        "name": values.get("Title") or "",
        "spec": spec,
        "hazards": map_hazards("\n".join(_collect_strings(ghs.get("Record")))),
        "sds_url": (
            f"{PUBCHEM_BASE}/compound/{cid}#section=Safety-and-Hazards"
        ),
    }
