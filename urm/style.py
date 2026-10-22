"""Оформление генератора отчётов внутри Streamlit.

CSS берётся из report_core.report_css() без изменений и ограничивается
областью .urm — так классы генератора (.kpi, .panel, .data-table, .notice …)
работают как в HTML-отчёте и не задевают разметку самого Streamlit.
Панели с графиками Plotly — это контейнеры Streamlit, оформленные так же,
как .panel генератора.
"""
from __future__ import annotations

from itertools import count

import pandas as pd  # noqa: F401 — для подсказки типа
import plotly.graph_objects as go
import streamlit as st

from urm.report_core import PLOTLY_CONFIG, report_css

SCOPE = ".urm"
_ids = count()


def _prefix(selectors: str) -> str:
    out = []
    for raw in selectors.split(","):
        sel = raw.strip()
        if not sel:
            continue
        if sel in (":root", "body", "html"):
            out.append(SCOPE)
        elif sel == "*":
            out.extend([SCOPE, f"{SCOPE} *"])
        else:
            out.append(f"{SCOPE} {sel}")
    return ",".join(dict.fromkeys(out))


def _scope_css(css: str) -> str:
    """Префиксует селекторы; блоки @media обрабатываются рекурсивно."""
    result, i, n = [], 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            break
        head = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[brace + 1 : j - 1]
        if head.startswith("@media"):
            result.append(f"{head}{{{_scope_css(body)}}}")
        elif head.startswith("@"):
            result.append(f"{head}{{{body}}}")
        else:
            result.append(f"{_prefix(head)}{{{body}}}")
        i = j
    return "".join(result)


