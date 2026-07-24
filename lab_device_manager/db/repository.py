from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional
from lab_device_manager.db.account_store import AccountStore
from lab_device_manager.db.experiment_store import ExperimentStore
from lab_device_manager.db.models import Device, Run, SampleRow, EventRow

_SCHEMA = Path(__file__).parent / "schema.sql"
_MIGRATIONS = Path(__file__).parent / "migrations"


class Repository:
    """SQLite persistence. Holds ONE persistent connection (so :memory: works)."""

    def __init__(self, db_path: str):
        # check_same_thread=False: the Engine drives Samplers on background threads,
        # and RunDetector writes to this connection from those threads. All
        # multi-statement write operations are serialized with _lock below.
        database_path = None if db_path == ":memory:" else Path(db_path)
        existing_database = bool(
            database_path
            and database_path.exists()
            and database_path.stat().st_size > 0
        )
        self.last_migration_backup = None
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")   # ms — wait+retry on SQLITE_BUSY before erroring (multi-device write contention)
        self._conn.row_factory = sqlite3.Row
        if existing_database:
            pending = self._pending_migration_paths()
            if pending:
                self.last_migration_backup = self._create_migration_backup(
                    database_path, pending[0].stem
                )
        self._conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self._apply_migrations()
        self._conn.commit()
        self.accounts = AccountStore(self._conn, self._lock)
        self.experiments = ExperimentStore(self._conn, self._lock)

    def _pending_migration_paths(self) -> list[Path]:
        table = self._conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='schema_version'"""
        ).fetchone()
        if table is None:
            applied = set()
        else:
            applied = {
                row[0]
                for row in self._conn.execute(
                    "SELECT version FROM schema_version"
                ).fetchall()
            }
        return [
            path
            for path in sorted(_MIGRATIONS.glob("*.sql"))
            if path.stem not in applied
        ]

    def _create_migration_backup(
        self, database_path: Path, next_version: str
    ) -> str:
        timestamp = int(time.time() * 1000)
        backup_path = database_path.with_name(
            f"{database_path.name}.pre-{next_version}-{timestamp}.bak"
        )
        backup = sqlite3.connect(backup_path)
        try:
            self._conn.backup(backup)
        finally:
            backup.close()
        return str(backup_path)

    def _apply_migrations(self):
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                 version TEXT PRIMARY KEY,
                 applied_at_ms INTEGER NOT NULL
               )"""
        )
        applied = {
            row[0] for row in self._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchall()
        }
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            version = path.stem
            if version in applied:
                continue
            safe_version = version.replace("'", "''")
            script = path.read_text(encoding="utf-8")
            applied_at_ms = int(time.time() * 1000)
            try:
                self._conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + "\n"
                    + "INSERT INTO schema_version(version, applied_at_ms) "
                    + f"VALUES('{safe_version}', {applied_at_ms});\n"
                    + "COMMIT;"
                )
            except Exception:
                self._conn.rollback()
                raise

    def close(self):
        connection = getattr(self, "_conn", None)
        if connection is not None:
            connection.close()
            self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def upsert_device(self, name: str, device_type: str, alias: str = "") -> int:
        with self._lock:
            row = self._conn.execute("SELECT id FROM device WHERE name=?", (name,)).fetchone()
            if row:
                if alias:
                    self._conn.execute("UPDATE device SET alias=? WHERE id=?", (alias, row["id"]))
                    self._conn.commit()
                return row["id"]
            from time import strftime
            c = self._conn.execute(
                "INSERT INTO device(name, device_type, alias, created_at) VALUES(?,?,?,?)",
                (name, device_type, alias, strftime("%Y-%m-%dT%H:%M:%S")),
            )
            self._conn.commit()
            return c.lastrowid

    def open_run(self, device_id: int, channel: int, started_ms: int,
                 setpoints: dict) -> int:
        with self._lock:
            c = self._conn.execute(
                """INSERT INTO run(device_id, channel, started_ms,
                      setpoints_json, operator, project_tag, experiment_tag,
                      work_mode, target_volume, target_volume_unit)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (device_id, channel, started_ms,
                 json.dumps(setpoints, ensure_ascii=False),
                 setpoints.get("operator"),
                 setpoints.get("project_tag"),
                 setpoints.get("experiment_tag"),
                 setpoints.get("work_mode"),
                 setpoints.get("target_volume"),
                 setpoints.get("target_volume_unit")),
            )
            self._conn.commit()
            return c.lastrowid

    def close_run(self, run_id: int, ended_ms: int, end_status: str,
                  actual_volume: Optional[float], actual_unit: Optional[str],
                  lifetime_acc: Optional[float], lifetime_acc_unit: Optional[str],
                  alarm_count: int):
        with self._lock:
            self._conn.execute(
                """UPDATE run SET ended_ms=?, duration_ms=?-started_ms, end_status=?,
                      result_acc_volume=?, result_acc_unit=?, actual_volume=?, actual_unit=?, alarm_count=? WHERE id=?""",
                (ended_ms, ended_ms, end_status, lifetime_acc, lifetime_acc_unit,
                 actual_volume, actual_unit, alarm_count, run_id),
            )
            self._conn.commit()

    def close_all_open_runs(
        self, ended_ms: int, end_status: str = "interrupted_restart"
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE run
                   SET ended_ms=?, duration_ms=?-started_ms, end_status=?
                   WHERE ended_ms IS NULL""",
                (ended_ms, ended_ms, end_status),
            )
            self._conn.commit()
            return cursor.rowcount

    def add_sample(self, run_id: Optional[int], device_id: int, ts_ms: int,
                   state: str, flow_rate: Optional[float],
                   delivered_volume: Optional[float], temp_c: Optional[float],
                   metrics_json: str):
        with self._lock:
            self._conn.execute(
                """INSERT INTO sample(run_id, device_id, ts_ms, state, flow_rate,
                      delivered_volume, temp_c, metrics_json) VALUES(?,?,?,?,?,?,?,?)""",
                (run_id, device_id, ts_ms, state, flow_rate, delivered_volume, temp_c, metrics_json),
            )
            self._conn.commit()

    def add_event(self, device_id: int, run_id: Optional[int], ts_ms: int,
                  event_type: str, severity: str, detail_json: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO event(device_id, run_id, ts_ms, event_type, severity, detail_json) VALUES(?,?,?,?,?,?)",
                (device_id, run_id, ts_ms, event_type, severity, detail_json),
            )
            self._conn.commit()

    def list_runs(self, limit: int = 50, offset: int = 0, device_id: Optional[int] = None,
                  operator: Optional[str] = None, project_tag: Optional[str] = None,
                  experiment_tag: Optional[str] = None, start_ms: Optional[int] = None,
                  end_ms: Optional[int] = None, end_status: Optional[str] = None,
                  tagged: Optional[bool] = None) -> list:
        where = ["1=1"]
        params: list = []
        if device_id is not None:
            where.append("device_id=?")
            params.append(device_id)
        if operator is not None:
            where.append("operator=?")
            params.append(operator)
        if project_tag is not None:
            where.append("project_tag=?")
            params.append(project_tag)
        if experiment_tag is not None:
            where.append("experiment_tag=?")
            params.append(experiment_tag)
        if start_ms is not None:
            where.append("started_ms>=?")
            params.append(start_ms)
        if end_ms is not None:
            where.append("started_ms<=?")
            params.append(end_ms)
        if end_status is not None:
            where.append("end_status=?")
            params.append(end_status)
        if tagged is not None:
            where.append("tagged=?")
            params.append(1 if tagged else 0)
        sql = f"SELECT * FROM run WHERE {' AND '.join(where)} ORDER BY started_ms DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_run(r) for r in rows]

    def list_runs_for_device(self, device_id: int, limit: int = 50) -> list:
        """Runs for ONE device only — used by /api/devices/<id> so a device
        detail page never leaks another device's runs."""
        rows = self._conn.execute(
            "SELECT * FROM run WHERE device_id=? ORDER BY started_ms DESC LIMIT ?",
            (device_id, limit)).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_run(self, run_id: int) -> Optional[Run]:
        row = self._conn.execute("SELECT * FROM run WHERE id=?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def list_samples_for_run(self, run_id: int, limit: int = 10000) -> list:
        rows = self._conn.execute(
            "SELECT * FROM sample WHERE run_id=? ORDER BY ts_ms ASC LIMIT ?",
            (run_id, limit)).fetchall()
        return [_row_to_sample(r) for r in rows]

    def list_samples_for_device(self, device_id: int, limit: int = 100) -> list:
        rows = self._conn.execute(
            "SELECT * FROM sample WHERE device_id=? ORDER BY ts_ms DESC LIMIT ?",
            (device_id, limit)).fetchall()
        return [_row_to_sample(r) for r in rows]

    def list_events_for_run(self, run_id: int, limit: int = 1000) -> list:
        rows = self._conn.execute(
            "SELECT * FROM event WHERE run_id=? ORDER BY ts_ms ASC LIMIT ?",
            (run_id, limit)).fetchall()
        return [EventRow(r["id"], r["device_id"], r["run_id"], r["ts_ms"],
                         r["event_type"], r["severity"], r["detail_json"]) for r in rows]

    def list_untagged_runs(self) -> list:
        rows = self._conn.execute(
            """SELECT r.* FROM run r WHERE r.tagged=0 AND r.ended_ms IS NOT NULL
               ORDER BY r.started_ms DESC"""
        ).fetchall()
        return [_row_to_run(r) for r in rows]

    def tag_run(self, run_id: int, operator: str, project_tag: str,
                experiment_tag: str, remark: str,
                operator_user_id: Optional[int] = None):
        with self._lock:
            self._conn.execute(
                """UPDATE run SET operator=?, project_tag=?, experiment_tag=?,
                      remark=?, tagged=1, operator_user_id=? WHERE id=?""",
                (
                    operator,
                    project_tag,
                    experiment_tag,
                    remark,
                    operator_user_id,
                    run_id,
                ),
            )
            self._conn.commit()

    def list_recent_events(self, device_id: Optional[int] = None,
                           limit: int = 50) -> list:
        if device_id is None:
            rows = self._conn.execute(
                "SELECT * FROM event ORDER BY ts_ms DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM event WHERE device_id=? ORDER BY ts_ms DESC LIMIT ?",
                (device_id, limit)).fetchall()
        return [EventRow(r["id"], r["device_id"], r["run_id"], r["ts_ms"],
                         r["event_type"], r["severity"], r["detail_json"]) for r in rows]


def _row_to_device(r) -> Device:
    return Device(
        id=r["id"], name=r["name"], device_type=r["device_type"], alias=r["alias"] or "",
        location=r["location"], asset_no=r["asset_no"], created_at=r["created_at"],
        is_active=r["is_active"] if r["is_active"] is not None else 1,
    )


def _row_to_run(r) -> Run:
    return Run(
        id=r["id"], device_id=r["device_id"], channel=r["channel"],
        started_ms=r["started_ms"], ended_ms=r["ended_ms"],
        duration_ms=r["duration_ms"], end_status=r["end_status"],
        operator=r["operator"], project_tag=r["project_tag"],
        experiment_tag=r["experiment_tag"], remark=r["remark"],
        tagged=bool(r["tagged"]),
        work_mode=r["work_mode"], target_volume=r["target_volume"],
        target_volume_unit=r["target_volume_unit"],
        result_acc_volume=r["result_acc_volume"], result_acc_unit=r["result_acc_unit"],
        actual_volume=r["actual_volume"], actual_unit=r["actual_unit"],
        alarm_count=r["alarm_count"],
        setpoints_json=r["setpoints_json"],
        operator_user_id=r["operator_user_id"],
    )


def _row_to_sample(r) -> SampleRow:
    return SampleRow(
        id=r["id"], run_id=r["run_id"], device_id=r["device_id"], ts_ms=r["ts_ms"],
        state=r["state"], flow_rate=r["flow_rate"], delivered_volume=r["delivered_volume"],
        temp_c=r["temp_c"], metrics_json=r["metrics_json"],
    )
