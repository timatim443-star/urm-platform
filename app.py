"""УРМ — учёт расходных материалов. Веб-версия отчёта УРМ v4 + маппинг + роли.

Запуск: streamlit run app.py --server.address=0.0.0.0 --server.port=8501

  urm/report_core.py — генератор отчётов без изменений (расчёты, графики, CSS);
  urm/snapshot.py    — снимок витрин: PostgreSQL → Parquet → DuckDB;
  urm/roles.py       — права ролей, единственное место настройки доступа;
  views/report.py    — страницы 01–10, перенос страниц генератора;
  views/mapping.py   — 11 Маппинг: справочники, правка в базе, проверки.
"""
from __future__ import annotations

import html as htmllib
import logging
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from urm import accounts, engine, notices, snapshot
from urm.auth import logout, require_login, verify_current_password
from urm.config import SETTINGS, VERSION
from urm.db import DatabaseUnavailable
from urm.report_core import export_excel
from urm.roles import PAGES
from urm.style import html, inject, reset_ids
from views import mapping, report, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
ASSETS = Path(__file__).resolve().parent / "assets"
st.set_page_config(page_title="УРМ — Учет расходных материалов", page_icon=str(ASSETS / "favicon.png"),
                   layout="wide", initial_sidebar_state="expanded")
inject()


def _img64(name: str) -> str:
    import base64

    path = ASSETS / name
    return base64.b64encode(path.read_bytes()).decode() if path.exists() else ""
user = require_login()


# ------------------------------------------------------------------ данные

try:
    snapshot_path, mem, meta = engine.current()
except Exception as exc:  # noqa: BLE001 — показываем причину вместо трассировки
    st.error(f"Данные недоступны: {exc}")
    st.stop()

labs_all = mem["lab"]
labs_all = labs_all[labs_all["lab_key"].astype(str) != ""].sort_values("lab_display_name")
if user.all_labs:
    labs_allowed = labs_all
else:
    wanted = {str(v).strip().casefold() for v in user.labs}
    labs_allowed = labs_all[
        labs_all["lab_key"].str.casefold().isin(wanted)
        | labs_all["lab_display_name"].astype(str).str.casefold().isin(wanted)
    ]
    if labs_allowed.empty:
        st.error("Учётной записи не назначена ни одна лаборатория (город). Обратитесь к администратору.")
        st.stop()
lab_names = dict(zip(labs_allowed["lab_key"], labs_allowed["lab_display_name"]))


@st.cache_resource(show_spinner=False, max_entries=32)
def _customers(path: str, labs: tuple[str, ...]) -> pd.DataFrame:
    main = engine._memory(path)["main"]
    if labs:
        main = main[main["lab_key"].isin(labs)]
    out = main.groupby("bin", as_index=False, observed=True).agg(
        partner_name=("partner_name", "first"), issued=("OneC_Issued", "sum"))
    # Все заказчики доступных городов, по убыванию выдачи. В выпадающем списке —
    # первые 5000; любой другой выбирается вводом БИН целиком.
    return out[out["bin"] != ""].sort_values("issued", ascending=False).reset_index(drop=True)


@st.cache_resource(show_spinner=False, max_entries=8)
def _materials(path: str) -> pd.DataFrame:
    main = engine._memory(path)["main"]
    return (main.groupby(["container_group", "container_display_name"], as_index=False, observed=True)["OneC_Issued"]
            .sum().sort_values("OneC_Issued", ascending=False))


months = [pd.Timestamp(m) for m in sorted(pd.to_datetime(mem["main"]["month"].dropna().unique()))]
if not months:
    st.warning("В снимке нет ни одного месяца с данными.")
    st.stop()


# ------------------------------------------------------------------ фильтры

def _persist(key: str, default):
    """Значения фильтров сохраняются при переходе между страницами."""
    saved = st.session_state.setdefault("_saved_filters", {})
    if key not in st.session_state:
        st.session_state[key] = saved.get(key, default)


def _remember(*keys: str) -> None:
    saved = st.session_state.setdefault("_saved_filters", {})
    for key in keys:
        saved[key] = st.session_state.get(key)


