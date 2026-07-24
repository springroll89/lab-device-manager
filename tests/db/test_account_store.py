import sqlite3

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from lab_device_manager.db.repository import Repository


def test_account_migration_and_initial_admin_are_idempotent():
    repo = Repository(":memory:")

    first = repo.accounts.ensure_initial_admin(
        generate_password_hash("admin")
    )
    second = repo.accounts.ensure_initial_admin(
        generate_password_hash("different")
    )
    auth_user = repo.accounts.get_auth_user_by_username("ADMIN")

    assert first["id"] == second["id"]
    assert first["username"] == "admin"
    assert first["role"] == "super_admin"
    assert first["must_change_password"] is True
    assert "password_hash" not in first
    assert check_password_hash(auth_user["password_hash"], "admin")


def test_account_store_creates_users_without_exposing_password_hash():
    repo = Repository(":memory:")
    admin = repo.accounts.ensure_initial_admin(
        generate_password_hash("admin")
    )

    created = repo.accounts.create_user(
        username="operator01",
        display_name="操作员一号",
        password_hash=generate_password_hash("Operator123"),
        role="operator",
        created_by=admin["id"],
    )
    auth_user = repo.accounts.get_auth_user_by_id(created["id"])

    assert created["role"] == "operator"
    assert created["must_change_password"] is True
    assert "password_hash" not in created
    assert check_password_hash(
        auth_user["password_hash"], "Operator123"
    )


def test_account_username_is_case_insensitive_unique():
    repo = Repository(":memory:")
    admin = repo.accounts.ensure_initial_admin(
        generate_password_hash("admin")
    )
    repo.accounts.create_user(
        username="tablet01",
        display_name="平板一号",
        password_hash=generate_password_hash("Tablet123"),
        role="operator",
        created_by=admin["id"],
    )

    with pytest.raises(sqlite3.IntegrityError):
        repo.accounts.create_user(
            username="TABLET01",
            display_name="重复平板",
            password_hash=generate_password_hash("Tablet456"),
            role="operator",
            created_by=admin["id"],
        )
