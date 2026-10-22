"""Проверка установки: подключение, права, таблицы, снимок. Ничего не меняет.

    python tools/doctor.py
    docker compose exec urm python tools/doctor.py

Каждая строка — одна проверка: ✓ всё в порядке, ! предупреждение, ✗ ошибка и что сделать.
Код выхода 1, если есть хоть одна ошибка.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _admin_env  # noqa: E402,F401 — путь к проекту и тихий streamlit

from urm import snapshot  # noqa: E402
from urm.config import APP_SCHEMA, SCHEMA, SETTINGS  # noqa: E402
from urm.db import DatabaseUnavailable, fetch_rows, rw_connection  # noqa: E402

errors = 0


def ok(text: str) -> None:
    print(f"  ✓ {text}")


def warn(text: str) -> None:
    print(f"  ! {text}")


def fail(text: str, fix: str) -> None:
    global errors
    errors += 1
    print(f"  ✗ {text}\n      → {fix}")


def cause(exc: BaseException) -> str:
    return str(exc.__cause__ or exc).strip().splitlines()[0]


def main() -> int:
    logging.disable(logging.CRITICAL)
    db = SETTINGS.db
    print("Настройки")
    if db.host and db.ro_user:
        ok(f"PostgreSQL {db.host}:{db.port}/{db.dbname}, чтение — {db.ro_user}")
    else:
        fail("не заданы PGHOST / URM_RO_USER", "заполните .env по образцу .env.example")
        return 1
    if SETTINGS.no_auth:
        warn("ТЕСТОВЫЙ РЕЖИМ: URM_NO_AUTH=1 — вход отключён, видно всё. Для пользователей уберите.")
    else:
        ok("вход по учётным записям включён")
    if db.write_enabled:
        ok(f"запись — {db.rw_user}")
    else:
        warn("URM_RW_USER не задан: маппинг и пользователи только на просмотр")

    print("Витрины urm2 (чтение)")
    try:
        state = fetch_rows(f"SELECT last_successful_at, reference_changed FROM {SCHEMA}.etl_state")  # noqa: S608
        ok(f"подключение есть, последняя публикация витрин: {state[0]['last_successful_at'] if state else '—'}")
        count = fetch_rows(f"SELECT count(*) AS n FROM {SCHEMA}.fact_month_container_bin")[0]["n"]  # noqa: S608
        ok(f"fact_month_container_bin: {count:,} строк".replace(",", " "))
    except DatabaseUnavailable as exc:
        fail(f"нет чтения витрин: {cause(exc)}", "проверьте логин/пароль, сеть и GRANT SELECT на схему urm2")
        return 1

    print("Пользователи платформы")
    try:
        version = fetch_rows(f"SELECT max(version) AS v FROM {APP_SCHEMA}.schema_version")[0]["v"]  # noqa: S608
        ok(f"схема {APP_SCHEMA}, версия миграций {version}")
        users = fetch_rows(f"SELECT role, is_active FROM {APP_SCHEMA}.app_user")  # noqa: S608
        admins = sum(1 for u in users if u["role"] == "admin" and u["is_active"])
        (ok if admins else warn)(f"пользователей {len(users)}, активных администраторов {admins}")
        if not admins and not SETTINGS.no_auth:
            fail("нет ни одного администратора — войти будет некому",
                 'python tools/users.py add <логин> "<Имя>" admin')
    except DatabaseUnavailable as exc:
        if SETTINGS.no_auth:
            warn(f"таблицы пользователей нет ({cause(exc)}) — для тестового режима не нужна")
        else:
            fail(f"таблица пользователей недоступна: {cause(exc)}", "python tools/migrate.py up")

    if db.write_enabled:
        print("Права на запись")
        checks = [(f"{SCHEMA}.container_mapping", "UPDATE"), (f"{SCHEMA}.lab_mapping", "UPDATE"),
                  (f"{APP_SCHEMA}.app_user", "UPDATE")]
        try:
            with rw_connection() as conn, conn.cursor() as cur:
                for table, privilege in checks:
                    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (table,))
                    if not cur.fetchone()["exists"]:
                        warn(f"{table}: таблицы нет")
                        continue
                    cur.execute("SELECT has_table_privilege(%s, %s) AS ok", (table, privilege))
                    if cur.fetchone()["ok"]:
                        ok(f"{privilege} {table}")
                    else:
                        fail(f"нет права {privilege} на {table}",
                             "sql/migrations/000_db_roles.sql или 002_app_user_write.up.sql")
        except DatabaseUnavailable as exc:
            fail(f"нет подключения на запись: {cause(exc)}", "проверьте URM_RW_USER / URM_RW_PASSWORD")

    print("Снимок данных")
    try:
        snapshot.ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=snapshot.ROOT):
            pass
        ok(f"папка {snapshot.ROOT} доступна на запись")
    except OSError as exc:
        fail(f"папка снимка недоступна: {exc}", "проверьте URM_SNAPSHOT_DIR и права (в Docker — том urm-snapshots)")
    meta = snapshot.current_meta()
    if meta:
        ok(f"текущий снимок от {meta.get('built_at')}, {meta.get('rows', {}).get('main', '?')} строк, "
           f"{meta.get('size_mb', '?')} МБ")
    else:
        warn("снимка ещё нет — соберётся при первом открытии или: python tools/build_snapshot.py")

    print("\nИтог:", "всё в порядке" if not errors else f"ошибок: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
