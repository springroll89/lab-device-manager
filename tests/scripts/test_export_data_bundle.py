from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from scripts.export_data_bundle import export_data_bundle


def test_export_data_bundle_uses_consistent_sqlite_backup(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE schema_version (version TEXT NOT NULL)")
    connection.execute("CREATE TABLE user_account (username TEXT NOT NULL)")
    connection.execute("CREATE TABLE trace_item (item_code TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO schema_version(version) VALUES "
        "('012_receipt_and_hazardous_waste')"
    )
    connection.executemany(
        "INSERT INTO user_account(username) VALUES (?)",
        [("admin",), ("operator",)],
    )
    connection.execute("INSERT INTO trace_item(item_code) VALUES ('CHEM-001')")
    connection.commit()
    connection.close()

    output = tmp_path / "puricore-data-transfer.zip"
    manifest = export_data_bundle(database, output)

    assert manifest["format_version"] == 1
    assert manifest["schema_version"] == "012_receipt_and_hazardous_waste"
    assert manifest["table_counts"] == {
        "schema_version": 1,
        "trace_item": 1,
        "user_account": 2,
    }
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"lab_device_manager.db", "manifest.json"}
        exported_database = archive.read("lab_device_manager.db")
        archived_manifest = json.loads(archive.read("manifest.json"))
    assert hashlib.sha256(exported_database).hexdigest() == archived_manifest["database_sha256"]