STREAMLIT_CSS = """
/* служебный скрипт cookie входа (urm/auth.py) — без места на странице */
div[class*="st-key-urm_auth_cookie"]{display:none}
/* ---------- УРМ · оформление (КДЛ «Олимп») ----------
   Цвета: страница #F3F6F9, карточки #FFF, линии #DDE5EE, текст #13263D, вторичный #5D6B7E,
   фирменный синий #0088C8 — активные элементы, фирменный жёлтый #F8C000 — только «вы здесь». */
.stApp{background:#f3f6f9}
.stApp, .stApp button, .stApp input, .stApp label, .urm{font-family:'Segoe UI',Inter,Roboto,'Helvetica Neue',Arial,sans-serif}
/* Уплотняем только область основного содержимого. Не затрагиваем таблицы и sidebar. */
/* Fill the main pane: Streamlit's wide mode may otherwise reserve ~5rem either side. */
[data-testid="stMainBlockContainer"],.block-container{
  max-width:none !important;width:100% !important;
  padding:8px 16px 20px !important;margin:0 !important;
}
@media(max-width:1080px){[data-testid="stMainBlockContainer"],.block-container{padding:10px 12px 20px !important}}

[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"],
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"]{gap:.55rem}
/* У ключевых контейнеров убираем лишний внутренний Streamlit-gap. */
div[class*="st-key-urmfilters"] [data-testid="stVerticalBlock"]{gap:.35rem}
div[class*="st-key-urmpanel"] [data-testid="stVerticalBlock"]{gap:.35rem}
/* HTML генератора не должен приносить свой фон страницы — отсюда были серые полосы под заголовками */
.urm{background:transparent !important;color:#13263d}

/* боковая панель */
[data-testid="stSidebar"]{background:#f8fafc}
[data-testid="stSidebar"] [data-testid="stPageLink"] a{border-radius:8px;padding:5px 9px;border-left:3px solid transparent}
[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover{background:#eef4f9}
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"],
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"]{background:#e6f3fa;border-left:3px solid #f8c000}
[data-testid="stSidebar"] [data-testid="stPageLink"] p{font-size:12px;line-height:1.3;color:#2b3d55}
[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"] p{color:#0b5f8a;font-weight:600}

/* Шапка v10.2: реальный Streamlit popover и HTML-дата на ОДНОМ вертикальном уровне.
   st.markdown(html()) ранее добавлял обёртку .urm / Markdown — отдельный поток с
   собственным line-height. Дата теперь st.html, а центры задаются на каждом DOM-уровне. */
.urm .top{margin:0;min-height:64px;display:flex;align-items:center}
.urm .title{min-width:0;padding:0}
.urm .title h1{font-size:21px;font-weight:700;letter-spacing:-.01em;
  border-left:4px solid #f8c000;padding:3px 0 3px 12px;margin:0;
  color:#13263d;line-height:1.25}
.urm .title p{padding-left:16px;margin:5px 0 0;font-size:11.5px;line-height:1.4;
  color:#5d6b7e;overflow-wrap:anywhere}
div[class*="st-key-urmtop"] [data-testid="stHorizontalBlock"]{align-items:center!important;gap:.65rem}
div[class*="st-key-urmtop"] [data-testid="stColumn"]{min-width:0}
div[class*="st-key-urmtop"]{margin:0 0 10px;padding-bottom:6px;min-height:70px}

/* Нормализуем прямые дочерние колонки правого toolbar, не содержимое popover. */
div[class*="st-key-urm_toolbar_right"]{min-width:0;margin:0;padding:0;border:0}
div[class*="st-key-urm_toolbar_right"] > [data-testid="stVerticalBlock"]{gap:0!important}
div[class*="st-key-urm_toolbar_right"] [data-testid="stHorizontalBlock"]{
  gap:10px!important;align-items:center!important;min-height:40px;height:40px}
div[class*="st-key-urm_toolbar_right"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]{
  box-sizing:border-box;min-width:0;height:40px;min-height:40px;
  align-self:center!important;display:flex!important;flex-direction:column;
  align-items:stretch!important;justify-content:center!important;padding:0!important;margin:0!important}
div[class*="st-key-urm_toolbar_right"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"]{
  box-sizing:border-box;display:flex!important;flex-direction:column;
  justify-content:center!important;min-height:40px;height:40px;
  width:100%;gap:0!important;margin:0!important;padding:0!important}
div[class*="st-key-urm_toolbar_right"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]{
  box-sizing:border-box;min-height:40px;height:40px;
  display:flex!important;align-items:center!important;justify-content:stretch;
  margin:0!important;padding:0!important;min-width:0}
/* Streamlit st.html renders a separate stHtml root: it also occupies exactly 40 px. */
div[class*="st-key-urm_toolbar_right"] [data-testid="stHtml"]{
  width:100%;min-width:0;height:40px;min-height:40px;
  display:flex!important;align-items:center!important;justify-content:flex-end;
  margin:0!important;padding:0!important}
.urm-toolbar-updated{box-sizing:border-box;width:100%;height:40px;min-height:40px;
  display:flex!important;align-items:center!important;justify-content:flex-end;
  gap:9px;padding:0 12px 0 0;margin:0;border-right:1px solid #dce5ef;
  font-size:12px;line-height:16px;font-weight:400;white-space:nowrap;
  color:#5d6b7e;text-align:right}
.urm-toolbar-updated span,.urm-toolbar-updated strong{
  box-sizing:border-box;display:inline-block;line-height:16px;margin:0;padding:0;
  font-size:12px;vertical-align:middle}
.urm-toolbar-updated strong{color:#13263d;font-weight:650;font-variant-numeric:tabular-nums}
/* Поповер — реальная кнопка. Её центр совпадает с центром строки даты. */
div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"]{
  width:100%;min-width:0;height:40px;min-height:40px;
  display:flex!important;align-items:center!important;justify-content:center;
  margin:0!important;padding:0!important}
div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"] button{
  box-sizing:border-box;display:flex;align-items:center;justify-content:center;
  width:100%;height:40px;min-height:40px;margin:0;padding:0 8px;
  font-size:12px;line-height:16px;font-weight:500;white-space:nowrap;
  border:1px solid #dce5ef;background:#f1f7fb;color:#13263d;border-radius:9px}
div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"] button:hover{
  border-color:#a9c9df;background:#eaf4fa}
div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"] button p{
  font-size:12px;line-height:16px;margin:0!important;padding:0!important;white-space:nowrap}
@media(max-width:1280px){
  .urm .title h1{font-size:20px}
  .urm-toolbar-updated{gap:6px;padding-right:9px}
  .urm-toolbar-updated span,.urm-toolbar-updated strong,
  div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"] button,
  div[class*="st-key-urm_toolbar_right"] [data-testid="stPopover"] button p{font-size:11px}
}
/* The system header is transparent when sidebar is collapsed and does not cover report title. */
header[data-testid="stHeader"],header.stAppHeader{background:transparent!important;box-shadow:none!important;pointer-events:none}
header[data-testid="stHeader"] button,header.stAppHeader button,header a,header [data-testid="stSidebarCollapsedControl"]{pointer-events:auto}
.stApp:has([data-testid="stSidebarCollapsedControl"]) div[class*="st-key-urmtop"]{padding-left:36px}


/* v10 Registry: one panel with header search/export. Risk badges mirror donut levels. */
div[class*="st-key-urm_registry"]{background:#fff;border:1px solid #dde5ee;border-radius:12px;
  padding:12px 14px 11px;margin:5px 0 12px;min-width:0}
div[class*="st-key-urm_registry"] [data-testid="stHorizontalBlock"]{align-items:center;gap:.75rem}
div[class*="st-key-urm_registry"] [data-testid="stTextInput"] input{min-height:36px;font-size:12px}
.urm .urm-registry-title{font-size:14px;line-height:1.3;color:#13263d;font-weight:700}
.urm .urm-registry-context{font-size:11px;color:#5d6b7e;margin:2px 0 7px;line-height:1.35}
.urm .urm-risk-pill{display:inline-flex;align-items:center;justify-content:center;gap:6px;
  padding:3px 8px;font-size:11px;font-weight:700;line-height:1.25;border-radius:999px;
  white-space:nowrap;border:1px solid transparent}
.urm .urm-risk-pill::before{content:"";display:block;width:7px;height:7px;
  flex:0 0 7px;border-radius:50%;background:var(--risk-color)}
.urm .urm-risk-critical{--risk-color:#e5484d;background:#fde9eb;color:#a82532;border-color:#f4c7cc}
.urm .urm-risk-high{--risk-color:#f59e0b;background:#fff1dd;color:#a55a09;border-color:#f7d7a3}
.urm .urm-risk-medium{--risk-color:#facc15;background:#fff8cf;color:#7f6000;border-color:#e6d87e}
.urm .urm-risk-neutral{--risk-color:#b9c6d3;background:#eef2f6;color:#516174;border-color:#d8e0e8}
/* Compact context labels; full partner/material name available via native title. */
.urm .urm-customer-summary{gap:6px 13px;padding:7px 11px}
.urm .urm-material-context{display:flex;align-items:center;gap:8px;min-width:0;
  background:#f8fbfe;border:1px solid #dce7f2;border-radius:9px;
  padding:8px 11px;margin:4px 0 9px;line-height:1.3;white-space:nowrap}
.urm .urm-material-context-label{color:#5d6b7e;font-size:12px;flex:0 0 auto}
.urm .urm-material-context strong{font-size:12px;font-weight:650;min-width:0;
  text-overflow:ellipsis;overflow:hidden;white-space:nowrap;color:#13263d}
.urm .urm-material-context .urm-hint{flex:0 0 18px}
.urm .urm-workflow-note{display:flex;align-items:center;gap:5px;
  font-size:11.5px;color:#5d6b7e;margin:5px 0 10px;padding:0 4px}
@media(max-width:900px){.urm .urm-material-context{flex-wrap:wrap;white-space:normal}
  .urm .urm-material-context strong{white-space:normal;overflow-wrap:anywhere}}

/* 04 Карточка заказчика: единый выделенный блок для заголовка, «?» и селектора. */
div[class*="st-key-urmcardpick"]{background:#fff;border:1px solid #dde5ee;border-radius:12px;
  padding:13px 15px 12px;margin:5px 0 11px}
div[class*="st-key-urmcardpick"] [data-testid="stVerticalBlock"]{gap:.35rem}
div[class*="st-key-urmcardpick"] [data-testid="stWidgetLabel"]{margin-bottom:5px}
div[class*="st-key-urmcardpick"] [data-testid="stWidgetLabel"] p,
.urm .urm-pick-label{font-size:13px;font-weight:650;color:#35465a}
div[class*="st-key-urmcardpick"] [data-baseweb="select"] > div{border:1px solid #bfd3e5;
  border-radius:9px;background:#fff;min-height:41px}
div[class*="st-key-urmcardpick"] [data-baseweb="select"] > div:hover{border-color:#0088c8}
.urm .urm-pick-single{background:#f8fbfe;border:1px solid #bfd3e5;border-radius:9px;
  padding:10px 12px;font-size:13px;color:#13263d;overflow-wrap:anywhere}

/* фильтры */
div[class*="st-key-urmfilters"]{background:#fff;border:1px solid #dde5ee;border-radius:12px;padding:8px 12px 9px;
  margin:0;gap:.3rem}
div[class*="st-key-urmfilters"] [data-testid="stWidgetLabel"] p{font-size:11.5px;color:#5d6b7e;font-weight:600}
div[class*="st-key-urmfilters"] button[kind="secondary"]{min-height:35px}
div[class*="st-key-urmfilters"] button[kind="primary"]{min-height:35px}
div[class*="st-key-urmfilters"] button p{font-size:12px}
div[class*="st-key-urmfilters"] [data-baseweb="select"]{font-size:12.5px}
div[class*="st-key-urmfilters"] input{font-size:12.5px}

/* Всплывающее полное название для длинных пунктов списков (управляет JS ниже). */
#urm-option-tooltip{position:fixed;z-index:2147483647 !important;pointer-events:none;display:none;
  width:max-content;max-width:min(430px, calc(100vw - 32px));padding:10px 12px;
  background:#fff;border:1px solid #bdd2e4;border-radius:10px;
  box-shadow:0 8px 22px rgba(19,38,61,.19);color:#13263d;
  font:12.5px/1.48 'Segoe UI',Inter,Arial,sans-serif;white-space:normal;
  overflow-wrap:anywhere;word-break:normal;text-align:left}
/* карточки-панели: одинаковые поля, равная высота в одном ряду */
div[class*="st-key-urmpanel"]{background:#fff;border:1px solid #dde5ee !important;border-radius:12px;
  padding:9px 12px 10px;box-shadow:0 1px 2px rgba(19,38,61,.04);height:100%;gap:.25rem;overflow:hidden}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-urmpanel"]){align-items:stretch}
[data-testid="stColumn"]:has([class*="st-key-urmpanel"]) > [data-testid="stVerticalBlock"]{height:100%}
[data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(> [class*="st-key-urmpanel"]){
  flex:1 1 auto;display:flex;flex-direction:column}
[data-testid="stLayoutWrapper"] > [class*="st-key-urmpanel"]{flex:1 1 auto}
.urm .section-head{display:block;min-height:0;margin:0 0 8px}
.urm .section-head h2{font-size:14px;font-weight:650;margin:0;color:#13263d;line-height:1.35}
/* Все диаграммы одного ряда получают общую линию начала области Plotly:
   короткий заголовок слева не тянет график выше длинного справа. */
div[class*="st-key-urmpanelpair_"] .urm .section-head{box-sizing:border-box;min-height:68px;
  margin-bottom:5px;display:flex;flex-direction:column;justify-content:flex-start}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-urmpanelpair_"]){gap:.75rem !important;align-items:stretch}
div[class*="st-key-urmpanelpair_"] .urm .section-head:has(.section-sub){min-height:68px}
div[class*="st-key-urmpanelpair_"] .urm .section-head h2{line-height:1.32}

/* 03 Заказчики: три графика — компактные панели без изменения общих графиков и таблиц.
   У отдельной правой колонки под заголовком и графиком нет лишнего пустого пространства. */
div[class*="st-key-urmpanelcust_"]{padding:9px 11px 9px !important}
div[class*="st-key-urmpanelcust_"] .urm .section-head{margin:0 0 3px !important;min-height:0 !important}
div[class*="st-key-urmpanelcust_"] .urm .section-head h2{
  font-size:12.5px !important;font-weight:650;line-height:1.3 !important}
div[class*="st-key-urmpanelcust_"] [data-testid="stVerticalBlock"]{gap:.2rem !important}
div[class*="st-key-urmpanelcust_"] [data-testid="stPlotlyChart"]{margin:0 !important}
/* Правая колонка 03: пагинация ближе к рейтингу, но без наезда на ось. */
div[class*="st-key-urmpanelcust_top_"] [data-testid="stHorizontalBlock"]{align-items:center}
div[class*="st-key-urmpanelcust_top_"] [data-testid="stButton"] button{
  min-height:31px;padding:3px 9px;font-size:11.5px}

/* Подсказка «?» рядом с графиком: перенос текста, поддержка наведения и клавиатуры. */
.urm .urm-hint{display:inline-flex;align-items:center;justify-content:center;position:relative;vertical-align:middle;
  width:18px;height:18px;margin-left:5px;border-radius:50%;border:1px solid #bfcede;background:#f1f7fb;
  color:#57708a;font-size:11px;font-weight:700;cursor:help;outline-offset:2px}
.urm .urm-hint:hover,.urm .urm-hint:focus-visible{color:#0072ce;background:#e6f3fa;border-color:#83b7d8}
.urm .urm-hint-popup{visibility:hidden;opacity:0;pointer-events:none;position:absolute;z-index:500;
  top:calc(100% + 7px);left:0;width:max-content;max-width:min(360px, calc(100vw - 42px));min-width:205px;
  padding:10px 12px;border-radius:9px;border:1px solid #ccdbe8;background:#fff;color:#13263d;
  box-shadow:0 8px 22px rgba(19,38,61,.16);font-size:12px;font-weight:400;line-height:1.5;
  text-align:left;white-space:normal;overflow-wrap:anywhere;transition:opacity .12s}
.urm .urm-hint:hover .urm-hint-popup,.urm .urm-hint:focus-visible .urm-hint-popup{
  visibility:visible;opacity:1}
div[class*="st-key-urmpanel"]:has(.urm-hint:hover),div[class*="st-key-urmpanel"]:has(.urm-hint:focus-visible){
  position:relative;z-index:20;overflow:visible}
/* Карточка заказчика: сохранены все реквизиты, но без высокой инфо-таблицы. */
.urm .urm-customer-summary{display:flex;align-items:center;gap:6px 16px;flex-wrap:wrap;
  background:#fff;border:1px solid #dde5ee;border-radius:10px;padding:8px 12px;line-height:1.35;font-size:12px}
.urm .urm-customer-summary strong{font-size:13px;min-width:0;overflow-wrap:anywhere}
.urm .urm-customer-detail{white-space:nowrap;color:#13263d}
.urm .urm-customer-detail small{color:#5d6b7e;font-size:11px}
.urm .urm-inline-info{display:flex;flex-direction:column;align-items:flex-start;gap:3px;background:#f8fbff;
  border:1px solid #dfeaf2;border-radius:10px;padding:10px 13px;margin:3px 0 10px;line-height:1.4}
.urm .urm-material-label{font-size:12px;font-weight:650;color:#5d6b7e}
.urm .urm-material-value{font-size:13px;font-weight:600;color:#13263d;overflow-wrap:anywhere}
.urm .urm-material-note{font-size:12px;font-weight:400;color:#5d6b7e}
.urm .section-sub{text-align:left;font-size:10.5px;color:#5d6b7e;margin-top:4px;margin-bottom:3px;line-height:1.4}
.urm .panel{background:#fff;border:1px solid #dde5ee;border-radius:12px;padding:16px 18px 12px;margin:0 0 12px;box-shadow:none}

/* v10 KPI: uniform left-aligned hierarchy; never squeeze value and trend onto the same line. */
.urm .kpis{display:grid !important;grid-template-columns:repeat(6,minmax(0,1fr)) !important;
  grid-auto-flow:row !important;gap:10px;margin:8px 0 10px;align-items:stretch}
.urm .kpi{box-sizing:border-box;display:grid;grid-template-columns:34px minmax(0,1fr);
  grid-template-rows:minmax(31px,auto) auto minmax(14px,auto) auto;
  column-gap:9px;row-gap:3px;min-width:0;min-height:116px;
  align-content:start;align-items:center;padding:11px 12px 10px;
  border:1px solid #dde5ee;border-radius:11px;background:#fff;overflow:hidden}
.urm .kpi-body{display:contents}
.urm .kpi-icon{grid-column:1;grid-row:1;width:34px;height:34px;border-radius:8px;
  background:#e7f3fa;color:#0088c8;font-size:12px;font-weight:700;
  display:flex;align-items:center;justify-content:center}
.urm .kpi-title{grid-column:2;grid-row:1;font-size:12px;font-weight:650;line-height:1.27;
  color:#3b4a5e;min-width:0;overflow-wrap:anywhere}
.urm .kpi-value{grid-column:1/-1;grid-row:2;font-size:clamp(19px,1.45vw,25px);font-weight:750;
  letter-spacing:-.012em;font-variant-numeric:tabular-nums;line-height:1.12;
  color:#13263d;margin:3px 0 0;white-space:nowrap;min-width:0}
.urm .kpi-value.kpi-text{white-space:normal;font-size:14px;line-height:1.25}
.urm .kpi-sub{grid-column:1/-1;grid-row:3;font-size:10.5px;color:#5d6b7e;line-height:1.25;
  margin:1px 0 0;white-space:normal;overflow-wrap:anywhere;min-height:0}
.urm .kpi-sub-extra{display:none}
.urm .kpi-trend{grid-column:1/-1;grid-row:4;justify-self:start;align-self:start;
  font-size:11px;font-weight:700;margin-top:1px;line-height:1.3;white-space:nowrap}
.urm .kpis:has(> .kpi:nth-child(5):last-child){grid-template-columns:repeat(5,minmax(0,1fr)) !important}
@media(max-width:1320px){
  .urm .kpis{grid-template-columns:repeat(3,minmax(0,1fr)) !important}
  .urm .kpis:has(> .kpi:nth-child(5):last-child){grid-template-columns:repeat(3,minmax(0,1fr)) !important}
  .urm .kpi-value{font-size:23px}
}
@media(max-width:750px){
  .urm .kpis,.urm .kpis:has(> .kpi:nth-child(5):last-child){grid-template-columns:repeat(2,minmax(0,1fr)) !important}
  .urm .kpi-value{font-size:21px}
}

/* пояснения — спокойнее, без «тревожной» заливки */
.urm .notice{background:#fffbeb;border:1px solid #f3e4b5;border-left:4px solid #f8c000;color:#5b4708;
  border-radius:8px;padding:7px 10px;font-size:11.5px;margin:0 0 8px;line-height:1.4}

/* таблицы: отступ от заголовка, свой контур, читаемая шапка, прокрутка внутри рамки, тянуть мышью */
.urm .table-wrap{max-height:620px;overflow:auto;cursor:grab;border:1px solid #e3e9f0;border-radius:10px;margin:8px 0 6px}
.urm .table-wrap.urm-dragging{cursor:grabbing;user-select:none}
.urm .data-table{font-size:12px}
.urm .data-table th{background:#f1f5f9;color:#3b4a5e;font-weight:600;font-size:11.5px;padding:10px 10px 9px;
  border-bottom:1px solid #dde5ee;white-space:normal;min-width:84px;max-width:210px;vertical-align:bottom;line-height:1.3}
.urm .data-table td{padding:8px 10px;min-width:96px;max-width:360px;border-bottom:1px solid #eef2f6;line-height:1.4;
  overflow-wrap:break-word;word-break:normal;hyphens:manual}
.urm .data-table tr:nth-child(even){background:#fafcfe}
.urm .data-table tr:hover{background:#f0f7fc}
.urm .data-table td.nowrap{white-space:nowrap}
/* компактные строки: одна линия, полный текст — по нажатию на строку или при наведении на ячейку */
.urm .data-table.compact td{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
.urm .data-table.compact td.wide{max-width:300px}
.urm .data-table.compact tbody tr{cursor:pointer}
.urm .data-table.compact tbody tr.open td{white-space:normal;overflow:visible;text-overflow:clip}
.urm .data-table.compact tbody tr.open{background:#eaf5fb}
.urm .data-table.compact tbody tr.open td:first-child{box-shadow:inset 3px 0 0 #0088c8}
.urm .data-table td.wide{min-width:190px}
.urm .data-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.urm .table-note{display:block;font-size:11.5px;color:#5d6b7e;padding:2px 2px 6px;margin:0;overflow-wrap:anywhere}
.urm .badge{font-size:11px;padding:3px 9px}
/* «К отработке»: разумная ширина колонок, длинные тексты переносятся */
.urm .table-wrap.watchlist td{min-width:96px;max-width:150px}
.urm .table-wrap.watchlist td:nth-child(1){min-width:112px;white-space:nowrap}
.urm .table-wrap.watchlist td:nth-child(2){min-width:200px;max-width:280px}
.urm .table-wrap.watchlist td:nth-child(3){min-width:110px}
.urm .table-wrap.watchlist td:nth-child(4){min-width:190px;max-width:240px}
.urm .table-wrap.watchlist td:nth-child(12){min-width:170px;max-width:240px}
.urm .table-wrap.watchlist td:nth-child(13){min-width:320px;max-width:420px}
.urm .table-wrap.watchlist td:nth-child(n+5):nth-child(-n+11){text-align:right;font-variant-numeric:tabular-nums}
div[class*="st-key-urmpanel"] [data-testid="stDownloadButton"] button{font-size:12.5px;color:#0077b8;padding:0 10px;min-height:32px;background:#f4fbff;border:1px solid #b9d8ea;border-radius:8px;
  white-space:nowrap;border-radius:8px}
div[class*="st-key-urmpanel"] [data-testid="stDownloadButton"] button:hover{background:#e9f5fb;border-color:#8fc0db}
div[class*="st-key-urmpanel"] [data-testid="stDownloadButton"] button p{white-space:nowrap}
/* колонка с кнопкой «Excel» — по ширине кнопки, заголовок забирает остальное: в узких блоках кнопка не обрезается */
div[class*="st-key-urmpanel"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stDownloadButton"]){
  flex-wrap:nowrap;gap:.5rem}
div[class*="st-key-urmpanel"] [data-testid="stColumn"]:has([data-testid="stDownloadButton"]){
  flex:0 0 auto !important;width:auto !important;min-width:fit-content !important}
div[class*="st-key-urmpanel"] [data-testid="stColumn"]:has(.section-head){flex:1 1 0 !important;min-width:0 !important}
/* Маппинг: стрелки прокрутки вкладок должны выглядеть как самостоятельные кнопки. */
[data-testid="stTabs"] [data-testid="stTabsScrollButton"],
[data-testid="stTabs"] button[aria-label*="Scroll" i],
[data-testid="stTabs"] button[aria-label*="Прокрут" i],
[data-testid="stTabs"] [data-baseweb="tab-list"] > button:not([role="tab"]){
  background:#e2f2fb !important;border:1px solid #8bbfdf !important;border-radius:8px !important;
  color:#006ca6 !important;box-shadow:0 1px 4px rgba(0,80,130,.16) !important;
  min-width:34px !important;min-height:34px !important;opacity:1 !important;
  display:inline-flex !important;align-items:center !important;justify-content:center !important}
[data-testid="stTabs"] [data-testid="stTabsScrollButton"]:hover,
[data-testid="stTabs"] button[aria-label*="Scroll" i]:hover,
[data-testid="stTabs"] [data-baseweb="tab-list"] > button:not([role="tab"]):hover{
  background:#cde8f9 !important;border-color:#0088c8 !important}

/* кнопки графика: светлая мягкая плашка вместо тёмно-серой */
.stPlotlyChart .modebar-container .modebar-group{background:rgba(241,245,249,.92) !important;border:1px solid #e3e9f0;
  border-radius:8px;padding:2px 4px !important;box-shadow:0 1px 2px rgba(19,38,61,.05)}
.stPlotlyChart .modebar-btn{border-radius:6px}
.stPlotlyChart .modebar-btn:hover{background:#e6f3fa}
.urm .data-table td.guid{white-space:nowrap;font-family:Consolas,'Cascadia Mono',monospace;font-size:11px;color:#3b4a5e}
.urm .info-strip{border:1px solid #dde5ee;border-radius:12px;padding:14px}
.urm .footer{font-size:11px;color:#8391a3;margin:20px 2px 4px}
.urm .empty{padding:36px 12px;color:#8391a3;font-size:13px;text-align:center}
"""

