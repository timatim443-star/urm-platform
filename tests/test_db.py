"""Проверки с настоящим PostgreSQL: миграции, пользователи, вход, правка маппинга.

Запускаются, только если задана URM_TEST_PG=1 и переменные подключения
(PGHOST, PGPORT, PGDATABASE, URM_RO_*, URM_RW_*) указывают на ПУСТУЮ тестовую базу
со схемой sql/dev/sandbox_schema.sql. На рабочей базе не запускать.
В GitHub Actions база поднимается автоматически (.github/workflows/ci.yml).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("URM_TEST_PG") != "1", reason="нужна тестовая база: URM_TEST_PG=1")

ROOT = Path(__file__).resolve().parent.parent


def _tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / args[0]), *args[1:]],
                          capture_output=True, text=True, cwd=ROOT, env=os.environ.copy())


@pytest.fixture(scope="module", autouse=True)
def migrated():
    assert _tool("migrate.py", "down", "0").returncode == 0
    up = _tool("migrate.py", "up")
    assert up.returncode == 0, up.stdout + up.stderr
    yield
    _tool("migrate.py", "down", "0")


def test_migrations_status_and_rollback():
    assert "✓" in _tool("migrate.py", "status").stdout
    assert _tool("migrate.py", "down", "1").returncode == 0
    assert "· не применена" in _tool("migrate.py", "status").stdout
    assert _tool("migrate.py", "up").returncode == 0


def test_users_and_login():
    from urm import accounts, auth

    accounts.create_user("boss", "Админ", "admin", [], "Admin-pass-1", "test")
    accounts.create_user("kim", "Ким", "city", ["lab0"], "City-pass-12", "test")
    with pytest.raises(accounts.AccountError):
        accounts.create_user("kim", "Дубль", "network", [], "Other-pass-1", "test")
    with pytest.raises(accounts.AccountError):
        accounts.create_user("lee", "Ли", "city", [], "City-pass-12", "test")  # город обязателен

    row = auth._load_user("KIM")  # логин без учёта регистра
    assert row and auth.verify_password("City-pass-12", row["password_hash"])
    assert not auth.verify_password("wrong-pass-1", row["password_hash"])

    accounts.set_password("kim", "New-pass-123", "test")
    assert auth.verify_current_password("kim", "New-pass-123")

    with pytest.raises(accounts.AccountError):  # последнего администратора не выключить
        accounts.update_user("boss", "Админ", "admin", [], False, "someone")
    accounts.update_user("kim", "Ким", "network", [], True, "boss")
    assert auth._load_user("kim")["role"] == "network"
    accounts.update_user("kim", "Ким", "network", [], False, "boss")
    assert auth._load_user("kim")["is_active"] is False


def test_users_cli():
    listing = _tool("users.py", "list")
    assert listing.returncode == 0 and "boss" in listing.stdout


def test_doctor_runs():
    result = _tool("doctor.py")
    assert "Витрины urm2" in result.stdout, result.stdout + result.stderr


def test_mapping_edit_marks_reference_changed():
    from urm import editor
    from urm.db import fetch_rows

    table = "container_mapping"
    base = {f: None for f in editor.SPECS[table].fields}
    base.update(container_group="GRP", lis_container_code="T1", lis_container_name="Пробирка",
                onec_mdm_key="MDM1", is_active=1, comment="")
    row = editor.insert_row(table, base)
    updated = editor.update_row(table, row, {"comment": "проверка"})
    with pytest.raises(editor.StaleRowError):
        editor.update_row(table, row, {"comment": "устаревшая правка"})
    assert fetch_rows("SELECT reference_changed FROM urm2.etl_state")[0]["reference_changed"] is True
    editor.insert_row(table, {**base, "lis_container_code": "T2", "onec_mdm_key": "MDM2"})
    editor.delete_row(table, updated)
