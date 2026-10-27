"""Вход и учётные записи.

Учётные записи хранятся в базе: <URM_APP_SCHEMA>.app_user (по умолчанию
urm2_app.app_user), см. sql/migrations/. Права ролей — urm/roles.py.

Тестовый режим URM_NO_AUTH=1 — без входа и без таблицы учётных записей:
действует роль администратора, видно всё, что доступно технически.

Сессия переживает обновление страницы (F5): после входа браузер получает
cookie со случайным токеном, сервер помнит токен (urm/sessions.py) и при
новом подключении восстанавливает пользователя. Права и активность каждый
раз сверяются с базой. Одной учёткой могут пользоваться сразу несколько
человек; администратор — только с одного устройства (single_session в roles.py).
Блокировки после неверных паролей нет.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

import streamlit as st

from urm.config import APP_SCHEMA, SETTINGS
from urm.db import DatabaseUnavailable, fetch_rows
from urm.roles import policy
from urm.sessions import COOKIE, SessionStore

log = logging.getLogger(__name__)

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 200_000
RECHECK_SECONDS = 300
CLOSED_MESSAGES = {
    "expired": "Сессия истекла. Войдите снова.",
    "other_device": "Под этой учётной записью вошли с другого устройства, поэтому здесь сессия закрыта.",
    "disabled": "Учётная запись выключена администратором.",
}     # как часто перечитывать из базы роль/города/активность вошедшего


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = str(stored).split("$", 3)
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


@dataclass(frozen=True)
class User:
    login: str
    name: str
    role: str
    labs: tuple[str, ...] = field(default=())  # lab_key или подпись лаборатории

    @property
    def policy(self) -> dict:
        return policy(self.role)

    @property
    def role_title(self) -> str:
        return self.policy["title"]

    @property
    def all_labs(self) -> bool:
        return bool(self.policy["all_labs"])

    def can_open(self, page_id: str) -> bool:
        return page_id in self.policy["pages"]

    @property
    def network_quality(self) -> bool:
        return bool(self.policy["network_quality"])

    @property
    def mapping_edit(self) -> bool:
        return bool(self.policy["mapping_edit"]) and SETTINGS.db.write_enabled


TEST_USER = User(login="test", name="Тестовый режим", role="admin")


def _load_user(login: str) -> dict | None:
    rows = fetch_rows(
        f"""
        SELECT login, display_name, role, labs, password_hash, is_active
        FROM {APP_SCHEMA}.app_user WHERE lower(login) = lower(%s)
        """,  # noqa: S608 — имя схемы задаёт администратор в окружении
        (login.strip(),),
    )
    return rows[0] if rows else None


@st.cache_resource
def _store() -> SessionStore:
    """Сессии входа — общие для всех подключений процесса (см. urm/sessions.py)."""
    return SessionStore()


def _cookie_js(token: str, seconds: int) -> None:
    """Записать (seconds > 0) или стереть (seconds = 0) cookie сессии в браузере.

    Streamlit не умеет ставить cookie со стороны сервера, поэтому это делает
    маленький скрипт на странице. В cookie — только случайный токен."""
    value = json.dumps(f"{COOKIE}={token}; path=/; max-age={max(0, int(seconds))}; SameSite=Lax")
    with st.container(key="urm_auth_cookie"):
        st.html(f"<script>document.cookie = {value} + (location.protocol === 'https:' ? '; Secure' : '');</script>",
                unsafe_allow_javascript=True)


def _cookie_token() -> str | None:
    try:
        return st.context.cookies.get(COOKIE) or None
    except Exception:  # noqa: BLE001 — вне браузера (тесты, командная строка) cookie нет
        return None


def _session_data(item: dict) -> dict:
    return {"login": item["login"], "name": item.get("display_name") or item["login"],
            "role": item["role"], "labs": list(item.get("labs") or [])}


def _start(item: dict, token: str) -> None:
    st.session_state["auth_user"] = _session_data(item)
    st.session_state["auth_token"] = token
    st.session_state["auth_checked_at"] = time.time()


def _restore_from_cookie() -> dict | None:
    """Новое подключение (F5, новая вкладка): вход по cookie, если сессия ещё жива."""
    if st.session_state.get("auth_logged_out"):
        return None  # cookie в этом подключении устарел: пользователь только что вышел
    token = _cookie_token()
    login = _store().login_for(token)
    if not login:
        reason = _store().closed_reason(token)
        if reason in CLOSED_MESSAGES:  # объясняем, почему снова форма входа
            st.session_state["auth_notice"] = CLOSED_MESSAGES[reason]
        return None
    try:
        item = _load_user(login)
    except Exception as exc:  # noqa: BLE001 — база недоступна: покажем форму входа с ошибкой
        log.warning("Не удалось восстановить сессию %s: %s", login, exc)
        return None
    if not item or not item.get("is_active", True):
        _store().revoke_login(login)
        st.session_state["auth_notice"] = CLOSED_MESSAGES["disabled"]
        log.info("AUDIT session_revoked login=%s", login)
        return None
    _start(item, token)  # type: ignore[arg-type]
    log.info("AUDIT session_restored login=%s", login)
    return st.session_state["auth_user"]


def _recheck(data: dict) -> dict | None:
    """Раз в несколько минут сверяет сессию с базой: выключенный пользователь
    выходит, изменённые роль и города применяются без повторного входа."""
    if time.time() - st.session_state.get("auth_checked_at", 0.0) < RECHECK_SECONDS:
        return data
    try:
        item = _load_user(data["login"])
    except Exception as exc:  # noqa: BLE001 — база временно недоступна: не выкидываем пользователя
        log.warning("Не удалось перепроверить пользователя %s: %s", data["login"], exc)
        return data
    if not item or not item.get("is_active", True):
        _store().revoke_login(data["login"])
        log.info("AUDIT session_revoked login=%s", data["login"])
        return None
    data = _session_data(item)
    st.session_state["auth_user"] = data
    st.session_state["auth_checked_at"] = time.time()
    return data


def current_user() -> User | None:
    if SETTINGS.no_auth:
        return TEST_USER
    data = st.session_state.get("auth_user")
    if not data:
        data = _restore_from_cookie()
        if not data:
            return None
    token = st.session_state.get("auth_token")
    if _store().login_for(token) != data["login"]:
        # Сессию закрыли: истёк срок, админ вошёл с другого устройства или учётку выключили.
        st.session_state["auth_notice"] = CLOSED_MESSAGES.get(
            _store().closed_reason(token) or "", "Сессия завершена. Войдите снова.")
        logout(revoke=False)
        return None
    data = _recheck(data)
    if data is None:  # выключили или удалили из базы — сессии этой учётки уже закрыты
        st.session_state["auth_notice"] = CLOSED_MESSAGES["disabled"]
        logout(revoke=False)
        return None
    if st.session_state.pop("auth_set_cookie", False):
        _cookie_js(token, int(_store().expires_at(token) - time.time()))
    return User(
        login=data["login"], name=data["name"], role=data["role"], labs=tuple(data.get("labs") or ())
    )


def logout(revoke: bool = True) -> None:
    """Выход на этом устройстве. Другие устройства с той же учёткой остаются в системе."""
    data = st.session_state.get("auth_user")
    if data and revoke:
        log.info("AUDIT logout login=%s", data.get("login"))
    if revoke:
        _store().revoke(st.session_state.get("auth_token"))
    for key in ("auth_user", "auth_token", "auth_started_at", "auth_checked_at", "auth_set_cookie"):
        st.session_state.pop(key, None)
    st.session_state["auth_logged_out"] = True
    st.session_state["auth_clear_cookie"] = True


def _login_form() -> None:
    _, middle, _ = st.columns([1, 1.1, 1])
    with middle:
        from pathlib import Path

        st.markdown("<div style='height:48px'></div>", unsafe_allow_html=True)
        logo = Path(__file__).resolve().parent.parent / "assets" / "logo_full.png"
        if logo.exists():
            import base64

            data = base64.b64encode(logo.read_bytes()).decode()
            st.markdown(f'<img src="data:image/png;base64,{data}" alt="КДЛ Олимп" '
                        'style="display:block;margin:0 auto;width:300px;max-width:100%">', unsafe_allow_html=True)
        st.markdown(
            "<div class='urm'><div class='brand-sub' style='margin:6px 0 16px;font-size:13px;text-align:center'>"
            "<b style='color:#10213f'>УРМ</b> · Учёт расходных материалов</div></div>",
            unsafe_allow_html=True,
        )
        if st.session_state.pop("auth_clear_cookie", False):
            _cookie_js("", 0)
        notice = st.session_state.pop("auth_notice", None)
        if notice:
            st.info(notice, icon=":material/info:")
        with st.form("login_form"):
            login = st.text_input("Логин")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", type="primary", width="stretch")
        if not submitted:
            return
        key = login.strip().lower()
        try:
            item = _load_user(login)
        except DatabaseUnavailable as exc:
            st.error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — таблицы может ещё не быть
            log.error("Учётные записи недоступны: %s", exc)
            st.error(
                "Таблица учётных записей недоступна. Пока база к этому не готова, "
                "запускайте платформу в тестовом режиме (URM_NO_AUTH=1)."
            )
            return
        stored = item["password_hash"] if item else hash_password(secrets.token_hex(16))
        if item and item.get("is_active", True) and verify_password(password, stored):
            single = bool(policy(item["role"]).get("single_session"))
            token, closed = _store().create(item["login"], SETTINGS.session_minutes, single=single)
            _start(item, token)
            st.session_state["auth_set_cookie"] = True
            st.session_state.pop("auth_logged_out", None)
            log.info("AUDIT login_ok login=%s role=%s", item["login"], item["role"])
            if closed:
                log.info("AUDIT sessions_closed login=%s count=%s reason=single_session", item["login"], closed)
            st.rerun()
        log.warning("AUDIT login_failed login=%s", key)
        if any(ord(ch) > 126 for ch in password):
            st.error("Неверный логин или пароль. Похоже, пароль набран не в английской раскладке.")
        else:
            st.error("Неверный логин или пароль.")


def require_login() -> User:
    user = current_user()
    if user is None:
        _login_form()
        st.stop()
    return user  # type: ignore[return-value]


def verify_current_password(login: str, password: str) -> bool:
    """Проверка текущего пароля перед его сменой."""
    item = _load_user(login)
    return bool(item) and verify_password(password, item["password_hash"])