# Таблицу можно тянуть мышью влево-вправо и вверх-вниз (как карту), не ища полосу прокрутки.
DRAG_JS = """
<script>
(function(){
  if (window.__urmDrag) return; window.__urmDrag = true;
  let s = null;
  document.addEventListener('mousedown', function(e){
    const w = e.target.closest && e.target.closest('.table-wrap');
    if (!w || e.button !== 0) return;
    s = {w: w, x: e.clientX, y: e.clientY, l: w.scrollLeft, t: w.scrollTop, moved: false};
  }, true);
  document.addEventListener('mousemove', function(e){
    if (!s) return;
    const dx = e.clientX - s.x, dy = e.clientY - s.y;
    if (!s.moved && Math.abs(dx) + Math.abs(dy) < 6) return;
    s.moved = true; s.w.classList.add('urm-dragging');
    s.w.scrollLeft = s.l - dx; s.w.scrollTop = s.t - dy; e.preventDefault();
  }, true);
  document.addEventListener('mouseup', function(){
    if (s) {
      s.w.classList.remove('urm-dragging');
      if (s.moved) { window.__urmDragged = true; setTimeout(function(){ window.__urmDragged = false; }, 0); }
    }
    s = null;
  }, true);
  // Компактные таблицы: строка в одну линию, по нажатию раскрывается целиком (и сворачивается обратно).
  document.addEventListener('click', function(e){
    if (window.__urmDragged) return;
    const row = e.target.closest && e.target.closest('table.compact tbody tr');
    if (row) row.classList.toggle('open');
  }, true);
})();
// Native select/multiselect lists cut off long names. Show a small wrapping popup on hover.
(function(){
  if (window.__urmOptionTooltip) return; window.__urmOptionTooltip = true;
  const tip = document.createElement('div'); tip.id = 'urm-option-tooltip';
  tip.setAttribute('role', 'tooltip'); document.body.appendChild(tip);
  let current = null, waiting = null;
  function optionOf(target) {
    if (!(target instanceof Element)) return null;
    const el = target.closest('[role="option"], [data-baseweb="menu"] li, .urm-material-name');
    // Do not attach to unrelated buttons or the actual multiselect widget.
    return el && (el.closest('[data-baseweb="menu"], [role="listbox"], [data-baseweb="popover"]')
        || el.getAttribute('role') === 'option' || el.matches('.urm-material-name')) ? el : null;
  }
  function hide() {
    if (waiting) { clearTimeout(waiting); waiting = null; }
    current = null; tip.style.display = 'none';
  }
  function position(el) {
    if (tip.style.display !== 'block' || !el) return;
    const width = tip.offsetWidth, height = tip.offsetHeight;
    const pop = el.closest('[data-baseweb="popover"], [role="listbox"], [data-baseweb="menu"]') || el;
    const bounds = pop.getBoundingClientRect(), line = el.getBoundingClientRect();
    // Prefer the free area beside the list (not below the hovered option).
    let x = bounds.right + 12;
    if (x + width > innerWidth - 12) x = bounds.left - width - 12;
    if (x < 12) x = Math.min(innerWidth - width - 12, Math.max(12, line.right - width));
    const y = Math.max(12, Math.min(line.top, innerHeight - height - 12));
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  document.addEventListener('mouseover', function(e){
    const el = optionOf(e.target);
    if (el === current) return;
    hide(); if (!el) return;
    const name = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (name.length < 28 && el.scrollWidth <= el.clientWidth + 3) return;
    current = el;
    waiting = setTimeout(function(){
      if (current !== el || !el.isConnected) return;
      tip.textContent = name; tip.style.display = 'block';
      document.body.appendChild(tip); // top-level fixed layer above Streamlit portal
      position(el); waiting = null;
    }, 300);
  }, true);
  document.addEventListener('mousemove', function(){
    if (current && tip.style.display === 'block') position(current);
  }, true);
  document.addEventListener('mouseout', function(e){
    if (current && e.target instanceof Node && current.contains(e.target)
        && (!e.relatedTarget || !current.contains(e.relatedTarget))) hide();
  }, true);
  document.addEventListener('scroll', hide, true);
  document.addEventListener('mousedown', hide, true);
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') hide(); }, true);
})();
</script>
"""


