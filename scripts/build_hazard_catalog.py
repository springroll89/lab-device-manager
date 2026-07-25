"""Build the local CAS lookup table from the official MEM Word attachment.

Usage:
    python scripts/build_hazard_catalog.py /path/to/危险化学品分类信息表.doc

The source document is the attachment published with 安监总厅管三〔2015〕80号.
Entry 1674 is updated according to 应急厅函〔2022〕300号.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


SOURCE_URL = (
    "https://www.mem.gov.cn/gk/gwgg/agwzlfl/gfxwj/2015/201509/"
    "W020171031378874265237.doc"
)
OUTPUT = (
    Path(__file__).parents[1]
    / "lab_device_manager"
    / "inventory"
    / "data"
    / "hazardous_catalog_2015_2022.json"
)
CAS_PATTERN = re.compile(r"\d{2,7}-\d{2}-\d")


def parse_document(source: Path) -> list[dict]:
    converted = subprocess.run(
        ["textutil", "-convert", "txt", "-stdout", str(source)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = [line.strip() for line in converted.splitlines()]
    starts: list[int] = []
    expected = 1
    for index, line in enumerate(lines):
        if line == str(expected):
            starts.append(index)
            expected += 1
    if len(starts) != 2828:
        raise RuntimeError(
            f"expected 2828 catalog entries, found {len(starts)}"
        )
    entries = []
    for entry_no, start in enumerate(starts, start=1):
        end = starts[entry_no] if entry_no < len(starts) else len(lines)
        block = [line for line in lines[start + 1 : end] if line]
        cases = [
            value for value in block if CAS_PATTERN.fullmatch(value)
        ]
        entries.append(
            {
                "entry_no": str(entry_no),
                "name": block[0],
                "cas_numbers": cases,
                "classification_text": block[
                    next(
                        (
                            index + 1
                            for index, value in enumerate(block)
                            if value in cases
                        ),
                        min(3, len(block)),
                    ) :
                ],
            }
        )
    entries[1673].update(
        {
            "name": "柴油",
            "cas_numbers": ["68334-30-5"],
            "classification_text": ["易燃液体,类别3"],
        }
    )
    return entries


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("provide the downloaded official .doc path")
    source = Path(sys.argv[1]).resolve()
    entries = parse_document(source)
    payload = {
        "title": "危险化学品分类信息表",
        "catalog_version": "危险化学品目录（2015版，含2022柴油调整）",
        "source_url": SOURCE_URL,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "entry_count": len(entries),
        "entries": entries,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
