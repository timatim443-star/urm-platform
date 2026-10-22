"""Пользователи платформы из командной строки (то же, что страница «12 Пользователи»).

    python tools/users.py list
    python tools/users.py add ivanov "Иванов А.С." admin
    python tools/users.py add kim "Ким А." city karaganda,astana
    python tools/users.py passwd ivanov
    python tools/users.py set ivanov network            # сменить роль (города — через запятую для city)
    python tools/users.py disable ivanov
    python tools/users.py enable ivanov
    python tools/users.py check ivanov                  # подходит ли пароль (ничего не меняет)

Пароль спрашивается скрыто. Ключи городов: python tools/list_labs.py
Первого администратора заводят так, с URM_ADMIN_USER/URM_ADMIN_PASSWORD
или URM_RW_USER в окружении.
"""
from __future__ import annotations

import _admin_env  # noqa: F401  — подключение под администратором
import getpass
import sys

from urm import accounts
from urm.db import DatabaseUnavailable

ACTOR = "cli"


def _ask_password() -> str:
    first = getpass.getpass("Пароль: ")
    if first != getpass.getpass("Пароль ещё раз: "):
        raise accounts.AccountError("Пароли не совпадают.")
    return first


def _labs(arg: str | None) -> list[str]:
    return [x.strip() for x in (arg or "").split(",") if x.strip()]


def _find(login: str) -> dict:
    for row in accounts.list_users():
        if row["login"] == login:
            return row
    raise accounts.AccountError(f"Пользователь «{login}» не найден.")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "list":
            for r in accounts.list_users():
                state = "" if r["is_active"] else "  [выключен]"
                print(f"{r['login']:<20} {r['role']:<8} {','.join(r['labs'] or []) or 'все':<30} "
                      f"{r['display_name']}{state}")
        elif cmd == "add" and len(args) >= 3:
            accounts.create_user(args[0], args[1], args[2], _labs(args[3] if len(args) > 3 else None),
                                 _ask_password(), ACTOR)
            print("Добавлен.")
        elif cmd == "passwd" and args:
            accounts.set_password(args[0], _ask_password(), ACTOR)
            print("Пароль изменён.")
        elif cmd == "set" and len(args) >= 2:
            row = _find(args[0])
            accounts.update_user(args[0], row["display_name"], args[1], _labs(args[2] if len(args) > 2 else None),
                                 row["is_active"], ACTOR)
            print("Сохранено.")
        elif cmd == "check" and args:
            from urm.auth import _load_user, verify_password

            row = _load_user(args[0])
            if not row:
                print(f"Пользователя «{args[0]}» нет в базе.")
                return 1
            password = getpass.getpass("Пароль: ")
            if any(not ch.isprintable() for ch in password):
                print("В введённом пароле есть невидимые символы (Ctrl+V в консоли Windows) — наберите вручную.")
            ok = verify_password(password, row["password_hash"])
            print(f"Пароль {'подходит' if ok else 'НЕ подходит'}; введено символов: {len(password)}; "
                  f"пользователь {'активен' if row['is_active'] else 'ВЫКЛЮЧЕН'}; роль {row['role']}.")
            return 0 if ok else 1
        elif cmd in ("disable", "enable") and args:
            row = _find(args[0])
            accounts.update_user(args[0], row["display_name"], row["role"], list(row["labs"] or []),
                                 cmd == "enable", ACTOR)
            print("Сохранено.")
        else:
            print(__doc__)
            return 2
    except (accounts.AccountError, DatabaseUnavailable) as exc:
        print("Ошибка:", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