def inject() -> None:
    st.markdown(
        f"<style>{_scope_css(report_css())}{STREAMLIT_CSS}</style>",
        unsafe_allow_html=True,
    )
    st.html(DRAG_JS, unsafe_allow_javascript=True)


def html(markup: str) -> None:
    """Готовый HTML генератора: KPI, таблицы, секции, примечания."""
    st.markdown(f'<div class="urm">{markup}</div>', unsafe_allow_html=True)


def _head(title: str, subtitle: str = "", hint: str | None = None) -> str:
    from html import escape

    sub = f'<div class="section-sub">{escape(subtitle)}</div>' if subtitle else ""
    help_icon = (f'<span class="urm-hint" tabindex="0" aria-label="Подсказка: {escape(hint, quote=True)}">'
                 f'?<span class="urm-hint-popup" role="tooltip">{escape(hint)}</span></span>') if hint else ""
    return f'<div class="section-head"><h2>{escape(title)}{help_icon}</h2>{sub}</div>'


def _russian_dates(fig: go.Figure) -> go.Figure:
    # В браузере Streamlit нет русской локали Plotly: «%b» дал бы англ. месяцы.
    for axis in fig.layout:
        if axis.startswith("xaxis") and getattr(fig.layout[axis], "tickformat", None) == "%b %Y":
            fig.layout[axis].tickformat = "%m.%Y"
    # Тепловые карты по месяцам: подписи «2025-01» Plotly сам превращает в даты и пишет «Jan 2025».
    import re

    for trace in fig.data:
        xs = getattr(trace, "x", None)
        if trace.type == "heatmap" and xs is not None and len(xs) and all(
                isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}", v) for v in xs):
            fig.update_xaxes(tickformat="%m.%Y")
    return fig


