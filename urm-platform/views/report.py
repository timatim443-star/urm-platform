"""Страницы 00–09 — перенос страниц генератора отчётов УРМ v4.

Каждая функция повторяет page_* из report_core: те же KPI, заголовки, подписи,
таблицы и графики в том же порядке. Отличия:
  * графики — живые Plotly внутри Streamlit, а не встроенный HTML;
  * на карточке заказчика и странице материалов объект можно выбрать
    (генератор брал заказчика с наибольшим риском и материал с наибольшей
    выдачей — они и остаются выбором по умолчанию).
"""
from __future__ import annotations

import re
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from urm import notices
from urm.charts import (
    chart_heatmap_lab_material,
    chart_monthly_lines,
    heatmap_legend_html,
    risk_donut,
    status_donut,
)
from urm.report_core import (
    CFG,
    add_plotly_defaults,
    anomalies_table_html,
    chart_anomaly_heatmap,
    chart_anomaly_trend,
    chart_anomaly_types,
    chart_city_monthly_heatmap,
    chart_city_monthly_lines,
    chart_customer_monthly,
    chart_dual_lab_bar,
    chart_mapping_coverage,
    chart_monthly_grouped,
    chart_procedure_materials,
    chart_procedure_monthly,
    chart_procedure_top,
    chart_quality_funnel,
    chart_quality_issues,
    chart_top_bar,
    dataframe_html,
    fmt_num,
    fmt_pct,
    kpi_card,
    kpis_overview,
    mapping_kpis,
    page_method,
)
from urm.style import compact, grid, html, panel_fig, panel_table
from urm.tables import customer_labels, signals_by_customer, signals_display, signals_html

# Страницы навигации (заполняет app.py) — для перехода между разделами.
PAGE_REFS: dict = {}

def _kpis(cards: list[str]) -> None:
    """Compact KPI labels; preserve full descriptions as native hover tooltips."""
    import re as _re
    from html import escape as _escape
    from html import unescape as _unescape

    def _fit(m):
        value = m.group(1)
        long_text = len(value) > 9 and not _re.fullmatch(r"[\d\s\u00a0,.%−+-]+", value)
        return f'<div class="kpi-value{" kpi-text" if long_text else ""}">{value}</div>'

    result = []
    for card in cards:
        card = _re.sub(r'<div class="kpi-value">(.*?)</div>', _fit, card)
        title = _re.search(r'<div class="kpi-title">(.*?)</div>', card, _re.S)
        sub = _re.search(r'<div class="kpi-sub">(.*?)</div>', card, _re.S)
        extra = _re.search(r'<div class="kpi-trend[^"]*">(.*?)</div>', card, _re.S)
        full = ' · '.join(_unescape(_re.sub(r'<[^>]+>', '', m.group(1))).strip()
                          for m in (title, sub, extra) if m and m.group(1).strip())
        # Period is essential context. Keep it visible even when secondary detail moves to hover.
        if sub:
            source = _unescape(_re.sub(r"<[^>]+>", "", sub.group(1))).strip()
            period = next((label for label in ("за последний месяц", "за выбранный период",
                                               "в текущем срезе", "за весь период", "на последнем месяце")
                           if label in source.lower()), None)
            if period:
                card = card.replace(sub.group(0), f'<div class="kpi-sub">{_escape(period)}</div>', 1)
            elif len(source) > 26:
                card = card.replace(sub.group(0), '<div class="kpi-sub kpi-sub-extra"></div>', 1)
        card = card.replace('<div class="kpi">',
                            f'<div class="kpi" title="{_escape(full, quote=True)}">', 1)
        result.append(card)
    html('<div class="kpis">' + ''.join(result) + '</div>')


def _display(df: pd.DataFrame) -> pd.DataFrame:
    """Числа для экрана: количества — целыми с разделителями, проценты — с одним знаком."""
    out = df.copy()
    for column in out.columns:
        series = out[column]
        if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        values = pd.to_numeric(series, errors="coerce")
        if "%" in str(column) or "pct" in str(column):
            out[column] = values.map(lambda v: fmt_num(v, 1) if pd.notna(v) else "")
        elif ((values.dropna() - values.dropna().round()).abs() < 1e-9).all():
            out[column] = values.map(lambda v: fmt_num(v) if pd.notna(v) else "")
        else:
            out[column] = values.map(lambda v: fmt_num(v, 2) if pd.notna(v) else "")
    return out


def _metric(title: str, df: pd.DataFrame, rename: dict[str, str], sort_by: str | None = None,
            ascending: bool = False, max_rows: int = 250, subtitle: str = "") -> None:
    """Таблица генератора (metric_table) + кнопка Excel со всеми строками."""
    full = df.copy()
    if sort_by and sort_by in full.columns:
        full = full.sort_values(sort_by, ascending=ascending)
    full = full[[c for c in rename if c in full.columns]].rename(columns=rename)
    panel_table(title, dataframe_html(_display(full), max_rows), full, subtitle)


def _frame(title: str, df: pd.DataFrame, max_rows: int | None = None, subtitle: str = "") -> None:
    """Таблица генератора (dataframe_html) + кнопка Excel со всеми строками."""
    has = df is not None and not df.empty
    panel_table(title, dataframe_html(_display(df) if has else df, max_rows), df if has else None, subtitle)


def _signals(title: str, anomalies: pd.DataFrame, max_rows: int, subtitle: str = "") -> None:
    """Сигналы, сведённые до заказчика (город × БИН). В Excel — и сводка, и разбивка по материалам."""
    grouped = signals_by_customer(anomalies)
    export = None
    if not grouped.empty:
        detail = anomalies.drop(columns=["lab_key"], errors="ignore").rename(columns={
            "severity": "Критичность", "Неотражено_ед": "Не подтверждено в ЛИС, ед.",
            "Неотражено_pct": "Не подтверждено в ЛИС, %"})
        export = {"По заказчикам": signals_display(grouped), "По материалам": detail}
    panel_table(title, signals_html(grouped, max_rows), export, subtitle)


