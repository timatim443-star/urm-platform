"""Лаборатории (= города) из dim_laboratory: ключ и название. Для заведения учётных записей.

    python tools/list_labs.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _admin_env  # noqa: E402,F401 — путь к проекту и тихий streamlit

import logging  # noqa: E402

from urm.config import SETTINGS  # noqa: E402
from urm.db import DatabaseUnavailable, fetch_rows  # noqa: E402


def _explain(exc: BaseException) -> str:
    """Причина отказа одной строкой, без трассировки."""
    text = str(exc.__cause__ or exc)
    db = SETTINGS.db
    if not db.ro_password or "no password supplied" in text:
        return "Пароль не введён (пустой)."
    if "password authentication failed" in text:
        return (f"Сервер отклонил пароль для пользователя «{db.ro_user}». "
                f"Введено символов: {len(db.ro_password)}. Проверьте лишние пробелы и раскладку.")
    if "does not exist" in text and "role" in text:
        return f"На сервере нет пользователя «{db.ro_user}»."
    if "timeout" in text.lower() or "could not translate host" in text or "Connection refused" in text:
        reason = text.strip().splitlines()[-1] if text.strip() else ""
        return (f"Сервер {db.host}:{db.port} недоступен. Проверьте сеть / VPN.\n"
                f"  Ответ системы: {reason}\n"
                f"  Проверка в PowerShell: Test-NetConnection {db.host} -Port {db.port}")
    if "permission denied" in text:
        return f"У пользователя «{db.ro_user}» нет прав на чтение схемы urm2."
    return text.strip().splitlines()[-1]


def main() -> int:
    logging.disable(logging.CRITICAL)  # подробности ниже одной строкой
    try:
        rows = fetch_rows("SELECT lab_key, lab_display_name FROM urm2.dim_laboratory ORDER BY lab_display_name")
    except DatabaseUnavailable as exc:
        print("Ошибка:", _explain(exc))
        return 1
    width = max((len(str(r["lab_key"])) for r in rows), default=10)
    for row in rows:
        print(f"{str(row['lab_key']):<{width}}  {row['lab_display_name']}")
    print(f"\nВсего: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
