"""12 Пользователи — учётные записи в базе: кто входит, с какой ролью и какими городами.

Доступна ролям с users_manage (по умолчанию — администратор). Сохранение
требует учётной записи на запись URM_RW_USER с правами из
sql/migrations/002_app_user_write.up.sql. Удаления нет — только выключение.
"""
from __future__ import annotations

import html as htmllib

import pandas as pd
import streamlit as st

from urm import accounts
from urm.auth import User
from urm.config import APP_SCHEMA, SETTINGS
from urm.db import DatabaseUnavailable
from urm.roles import ROLES
from urm.style import html


def _role_label(role: str) -> str:
    return f"{ROLES[role]['title']} ({role})" if role in ROLES else role


def users_page(user: User, lab_names: dict[str, str]) -> None:
    html('<div class="notice">Учётные записи хранятся в базе '
         f'(<code>{htmllib.escape(APP_SCHEMA)}.app_user</code>). Права ролей настраиваются в '
         '<code>urm/roles.py</code>. Пользователя не удаляют, а выключают.</div>')
    try:
        rows = accounts.list_users()
    except DatabaseUnavailable as exc:
        st.error(f"{exc} Если таблицы ещё нет — выполните sql/migrations/001_app_schema.up.sql "
                 "(см. docs/02_АДМИНИСТРИРОВАНИЕ.md).")
        return
    can_write = SETTINGS.db.write_enabled and user.policy.get("users_manage", False)
    if not SETTINGS.db.write_enabled:
        st.info("Только просмотр: не задана учётная запись на запись (URM_RW_USER).", icon="ℹ️")

    table = pd.DataFrame(rows)
    if not table.empty:
        view = pd.DataFrame({
            "Логин": table["login"],
            "Имя": table["display_name"],
            "Роль": table["role"].map(_role_label),
            "Города": table["labs"].map(lambda v: ", ".join(lab_names.get(x, x) for x in (v or [])) or "все"),
            "Активен": table["is_active"].map({True: "да", False: "нет"}),
            "Изменён": pd.to_datetime(table["updated_at"]).dt.strftime("%Y-%m-%d %H:%M"),
        })
        st.dataframe(view, hide_index=True, width="stretch")
    else:
        st.warning("Пользователей пока нет. Заведите первого администратора ниже "
                   "или командой tools/users.py (см. docs/02_АДМИНИСТРИРОВАНИЕ.md).")
    if not can_write:
        return

    roles = list(ROLES)
    lab_keys = list(lab_names)
    add_tab, edit_tab, pwd_tab = st.tabs(["Добавить", "Изменить", "Сменить пароль"])

    with add_tab, st.form("user_add", clear_on_submit=True):
        c1, c2 = st.columns(2)
        login = c1.text_input("Логин", placeholder="ivanov")
        name = c2.text_input("Имя", placeholder="Иванов А.С.")
        role = c1.selectbox("Роль", roles, format_func=_role_label)
        labs = c2.multiselect("Города (для роли «Город»)", lab_keys, format_func=lambda v: lab_names.get(v, v),
                              placeholder="Выберите города")
        password = c1.text_input("Пароль", type="password", help=accounts.PASSWORD_RULE.capitalize())
        password2 = c2.text_input("Пароль ещё раз", type="password")
        if st.form_submit_button("Добавить пользователя", type="primary"):
            if password != password2:
                st.error("Пароли не совпадают.")
            else:
                _run(lambda: accounts.create_user(login, name, role, labs, password, user.login),
                     f"Пользователь «{login.strip().lower()}» добавлен.")

    logins = [r["login"] for r in rows]
    by_login = {r["login"]: r for r in rows}
    with edit_tab:
        if not logins:
            st.caption("Пока некого изменять.")
        else:
            target = st.selectbox("Пользователь", logins, key="user_edit_pick",
                                  format_func=lambda v: f"{v} — {by_login[v]['display_name']}")
            current = by_login[target]
            with st.form(f"user_edit_{target}"):
                c1, c2 = st.columns(2)
                name = c1.text_input("Имя", value=current["display_name"] or "")
                role = c2.selectbox("Роль", roles, index=roles.index(current["role"]) if current["role"] in roles else 0,
                                    format_func=_role_label)
                labs = c1.multiselect("Города (для роли «Город»)", lab_keys,
                                      default=[x for x in (current["labs"] or []) if x in lab_names],
                                      format_func=lambda v: lab_names.get(v, v), placeholder="Выберите города")
                active = c2.checkbox("Активен (может входить)", value=bool(current["is_active"]))
                unknown = [x for x in (current["labs"] or []) if x not in lab_names]
                if unknown:
                    st.caption("В базе также указаны: " + ", ".join(unknown) + " — таких городов нет в данных; "
                               "при сохранении они будут убраны.")
                if st.form_submit_button("Сохранить", type="primary"):
                    _run(lambda: accounts.update_user(target, name, role, labs, active, user.login),
                         f"Изменения для «{target}» сохранены.")

    with pwd_tab:
        if logins:
            target = st.selectbox("Пользователь", logins, key="user_pwd_pick")
            with st.form("user_pwd", clear_on_submit=True):
                password = st.text_input("Новый пароль", type="password")
                password2 = st.text_input("Новый пароль ещё раз", type="password")
                if st.form_submit_button("Установить пароль", type="primary"):
                    if password != password2:
                        st.error("Пароли не совпадают.")
                    else:
                        _run(lambda: accounts.set_password(target, password, user.login),
                             f"Пароль для «{target}» изменён. Сообщите его пользователю лично.")


def _run(action, success: str) -> None:
    try:
        action()
    except (accounts.AccountError, DatabaseUnavailable) as exc:
        st.error(str(exc))
        return
    st.success(success)