PAIR_HEIGHT = 350  # парные графики компактнее; тепловые карты сохраняют собственную высоту


def panel_fig(title: str, fig: go.Figure, subtitle: str = "", footer: str = "", pair: bool = False,
              controls=None, hint: str | None = None, customer_panel: str = "") -> None:
    """Секция генератора с графиком внутри. footer — HTML под графиком (например, легенда).
    pair=True — график стоит в ряду с другим: высота одинаковая, чтобы карточки не «гуляли»."""
    if pair:
        fig.update_layout(height=PAIR_HEIGHT)
    # Кнопки графика (снимок, на весь экран): без тёмной подложки темы, значки светлые, синие при наведении.
    fig.update_layout(modebar=dict(bgcolor="rgba(0,0,0,0)", color="#a3b1c2", activecolor="#0088c8"))
    def _size(trace) -> int:
        for name in ("x", "values", "z", "y"):
            value = getattr(trace, name, None)
            if value is not None:
                try:
                    return len(value)
                except TypeError:
                    return 1
        return 0
    empty = not any(_size(t) for t in fig.data)
    panel_key = (f"urmpanelcust_{customer_panel}_{next(_ids)}" if customer_panel
                 else f"urmpanel{'pair' if pair else ''}_{next(_ids)}")
    with st.container(key=panel_key):
        html(_head(title, subtitle, hint))
        if empty:  # вместо пустых осей — понятное сообщение
            html('<div class="empty">Нет данных для выбранного среза</div>')
        else:
            st.plotly_chart(_russian_dates(fig), width="stretch", config=PLOTLY_CONFIG)
        if footer:
            html(footer)
        if controls is not None:  # кнопки под графиком внутри той же карточки (например, листание)
            controls()


