from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


_DATA = (
    Path(__file__).parent
    / "data"
    / "hazardous_catalog_2015_2022.json"
)
_CAS_PATTERN = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")


def normalize_cas(value: str) -> str:
    cas = str(value or "").strip()
    match = _CAS_PATTERN.fullmatch(cas)
    if match is None:
        raise ValueError("CAS 号格式无效")
    digits = "".join(match.groups()[:2])
    checksum = sum(
        int(digit) * weight
        for weight, digit in enumerate(reversed(digits), start=1)
    ) % 10
    if checksum != int(match.group(3)):
        raise ValueError("CAS 号校验位不正确")
    return cas


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict, dict[str, list[dict]]]:
    payload = json.loads(_DATA.read_text(encoding="utf-8"))
    by_cas: dict[str, list[dict]] = {}
    for entry in payload["entries"]:
        for cas in entry["cas_numbers"]:
            by_cas.setdefault(cas, []).append(entry)
    return payload, by_cas


def _suggestions(classification: list[str]) -> tuple[list[str], list[str]]:
    text = "；".join(classification)
    pictograms = set()
    storage_groups = set()
    if any(
        token in text
        for token in ("爆炸物", "自反应物质", "有机过氧化物")
    ):
        pictograms.add("GHS01")
    if any(
        token in text
        for token in (
            "易燃",
            "自燃",
            "自热",
            "遇水放出易燃气体",
        )
    ):
        pictograms.add("GHS02")
        storage_groups.add("flammable")
    if "氧化性" in text:
        pictograms.add("GHS03")
        storage_groups.add("oxidizer")
    if "加压气体" in text:
        pictograms.add("GHS04")
        storage_groups.add("compressed_gas")
    if any(token in text for token in ("腐蚀", "严重眼损伤")):
        pictograms.add("GHS05")
    if re.search(r"急性毒性[^；]*类别[123]", text):
        pictograms.add("GHS06")
        storage_groups.add("toxic")
    elif any(token in text for token in ("急性毒性", "刺激", "致敏")):
        pictograms.add("GHS07")
    if any(
        token in text
        for token in (
            "致癌",
            "生殖细胞致突变",
            "生殖毒性",
            "特异性靶器官毒性",
            "吸入危害",
        )
    ):
        pictograms.add("GHS08")
    if "危害水生环境" in text:
        pictograms.add("GHS09")
    if "遇水放出易燃气体" in text:
        storage_groups.add("water_reactive")
    if "自燃" in text:
        storage_groups.add("pyrophoric")
    return sorted(pictograms), sorted(storage_groups)


def lookup_hazardous_catalog(cas_value: str) -> dict:
    cas = normalize_cas(cas_value)
    metadata, by_cas = _catalog()
    entries = by_cas.get(cas, [])
    if not entries:
        return {
            "cas_no": cas,
            "matched": False,
            "hazardous_status": "pending_review",
            "catalog_source": "危险化学品分类信息表",
            "catalog_version": metadata["catalog_version"],
            "source_url": metadata["source_url"],
            "warning": (
                "CAS 未匹配目录不等于非危险化学品；仍须结合浓度、"
                "混合物规则、SDS 和危险特性完成法规复核。"
            ),
        }
    classification = list(
        dict.fromkeys(
            value
            for entry in entries
            for value in entry["classification_text"]
            if value
        )
    )
    pictograms, storage_groups = _suggestions(classification)
    return {
        "cas_no": cas,
        "matched": True,
        "hazardous_status": "listed",
        "catalog_source": "危险化学品分类信息表",
        "catalog_version": metadata["catalog_version"],
        "catalog_entries": [
            {
                "entry_no": entry["entry_no"],
                "name": entry["name"],
            }
            for entry in entries
        ],
        "classification": classification,
        "suggested_ghs_pictograms": pictograms,
        "suggested_storage_groups": storage_groups,
        "source_url": metadata["source_url"],
        "warning": (
            "自动结果仅用于初筛；储存组、标签和 SDS 必须由授权人员"
            "结合产品浓度及供应商资料复核。"
        ),
    }
