from __future__ import annotations

import functools
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


ROLE_LABELS = {
    "super_admin": "最高管理员",
    "supervisor": "实验室主管",
    "operator": "操作员",
}
MANAGEMENT_ROLES = {"super_admin", "supervisor"}
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_LOGIN_WINDOW_SECONDS = 5 * 60
_LOGIN_MAX_FAILURES = 5


def _password_error(password: str) -> str | None:
    if len(password) < 8:
        return "新密码至少需要 8 位"
    if len(password) > 128:
        return "新密码不能超过 128 位"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "新密码需同时包含字母和数字"
    return None


class AuthManager:
    """Database-backed authentication and role authorization."""

    def __init__(self, app, repo):
        self.app = app
        self.repo = repo
        self.blueprint = Blueprint("auth", __name__)
        self._failed_logins: dict[tuple[str, str], list[float]] = {}
        self._failed_login_lock = threading.Lock()
        configured_initial_password = os.environ.get(
            "LAB_INITIAL_ADMIN_PASSWORD"
        )
        self._using_insecure_initial_password = (
            configured_initial_password is None
        )
        initial_password = configured_initial_password or "admin"
        repo.accounts.ensure_initial_admin(
            generate_password_hash(initial_password)
        )
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Strict",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        )
        self._register_routes()
        app.register_blueprint(self.blueprint)
        app.before_request(self.require_login)
        app.after_request(self.add_security_headers)

    def _register_routes(self) -> None:
        self.blueprint.add_url_rule(
            "/login", "login_page", self.login_page, methods=["GET"]
        )
        self.blueprint.add_url_rule(
            "/login", "login", self.login, methods=["POST"]
        )
        self.blueprint.add_url_rule(
            "/logout", "logout", self.logout, methods=["POST"]
        )
        self.blueprint.add_url_rule(
            "/api/auth-mode",
            "auth_mode",
            self.auth_mode,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/session",
            "api_session",
            self.api_session,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/change-password",
            "change_password_page",
            self.change_password_page,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/account/password",
            "change_password",
            self.change_password,
            methods=["POST"],
        )
        self.blueprint.add_url_rule(
            "/accounts",
            "accounts_page",
            self.accounts_page,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/accounts",
            "list_accounts",
            self.list_accounts,
            methods=["GET"],
        )
        self.blueprint.add_url_rule(
            "/api/accounts",
            "create_account",
            self.create_account,
            methods=["POST"],
        )
        self.blueprint.add_url_rule(
            "/api/accounts/<int:user_id>/reset-password",
            "reset_account_password",
            self.reset_account_password,
            methods=["POST"],
        )
        self.blueprint.add_url_rule(
            "/api/accounts/<int:user_id>/status",
            "set_account_status",
            self.set_account_status,
            methods=["PATCH"],
        )

    def _is_test_bypass(self) -> bool:
        return bool(
            self.app.testing
            and self.app.config.get("AUTH_TEST_BYPASS", False)
        )

    @staticmethod
    def _test_user() -> dict:
        return {
            "id": 0,
            "username": "test-admin",
            "display_name": "测试管理员",
            "role": "super_admin",
            "is_active": True,
            "must_change_password": False,
        }

    def _is_public_request(self) -> bool:
        if request.endpoint in {
            "auth.login_page",
            "auth.login",
            "auth.auth_mode",
        }:
            return True
        if request.endpoint == "static":
            filename = str((request.view_args or {}).get("filename") or "")
            return not filename.endswith(".html") or filename == "login.html"
        return False

    def require_login(self):
        if self._is_test_bypass():
            g.current_user = self._test_user()
            return None
        if self._is_public_request():
            return None
        raw_user_id = session.get("user_id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            session.clear()
            return self.unauthorized_response()
        user = self.repo.accounts.get_user_by_id(user_id)
        if user is None or not user["is_active"]:
            session.clear()
            return self.unauthorized_response()
        g.current_user = user
        session["username"] = user["username"]
        session["display_name"] = user["display_name"]
        session["role"] = user["role"]
        session["must_change_password"] = user["must_change_password"]
        allowed_during_password_change = {
            "auth.api_session",
            "auth.change_password_page",
            "auth.change_password",
            "auth.logout",
        }
        if (
            user["must_change_password"]
            and request.endpoint not in allowed_during_password_change
        ):
            if request.path.startswith("/api/") or request.is_json:
                return jsonify(
                    {
                        "error": "password_change_required",
                        "change_password_url": "/change-password",
                    }
                ), 403
            return redirect(
                url_for(
                    "auth.change_password_page",
                    next=self._safe_local_path(request.full_path),
                )
            )
        return None

    @staticmethod
    def unauthorized_response():
        if request.path.startswith("/api/") or request.is_json:
            return jsonify({"error": "unauthorized"}), 401
        next_path = AuthManager._safe_local_path(request.full_path)
        return redirect(url_for("auth.login_page", next=next_path))

    @staticmethod
    def _safe_local_path(value: str | None) -> str:
        candidate = str(value or "").strip()
        if (
            candidate.startswith("/")
            and not candidate.startswith("//")
            and "\x00" not in candidate
        ):
            return candidate.rstrip("?")
        return "/experiments"

    def current_user(self) -> dict | None:
        if self._is_test_bypass():
            return getattr(g, "current_user", self._test_user())
        return getattr(g, "current_user", None)

    def current_operator(self) -> str:
        user = self.current_user()
        return str(
            (user or {}).get("display_name")
            or (user or {}).get("username")
            or ""
        ).strip()

    def login_required(self, view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if self.current_user() is None:
                return self.unauthorized_response()
            return view(*args, **kwargs)

        return wrapped

    def roles_required(self, *roles: str):
        allowed = set(roles)

        def decorator(view):
            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                user = self.current_user()
                if user is None:
                    return self.unauthorized_response()
                if user["role"] not in allowed:
                    if request.path.startswith("/api/") or request.is_json:
                        return jsonify({"error": "forbidden"}), 403
                    abort(403)
                return view(*args, **kwargs)

            return wrapped

        return decorator

    def csrf_required(self, view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            csrf_error = self._csrf_error()
            if csrf_error is not None:
                return csrf_error
            return view(*args, **kwargs)

        return wrapped

    @staticmethod
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        if request.path in {"/login", "/accounts", "/change-password"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def login_page(self):
        return send_from_directory(self.app.static_folder, "login.html")

    @staticmethod
    def auth_mode():
        return jsonify(
            {
                "password_required": True,
                "account_auth": True,
                "initial_username": "admin",
            }
        )

    def _login_key(self, username: str) -> tuple[str, str]:
        return (
            str(request.remote_addr or "local"),
            username.casefold(),
        )

    def _login_is_blocked(self, key: tuple[str, str]) -> bool:
        now = time.monotonic()
        with self._failed_login_lock:
            recent = [
                attempt
                for attempt in self._failed_logins.get(key, [])
                if now - attempt < _LOGIN_WINDOW_SECONDS
            ]
            self._failed_logins[key] = recent
            return len(recent) >= _LOGIN_MAX_FAILURES

    def _record_login_failure(self, key: tuple[str, str]) -> None:
        with self._failed_login_lock:
            self._failed_logins.setdefault(key, []).append(time.monotonic())

    def _clear_login_failures(self, key: tuple[str, str]) -> None:
        with self._failed_login_lock:
            self._failed_logins.pop(key, None)

    def login(self):
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()[:64]
        password = str(body.get("password") or "")
        if not username or not password:
            return jsonify(
                {"ok": False, "error": "credentials_required"}
            ), 400
        key = self._login_key(username)
        if self._login_is_blocked(key):
            return jsonify(
                {"ok": False, "error": "too_many_attempts"}
            ), 429
        user = self.repo.accounts.get_auth_user_by_username(username)
        valid = bool(
            user
            and user["is_active"]
            and check_password_hash(user["password_hash"], password)
        )
        if not valid:
            self._record_login_failure(key)
            return jsonify(
                {"ok": False, "error": "invalid_credentials"}
            ), 401
        if (
            self._using_insecure_initial_password
            and user["username"] == "admin"
            and user["must_change_password"]
            and request.remote_addr not in {"127.0.0.1", "::1"}
        ):
            return jsonify(
                {
                    "ok": False,
                    "error": "initial_setup_local_only",
                    "message": "首次管理员密码只能在服务器本机修改",
                }
            ), 403
        self._clear_login_failures(key)
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["display_name"] = user["display_name"]
        session["role"] = user["role"]
        session["must_change_password"] = user["must_change_password"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        self.repo.accounts.touch_login(user["id"])
        return jsonify(
            {
                "ok": True,
                "username": user["username"],
                "operator": user["display_name"],
                "role": user["role"],
                "role_label": ROLE_LABELS[user["role"]],
                "must_change_password": user["must_change_password"],
            }
        )

    @staticmethod
    def logout():
        session.clear()
        return redirect(url_for("auth.login_page"))

    def _csrf_token(self) -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return str(token)

    def _csrf_valid(self) -> bool:
        expected = str(session.get("csrf_token") or "")
        supplied = str(request.headers.get("X-CSRF-Token") or "")
        return bool(
            expected
            and supplied
            and hmac.compare_digest(expected, supplied)
        )

    def _csrf_error(self):
        if self._is_test_bypass():
            return None
        if self._csrf_valid():
            return None
        return jsonify({"error": "invalid_csrf_token"}), 403

    def api_session(self):
        user = self.current_user()
        if user is None:
            return jsonify({"error": "unauthorized"}), 401
        role = user["role"]
        return jsonify(
            {
                "authenticated": True,
                "user_id": user["id"],
                "username": user["username"],
                "operator": user["display_name"],
                "role": role,
                "role_label": ROLE_LABELS[role],
                "must_change_password": user["must_change_password"],
                "can_manage_accounts": role in MANAGEMENT_ROLES,
                "can_delete_experiments": role == "super_admin",
                "csrf_token": self._csrf_token(),
            }
        )

    def change_password_page(self):
        return send_from_directory(
            self.app.static_folder, "change-password.html"
        )

    def change_password(self):
        csrf_error = self._csrf_error()
        if csrf_error is not None:
            return csrf_error
        user = self.repo.accounts.get_auth_user_by_id(
            self.current_user()["id"]
        )
        body = request.get_json(silent=True) or {}
        current_password = str(body.get("current_password") or "")
        new_password = str(body.get("new_password") or "")
        if not check_password_hash(
            user["password_hash"], current_password
        ):
            return jsonify({"error": "current_password_incorrect"}), 400
        error = _password_error(new_password)
        if error:
            return jsonify(
                {"error": "invalid_new_password", "message": error}
            ), 400
        if check_password_hash(user["password_hash"], new_password):
            return jsonify(
                {
                    "error": "password_unchanged",
                    "message": "新密码不能与当前密码相同",
                }
            ), 400
        updated = self.repo.accounts.update_password(
            user_id=user["id"],
            password_hash=generate_password_hash(new_password),
            actor_user_id=user["id"],
            must_change_password=False,
            action="password_changed",
        )
        session["must_change_password"] = False
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(
            {
                "ok": True,
                "must_change_password": updated["must_change_password"],
                "csrf_token": session["csrf_token"],
            }
        )

    def accounts_page(self):
        if self.current_user()["role"] not in MANAGEMENT_ROLES:
            abort(403)
        return send_from_directory(self.app.static_folder, "accounts.html")

    def _visible_users(self, actor: dict) -> list[dict]:
        users = self.repo.accounts.list_users()
        if actor["role"] == "super_admin":
            return users
        return [
            user
            for user in users
            if user["role"] == "operator" or user["id"] == actor["id"]
        ]

    def list_accounts(self):
        actor = self.current_user()
        if actor["role"] not in MANAGEMENT_ROLES:
            return jsonify({"error": "forbidden"}), 403
        return jsonify(
            {
                "users": self._visible_users(actor),
                "creatable_roles": (
                    ["operator", "supervisor"]
                    if actor["role"] == "super_admin"
                    else ["operator"]
                ),
                "role_labels": ROLE_LABELS,
            }
        )

    def create_account(self):
        actor = self.current_user()
        if actor["role"] not in MANAGEMENT_ROLES:
            return jsonify({"error": "forbidden"}), 403
        csrf_error = self._csrf_error()
        if csrf_error is not None:
            return csrf_error
        body = request.get_json(silent=True) or {}
        username = str(body.get("username") or "").strip()
        display_name = str(body.get("display_name") or "").strip()
        role = str(body.get("role") or "").strip()
        password = str(body.get("password") or "")
        allowed_roles = (
            {"supervisor", "operator"}
            if actor["role"] == "super_admin"
            else {"operator"}
        )
        if not _USERNAME_PATTERN.fullmatch(username):
            return jsonify(
                {
                    "error": "invalid_username",
                    "message": "账号需为 3–32 位字母、数字、点、横线或下划线",
                }
            ), 400
        if not display_name or len(display_name) > 64:
            return jsonify(
                {
                    "error": "invalid_display_name",
                    "message": "姓名不能为空且不能超过 64 个字符",
                }
            ), 400
        if role not in allowed_roles:
            return jsonify({"error": "role_not_allowed"}), 403
        error = _password_error(password)
        if error:
            return jsonify(
                {"error": "invalid_password", "message": error}
            ), 400
        try:
            created = self.repo.accounts.create_user(
                username=username,
                display_name=display_name,
                password_hash=generate_password_hash(password),
                role=role,
                created_by=actor["id"],
            )
        except sqlite3.IntegrityError:
            return jsonify({"error": "username_exists"}), 409
        return jsonify(created), 201

    @staticmethod
    def _can_manage(actor: dict, target: dict) -> bool:
        if actor["id"] == target["id"]:
            return False
        if actor["role"] == "super_admin":
            return target["role"] in {"supervisor", "operator"}
        if actor["role"] == "supervisor":
            return target["role"] == "operator"
        return False

    def reset_account_password(self, user_id: int):
        actor = self.current_user()
        target = self.repo.accounts.get_user_by_id(user_id)
        if target is None:
            return jsonify({"error": "account_not_found"}), 404
        if not self._can_manage(actor, target):
            return jsonify({"error": "forbidden"}), 403
        csrf_error = self._csrf_error()
        if csrf_error is not None:
            return csrf_error
        password = str(
            (request.get_json(silent=True) or {}).get("password") or ""
        )
        error = _password_error(password)
        if error:
            return jsonify(
                {"error": "invalid_password", "message": error}
            ), 400
        updated = self.repo.accounts.update_password(
            user_id=user_id,
            password_hash=generate_password_hash(password),
            actor_user_id=actor["id"],
            must_change_password=True,
            action="password_reset",
        )
        return jsonify(updated)

    def set_account_status(self, user_id: int):
        actor = self.current_user()
        target = self.repo.accounts.get_user_by_id(user_id)
        if target is None:
            return jsonify({"error": "account_not_found"}), 404
        if not self._can_manage(actor, target):
            return jsonify({"error": "forbidden"}), 403
        csrf_error = self._csrf_error()
        if csrf_error is not None:
            return csrf_error
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("is_active"), bool):
            return jsonify({"error": "is_active_must_be_boolean"}), 400
        updated = self.repo.accounts.set_active(
            user_id=user_id,
            is_active=body["is_active"],
            actor_user_id=actor["id"],
        )
        return jsonify(updated)