def panel_html(title: str, body: str, subtitle: str = "") -> None:
    from urm.report_core import section

    html(section(title, body, subtitle))


def panel_table(title: str, body: str, export: "pd.DataFrame | dict | None" = None, subtitle: str = "") -> None:
    """Секция с таблицей и маленькой кнопкой «Excel» справа в заголовке.

    export — что выгружать: таблица целиком (а не только показанные строки)
    или {имя листа: таблица}. Книга собирается только при нажатии кнопки.
    """
    import re

    from urm.tables import xlsx

    with st.container(key=f"urmpanel_{next(_ids)}"):
        if export is None:
            html(_head(title, subtitle))
        else:
            head, button = st.columns([10.5, 1.8], gap="small", vertical_alignment="center")
            with head:
                html(_head(title, subtitle))
            sheets = export if isinstance(export, dict) else {title: export}
            name = re.sub(r"[^\w\- ]+", "", title).strip().replace(" ", "_")[:60] or "таблица"
            button.download_button(
                "Excel", data=lambda: xlsx(sheets), file_name=f"УРМ_{name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"xl_{next(_ids)}", type="tertiary", icon=":material/download:",
                help="Скачать таблицу целиком в Excel", width="content")
        html(_numeric_cells(body))


def _numeric_cells(table_html: str) -> str:
    """Ячейки-числа — по правому краю (так суммы читаются столбиком)."""
    import re

    out = re.sub(r"<td>(-?[\d\u00a0 ]+(?:,\d+)?%?)</td>", r'<td class="num">\1</td>', table_html)
    # GUID (код MDM 1С) — одной строкой, а не столбиком по 8 символов
    return re.sub(r"<td>([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})</td>",
                  r'<td class="guid">\1</td>', out)


def compact(table_html: str) -> str:
    """Таблица генератора → компактная: строка в одну линию, раскрывается по нажатию."""
    import html as _h
    import re

    # pandas пишет class="dataframe data-table", генератор — class="data-table"
    out = re.sub(r'class="([^"]*\bdata-table\b[^"]*)"', lambda m: f'class="{m.group(1)} compact"', table_html, count=1)
    # полный текст ячейки — во всплывающей подсказке (title), чтобы прочитать, не раскрывая строку
    def _title(match):
        inner = match.group(2)
        text = _h.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if len(text) < 28:
            return match.group(0)
        return f'<td{match.group(1)} title="{_h.escape(text, quote=True)}">{inner}</td>'
    return re.sub(r"<td([^>]*)>(.*?)</td>", _title, out, flags=re.S)


def reset_ids() -> None:
    global _ids
    _ids = count()


GRIDS = {
    "grid-2": [2, 1],
    "grid-2-eq": [1, 1],
    "grid-3": [1, 1, 1],
    "grid-wide-side": [3, 1],
}


def grid(kind: str):
    return st.columns(GRIDS[kind], gap="small")


