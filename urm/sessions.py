"""Сессии входа: переживают обновление страницы (F5) и перезапуск сервера.

Как устроено:
  * при входе создаётся случайный токен; браузер хранит его в cookie `urm_session`
    (в cookie нет ни пароля, ни роли — только случайная строка);
  * на сервере хранится не сам токен, а его SHA-256 → логин и срок действия.
    Записи держатся в памяти процесса и дублируются в файл
    <URM_SNAPSHOT_DIR>/_sessions.json, чтобы перезапуск сервера не выкидывал всех;
  * при каждом открытии страницы токен проверяется по этому хранилищу, а пользователь —
    по базе (выключен, сменили роль или города — применяется сразу);
  * «Выйти» удаляет запись — cookie этого браузера больше ничего не открывает;
  * срок — URM_SESSION_MINUTES (по умолчанию 240 минут = 4 часа) с момента входа.

Одна учётная запись может быть открыта на многих устройствах одновременно
(общая учётка города). Для ролей с `single_session` (администратор) новый вход
закрывает все прежние сессии этой учётки — работать с двух устройств сразу нельзя.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE = "urm_session"


def _path() -> Path:
    custom = os.environ.get("URM_SESSION_FILE", "").strip()
    if custom:
        return Path(custom).resolve()
    return Path(os.environ.get("URM_SNAPSHOT_DIR", "snapshots")).resolve() / "_sessions.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionStore:
    """Потокобезопасное хранилище: {sha256(токен): {login, created, expires}}."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _path()
        self._lock = threading.Lock()
        self._items: dict[str, dict] = self._read()
        self._closed: dict[str, str] = {}  # почему закрыта сессия (для сообщения на форме входа); только в памяти

    # ---------------------------------------------------------------- файл
    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("expires", 0) > now}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:  # повреждённый файл — начинаем с чистого листа
            log.warning("Файл сессий %s не прочитан (%s) — все войдут заново.", self.path, exc)
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            part = self.path.with_name(self.path.name + ".part")
            part.write_text(json.dumps(self._items), encoding="utf-8")
            os.replace(part, self.path)
        except OSError as exc:  # не критично: сессии продолжат жить в памяти
            log.warning("Не удалось сохранить файл сессий %s: %s", self.path, exc)

    def _purge(self, now: float) -> bool:
        dead = [k for k, v in self._items.items() if v.get("expires", 0) <= now]
        for key in dead:
            del self._items[key]
            self._note(key, "expired")
        return bool(dead)

    def _note(self, key: str, reason: str) -> None:
        if len(self._closed) > 5000:  # не копим бесконечно
            self._closed.clear()
        self._closed[key] = reason

    # ---------------------------------------------------------------- API
    def create(self, login: str, minutes: int, single: bool = False) -> tuple[str, int]:
        """Новая сессия. single=True — закрыть остальные сессии этой учётки.
        Возвращает (токен, сколько прежних сессий закрыто)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._purge(now)
            closed = 0
            if single:
                for key in [k for k, v in self._items.items() if v.get("login") == login]:
                    del self._items[key]
                    self._note(key, "other_device")
                    closed += 1
            self._items[_digest(token)] = {"login": login, "created": now, "expires": now + minutes * 60}
            self._save()
        return token, closed

    def login_for(self, token: str | None) -> str | None:
        """Логин по токену или None (нет такой сессии, истекла, закрыта)."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            item = self._items.get(_digest(token))
            if not item:
                return None
            if item.get("expires", 0) <= now:
                del self._items[_digest(token)]
                self._note(_digest(token), "expired")
                self._save()
                return None
            return item.get("login")

    def expires_at(self, token: str) -> float:
        with self._lock:
            return float(self._items.get(_digest(token), {}).get("expires", 0))

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._items.pop(_digest(token), None) is not None:
                self._save()

    def revoke_login(self, login: str) -> int:
        """Закрыть все сессии учётки (например, её выключили)."""
        with self._lock:
            keys = [k for k, v in self._items.items() if v.get("login") == login]
            for key in keys:
                del self._items[key]
                self._note(key, "disabled")
            if keys:
                self._save()
            return len(keys)

    def closed_reason(self, token: str | None) -> str | None:
        """Почему сессия закрыта: expired / other_device / disabled; None — неизвестно (например, после
        перезапуска сервера)."""
        if not token:
            return None
        with self._lock:
            return self._closed.pop(_digest(token), None)

    def count(self, login: str) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for v in self._items.values() if v.get("login") == login and v.get("expires", 0) > now)