def _all_months(value) -> str:
    """Все месяцы сохраняются: обрезается только видимая строка CSS, не сами данные."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " · ".join(str(part).strip() for part in re.split(r"[;,]", str(value)) if part.strip())


def _frame_compact(title: str, df: pd.DataFrame, max_rows: int | None = None, subtitle: str = "") -> None:
    """Экранная таблица компактнее (одна строка — одна строка), Excel — полный исходник."""
    has = df is not None and not df.empty
    if not has:
        panel_table(title, dataframe_html(df, max_rows), None, subtitle)
        return
    shown = _display(df.copy())
    if "Месяцы" in shown.columns:
        shown["Месяцы"] = df["Месяцы"].map(_all_months)
    panel_table(title, compact(dataframe_html(shown, max_rows)), df, subtitle)


# ------------------------------------------------------------------ 01

def overview(r: dict) -> None:
    s, anomalies = r["s"], r["anomalies"]
    monthly, labs, customers = s["monthly"], s["labs"], s["customers"]
    if len(labs) > 1 and not anomalies.empty:
        lab_problem = (
            anomalies[anomalies["severity"].isin(["Critical", "High"])]
            .groupby("Лаборатория").size().reset_index(name="signals")
        )
        right = chart_top_bar(lab_problem, "Лаборатория", "signals", top_n=10,
                              x_title="Количество критичных сигналов", y_title="Лаборатория / город",
                              max_label=28, height=385)
        right_title = "Лаборатории с наибольшим числом рисков"
    else:
        c = customers.copy()
        c["unaccounted"] = (c["issued"] - c["consumed"]).clip(lower=0)
        c["label"] = customer_labels(c)
        right = chart_top_bar(c, "label", "unaccounted", top_n=10,
                              x_title="Не подтверждено в ЛИС, ед.", y_title="Заказчик",
                              max_label=32, height=385)
        right_title = "Заказчики с наибольшим объемом, не подтвержденным в ЛИС"

    html(kpis_overview(monthly, anomalies))
    left, side = grid("grid-2")
    with left:
        panel_fig("Динамика выдачи, использования и расчетной дельты*", chart_monthly_lines(monthly),
                  pair=True, hint=notices.text("hint_monthly_lines"))
    with side:
        panel_fig(right_title, right, pair=True)
    panel_fig("Лаборатория × материал: зоны внимания", chart_heatmap_lab_material(r["lmc"]),
              footer=heatmap_legend_html(), hint=notices.text("hint_lab_material"))
    _signals("Что требует внимания", anomalies, 50,
             "Сигналы по заказчикам; детали по материалам доступны в Excel")


# ------------------------------------------------------------------ 02

def labs_page(r: dict) -> None:
    df, s, anomalies = r["fact"], r["s"], r["anomalies"]
    monthly, labs, customers = s["monthly"], s["labs"], s["customers"]
    problem_labs = (
        anomalies[anomalies["severity"].isin(["Critical", "High"])]["lab_key"].nunique()
        if not anomalies.empty else 0
    )
    total_gap = max(float(df["OneC_Issued"].sum() - df["LIS_Consumed"].sum()), 0.0)
    _kpis([
        kpi_card("Лабораторий / городов", fmt_num(labs["lab_key"].nunique()), "в текущем отчете", icon="L"),
        kpi_card("Выдано 1С", fmt_num(df["OneC_Issued"].sum()), "за выбранный период", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(df["LIS_Consumed"].sum()), "за выбранный период", icon="LIS"),
        kpi_card("Не подтверждено в ЛИС*", fmt_num(total_gap), "положительная часть 1С − ЛИС", icon="Δ"),
        kpi_card("Заказчиков", fmt_num(df["bin"].nunique()), "уникальных БИН", icon="C"),
        kpi_card("Проблемных лабораторий", fmt_num(problem_labs), "критично / очень критично", icon="!"),
    ])
    if len(labs) > 1:
        main_chart = chart_dual_lab_bar(labs)
        lab_problem = (
            anomalies[anomalies["severity"].isin(["Critical", "High"])]
            .groupby("Лаборатория").size().reset_index(name="signals")
            if not anomalies.empty else pd.DataFrame()
        )
        top_problem = chart_top_bar(lab_problem, "Лаборатория", "signals", top_n=10,
                                    x_title="Количество критичных сигналов", y_title="Лаборатория / город",
                                    max_label=28, height=385)
        table = (labs, {
            "lab_display_name": "Лаборатория / город", "issued": "Выдано 1С, ед.",
            "consumed": "Использовано ЛИС, ед.", "calc_balance": "Расчетная дельта*, ед.",
            "utilization_pct": "Доля использования, %", "customers": "Заказчиков", "containers": "Материалов",
        }, 100)
        title = "Выдано и использовано по лабораториям / городам"
        risk_lines = chart_city_monthly_lines(r["lmc"])
        risk_lines.update_layout(height=385, margin=dict(b=105))
        panel_fig("Риск по месяцам: лаборатории с наибольшим разрывом", risk_lines,
                  hint=notices.text("hint_city_risk_lines"))
        risk_heatmap = chart_city_monthly_heatmap(r["lmc"])
        risk_heatmap.update_layout(height=310, margin=dict(l=90, r=66, t=20, b=56))
        risk_heatmap.update_traces(colorbar=dict(len=1.0, thickness=16, y=.5, yanchor="middle"),
                                   selector=dict(type="heatmap"))
        panel_fig("Риск по месяцам: все лаборатории", risk_heatmap,
                  "Компактная тепловая карта по всем лабораториям и месяцам",
                  hint=notices.text("hint_city_risk_heatmap"))
    else:
        main_chart = chart_monthly_grouped(monthly)
        c = customers.copy()
        c["unaccounted"] = (c["issued"] - c["consumed"]).clip(lower=0)
        c["label"] = customer_labels(c)
        top_problem = chart_top_bar(c, "label", "unaccounted", top_n=10,
                                    x_title="Не подтверждено в ЛИС, ед.", y_title="Заказчик",
                                    max_label=32, height=385)
        table = (customers, {
            "bin": "БИН", "partner_name": "Заказчик", "issued": "Выдано 1С, ед.",
            "consumed": "Использовано ЛИС, ед.", "calc_balance": "Расчетная дельта*, ед.",
            "utilization_pct": "Доля использования, %", "risk": "Риск",
        }, CFG.html_table_rows)
        title = "Выдано и использовано по месяцам"

    left, side = grid("grid-2")
    with left:
        panel_fig(title, main_chart, pair=True)
    with side:
        panel_fig("Лаборатории / объекты с наибольшим числом критичных сигналов", top_problem, pair=True)
    frame, columns, rows = table
    _metric("Детальная таблица", frame, columns, sort_by="calc_balance", max_rows=rows)


# ------------------------------------------------------------------ 03

def _registry_risk_badges(body: str, ranks: list[float]) -> str:
    """Заменяет только последний td столбца «Риск» безопасным бейджем (все остальные td экранированы).

    Цвет / подпись берутся из RISK_LEVELS графика, без изменения экспортируемого DataFrame.
    """
    from urm.charts import RISK_LEVELS
    labels = {int(rank): (label, color) for rank, label, color in RISK_LEVELS}
    # severity_rank=1 график считает «Без сигнала»; таблица должна показывать то же самое.
    rows = iter(ranks)
    def row_badge(match):
        rank = int(next(rows, 0) or 0)
        label, _ = labels.get(rank if rank > 1 else 0, labels[0])
        hint = label
        if rank == 1:
            label = "Информация"
            hint = "Информация — в пончике включено в группу «Без сигнала»"
        css = {4: "critical", 3: "high", 2: "medium"}.get(rank, "neutral")
        badge = f'<span class="urm-risk-pill urm-risk-{css}" title="{escape(hint, quote=True)}">{escape(label)}</span>'
        return re.sub(r"<td(?:\s[^>]*)?>[^<>]*</td>(?=\s*</tr>)", f'<td>{badge}</td>', match.group(0),
                      count=1, flags=re.S)
    return re.sub(r"<tr>\s*<td.*?</tr>", row_badge, body, flags=re.S)


def customers_page(r: dict) -> None:
    s = r["s"]
    customers = s["customers"].copy()
    total = len(customers)
    active = int((customers["issued"] + customers["consumed"] > 0).sum())
    problem = int((customers["severity_rank"] >= 3).sum())
    matched = int((customers["status"] == "Matched").sum())
    mask = customers["issued"] > 0
    avg_util = (
        np.average(customers.loc[mask, "utilization_pct"].fillna(0), weights=customers.loc[mask, "issued"])
        if mask.any() else np.nan
    )
    critical_customers = int((customers["severity_rank"] >= 4).sum())
    customers["unaccounted"] = (customers["issued"] - customers["consumed"]).clip(lower=0)
    _kpis([
        kpi_card("Всего заказчиков", fmt_num(total), "БИН × лаборатория", icon="C"),
        kpi_card("Активных", fmt_num(active), "есть движение", icon="A"),
        kpi_card("Проблемных", fmt_num(problem), "критично / очень критично", icon="!"),
        kpi_card("Matched", fmt_num(matched), "на последнем месяце", icon="M"),
        kpi_card("Средняя доля использования", fmt_pct(avg_util), "взвешено по выдаче", icon="%"),
        kpi_card("Очень критичных", fmt_num(critical_customers), "первый приоритет проверки", icon="!!"),
    ])
    customers["label"] = customer_labels(customers)
    left, right = st.columns([1, 1.35], gap="small")
    with left:
        panel_fig("Заказчики по уровню риска", risk_donut(customers),
                  hint="Самый тяжёлый бизнес-сигнал заказчика в выбранном периоде. Цвета статусов совпадают с реестром.",
                  customer_panel="donut")
        panel_fig("Распределение по MatchStatus", status_donut(customers),
                  hint="Технический статус присутствия данных 1С и ЛИС; отличается от уровня бизнес-риска.",
                  customer_panel="donut")
    with right:
        _top_customers(customers)

    # Локальный поиск находится прямо в шапке реестра; KPI и графики он не фильтрует.
    from urm.tables import xlsx
    with st.container(key="urm_registry"):
        title_col, search_col, excel_col = st.columns([4.2, 3.4, 0.9], gap="small", vertical_alignment="center")
        with title_col:
            html('<div class="urm-registry-title">Реестр заказчиков</div>')
        with search_col:
            search = st.text_input("Поиск только в реестре", key="cust_search",
                                   label_visibility="collapsed", placeholder="Поиск в реестре: БИН или название",
                                   help="Фильтрует только строки таблицы, не KPI и не графики. Общий фильтр — сверху.")
        view = customers
        if search.strip():
            needle = search.strip().lower()
            view = view[
                view["bin"].astype(str).str.contains(needle, na=False, regex=False)
                | view["partner_name"].astype(str).str.lower().str.contains(needle, na=False, regex=False)
            ]
        registry = view.sort_values("severity_rank", ascending=False)
        rename = {
            "bin": "БИН", "partner_name": "Заказчик", "lab_display_name": "Лаборатория",
            "issued": "Выдано 1С, ед.", "consumed": "Использовано ЛИС, ед.",
            "unaccounted": "Не подтверждено в ЛИС, ед.", "calc_balance": "Расчетная дельта*, ед.",
            "utilization_pct": "Доля использования, %", "status": "MatchStatus", "risk": "Риск",
        }
        exported = registry[[c for c in rename if c in registry.columns]].rename(columns=rename)
        with excel_col:
            st.download_button("Excel", data=lambda frame=exported: xlsx({"Реестр заказчиков": frame}),
                               file_name="УРМ_Реестр_заказчиков.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               icon=":material/download:", type="secondary", key="xl_registry", width="stretch")
        html('<div class="urm-registry-context">Поиск действует только на реестр. '
             'Для изменения всех графиков используйте фильтр в шапке.</div>')
        body = dataframe_html(_display(exported), CFG.html_table_rows)
        html(_registry_risk_badges(body, registry["severity_rank"].tolist()))

TOP_LIMIT = 100   # «Топ заказчиков»: сколько всего
TOP_PAGE = 15     # и сколько на одной странице


def _top_customers(customers: pd.DataFrame) -> None:
    """Топ-100 заказчиков по объёму, не подтверждённому в ЛИС, по 15 на страницу."""
    top = customers[customers["unaccounted"] > 0].nlargest(TOP_LIMIT, "unaccounted")
    pages = max(1, -(-len(top) // TOP_PAGE))
    page = min(int(st.session_state.get("top_page", 0)), pages - 1)
    part = top.iloc[page * TOP_PAGE:(page + 1) * TOP_PAGE].copy()
    first = page * TOP_PAGE + 1
    part["label"] = [f"{n}. {lbl}" for n, lbl in zip(range(first, first + len(part)), part["label"])]
    # равные значения не должны перемешивать порядок мест: крошечная поправка по месту в списке
    part["unaccounted"] = part["unaccounted"].astype(float) - pd.Series(range(len(part)), index=part.index) * 1e-6
    # 15 строк по ~24px с запасом на ось: высота не зависит от высоты двух пончиков.
    height = 420 if len(part) >= 12 else max(300, 130 + 25 * len(part))
    fig = chart_top_bar(part, "label", "unaccounted", top_n=TOP_PAGE, x_title="Не подтверждено в ЛИС, ед.",
                        y_title="", max_label=34, height=height)
    # Подписи выводятся справа от бара. Небольшой резерв по X защищает от обрезания,
    # но больше не оставляет огромную пустую полосу справа.
    if not part.empty:
        right_limit = float(part["unaccounted"].max())
        if right_limit > 0:
            fig.update_xaxes(range=[0, right_limit * 1.16])
    fig.update_layout(
        margin=dict(l=204, r=34, t=8, b=48),
        font=dict(size=10.5),
    )
    fig.update_xaxes(tickfont=dict(size=10), title_font=dict(size=10.5))
    fig.update_yaxes(tickfont=dict(size=10))
    fig.update_traces(textfont=dict(size=10), width=0.68)


    def pager() -> None:
        if pages <= 1:
            return
        prev, info, nxt = st.columns([1, 2, 1], vertical_alignment="center")
        if prev.button("Назад", icon=":material/chevron_left:", disabled=page == 0, width="stretch", key="top_prev"):
            st.session_state["top_page"] = page - 1
            st.rerun()
        info.markdown(f"<div style='text-align:center;font-size:11.5px;color:#5d6b7e'>"
                      f"{page * TOP_PAGE + 1}–{page * TOP_PAGE + len(part)} из {len(top)}</div>", unsafe_allow_html=True)
        if nxt.button("Вперёд", icon=":material/chevron_right:", icon_position="right", disabled=page >= pages - 1,
                      width="stretch", key="top_next"):
            st.session_state["top_page"] = page + 1
            st.rerun()

    panel_fig("Топ заказчиков по объему, не подтвержденному в ЛИС", fig,
              controls=pager, customer_panel="top")


# ------------------------------------------------------------------ 04

def _customer_options(customers: pd.DataFrame) -> tuple[list[str], dict[str, str], pd.DataFrame]:
    """Заказчики для выпадающего поиска: по убыванию риска, как в HTML-отчёте."""
    work = customers.copy()
    work["_abs"] = work["calc_balance"].abs()
    ordered = work.sort_values(["severity_rank", "_abs", "issued"], ascending=[False, False, False])
    ordered = ordered.drop_duplicates(["lab_key", "bin"])  # один заказчик в городе — одна строка списка
    ids = (ordered["lab_key"].astype(str) + "|" + ordered["bin"].astype(str)).tolist()
    labels = {
        f"{row.lab_key}|{row.bin}": f"{row.bin} · {row.partner_name} · {row.lab_display_name}"
        for row in ordered.itertuples()
    }
    return ids, labels, ordered


def customer_card(r: dict) -> None:
    df, s, anomalies = r["fact"], r["s"], r["anomalies"]
    customers = s["customers"].copy()
    if customers.empty:
        html('<div class="empty">Нет заказчиков для карточки</div>')
        return
    ids, labels, ordered = _customer_options(customers)
    target = st.session_state.pop("_card_target", None)
    if target in labels:
        st.session_state["card_pick"] = target  # переход из раздела 03
    elif st.session_state.get("card_pick") not in labels:
        st.session_state.pop("card_pick", None)
    # Единый белый блок: подпись, значок «?» и выпадающий список не разнесены по разным секциям.
    with st.container(key="urmcardpick"):
        if len(ids) == 1:
            picked = ids[0]
            html('<div class="urm-pick-label">Заказчик для карточки '
                 '<span class="urm-hint" tabindex="0" aria-label="Выбран единственный доступный заказчик">?'
                 '<span class="urm-hint-popup" role="tooltip">В текущем срезе только один заказчик; '
                 'дополнительный выбор не нужен.</span></span></div>'
                 f'<div class="urm-pick-single" title="{escape(labels[picked], quote=True)}">'
                 f'{escape(labels[picked])}</div>')
        else:
            picked = st.selectbox("Заказчик для карточки", ids,
                                  format_func=lambda v: labels.get(v, v), key="card_pick",
                                  help="Выберите лабораторию и БИН. Общий фильтр в шапке ограничивает список.")
    lab_key, bin_ = picked.split("|", 1)
    top = ordered[(ordered["lab_key"] == lab_key) & (ordered["bin"] == bin_)].iloc[0]

    cust_df = df[(df["lab_key"] == top["lab_key"]) & (df["bin"] == top["bin"])].copy()
    cust_anom = (
        anomalies[(anomalies["lab_key"] == top["lab_key"]) & (anomalies["БИН"] == top["bin"])]
        if not anomalies.empty else pd.DataFrame()
    )
    materials = cust_df.groupby(["container_group", "container_display_name"], as_index=False).agg(
        issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    materials["calc_balance"] = materials["issued"] - materials["consumed"]
    materials["utilization_pct"] = np.where(materials["issued"] > 0,
                                            materials["consumed"] / materials["issued"] * 100, np.nan)
    cm = cust_df.groupby("month").agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"),
                                      containers=("container_group", "nunique")).reset_index().sort_values("month")
    cm["calc_balance"] = (cm["issued"] - cm["consumed"]).cumsum()
    cm["utilization_pct"] = np.where(cm["issued"] > 0, cm["consumed"] / cm["issued"] * 100, np.nan)

    # Одна компактная строка вместо высокой информационной карточки с шестью колонками.
    last_month = cust_df["month"].max().strftime("%Y-%m") if not cust_df.empty else "—"
    info_items = [
        ("БИН", top["bin"]), ("Лаборатория", top["lab_display_name"]),
        ("Статус", top.get("status", "—")),
        ("Риск", top.get("risk", "—") or "Низкий/нет сигнала"),
        ("Последний месяц", last_month),
    ]
    details = "".join(
        f'<span class="urm-customer-detail"><small>{escape(str(label))}:</small> '
        f'{escape(str(value))}</span>' for label, value in info_items)
    html(f'<div class="urm-customer-summary">{details}</div>')
    _kpis([
        kpi_card("Выдано 1С", fmt_num(top["issued"]), "за выбранный период", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(top["consumed"]), "за выбранный период", icon="LIS"),
        kpi_card("Расчетная дельта*", fmt_num(top["calc_balance"]), "накопительно", icon="Δ"),
        kpi_card("Доля использования", fmt_pct(top["utilization_pct"]), "ЛИС / 1С", icon="%"),
        kpi_card("Материалов", fmt_num(top["containers"]), "в истории", icon="M"),
        kpi_card("Аномалий", fmt_num(len(cust_anom)), "текущих сигналов", icon="!"),
    ])
    if not materials.empty:
        d = materials.copy()
        d["label"] = d["container_group"].astype(str)
        d = d.nlargest(10, "calc_balance").sort_values("calc_balance")
        bal_chart = go.Figure(go.Bar(
            x=d["calc_balance"], y=d["label"], orientation="h", customdata=d[["container_display_name"]],
            text=[fmt_num(v) for v in d["calc_balance"]], textposition="outside", cliponaxis=False,
            hovertemplate="%{customdata[0]}<br>Расчетная дельта*: %{x:,.0f} ед.<extra></extra>"))
        bal_chart = add_plotly_defaults(bal_chart, height=390, x_title="Расчетная дельта*, ед.",
                                        y_title="Код материала", left_margin=90, legend=False)
    else:
        bal_chart = add_plotly_defaults(go.Figure(), height=390)

    left, side = grid("grid-2")
    with left:
        panel_fig("Движение по месяцам", chart_customer_monthly(cust_df), pair=True)
    with side:
        panel_fig("Расчетная дельта* по материалам", bal_chart,
                  "На оси используются короткие коды; полное название — при наведении", pair=True)
    _metric("История по материалам", materials, {
        "container_group": "Код материала", "container_display_name": "Материал",
        "issued": "Выдано 1С, ед.", "consumed": "Использовано ЛИС, ед.",
        "calc_balance": "Расчетная дельта*, ед.", "utilization_pct": "Доля использования, %",
    }, sort_by="calc_balance", max_rows=100)
    _metric("История по месяцам", cm, {
        "month": "Месяц", "issued": "Выдано 1С, ед.", "consumed": "Использовано ЛИС, ед.",
        "calc_balance": "Накопительная расчетная дельта*, ед.",
        "utilization_pct": "Доля использования, %", "containers": "Материалов",
    }, sort_by="month", ascending=False, max_rows=100)
    if not cust_anom.empty:
        detail = cust_anom.drop(columns=["lab_key"], errors="ignore")
        panel_table("Аналитические сигналы по заказчику (по материалам)", compact(anomalies_table_html(cust_anom, 100)),
                    detail, "Нажмите на строку, чтобы раскрыть её целиком")


# ------------------------------------------------------------------ 05

def materials_page(r: dict) -> None:
    df, s, anomalies = r["fact"], r["s"], r["anomalies"]
    materials = s["materials"].copy()
    if materials.empty:
        html('<div class="empty">Нет данных по материалам</div>')
        return
    ordered = materials.sort_values("issued", ascending=False)
    names = dict(zip(ordered["container_group"], ordered["container_display_name"]))
    # Детальные графики ниже всегда показывают один материал: если общий фильтр уже сузил
    # выбор до одного — берём его, иначе автоматически берём самый выдаваемый материал текущего среза.
    cg = str(ordered.iloc[0]["container_group"])
    selected = ordered.iloc[0]
    md = df[df["container_group"] == cg].copy()
    mm = md.groupby("month").agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum")).reset_index().sort_values("month")
    mm["period_delta"] = mm["issued"] - mm["consumed"]
    mm["calc_balance"] = mm["period_delta"].cumsum()
    mm["utilization_pct"] = np.where(mm["issued"] > 0, mm["consumed"] / mm["issued"] * 100, np.nan)
    labs_mat = md.groupby("lab_display_name", as_index=False).agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    labs_mat["unaccounted"] = (labs_mat["issued"] - labs_mat["consumed"]).clip(lower=0)
    cust_mat = md.groupby(["bin", "partner_name"], as_index=False, observed=True).agg(
        issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    cust_mat["unaccounted"] = (cust_mat["issued"] - cust_mat["consumed"]).clip(lower=0)
    cust_mat["label"] = customer_labels(cust_mat)
    materials["unaccounted"] = (materials["issued"] - materials["consumed"]).clip(lower=0)
    problem_customers = (
        anomalies[(anomalies["Контейнер"].eq(selected["container_display_name"]))
                  & (anomalies["severity"].isin(["Critical", "High"]))]["БИН"].nunique()
        if not anomalies.empty else 0
    )
    _kpis([
        kpi_card("Выдано 1С", fmt_num(df["OneC_Issued"].sum()), "в текущем срезе", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(df["LIS_Consumed"].sum()), "в текущем срезе", icon="LIS"),
        kpi_card("Не подтверждено в ЛИС*", fmt_num(max(df["OneC_Issued"].sum() - df["LIS_Consumed"].sum(), 0)),
                 "положительная часть 1С − ЛИС", icon="Δ"),
        kpi_card("Материалов", fmt_num(materials["container_group"].nunique()), "типов", icon="M"),
        kpi_card("Лабораторий", fmt_num(df["lab_key"].nunique()), "в отчете", icon="L"),
        kpi_card("Проблемных заказчиков (материал)", fmt_num(problem_customers), f"по {cg}", icon="!"),
    ])
    html(f'<div class="urm-material-context" title="{escape(str(names.get(cg, cg)), quote=True)}">'
         f'<span class="urm-material-context-label">Детальные графики:</span>'
         f'<strong>{escape(cg)} — {escape(str(names.get(cg, cg)))}</strong>'
         '<span class="urm-hint" tabindex="0" aria-label="Как выбирается материал">?'
         '<span class="urm-hint-popup" role="tooltip">Автоматически выбран материал с наибольшей выдачей '
         'в текущем срезе. Для изменения среза используйте фильтр «Материал» сверху.</span></span>'
         '</div>')
    lab_chart = chart_top_bar(labs_mat, "lab_display_name", "unaccounted", top_n=10,
                              x_title="Не подтверждено в ЛИС, ед.", y_title="Лаборатория / город",
                              max_label=28, height=390)
    dmat = materials.nlargest(12, "issued").sort_values("issued").copy()
    material_chart = go.Figure(go.Bar(
        x=dmat["issued"], y=dmat["container_group"], orientation="h",
        customdata=np.stack([dmat["container_display_name"], dmat["consumed"], dmat["unaccounted"]], axis=-1),
        text=[fmt_num(v) for v in dmat["issued"]], textposition="outside", cliponaxis=False,
        hovertemplate=("<b>%{customdata[0]}</b><br>Код: %{y}<br>Выдано 1С: %{x:,.0f} ед.<br>"
                       "Использовано ЛИС: %{customdata[1]:,.0f} ед.<br>Не подтверждено в ЛИС: "
                       "%{customdata[2]:,.0f} ед.<extra></extra>")))
    material_chart = add_plotly_defaults(material_chart, height=410, x_title="Объем выдачи по 1С, ед.",
                                         y_title="Код материала", left_margin=80, legend=False)
    cust_chart = chart_top_bar(cust_mat, "label", "unaccounted", top_n=10,
                               x_title="Не подтверждено в ЛИС, ед.", y_title="Заказчик",
                               max_label=32, height=400)
    left, side = grid("grid-2")
    with left:
        panel_fig("Динамика выбранного материала", chart_monthly_lines(mm), pair=True,
                  hint=f"{names.get(cg, cg)}. {notices.text('hint_monthly_lines') or ''}")
    with side:
        panel_fig("Где по выбранному материалу больше всего не подтверждено в ЛИС", lab_chart, pair=True)
    a, b = grid("grid-2-eq")
    with a:
        panel_fig("Топ материалов по объему выдачи", material_chart, pair=True,
                  hint=notices.text("hint_material_top"))
    with b:
        panel_fig("Топ заказчиков по не подтвержденному в ЛИС объему", cust_chart, pair=True)
    _metric("Материалы", materials, {
        "container_group": "Код", "container_display_name": "Материал", "issued": "Выдано 1С, ед.",
        "consumed": "Использовано ЛИС, ед.", "unaccounted": "Не подтверждено в ЛИС, ед.",
        "calc_balance": "Расчетная дельта*, ед.", "utilization_pct": "Доля использования, %",
        "customers": "Заказчиков", "labs": "Лабораторий",
    }, sort_by="issued", max_rows=CFG.html_table_rows)


# ------------------------------------------------------------------ 06

def procedure_page(r: dict) -> None:
    detail, monthly, internal = r["proc_detail"], r["proc_monthly"], r["data"]["internal"]
    if detail.empty:
        html('<div class="empty">Нет данных по процедурным кабинетам</div>')
        return
    total_1c, total_lis = float(detail["1C"].sum()), float(detail["LIS"].sum())
    positive_gap = float((detail["1C"] - detail["LIS"]).clip(lower=0).sum())
    reverse_gap = float((detail["LIS"] - detail["1C"]).clip(lower=0).sum())
    rooms = detail["procedure_uid"].nunique()
    problematic = (
        detail.assign(gap=(detail["1C"] - detail["LIS"]).clip(lower=0))
        .groupby("procedure_uid")["gap"].sum().gt(0).sum()
    )
    _kpis([
        kpi_card("Выдано / списано 1С", fmt_num(total_1c), "процедурные кабинеты", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(total_lis), "процедурные кабинеты", icon="LIS"),
        kpi_card("Не подтверждено в ЛИС", fmt_num(positive_gap), "1С выше ЛИС — основной риск", icon="!"),
        kpi_card("Обратное расхождение", fmt_num(reverse_gap), "ЛИС выше 1С — контроль", icon="↔"),
        kpi_card("Процедурных кабинетов", fmt_num(rooms), "уникальных UID", icon="ПК"),
        kpi_card("Проблемных ПК", fmt_num(problematic), "есть положительный разрыв 1С − ЛИС", icon="!!"),
    ])
    dtable = detail.copy()
    dtable["Не подтверждено в ЛИС"] = (dtable["1C"] - dtable["LIS"]).clip(lower=0)
    dtable["Обратное расхождение"] = (dtable["LIS"] - dtable["1C"]).clip(lower=0)
    dtable = dtable.sort_values("Не подтверждено в ЛИС", ascending=False).rename(columns={
        "month": "Месяц", "lab_display_name": "Лаборатория", "procedure_uid": "UID ПК",
        "procedure_room_name": "Процедурный кабинет", "container_group": "Код материала",
        "container_display_name": "Материал"})
    first, second = grid("grid-2-eq")
    with first:
        panel_fig("1С vs ЛИС по месяцам", chart_procedure_monthly(monthly), pair=True)
    with second:
        panel_fig("Расхождение по материалам", chart_procedure_materials(detail), pair=True,
                  hint=notices.text("hint_procedure_materials"))
    # Сравнение кабинетов содержит до 12 длинных названий — оставляем всю ширину.
    panel_fig("Процедурные кабинеты: двустороннее расхождение 1С ↔ ЛИС", chart_procedure_top(detail),
              hint=notices.text("hint_procedure_gap"))
    _frame("Процедурные кабинеты — детальная таблица", dtable, CFG.html_table_rows,
           "Таблица расположена внизу на всю ширину")
    if internal is not None and not internal.empty:
        sort = ["month", "lab_display_name", "metric_sort"] if "metric_sort" in internal.columns else ["month"]
        idf = internal.copy().sort_values(sort).rename(columns={
            "month": "Месяц", "lab_display_name": "Лаборатория", "metric": "Метрика",
            "amount": "Количество", "rows": "Строк источника"})
        _frame("Внутреннее потребление 1С",
               idf[[c for c in ["Месяц", "Лаборатория", "Метрика", "Количество", "Строк источника"] if c in idf.columns]],
               120, "Отдельный поток; не суммируется с партнерскими фактами")


# ------------------------------------------------------------------ 07

def anomalies_page(r: dict) -> None:
    anomalies, exceptions = r["anomalies"], r["data"]["exceptions"]
    has = not anomalies.empty
    critical = int((anomalies["severity"] == "Critical").sum()) if has else 0
    high = int((anomalies["severity"] == "High").sum()) if has else 0
    medium = int((anomalies["severity"] == "Medium").sum()) if has else 0
    primary = int((anomalies["Направление"] == "1С > ЛИС").sum()) if has and "Направление" in anomalies.columns else 0
    reverse = int((anomalies["Направление"] == "ЛИС > 1С").sum()) if has and "Направление" in anomalies.columns else 0
    _kpis([
        kpi_card("Всего сигналов", fmt_num(len(anomalies)), "текущий снимок", icon="!"),
        kpi_card("Очень критично", fmt_num(critical), "первый приоритет", icon="!!"),
        kpi_card("Критично", fmt_num(high), "требует проверки", icon="H"),
        kpi_card("Внимание", fmt_num(medium), "контроль сверки", icon="M"),
        kpi_card("Главный риск 1С > ЛИС", fmt_num(primary), "выдали больше, чем отражено", icon="1С"),
        kpi_card("Обратные ЛИС > 1С", fmt_num(reverse), "аномалия меньшего приоритета", icon="LIS"),
    ])
    a, b = grid("grid-2-eq")
    with a:
        panel_fig("Аномалии по причинам", chart_anomaly_types(anomalies), pair=True)
    with b:
        panel_fig("Динамика аналитических сигналов", chart_anomaly_trend(r["signals"]), pair=True,
                  hint="Очень критично — первый приоритет; критично — второй. Внимание — обратные и прочие расхождения.")
    panel_fig("Лаборатория × причина риска", chart_anomaly_heatmap(anomalies),
              hint=notices.text("hint_anomaly_heatmap"))
    _signals("Реестр аномалий — сигналы по заказчикам", anomalies, CFG.html_table_rows,
             "Сводные сигналы по БИН; детализация по материалам — в Excel")
    exc = exceptions.copy()
    if not exc.empty:
        exc = exc.sort_values("amount", ascending=False).rename(columns={
            "month": "Месяц", "lab_display_name": "Лаборатория", "bin": "БИН",
            "container_display_name": "Материал", "side": "Сторона", "party_name": "Участник",
            "amount": "Количество"})
    _frame("Исходные исключения LIS / 1С — строки источников",
           exc[[c for c in ["Месяц", "Лаборатория", "БИН", "Материал", "Сторона", "Участник", "Количество"] if c in exc.columns]],
           100, "Данные из fact_exceptions, 100 строк с наибольшим количеством; все строки среза — кнопка Excel")


# ------------------------------------------------------------------ 08

def quality_page(r: dict, network: bool) -> None:
    d = r["data"]
    cov, qi, funnel = d["mapping_coverage"], d["quality_issues"], d["quality_funnel"]
    conflicts, ul, uo = d["mapping_conflicts"], d["unmapped_lis"], d["unmapped_onec"]
    ult = ul.rename(columns={
        "lis_container_code": "Код LIS", "lis_container_name_raw": "Материал LIS", "lab_display_name": "Лаборатория",
        "rows_count": "Строк", "total_consumed": "Использовано", "unique_bins": "БИН",
        "unique_hospitals": "Организаций", "months": "Месяцы"})
    uot = uo.rename(columns={
        "onec_mdm_key": "MDM 1C", "onec_nomenclature_name": "Номенклатура 1C", "onec_article": "Артикул",
        "lab_display_name": "Лаборатория", "onec_warehouse_group": "Складская группа", "rows_count": "Строк",
        "total_issued": "Выдано", "unique_bins": "БИН", "unique_counterparties": "Контрагентов", "months": "Месяцы"})
    notices.show("dq_snapshot")
    if network:
        html(mapping_kpis(cov, conflicts, qi))
        c1, c2, c3 = grid("grid-3")
        with c1:
            panel_fig("Quality funnel", chart_quality_funnel(funnel))
        with c2:
            panel_fig("Проблемы качества по типам", chart_quality_issues(qi))
        with c3:
            panel_fig("Покрытие маппинга", chart_mapping_coverage(cov))
    else:
        notices.show("dq_city_hidden")
    # Таблицы реестров идут друг за другом, каждая на полную ширину. На экране — компактнее,
    # чтобы длинные значения не раздували высоту строки; в Excel сохраняется полный исходник.
    _frame_compact("Unmapped LIS", ult, 35, "Длинные строки сокращены на экране; полный текст виден по наведению и в Excel")
    _frame_compact("Unmapped 1C MDM", uot, 35, "Нажмите на строку, чтобы увидеть все месяцы; полные значения также доступны в Excel")
    if network:
        _frame_compact("Quality issues", qi.rename(columns={
            "source": "Источник", "issue_type": "Тип проблемы", "severity": "Серьезность", "rows": "Строки",
            "quantity": "Количество", "details": "Детали"}), 200)
        _frame_compact("Конфликты маппинга", conflicts, 100)


# ------------------------------------------------------------------ 09

def watchlist_page(r: dict) -> None:
    work = r["watchlist"]
    if work.empty:
        html('<div class="empty">Нет объектов для оперативной отработки</div>')
        return
    critical_n = int((work["severity_rank"].fillna(0) >= 4).sum())
    high_n = int((work["severity_rank"].fillna(0) >= 3).sum())
    zero_lis = int(((work["last_issued"].fillna(0) > 0) & (work["last_consumed"].fillna(0) == 0)).sum())
    total_gap = float(work["last_gap"].fillna(0).sum())
    _kpis([
        kpi_card("К отработке сейчас", fmt_num(len(work)), "заказчик × лаборатория", icon="!"),
        kpi_card("Очень критично", fmt_num(critical_n), "нужно проверить в первую очередь", icon="!!"),
        kpi_card("Критично и выше", fmt_num(high_n), "включая очень критичных", icon="H"),
        kpi_card("Выдали, а ЛИС = 0", fmt_num(zero_lis), "самый опасный сценарий", icon="0"),
        kpi_card("Не подтверждено в ЛИС за последний месяц", fmt_num(total_gap), "суммарный месячный объем", icon="Δ"),
    ])
    html('<div class="urm-workflow-note">Очередь ежемесячной проверки · сначала 1С больше ЛИС'
         '<span class="urm-hint" tabindex="0" aria-label="Как использовать список">?'
         '<span class="urm-hint-popup" role="tooltip">Проверить документы, фактические возвраты '
         'и корректировки. «Критично и выше» включает «Очень критично»; это не отдельные группы '
         'для сложения.</span></span></div>')
    work = work.copy()
    work["label"] = customer_labels(work)
    chart_top = chart_top_bar(work, "label", "last_gap", top_n=15,
                              x_title="Не подтверждено в ЛИС за последний месяц, ед.", y_title="Заказчик",
                              max_label=34, height=345)
    city = work.groupby("lab_display_name", as_index=False).agg(last_gap=("last_gap", "sum"), customers=("bin", "nunique"))
    city_chart = chart_top_bar(city, "lab_display_name", "last_gap", top_n=12,
                               x_title="Не подтверждено в ЛИС за последний месяц, ед.",
                               y_title="Лаборатория / город", max_label=28, height=345)
    a, b = grid("grid-2-eq")
    with a:
        panel_fig("Топ заказчиков к отработке", chart_top, pair=True,
                  hint="Заказчики с самым большим объёмом, не подтверждённым ЛИС за последний месяц.")
    with b:
        panel_fig("Где сосредоточен риск по лабораториям / городам", city_chart, pair=True,
                  hint="Сумма риска по лабораториям за последний месяц для первой очереди проверки.")
    cols = ["bin", "partner_name", "lab_display_name", "priority_label", "last_issued", "last_consumed", "last_gap",
            "issued", "consumed", "unaccounted", "utilization_pct", "reasons", "why"]
    tbl = work[[c for c in cols if c in work.columns]].rename(columns={
        "bin": "БИН", "partner_name": "Заказчик", "lab_display_name": "Город",
        "priority_label": "Почему в списке",
        "last_issued": "Выдано 1С (посл. мес.)", "last_consumed": "Использовано ЛИС (посл. мес.)",
        "last_gap": "Не подтверждено (посл. мес.)",
        "issued": "Выдано 1С (период)", "consumed": "Использовано ЛИС (период)",
        "unaccounted": "Не подтверждено (период)", "utilization_pct": "Доля использования, %",
        "reasons": "Тип сигнала", "why": "Обоснование"})
    for column in tbl.columns:  # количества — целыми, доля — с одним знаком
        if column.endswith(")") and pd.api.types.is_numeric_dtype(tbl[column]):
            tbl[column] = tbl[column].round(0).astype("Int64")
    if "Доля использования, %" in tbl.columns:
        tbl["Доля использования, %"] = tbl["Доля использования, %"].round(1)
    shown = tbl.copy()  # на экране — с разделителями разрядов, в Excel — числа
    for column in shown.columns:
        if column.endswith(")") and str(shown[column].dtype) == "Int64":
            shown[column] = shown[column].map(lambda v: fmt_num(v) if pd.notna(v) else "")
    body = compact(dataframe_html(shown, CFG.html_table_rows)).replace('class="table-wrap"', 'class="table-wrap watchlist"', 1)
    panel_table("Реестр заказчиков для отработки", body, tbl,
                "Нажмите на строку, чтобы раскрыть её целиком. Таблицу можно тянуть мышью влево-вправо; "
                "все строки — кнопка Excel")


# ------------------------------------------------------------------ 10

def method_page() -> None:
    """Методика генератора без строк про источник, область и период."""
    body = page_method("", "", pd.DataFrame({"month": pd.to_datetime([])}))
    body = re.sub(r"\s*<p><b>Источник:</b>.*?</p><p><b>Доступный период:</b>.*?</p>", "", body, count=1, flags=re.S)
    html(body)
