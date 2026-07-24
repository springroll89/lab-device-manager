from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Optional


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decode_user(
    row: sqlite3.Row | None, *, include_password: bool = False
) -> Optional[dict]:
    if row is None:
        return None
    result = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "created_by": row["created_by"],
        "created_at_ms": row["created_at_ms"],
        "updated_at_ms": row["updated_at_ms"],
        "last_login_at_ms": row["last_login_at_ms"],
    }
    if include_password:
        result["password_hash"] = row["password_hash"]
    return result


class AccountStore:
    """Persistence boundary for application users and account audit events."""

    def __init__(
        self, connection: sqlite3.Connection, lock: threading.RLock
    ):
        self._conn = connection
        self._lock = lock

    def ensure_initial_admin(self, password_hash: str) -> dict:
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM user_account ORDER BY id LIMIT 1"
            ).fetchone()
            if existing is not None:
                return _decode_user(existing)
            now_ms = _now_ms()
            cursor = self._conn.execute(
                """INSERT INTO user_account(
                     username, display_name, password_hash, role,
                     is_active, must_change_password, created_by,
                     created_at_ms, updated_at_ms
                   ) VALUES(?, ?, ?, 'super_admin', 1, 1, NULL, ?, ?)""",
                ("admin", "系统管理员", password_hash, now_ms, now_ms),
            )
            user_id = cursor.lastrowid
            self._add_audit(
                None,
                user_id,
                "initial_admin_created",
                {"username": "admin", "role": "super_admin"},
            )
            self._conn.commit()
            return self.get_user_by_id(user_id)

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_account WHERE id=?",
                (user_id,),
            ).fetchone()
            return _decode_user(row)

    def get_auth_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_account WHERE id=?",
                (user_id,),
            ).fetchone()
            return _decode_user(row, include_password=True)

    def get_auth_user_by_username(self, username: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM user_account WHERE username=? COLLATE NOCASE",
                (username,),
            ).fetchone()
            return _decode_user(row, include_password=True)

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM user_account
                   ORDER BY
                     CASE role
                       WHEN 'super_admin' THEN 0
                       WHEN 'supervisor' THEN 1
                       ELSE 2
                     END,
                     username COLLATE NOCASE"""
            ).fetchall()
            return [_decode_user(row) for row in rows]

    def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
        created_by: int,
    ) -> dict:
        with self._lock:
            now_ms = _now_ms()
            cursor = self._conn.execute(
                """INSERT INTO user_account(
                     username, display_name, password_hash, role,
                     is_active, must_change_password, created_by,
                     created_at_ms, updated_at_ms
                   ) VALUES(?, ?, ?, ?, 1, 1, ?, ?, ?)""",
                (
                    username,
                    display_name,
                    password_hash,
                    role,
                    created_by,
                    now_ms,
                    now_ms,
                ),
            )
            user_id = cursor.lastrowid
            self._add_audit(
                created_by,
                user_id,
                "account_created",
                {"username": username, "role": role},
            )
            self._conn.commit()
            return self.get_user_by_id(user_id)

    def update_password(
        self,
        *,
        user_id: int,
        password_hash: str,
        actor_user_id: int,
        must_change_password: bool,
        action: str,
    ) -> dict:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE user_account
                   SET password_hash=?, must_change_password=?,
                       updated_at_ms=?
                   WHERE id=?""",
                (
                    password_hash,
                    int(must_change_password),
                    _now_ms(),
                    user_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("user account not found")
            self._add_audit(
                actor_user_id,
                user_id,
                action,
                {"must_change_password": must_change_password},
            )
            self._conn.commit()
            return self.get_user_by_id(user_id)

    def set_active(
        self,
        *,
        user_id: int,
        is_active: bool,
        actor_user_id: int,
    ) -> dict:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE user_account
                   SET is_active=?, updated_at_ms=?
                   WHERE id=?""",
                (int(is_active), _now_ms(), user_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("user account not found")
            self._add_audit(
                actor_user_id,
                user_id,
                "account_enabled" if is_active else "account_disabled",
                {},
            )
            self._conn.commit()
            return self.get_user_by_id(user_id)

    def touch_login(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE user_account
                   SET last_login_at_ms=?, updated_at_ms=updated_at_ms
                   WHERE id=?""",
                (_now_ms(), user_id),
            )
            self._conn.commit()

    def _add_audit(
        self,
        actor_user_id: int | None,
        target_user_id: int | None,
        action: str,
        detail: dict,
    ) -> None:
        self._conn.execute(
            """INSERT INTO account_audit(
                 actor_user_id, target_user_id, action,
                 detail_json, created_at_ms
               ) VALUES(?, ?, ?, ?, ?)""",
            (
                actor_user_id,
                target_user_id,
                action,
                json.dumps(
                    detail, ensure_ascii=False, separators=(",", ":")
                ),
                _now_ms(),
            ),
        )