def _valid_period(value) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Период — всегда пара месяцев из снимка; всё остальное отбрасывается."""
    try:
        first, last = (pd.Timestamp(v) for v in value)
    except (TypeError, ValueError):
        return None
    if first not in months or last not in months:
        return None
    return (min(first, last), max(first, last))


def _bump_period_widget() -> None:
    # Новый ключ — ползунок создаётся заново с нужным значением. Так он всегда
    # остаётся диапазоном и не зависит от того, как Streamlit хранит состояние виджета.
    st.session_state["period_v"] = st.session_state.get("period_v", 0) + 1


def filter_bar() -> engine.Scope:
    """Две строки: период, город и быстрые кнопки периода; затем заказчик и материал на всю ширину."""
    if st.session_state.pop("_reset", False):
        st.session_state["_saved_filters"] = {}
        for key in ("period", "f_labs", "f_bins", "f_mats"):
            st.session_state.pop(key, None)
        _bump_period_widget()

    period = _valid_period(st.session_state.get("period")) or (months[0], months[-1])
    st.session_state["period"] = period
    _persist("f_labs", [])
    st.session_state["f_labs"] = [k for k in st.session_state["f_labs"] if k in lab_names]
    _persist("f_bins", [])
    _persist("f_mats", [])

    windows = engine.presets(mem["main"])

    def _set_period(start, end) -> None:
        st.session_state["period"] = (pd.Timestamp(start), pd.Timestamp(end))
        _bump_period_widget()

    with st.container(key="urmfilters"):
        widths = [2.3, 1.7] + [1.2 if i == 0 else 0.8 for i in range(len(windows))] + [1.0]
        row = st.columns(widths, gap="small", vertical_alignment="bottom")
        with row[0]:
            chosen = st.select_slider("Период", options=months, value=period,
                                      key=f"period_w{st.session_state.get('period_v', 0)}",
                                      format_func=lambda v: pd.Timestamp(v).strftime("%Y-%m"))
            st.session_state["period"] = _valid_period(chosen) or period
        with row[1]:
            st.multiselect("Город / лаборатория", list(lab_names), key="f_labs",
                           format_func=lambda v: lab_names.get(v, v),
                           placeholder="Все доступные" if not user.all_labs else "Все")
        current = st.session_state["period"]
        for column, (label, (start, end)) in zip(row[2:], windows.items()):
            active = (pd.Timestamp(start), pd.Timestamp(end)) == tuple(current)
            column.button(label, on_click=_set_period, args=(start, end), width="stretch",
                          key=f"preset_{label}", type="primary" if active else "secondary")
        with row[-1]:
            if st.button("Сбросить", width="stretch", type="tertiary", icon=":material/restart_alt:"):
                st.session_state["_reset"] = True
                st.rerun()

        selected_labs = tuple(st.session_state["f_labs"])
        scope_labs = selected_labs or (tuple(lab_names) if not user.all_labs else ())
        catalog = _customers(snapshot_path, scope_labs)
        labels = dict(zip(catalog["bin"], catalog["partner_name"]))
        st.session_state["f_bins"] = list(dict.fromkeys(
            str(b).strip() for b in st.session_state["f_bins"] if str(b).strip() in labels))
        options = catalog["bin"].head(5000).tolist()
        options += [b for b in st.session_state["f_bins"] if b not in set(options)]
        mats = _materials(snapshot_path)
        mat_names = dict(zip(mats["container_group"], mats["container_display_name"]))
        second = st.columns([1, 1], gap="small")
        with second[0]:
            st.multiselect("Заказчик (БИН)", options, key="f_bins", accept_new_options=True,
                           format_func=lambda v: f"{v} · {labels.get(v, '')}",
                           placeholder="Все заказчики — начните вводить БИН или название",
                           help=None if len(catalog) <= 5000 else
                           notices.text("hint_bins_limit", total=f"{len(catalog):,}".replace(",", " ")))
        with second[1]:
            st.multiselect("Материал", mats["container_group"].tolist(), key="f_mats",
                           format_func=lambda v: f"{v} — {mat_names.get(v, '')}",
                           placeholder="Все материалы")
    _remember("f_labs", "f_bins", "f_mats")

    start, end = st.session_state["period"]
    return engine.Scope(
        month_from=pd.Timestamp(start).strftime("%Y-%m-01"),
        month_to=pd.Timestamp(end).strftime("%Y-%m-01"),
        labs=scope_labs,
        bins=tuple(st.session_state["f_bins"]),
        materials=tuple(st.session_state["f_mats"]),
        network_quality=user.network_quality and not (scope_labs and not user.all_labs),
    )


def _scope_title(scope: engine.Scope | None) -> tuple[str, str]:
    if scope is None:
        return "", ""
    names = [lab_names.get(k, k) for k in scope.labs]
    if not names:
        return "УРМ: Общий аналитический отчет", "Вся компания / все лаборатории"
    if len(names) == 1:
        return f"УРМ: {names[0]}", f"Отдельный отчет: {names[0]}"
    return "УРМ: Сводный отчет", "Лаборатории: " + ", ".join(names)


def _own_password() -> None:
    with st.form("own_password", clear_on_submit=True, border=False):
        old = st.text_input("Текущий пароль", type="password")
        new = st.text_input("Новый пароль", type="password")
        new2 = st.text_input("Новый пароль ещё раз", type="password")
        if st.form_submit_button("Сменить пароль", width="stretch"):
            try:
                if not verify_current_password(user.login, old):
                    st.error("Текущий пароль неверен.")
                elif new != new2:
                    st.error("Новые пароли не совпадают.")
                else:
                    accounts.set_password(user.login, new, user.login)
                    st.success("Пароль изменён.")
            except (accounts.AccountError, DatabaseUnavailable) as exc:
                st.error(str(exc))


def top_bar(slot, page_id: str, title: str = "", subtitle: str = "", latest: str | None = None) -> None:
    """Шапка: заголовок слева, дата и настоящее меню Streamlit — в одной правой строке."""
    with slot:
        title_col, toolbar_col = st.columns([5.1, 4.9], gap="small", vertical_alignment="center")
        with title_col:
            if title:
                html(f'''<div class="top"><div class="title"><h1>{htmllib.escape(title)}</h1>
                    <p>{htmllib.escape(PAGES[page_id])} · {htmllib.escape(subtitle)}</p></div></div>''')
        with toolbar_col, st.container(key="urm_toolbar_right"):
            update_col, profile_col = st.columns([2.7, 1.5], gap="small", vertical_alignment="center")
            with update_col:
                updated = htmllib.escape(str(meta.get("built_at", "—"))[:16])
                # Здесь намеренно st.html, а не общий html(): он добавляет .urm внутри
                # Markdown, который получает собственную высоту/отступы в Streamlit.
                # Дата и настоящий popover-профиль теперь центрируются одинаковыми
                # 40px-оболочками (см. urm/style.py).
                st.html(f'''<div class="urm-toolbar-updated">
                    <span>Последнее обновление</span><strong>{updated}</strong>
                </div>''')
            with profile_col:
                role = "Демонстрационный доступ" if SETTINGS.no_auth else user.role_title
                display_name = "Тестовый Тест" if SETTINGS.no_auth else user.name
                cities = "все города" if user.all_labs else ", ".join(lab_names.values())
                with st.popover(display_name, icon=":material/account_circle:"):
                    st.caption(f"{role} · {cities}")
                    # у общих учёток (город, все города) пароль меняет только администратор — см. roles.py
                    if not SETTINGS.no_auth and SETTINGS.db.write_enabled and user.policy.get("own_password", True):
                        with st.expander("Сменить пароль"):
                            _own_password()
                    if SETTINGS.no_auth:
                        if st.button("Выйти", key="demo_exit_btn", width="stretch",
                                     help="Демонстрационный выход; вход по паролю отключён"):
                            st.session_state["_demo_signed_out"] = True
                            st.rerun()
                    elif st.button("Выйти", width="stretch", key="logout_btn"):
                        logout()
                        st.rerun()


# ------------------------------------------------------------------ страницы

@st.cache_resource(show_spinner=False, max_entries=4, ttl=6 * 3600)
def _full_export_cached(path: str, scope: engine.Scope) -> bytes:
    """Книга один раз на снимок и срез: повторное скачивание — мгновенно, для всех пользователей."""
    return _full_export(engine.compute(path, scope), scope)


def _full_export(r: dict, scope: engine.Scope) -> bytes:
    """Полная книга Excel по текущему срезу — как в генераторе отчётов."""
    where, params = engine._where(scope, bins=False)
    data = dict(r["data"])
    data["procedure"] = snapshot.query(Path(snapshot_path), "procedure", where, params)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "urm.xlsx"
        export_excel(path, r["fact"], data, r["anomalies"], None)
        return path.read_bytes()


def run(page_id: str) -> None:
    reset_ids()
    if SETTINGS.no_auth and st.session_state.get("_demo_signed_out", False):
        st.markdown("### Тестовый сеанс завершён")
        st.info("Демонстрационный выход: вход по паролю отключён. Данные и права доступа не изменились.")
        if st.button("Вернуться в тестовый режим", type="primary", key="demo_enter_btn"):
            st.session_state.pop("_demo_signed_out", None)
            st.rerun()
        return
    top = st.container(key="urmtop")
    if not user.can_open(page_id):
        top_bar(top, page_id)
        st.error("Раздел недоступен для вашей роли.")
        return
    if page_id == "users":  # учётные записи не зависят от фильтров данных
        top_bar(top, page_id, "УРМ: пользователи", "учётные записи и роли")
        users.users_page(user, dict(zip(labs_all["lab_key"], labs_all["lab_display_name"])))
        return
    if page_id == "method":  # главная страница: без фильтров и пояснений
        top_bar(top, page_id)
        report.method_page()
        return

    if page_id == "quality":
        # Большинство DQ-витрин не имеет измерений по периоду / БИН / материалу.
        # Здесь применяем только серверный допуск к лабораториям; лишних виджетов нет.
        allowed_labs = () if user.all_labs else tuple(lab_names)
        scope = engine.Scope(
            month_from=pd.Timestamp(months[0]).strftime("%Y-%m-01"),
            month_to=pd.Timestamp(months[-1]).strftime("%Y-%m-01"),
            labs=allowed_labs, network_quality=user.network_quality,
        )
    else:
        scope = filter_bar()
    title, scope_label = _scope_title(scope)
    st.session_state["_scope_label"] = scope_label
    top_bar(top, page_id, title, scope_label, scope.month_to[:7])
    if page_id == "anomalies":
        notices.show("delta_warning_anomalies")
    elif page_id != "mapping":
        notices.show("delta_warning")
    r = engine.compute(snapshot_path, scope)
    if r["fact"].empty and page_id not in ("quality", "mapping"):
        html('<div class="empty">В выбранном срезе нет данных. Измените период или фильтры.</div>')
        return
    excel_slot.download_button(
        "Excel по текущему срезу", data=lambda: _full_export_cached(snapshot_path, scope), file_name="УРМ_срез.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch",
        icon=":material/download:", help="Все листы генератора отчётов: динамика, лаборатории, заказчики, "
        "материалы, аномалии, исключения, ПК, детальные данные — по выбранным фильтрам. По всей сети "
        "за весь период файл большой (десятки МБ) и готовится до минуты; по городу — секунды.")

    if page_id == "overview":
        report.overview(r)
    elif page_id == "labs":
        report.labs_page(r)
    elif page_id == "customers":
        report.customers_page(r)
    elif page_id == "customer_card":
        report.customer_card(r)
    elif page_id == "materials":
        report.materials_page(r)
    elif page_id == "procedure":
        report.procedure_page(r)
    elif page_id == "anomalies":
        report.anomalies_page(r)
    elif page_id == "quality":
        report.quality_page(r, scope.network_quality)
    elif page_id == "watchlist":
        report.watchlist_page(r)
    elif page_id == "mapping":
        mapping.mapping_page(user, r, engine.state())
    timing = f' • срез: {r["source"]}, {r["seconds"]} с' if notices.enabled("footer_timing") else ""
    html(f'<div class="footer">УРМ • веб-версия отчета v4{timing} • '
         f'данные на {htmllib.escape(meta.get("built_at", "—"))}</div>')


def _page(page_id: str):
    def render() -> None:
        run(page_id)
    render.__name__ = f"page_{page_id}"
    return st.Page(render, title=PAGES[page_id], url_path=page_id,
                   default=(page_id == user.policy["pages"][0]))


pages = [_page(p) for p in PAGES if user.can_open(p)]
navigation = st.navigation(pages, position="hidden")
report.PAGE_REFS.clear()
report.PAGE_REFS.update({p.url_path: p for p in pages})


# ------------------------------------------------------------------ боковая панель

with st.sidebar:
    logo = _img64("logo_full.png")
    if logo:
        html(f'<img src="data:image/png;base64,{logo}" alt="КДЛ Олимп" '
             'style="width:100%;max-width:230px;display:block;margin:0 auto 10px">')
    html('<div style="margin:0 2px 14px;font-size:12px;color:#5d6b7e;text-align:center;white-space:nowrap">'
         '<b style="color:#13263d;font-size:14px">УРМ</b> · учёт расходных материалов</div>')
    for page in pages:
        st.page_link(page)
    st.divider()
    excel_slot = st.container()  # кнопку выгрузки ставит страница: ей известен срез
    building = snapshot.status()
    build_info = f" · сборка {meta.get('seconds', {}).get('total', '—')} с" if notices.enabled("sidebar_build") else ""
    st.caption(f"Данные на: {meta.get('built_at', '—')}{build_info} · версия {VERSION}")
    if building.get("building"):
        st.caption(f"Идёт обновление снимка: {building.get('stage') or 'подготовка'}…")
    if building.get("error"):
        st.caption(f"Последняя сборка не удалась: {building['error'][:160]}")
    if user.policy.get("mapping_edit") and st.button("Пересобрать снимок сейчас", width="stretch"):
        with st.spinner("Пересобираю снимок…"):
            snapshot.build(engine._publication())
        st.cache_resource.clear()
        st.rerun()

navigation.run()
