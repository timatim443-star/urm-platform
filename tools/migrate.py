"""Миграции базы платформы (схема urm2_app). Витрины urm2 не трогаются.

    python tools/migrate.py status     # какие миграции применены
    python tools/migrate.py up         # применить все новые
    python tools/migrate.py down 2     # откатить до версии 2 (выполнит *.down.sql новее неё)

Нужна учётная запись с правом создавать схему: URM_ADMIN_USER/URM_ADMIN_PASSWORD
(или URM_RW_USER). Файлы — sql/migrations/NNN_имя.up.sql и NNN_имя.down.sql.
Миграция 000 (учётные записи PostgreSQL) выполняется администратором БД вручную.
"""
from __future__ import annotations

import _admin_env  # noqa: F401  — подключение под администратором
import re
import sys
from pathlib import Path

import psycopg

from urm.config import APP_SCHEMA
from urm.db import DatabaseUnavailable, rw_connection

ROOT = Path(__file__).resolve().parent.parent / "sql" / "migrations"
NAME = re.compile(r"^(\d{3})_(.+)\.(up|down)\.sql$")


def _files() -> dict[int, dict[str, Path]]:
    found: dict[int, dict[str, Path]] = {}
    for path in sorted(ROOT.glob("*.sql")):
        match = NAME.match(path.name)
        if match and int(match.group(1)) > 0:
            found.setdefault(int(match.group(1)), {})[match.group(3)] = path
    return found


def _applied(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"{APP_SCHEMA}.schema_version",))
        if not cur.fetchone()["ok"]:
            return set()
        cur.execute(f"SELECT version FROM {APP_SCHEMA}.schema_version")  # noqa: S608
        return {row["version"] for row in cur.fetchall()}


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    files = _files()
    try:
        with rw_connection() as conn:
            conn.autocommit = True
            applied = _applied(conn)
            if command == "status":
                for version, parts in files.items():
                    mark = "✓ применена" if version in applied else "· не применена"
                    print(f"{version:03d}  {parts['up'].stem.removesuffix('.up'):<28} {mark}")
                return 0
            if command == "up":
                pending = [v for v in files if v not in applied]
                if not pending:
                    print("Новых миграций нет.")
                for version in pending:
                    with conn.transaction():
                        conn.execute(files[version]["up"].read_text(encoding="utf-8"))
                    print(f"{version:03d} применена")
                return 0
            if command == "down" and len(argv) > 2:
                target = int(argv[2])
                for version in sorted((v for v in applied if v > target), reverse=True):
                    down = files.get(version, {}).get("down")
                    if not down:
                        print(f"Для {version:03d} нет файла отката — остановлено.")
                        return 1
                    with conn.transaction():
                        conn.execute(down.read_text(encoding="utf-8"))
                    print(f"{version:03d} откачена")
                return 0
    except (DatabaseUnavailable, psycopg.Error) as exc:
        print("Ошибка:", str(exc).strip().splitlines()[0])
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
