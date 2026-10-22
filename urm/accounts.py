"""Учётные записи в базе: чтение, создание, изменение, смена пароля.

Таблица <URM_APP_SCHEMA>.app_user (sql/migrations/001_app_schema.up.sql).
Читает учётная запись на чтение (URM_RO_USER), пишет — на запись (URM_RW_USER):
ей нужны права из sql/migrations/002_app_user_write.up.sql.

Удаления нет: пользователя выключают (is_active = false) — так сохраняется
история, и случайно удалить последнего администратора нельзя.
"""
from __future__ import annotations

import logging
import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from urm.auth import hash_password
from urm.config import APP_SCHEMA, SETTINGS
from urm.db import DatabaseUnavailable, fetch_rows, rw_connection
from urm.roles import ROLES

log = logging.getLogger(__name__)

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")
# Пароль: любая длина (хотя бы 1 символ), только латиница, цифры и знаки ASCII без пробелов.
# Кириллица не принимается — частая ошибка «набрал в русской раскладке».
PASSWORD_RE = re.compile(r"^[\x21-\x7e]+$")
PASSWORD_RULE = "только латинские буквы, цифры и знаки (!@#…), без пробелов и кириллицы"


class AccountError(ValueError):
    """Ошибка, которую можно показать пользователю как есть."""


def _table() -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(APP_SCHEMA), sql.Identifier("app_user"))


def check_password(password: str) -> None:
    if not password:
        raise AccountError("Введите пароль.")
    if any(not ch.isprintable() for ch in password):
        # Ctrl+V в консоли Windows при скрытом вводе даёт невидимый символ \x16 вместо вставки.
        raise AccountError("В пароле есть невидимые символы — наберите его вручную, без Ctrl+V.")
    if not PASSWORD_RE.match(password):
        raise AccountError(f"Пароль: {PASSWORD_RULE}.")


def _check(role: str, labs: list[str], name: str) -> None:
    if role not in ROLES:
        raise AccountError(f"Неизвестная роль «{role}». Роли: {', '.join(ROLES)}.")
    if not ROLES[role]["all_labs"] and not labs:
        raise AccountError("Для этой роли нужно выбрать хотя бы один город.")
    if not name.strip():
        raise AccountError("Укажите имя пользователя.")


def list_users() -> list[dict]:
    return fetch_rows(
        f"SELECT login, display_name, role, labs, is_active, created_at, updated_at "  # noqa: S608
        f"FROM {APP_SCHEMA}.app_user ORDER BY is_active DESC, role, login"
    )


def _guard(func):
    def wrapper(*args, **kwargs):
        if not SETTINGS.db.write_enabled:
            raise AccountError("Не задана учётная запись на запись (URM_RW_USER) — изменения недоступны.")
        try:
            return func(*args, **kwargs)
        except (AccountError, DatabaseUnavailable):
            raise
        except psycopg.errors.UniqueViolation as exc:
            raise AccountError("Такой логин уже есть.") from exc
        except psycopg.errors.InsufficientPrivilege as exc:
            log.error("Нет прав на запись в app_user: %s", exc)
            raise AccountError("У учётной записи на запись нет прав на таблицу пользователей. "
                               "Выполните sql/migrations/002_app_user_write.up.sql.") from exc
        except psycopg.errors.CheckViolation as exc:
            raise AccountError(f"База не приняла значение: {exc.diag.constraint_name}.") from exc
        except psycopg.Error as exc:
            log.error("Ошибка записи пользователя: %s", exc)
            raise DatabaseUnavailable("Не удалось сохранить. Подробности — в журнале приложения.") from exc
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def _active_admins(cur) -> list[str]:
    cur.execute(sql.SQL("SELECT login FROM {} WHERE role = 'admin' AND is_active FOR UPDATE").format(_table()))
    return [row["login"] for row in cur.fetchall()]


@_guard
def create_user(login: str, name: str, role: str, labs: list[str], password: str, actor: str) -> None:
    login = login.strip().lower()
    if not LOGIN_RE.match(login):
        raise AccountError("Логин: 3–40 символов, латиница в нижнем регистре, цифры, точка, дефис, подчёркивание.")
    _check(role, labs, name)
    check_password(password)
    with rw_connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql.SQL("INSERT INTO {} (login, display_name, role, labs, password_hash) VALUES (%s, %s, %s, %s, %s)")
            .format(_table()),
            (login, name.strip(), role, labs, hash_password(password)),
        )
    log.info("AUDIT user_created login=%s role=%s labs=%s by=%s", login, role, labs, actor)


@_guard
def update_user(login: str, name: str, role: str, labs: list[str], is_active: bool, actor: str) -> None:
    _check(role, labs, name)
    with rw_connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        admins = _active_admins(cur)
        if login in admins and (role != "admin" or not is_active) and len(admins) <= 1:
            raise AccountError("Это последний активный администратор — его нельзя выключить или понизить.")
        if login == actor and not is_active:
            raise AccountError("Нельзя выключить собственную учётную запись.")
        cur.execute(
            sql.SQL("UPDATE {} SET display_name = %s, role = %s, labs = %s, is_active = %s, updated_at = now() "
                    "WHERE login = %s").format(_table()),
            (name.strip(), role, labs, is_active, login),
        )
        if cur.rowcount != 1:
            raise AccountError("Пользователь не найден — обновите страницу.")
    log.info("AUDIT user_updated login=%s role=%s labs=%s active=%s by=%s", login, role, labs, is_active, actor)


@_guard
def set_password(login: str, password: str, actor: str) -> None:
    check_password(password)
    with rw_connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            sql.SQL("UPDATE {} SET password_hash = %s, updated_at = now() WHERE login = %s").format(_table()),
            (hash_password(password), login),
        )
        if cur.rowcount != 1:
            raise AccountError("Пользователь не найден.")
    log.info("AUDIT password_set login=%s by=%s", login, actor)
