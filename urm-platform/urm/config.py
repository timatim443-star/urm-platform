"""Настройки из переменных окружения.

Тестовый режим URM_NO_AUTH=1: вход не запрашивается, учётные записи в базе
не нужны, показывается всё, что доступно технически.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

SCHEMA = "urm2"
APP_SCHEMA = os.environ.get("URM_APP_SCHEMA", "urm2_app").strip() or "urm2_app"
UNMAPPED_LAB_KEY = "__unmapped__"


def _version() -> str:
    from pathlib import Path

    file = Path(__file__).resolve().parent.parent / "VERSION"
    return os.environ.get("URM_VERSION") or (file.read_text(encoding="utf-8").strip() if file.exists() else "dev")


VERSION = _version()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    sslmode: str
    ro_user: str
    ro_password: str
    rw_user: str
    rw_password: str
    connect_timeout: int
    statement_timeout_ms: int
    lock_timeout_ms: int

    @property
    def write_enabled(self) -> bool:
        return bool(self.rw_user and self.rw_password)


@dataclass(frozen=True)
class AppSettings:
    db: DbSettings
    cache_ttl: int
    session_minutes: int
    no_auth: bool


def load_settings() -> AppSettings:
    db = DbSettings(
        host=os.environ.get("PGHOST", "").strip(),
        port=_int("PGPORT", 5432),
        dbname=os.environ.get("PGDATABASE", "URM").strip(),
        sslmode=os.environ.get("PGSSLMODE", "prefer").strip(),
        ro_user=os.environ.get("URM_RO_USER", "").strip() or os.environ.get("PGUSER", "").strip(),
        ro_password=os.environ.get("URM_RO_PASSWORD", "") or os.environ.get("PGPASSWORD", ""),
        rw_user=os.environ.get("URM_RW_USER", "").strip(),
        rw_password=os.environ.get("URM_RW_PASSWORD", ""),
        connect_timeout=_int("URM_CONNECT_TIMEOUT", 10),
        statement_timeout_ms=_int("URM_STATEMENT_TIMEOUT_MS", 180_000),
        lock_timeout_ms=_int("URM_LOCK_TIMEOUT_MS", 5_000),
    )
    return AppSettings(
        db=db,
        cache_ttl=_int("URM_CACHE_TTL", 600),
        session_minutes=_int("URM_SESSION_MINUTES", 240),
        no_auth=_flag("URM_NO_AUTH"),
    )


SETTINGS = load_settings()
