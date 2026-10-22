"""Доступ к PostgreSQL.

Два независимых соединения:
  * read-only (URM_RO_USER) — все дашборды и выгрузки;
  * read-write (URM_RW_USER) — только редактор справочников.

Чтение связанного набора таблиц выполняется одной короткой транзакцией
REPEATABLE READ READ ONLY с предварительным ACCESS SHARE на выбранных
таблицах: публикация витрин заменяет таблицы через TRUNCATE/COPY, и без
такой блокировки отдельные SELECT могут попасть по разные стороны
обновления (раздел 11 руководства).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterable, Sequence

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from urm.config import SCHEMA, SETTINGS

log = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    """Поднимается вместо показа пользователю текста серверной ошибки."""


def _options(read_only: bool, statement_timeout_ms: int | None = None) -> str:
    timeout = statement_timeout_ms or SETTINGS.db.statement_timeout_ms
    parts = [
        f"-c statement_timeout={timeout}",
        f"-c lock_timeout={SETTINGS.db.lock_timeout_ms}",
        "-c idle_in_transaction_session_timeout=60000",
        f"-c search_path={SCHEMA},public",
        "-c application_name=urm2_platform",
    ]
    if read_only:
        parts.append("-c default_transaction_read_only=on")
    return " ".join(parts)


def _connect(user: str, password: str, read_only: bool, statement_timeout_ms: int | None = None):
    db = SETTINGS.db
    if not db.host or not user:
        raise DatabaseUnavailable("Не заданы параметры подключения к PostgreSQL.")
    return psycopg.connect(
        host=db.host,
        port=db.port,
        dbname=db.dbname,
        user=user,
        password=password,
        sslmode=db.sslmode or None,
        connect_timeout=db.connect_timeout,
        row_factory=dict_row,
        options=_options(read_only, statement_timeout_ms),
    )


@contextmanager
def ro_connection(statement_timeout_ms: int | None = None):
    db = SETTINGS.db
    try:
        conn = _connect(db.ro_user, db.ro_password, True, statement_timeout_ms)
    except psycopg.Error as exc:
        log.error("RO connect failed: %s", exc)
        raise DatabaseUnavailable("Не удалось подключиться к базе в режиме чтения.") from exc
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def rw_connection():
    db = SETTINGS.db
    if not db.write_enabled:
        raise DatabaseUnavailable("Учётная запись с правом записи не настроена.")
    try:
        conn = _connect(db.rw_user, db.rw_password, False, 20_000)
    except psycopg.Error as exc:
        log.error("RW connect failed: %s", exc)
        raise DatabaseUnavailable("Не удалось подключиться к базе в режиме записи.") from exc
    try:
        yield conn
    finally:
        conn.close()


def _lock_statement(tables: Sequence[str]) -> sql.Composed:
    identifiers = [sql.Identifier(SCHEMA, name) for name in tables]
    return sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(sql.SQL(", ").join(identifiers))


def fetch_frames(
    queries: Sequence[tuple[str, Sequence]],
    lock_tables: Iterable[str] = (),
    statement_timeout_ms: int | None = None,
) -> list[pd.DataFrame]:
    """Выполняет несколько SELECT в одном согласованном снимке.

    queries: последовательность (sql, params). Имена таблиц в SQL пишутся
    явно в коде модуля queries; пользовательский ввод передаётся только
    параметрами драйвера.
    """
    tables = tuple(dict.fromkeys(lock_tables))
    result: list[pd.DataFrame] = []
    try:
        with ro_connection(statement_timeout_ms) as conn:
            conn.read_only = True
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            with conn.transaction():
                with conn.cursor() as cur:
                    if tables:
                        cur.execute(_lock_statement(tables))
                    for statement, params in queries:
                        cur.execute(statement, params)
                        rows = cur.fetchall()
                        columns = [desc.name for desc in (cur.description or [])]
                        result.append(pd.DataFrame(rows, columns=columns))
    except DatabaseUnavailable:
        raise
    except psycopg.Error as exc:
        log.error("Read failed: %s", exc)
        raise DatabaseUnavailable(
            "Не удалось прочитать данные. Возможна публикация витрин или ограничение времени запроса."
        ) from exc
    return result


def fetch_df(statement: str, params: Sequence = (), lock_tables: Iterable[str] = ()) -> pd.DataFrame:
    return fetch_frames([(statement, params)], lock_tables)[0]


def fetch_rows(statement: str, params: Sequence = ()) -> list[dict]:
    """Чтение без pandas: типы остаются ровно такими, какими их вернул драйвер.

    Нужно редактору: сравнение «ожидаемой» строки с текущей делается по точным
    значениям, а pandas привёл бы date к Timestamp и сломал бы это сравнение.
    """
    try:
        with ro_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params)
                return [dict(row) for row in cur.fetchall()]
    except DatabaseUnavailable:
        raise
    except psycopg.Error as exc:
        log.error("Read rows failed: %s", exc)
        raise DatabaseUnavailable("Не удалось прочитать справочник.") from exc
