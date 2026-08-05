from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DATABASE_ARCHIVE_NAME = "lab_device_manager.db"
MANIFEST_ARCHIVE_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _inspect_database(path: Path) -> tuple[dict[str, int], str | int | None]:
    connection = sqlite3.connect(path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {
            name: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(name)}"
                ).fetchone()[0]
            )
            for name in table_names
        }
        schema_version = None
        if "schema_version" in counts:
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            if row is not None and row[0] is not None:
                schema_version = row[0]
        return counts, schema_version
    finally:
        connection.close()


def export_data_bundle(database: Path, output: Path) -> dict[str, object]:
    database = database.resolve()
    output = output.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"database does not exist: {database}")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="puricore-export-") as temp_dir:
        backup_path = Path(temp_dir) / DATABASE_ARCHIVE_NAME
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        table_counts, schema_version = _inspect_database(backup_path)
        manifest: dict[str, object] = {
            "application": "Puricore Lab Device Manager",
            "format_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "database_file": DATABASE_ARCHIVE_NAME,
            "database_sha256": _sha256(backup_path),
            "schema_version": schema_version,
            "table_counts": table_counts,
        }
        manifest_path = Path(temp_dir) / MANIFEST_ARCHIVE_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(backup_path, DATABASE_ARCHIVE_NAME)
            archive.write(manifest_path, MANIFEST_ARCHIVE_NAME)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a verified Puricore database bundle for an offline PC."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/lab_device_manager.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "Desktop" / "puricore-data-transfer.zip",
    )
    args = parser.parse_args()
    manifest = export_data_bundle(args.database, args.output)
    print(f"Export completed: {args.output.resolve()}")
    print(f"Tables included: {len(manifest['table_counts'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
