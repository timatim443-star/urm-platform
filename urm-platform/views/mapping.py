"""11 Маппинг — справочники ЛИС ↔ 1С: просмотр, правка в базе, проверки.

Правка пишется в те же входные таблицы схемы urm2, из которых подрядчик
считает витрины: изменение применяется в базе, а не в интерфейсе. Витрины и
снимок обновятся после ближайшего успешного ночного расчёта.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from urm import editor, validate
from urm.auth import User
from urm.db import DatabaseUnavailable
from urm.report_core import dataframe_html, fmt_num, kpi_card
from urm.style import html, panel_table


def _after_write(message: str) -> None:
    st.cache_data.clear()
    st.session_state["map_message"] = message
    st.rerun()


def _form_fields(spec, current: dict, prefix: str) -> dict:
    values: dict = {}
    for field in spec.fields:
        value = current.get(field)
        if field == "is_active":
            checked = st.checkbox("Активна (участвует в расчёте)", value=bool(value) if current else True,
                                  key=f"{prefix}_a")
            values[field] = (1 if checked else 0) if spec.active_kind == "smallint" else checked
        elif field in ("valid_from", "valid_to"):
            use = st.checkbox(f"Задать «{field}»", value=value is not None, key=f"{prefix}_u_{field}")
            picked = st.date_input(field, value=value or date.today().replace(day=1), disabled=not use,
                                   key=f"{prefix}_d_{field}", format="YYYY-MM-DD")
            values[field] = picked if use else None
        else:
            values[field] = st.text_input(field, value=value or "", key=f"{prefix}_t_{field}")
    return values


def _table_editor(table: str, can_edit: bool) -> None:
    spec = editor.SPECS[table]
    st.caption(spec.hint)
    try:
        rows = editor.read_rows(table)
        total, active = editor.count_active(table)
    except DatabaseUnavailable as exc:
        st.error(str(exc))
        return
    search = st.text_input("Фильтр по подстроке", key=f"map_search_{table}")
    visible = rows
    if search.strip():
        needle = search.strip().lower()
        visible = [r for r in rows if any(needle in str(v).lower() for v in r.values())]
    frame = pd.DataFrame(visible)
    panel_table(spec.title, dataframe_html(frame, 500), frame if not frame.empty else None,
                f"Строк всего: {total}, активных: {active}")

    if not can_edit:
        st.info("Только просмотр: у учётной записи нет права записи или не задан URM_RW_USER.", icon="ℹ️")
        return

    key_field = spec.key
    if visible:
        labels = {
            r[key_field]: " | ".join(str(r.get(f, "")) for f in spec.fields[:4] if r.get(f) not in (None, ""))
            for r in visible
        }
        chosen = st.selectbox("Строка для изменения", list(labels),
                              format_func=lambda v: f"#{v}: {labels[v]}" if key_field == "row_id" else str(v),
                              key=f"map_pick_{table}")
        expected = next(r for r in visible if r[key_field] == chosen)
        with st.form(f"map_edit_{table}_{chosen}"):
            values = _form_fields(spec, expected, f"map_e_{table}_{chosen}")
            if key_field == "value":
                values.pop("value", None)  # ключ правила меняется через «добавить + выключить старое»
            submitted = st.form_submit_button("Сохранить в базу", type="primary")
        if submitted:
            changes = {f: v for f, v in values.items() if v != expected.get(f)}
            if not changes:
                st.info("Изменений нет.")
            else:
                try:
                    editor.update_row(table, expected, changes)
                except (editor.ValidationError, editor.StaleRowError, DatabaseUnavailable) as exc:
                    st.error(str(exc))
                else:
                    _after_write(f"«{spec.title}»: изменение сохранено в базе.")
        with st.expander("Удалить выбранную строку"):
            st.caption("Предпочтительнее выключить строку флагом «Активна»: истории версий в базе нет.")
            if st.button("Удалить", key=f"map_del_{table}_{chosen}"):
                try:
                    editor.delete_row(table, expected)
                except (editor.ValidationError, editor.StaleRowError, DatabaseUnavailable) as exc:
                    st.error(str(exc))
                else:
                    _after_write(f"«{spec.title}»: строка удалена.")

    with st.expander("Добавить строку"):
        with st.form(f"map_add_{table}"):
            values = _form_fields(spec, {}, f"map_n_{table}")
            created = st.form_submit_button("Добавить в базу", type="primary")
        if created:
            try:
                editor.insert_row(table, values)
            except (editor.ValidationError, DatabaseUnavailable) as exc:
                st.error(str(exc))
            else:
                _after_write(f"«{spec.title}»: строка добавлена.")


def mapping_page(user: User, r: dict, state: pd.Series) -> None:
    message = st.session_state.pop("map_message", None)
    if message:
        st.success(message)
    if not state.empty and bool(state.get("reference_changed")):
        html('<div class="notice">Справочники изменены после последней публикации — витрины и снимок '
             'этим изменениям ещё не соответствуют. Обновятся после ближайшего ночного расчёта.</div>')
    html('<div class="notice"><b>Правка сохраняется сразу и напрямую в базу.</b> Истории версий и автора '
         'правки в базе нет. Не редактируйте один справочник одновременно здесь и в штатном интерфейсе '
         'URM2: он сохраняет таблицу целиком и может затереть правку.</div>')

    d = r["data"]
    can_edit = user.mapping_edit
    html('<div class="kpis">' + "".join([
        kpi_card("Unmapped LIS", fmt_num(len(d["unmapped_lis"])), "кодов в срезе", icon="LIS"),
        kpi_card("Unmapped 1C", fmt_num(len(d["unmapped_onec"])), "позиций MDM в срезе", icon="1С"),
        kpi_card("Mapping conflicts", fmt_num(len(d["mapping_conflicts"])), "из опубликованной диагностики", icon="!"),
        kpi_card("Справочников", fmt_num(len(editor.SPECS)), "входных таблиц", icon="M"),
        kpi_card("Запись в базу", "включена" if can_edit else "выключена",
                 "роль и URM_RW_USER", icon="✎"),
    ]) + "</div>")

    names = list(editor.SPECS)
    tabs = st.tabs([editor.SPECS[n].title for n in names] + ["Проверки справочников", "Что завести"])
    for tab, name in zip(tabs, names):
        with tab:
            _table_editor(name, can_edit)

    with tabs[-2]:
        st.caption(
            "Проверки читают текущее состояние справочников и ничего не меняют. Они ловят то, чего нет "
            "в опубликованной диагностике: пересечения периодов по одному MDM, расхождения признаков "
            "дедупликации ЛИС, конфликты алиасов лабораторий, пустые и эквивалентные токены правил."
        )
        if st.button("Выполнить проверки", type="primary", key="map_checks"):
            try:
                st.session_state["map_checks"] = validate.run_all()
            except DatabaseUnavailable as exc:
                st.error(str(exc))
        results = st.session_state.get("map_checks")
        if results:
            summary = validate.summary(results)
            if ((summary["Важность"] == "Критично") & (summary["Найдено строк"] > 0)).any():
                html('<div class="notice"><b>Есть критичные находки:</b> такие строки способны размножать '
                     'количество в витринах. Разберите их до управленческих выводов.</div>')
            panel_table("Итог проверок", dataframe_html(summary), summary)
            for check, frame in results:
                if not frame.empty:
                    panel_table(f"{check.severity}: {check.title}", dataframe_html(frame, 300), frame, check.explanation)
        else:
            st.info("Нажмите «Выполнить проверки».")

    with tabs[-1]:
        st.caption("Рабочий список: коды с наибольшим объёмом, не попавшие в сверку. Часть позиций 1С — "
                   "не контейнеры (салфетки, иглы, держатели), их заводить не нужно.")
        ul = d["unmapped_lis"].sort_values("total_consumed", ascending=False) if not d["unmapped_lis"].empty else d["unmapped_lis"]
        uo = d["unmapped_onec"].sort_values("total_issued", ascending=False) if not d["unmapped_onec"].empty else d["unmapped_onec"]
        panel_table("Unmapped LIS — по объёму", dataframe_html(ul, 200), ul if not ul.empty else None)
        panel_table("Unmapped 1C MDM — по объёму", dataframe_html(uo, 200), uo if not uo.empty else None)
