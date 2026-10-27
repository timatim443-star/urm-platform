from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs


# ============================================================
# URM HTML REPORT GENERATOR v4
# Готовые витрины IT -> автономные HTML + Excel
# ============================================================

VERSION = "4.0"


@dataclass
class AnalyticsConfig:
    # Аналитические пороги. После бизнес-валидации меняются здесь.
    critical_utilization_pct: float = 20.0
    high_utilization_pct: float = 50.0
    critical_stock_months: float = 6.0
    high_stock_months: float = 3.0
    reverse_ratio_high_pct: float = 150.0
    html_table_rows: int = 250
    excel_max_detail_rows: int = 900_000
    top_n: int = 12
    max_partner_label: int = 34
    max_material_label: int = 28


CFG = AnalyticsConfig()

REQUIRED_FILES = {
    "01_Основные_витрины_CSV": [
        "fact_month_container_bin.csv",
        "fact_exceptions.csv",
        "fact_procedure_room_container_month_metric.csv",
        "fact_internal_consumption_lab_month.csv",
    ],
    "02_Справочники_CSV": [
        "bin_parties.csv",
        "dim_container.csv",
        "dim_laboratory.csv",
        "match_status_filter.csv",
    ],
    "03_Контроль_качества_CSV": [
        "mapping_conflicts.csv",
        "mapping_coverage_summary.csv",
        "quality_funnel.csv",
        "quality_issues.csv",
        "unmapped_lis_details.csv",
        "unmapped_onec_details.csv",
    ],
}

SEVERITY_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "": 0}
SEVERITY_RU = {
    "Critical": "Очень критично",
    "High": "Критично",
    "Medium": "Внимание",
    "Low": "Информация",
    "": "—",
}


def severity_ru(value: object) -> str:
    return SEVERITY_RU.get(str(value or ""), str(value or "—"))


# ============================================================
# БАЗОВЫЕ УТИЛИТЫ
# ============================================================

def fmt_num(v, decimals: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    try:
        n = float(v)
    except Exception:
        return str(v)
    if decimals == 0:
        return f"{n:,.0f}".replace(",", " ")
    return f"{n:,.{decimals}f}".replace(",", " ").replace(".", ",")


def fmt_pct(v, decimals: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{float(v):.{decimals}f}%".replace(".", ",")


def fmt_change(current, previous, pct: bool = False) -> tuple[str, str]:
    if previous is None or pd.isna(previous):
        return "", "neutral"
    try:
        c = float(current)
        p = float(previous)
    except Exception:
        return "", "neutral"
    if pct:
        delta = c - p
        sign = "+" if delta > 0 else ""
        return f"{sign}{delta:.1f} п.п.".replace(".", ","), "up" if delta > 0 else "down" if delta < 0 else "neutral"
    if p == 0:
        return "", "neutral"
    delta = (c - p) / abs(p) * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%".replace(".", ","), "up" if delta > 0 else "down" if delta < 0 else "neutral"


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", str(name))
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name[:120] or "Без_названия"


def short_label(value: object, max_chars: int = 42) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(s) <= max_chars:
        return s
    return textwrap.shorten(s, width=max_chars, placeholder="…")


def wrap_label(value: object, width: int = 28, max_lines: int = 2) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    lines = textwrap.wrap(s, width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = short_label(lines[-1], width - 1)
    return "<br>".join(lines)


def read_csv(path: Path, dtype: dict | None = None) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False, dtype=dtype)


def numericize(df: pd.DataFrame, columns: Iterable[str]) -> None:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def normalize_month(df: pd.DataFrame, col: str = "month") -> None:
    if col in df.columns:
        raw = df[col].astype("string").fillna("")
        df[col] = pd.to_datetime(raw.where(raw.str.len() != 7, raw + "-01"), errors="coerce")


def uniq_text(values: pd.Series) -> str:
    vals: list[str] = []
    for v in values.dropna().astype(str):
        v = re.sub(r"\s+", " ", v).strip()
        if v and v not in vals:
            vals.append(v)
    return " | ".join(vals[:6])


def choose_partner_name(row: pd.Series) -> str:
    lis = str(row.get("LIS_Hospitals", "") or "").strip()
    onec = str(row.get("OneC_Counterparties", "") or "").strip()
    if lis and onec:
        if lis.casefold() == onec.casefold():
            return onec
        # Для отображения сначала 1C, затем LIS. Полное значение остается в hover/таблице.
        return f"1C: {onec} | LIS: {lis}"
    return onec or lis or f"БИН {row.get('bin', '')}"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================
# PLOTLY: РУССКИЕ ПОДСКАЗКИ + ЕДИНЫЙ СТИЛЬ
# ============================================================

PLOTLY_RU_LOCALE_JS = r'''
Plotly.register({
  moduleType: 'locale',
  name: 'ru',
  dictionary: {
    'Autoscale': 'Автомасштаб',
    'Box Select': 'Выделение рамкой',
    'Click to enter Colorscale title': 'Нажмите, чтобы задать название цветовой шкалы',
    'Click to enter Component A title': 'Нажмите, чтобы задать название компонента A',
    'Click to enter Component B title': 'Нажмите, чтобы задать название компонента B',
    'Click to enter Component C title': 'Нажмите, чтобы задать название компонента C',
    'Click to enter Plot title': 'Нажмите, чтобы задать название графика',
    'Click to enter X axis title': 'Нажмите, чтобы задать название оси X',
    'Click to enter Y axis title': 'Нажмите, чтобы задать название оси Y',
    'Compare data on hover': 'Сравнивать данные при наведении',
    'Double-click on legend to isolate one trace': 'Двойной щелчок по легенде — оставить только один ряд',
    'Double-click to zoom back out': 'Двойной щелчок — вернуться к исходному масштабу',
    'Double-click on plot to zoom back out': 'Двойной щелчок по графику — вернуть исходный масштаб',
    'Click and drag to zoom': 'Зажмите и протяните, чтобы увеличить область',
    'Click and drag to pan': 'Зажмите и протяните, чтобы переместить график',
    'Download plot as a png': 'Сохранить график как PNG',
    'Edit in Chart Studio': 'Редактировать в Chart Studio',
    'Lasso Select': 'Выделение лассо',
    'Orbital rotation': 'Орбитальное вращение',
    'Pan': 'Перемещение',
    'Produced with Plotly.js': 'Создано с Plotly.js',
    'Reset': 'Сбросить',
    'Reset axes': 'Сбросить оси',
    'Reset camera to default': 'Сбросить камеру',
    'Reset camera to last save': 'Вернуть предыдущую камеру',
    'Reset view': 'Сбросить вид',
    'Reset views': 'Сбросить виды',
    'Show closest data on hover': 'Показывать ближайшие данные при наведении',
    'Snapshot succeeded': 'Изображение сохранено',
    'Toggle Spike Lines': 'Показать/скрыть направляющие',
    'Toggle show closest data on hover': 'Показать/скрыть ближайшие данные',
    'Zoom': 'Масштабирование',
    'Zoom in': 'Увеличить',
    'Zoom out': 'Уменьшить'
  },
  format: {
    days: ['Воскресенье','Понедельник','Вторник','Среда','Четверг','Пятница','Суббота'],
    shortDays: ['Вс','Пн','Вт','Ср','Чт','Пт','Сб'],
    months: ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'],
    shortMonths: ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'],
    date: '%d.%m.%Y'
  }
});
'''

PLOTLY_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "locale": "ru",
    "showTips": True,
    "scrollZoom": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    "toImageButtonOptions": {"format": "png", "filename": "URM_chart", "scale": 2},
}


def add_plotly_defaults(
    fig: go.Figure,
    *,
    height: int = 360,
    x_title: str = "",
    y_title: str = "",
    left_margin: int = 68,
    bottom_margin: int = 58,
    hovermode: str | bool = "closest",
    legend: bool = True,
) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=left_margin, r=25, t=22, b=bottom_margin),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(family="Arial, 'Segoe UI', sans-serif", size=12, color="#172b4d"),
        hovermode=hovermode,
        separators=", ",
        showlegend=legend,
    )
    if legend:
        fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.16, x=0, font=dict(size=11)))
    fig.update_xaxes(
        title_text=x_title,
        showgrid=False,
        linecolor="#dfe7f1",
        tickfont=dict(size=11),
        title_font=dict(size=12),
        automargin=True,
    )
    fig.update_yaxes(
        title_text=y_title,
        gridcolor="#edf2f7",
        zerolinecolor="#cbd5e1",
        tickfont=dict(size=11),
        title_font=dict(size=12),
        automargin=True,
    )
    return fig


def fig_html(fig: go.Figure) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=False, config=PLOTLY_CONFIG)


# ============================================================
# ЗАГРУЗКА ВИТРИН
# ============================================================

def validate_structure(root: Path) -> list[str]:
    missing: list[str] = []
    for folder, files in REQUIRED_FILES.items():
        for file in files:
            p = root / folder / file
            if not p.exists():
                missing.append(str(p))
    return missing


def load_data(root: Path) -> dict[str, pd.DataFrame]:
    base = root / "01_Основные_витрины_CSV"
    dims = root / "02_Справочники_CSV"
    dq = root / "03_Контроль_качества_CSV"

    main = read_csv(
        base / "fact_month_container_bin.csv",
        dtype={"month": "string", "lab_key": "string", "container_group": "string", "bin": "string", "MatchStatus": "string"},
    )
    normalize_month(main)
    numericize(main, [
        "LIS_Consumed", "LIS_UniqueCustomers", "OneC_Issued",
        "OneC_UniqueCounterparties", "Delta_IssuedMinusConsumed",
        "Coverage_Issued_to_Consumed",
    ])
    main["bin"] = main["bin"].fillna("").astype(str).str.strip()

    parties = read_csv(
        dims / "bin_parties.csv",
        dtype={"month": "string", "lab_key": "string", "container_group": "string", "bin": "string"},
    )
    normalize_month(parties)
    parties["bin"] = parties["bin"].fillna("").astype(str).str.strip()

    party_map = (
        parties.groupby(["month", "lab_key", "container_group", "bin"], dropna=False)
        .agg({"LIS_Hospitals": uniq_text, "OneC_Counterparties": uniq_text})
        .reset_index()
    )
    party_map["partner_name"] = party_map.apply(choose_partner_name, axis=1)
    main = main.merge(
        party_map[["month", "lab_key", "container_group", "bin", "partner_name"]],
        on=["month", "lab_key", "container_group", "bin"],
        how="left",
    )
    main["partner_name"] = main["partner_name"].fillna(main["bin"].map(lambda x: f"БИН {x}" if x else "Без БИН"))

    lab = read_csv(dims / "dim_laboratory.csv", dtype={"lab_key": "string"})
    containers = read_csv(dims / "dim_container.csv", dtype={"container_group": "string"})
    statuses = read_csv(dims / "match_status_filter.csv", dtype={"MatchStatus": "string"})

    exceptions = read_csv(
        base / "fact_exceptions.csv",
        dtype={"month": "string", "lab_key": "string", "bin": "string", "container_group": "string", "side": "string"},
    )
    normalize_month(exceptions)
    numericize(exceptions, ["amount"])

    procedure = read_csv(
        base / "fact_procedure_room_container_month_metric.csv",
        dtype={"month": "string", "lab_key": "string", "procedure_uid": "string", "container_group": "string", "MatchStatus": "string", "metric": "string"},
    )
    normalize_month(procedure)
    numericize(procedure, ["metric_sort", "amount"])

    internal = read_csv(
        base / "fact_internal_consumption_lab_month.csv",
        dtype={"month": "string", "lab_key": "string", "metric": "string"},
    )
    normalize_month(internal)
    numericize(internal, ["metric_sort", "amount", "rows"])

    quality_issues = read_csv(dq / "quality_issues.csv")
    numericize(quality_issues, ["rows", "quantity"])
    mapping_conflicts = read_csv(dq / "mapping_conflicts.csv")
    numericize(mapping_conflicts, ["group_count"])
    mapping_coverage = read_csv(dq / "mapping_coverage_summary.csv")
    numericize(mapping_coverage, ["rows", "quantity", "unique_codes"])
    quality_funnel = read_csv(dq / "quality_funnel.csv")
    numericize(quality_funnel, ["rows"])
    unmapped_lis = read_csv(dq / "unmapped_lis_details.csv")
    numericize(unmapped_lis, ["rows_count", "total_consumed", "unique_bins", "unique_hospitals"])
    unmapped_onec = read_csv(dq / "unmapped_onec_details.csv")
    numericize(unmapped_onec, ["rows_count", "total_issued", "unique_bins", "unique_counterparties"])

    return {
        "main": main,
        "parties": parties,
        "lab": lab,
        "containers": containers,
        "statuses": statuses,
        "exceptions": exceptions,
        "procedure": procedure,
        "internal": internal,
        "quality_issues": quality_issues,
        "mapping_conflicts": mapping_conflicts,
        "mapping_coverage": mapping_coverage,
        "quality_funnel": quality_funnel,
        "unmapped_lis": unmapped_lis,
        "unmapped_onec": unmapped_onec,
    }


# ============================================================
# КАНОНИЧЕСКИЕ РАСЧЕТЫ
# ============================================================

def enrich_fact(main: pd.DataFrame) -> pd.DataFrame:
    df = main.copy()
    df["LIS_Consumed"] = df["LIS_Consumed"].fillna(0.0)
    df["OneC_Issued"] = df["OneC_Issued"].fillna(0.0)
    df["period_delta"] = df["OneC_Issued"] - df["LIS_Consumed"]
    df["utilization_pct"] = np.where(df["OneC_Issued"] > 0, df["LIS_Consumed"] / df["OneC_Issued"] * 100.0, np.nan)
    df = df.sort_values(["lab_key", "bin", "container_group", "month"])
    df["calc_balance"] = df.groupby(["lab_key", "bin", "container_group"], dropna=False)["period_delta"].cumsum()
    return df


def snapshot_by_grain(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    latest = df["month"].max()
    hist = df[df["month"] <= latest].copy()
    totals = (
        hist.groupby(["lab_key", "lab_display_name", "bin", "partner_name", "container_group", "container_display_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
        .reset_index()
    )
    totals["calc_balance"] = totals["issued"] - totals["consumed"]
    totals["utilization_pct"] = np.where(totals["issued"] > 0, totals["consumed"] / totals["issued"] * 100.0, np.nan)

    recent_months = sorted(hist["month"].dropna().unique())[-3:]
    recent = hist[hist["month"].isin(recent_months)]
    avg3 = (
        recent.groupby(["lab_key", "bin", "container_group"], dropna=False)["LIS_Consumed"]
        .sum().div(max(len(recent_months), 1))
        .rename("avg_consumed_3m").reset_index()
    )
    totals = totals.merge(avg3, on=["lab_key", "bin", "container_group"], how="left")
    totals["months_stock"] = np.where(totals["avg_consumed_3m"] > 0, totals["calc_balance"] / totals["avg_consumed_3m"], np.nan)
    return totals


def build_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Один основной сигнал на связку лаборатория × БИН × материал."""
    if df.empty:
        return pd.DataFrame()

    latest = df["month"].max()
    latest_df = df[df["month"] == latest].copy()
    snap = snapshot_by_grain(df)

    latest_metrics = (
        latest_df.groupby(["lab_key", "bin", "container_group"], dropna=False)
        .agg(
            latest_issued=("OneC_Issued", "sum"),
            latest_consumed=("LIS_Consumed", "sum"),
            match_status=("MatchStatus", lambda x: " | ".join(sorted(set(x.dropna().astype(str)))))
        ).reset_index()
    )
    snap = snap.merge(latest_metrics, on=["lab_key", "bin", "container_group"], how="left")
    snap[["latest_issued", "latest_consumed"]] = snap[["latest_issued", "latest_consumed"]].fillna(0.0)
    snap["match_status"] = snap["match_status"].fillna("")
    snap["latest_utilization_pct"] = np.where(
        snap["latest_issued"] > 0,
        snap["latest_consumed"] / snap["latest_issued"] * 100.0,
        np.nan,
    )
    snap["latest_unaccounted"] = (snap["latest_issued"] - snap["latest_consumed"]).clip(lower=0)
    snap["latest_unaccounted_pct"] = np.where(
        snap["latest_issued"] > 0,
        snap["latest_unaccounted"] / snap["latest_issued"] * 100.0,
        np.nan,
    )
    snap["reverse_gap"] = (snap["latest_consumed"] - snap["latest_issued"]).clip(lower=0)
    snap["reverse_ratio_pct"] = np.where(
        snap["latest_issued"] > 0,
        snap["latest_consumed"] / snap["latest_issued"] * 100.0,
        np.where(snap["latest_consumed"] > 0, np.inf, np.nan),
    )

    rows: list[dict] = []

    def emit(r: pd.Series, severity: str, anomaly_type: str, value_text: str, direction: str, reason: str):
        rows.append({
            "severity": severity,
            "lab_key": r["lab_key"],
            "Лаборатория": r["lab_display_name"],
            "БИН": r["bin"],
            "Заказчик": r["partner_name"],
            "Контейнер": r["container_display_name"],
            "Тип аномалии": anomaly_type,
            "Значение": value_text,
            "Период": latest.strftime("%Y-%m") if pd.notna(latest) else "",
            "Расчетная дельта": r["calc_balance"],
            "Неотражено_ед": r["latest_unaccounted"],
            "Неотражено_pct": r["latest_unaccounted_pct"],
            "Направление": direction,
            "Почему": reason,
        })

    for _, r in snap.iterrows():
        issued = float(r["latest_issued"] or 0)
        consumed = float(r["latest_consumed"] or 0)
        util = r["latest_utilization_pct"]
        months_stock = r["months_stock"]
        status = str(r["match_status"] or "")

        if issued > 0 and consumed == 0:
            emit(r, "Critical", "Выдали, но в ЛИС 0",
                 f"{fmt_num(issued)} ед. выдано; 0 в ЛИС", "1С > ЛИС",
                 "Есть выдача по 1С, но в последнем месяце ЛИС не показывает использования. Первый приоритет проверки.")
            continue

        if issued > 0 and pd.notna(util) and util <= CFG.critical_utilization_pct:
            emit(r, "Critical", "Очень низкое использование после выдачи",
                 f"Использовано {fmt_pct(util)}; не отражено {fmt_num(r['latest_unaccounted'])} ед.", "1С > ЛИС",
                 f"После выдачи в ЛИС отражено не более {CFG.critical_utilization_pct:.0f}% объема.")
            continue

        if issued > 0 and pd.notna(util) and util <= CFG.high_utilization_pct:
            emit(r, "High", "Низкое использование после выдачи",
                 f"Использовано {fmt_pct(util)}; не отражено {fmt_num(r['latest_unaccounted'])} ед.", "1С > ЛИС",
                 "Существенная часть выданного объема не подтверждена использованием в ЛИС.")
            continue

        if pd.notna(months_stock) and r["calc_balance"] > 0 and months_stock >= CFG.critical_stock_months:
            emit(r, "Critical", "Очень большой накопленный неиспользованный объем",
                 f"{float(months_stock):.1f} мес. среднего потребления".replace(".", ","), "1С > ЛИС",
                 f"Расчетная дельта эквивалентна как минимум {CFG.critical_stock_months:.0f} месяцам среднего потребления.")
            continue

        if pd.notna(months_stock) and r["calc_balance"] > 0 and months_stock >= CFG.high_stock_months:
            emit(r, "High", "Большой накопленный неиспользованный объем",
                 f"{float(months_stock):.1f} мес. среднего потребления".replace(".", ","), "1С > ЛИС",
                 f"Расчетная дельта эквивалентна как минимум {CFG.high_stock_months:.0f} месяцам среднего потребления.")
            continue

        if "1C_only" in status and issued > 0:
            emit(r, "High", "1C_only: выдача без стороны ЛИС", f"{fmt_num(issued)} ед.", "1С > ЛИС",
                 "В текущем срезе есть сторона 1С, но отсутствует сторона ЛИС.")
            continue

        if consumed > 0 and issued == 0:
            emit(r, "Medium", "ЛИС есть, выдачи 1С нет", f"{fmt_num(consumed)} ед. в ЛИС", "ЛИС > 1С",
                 "Использование отражено в ЛИС, но соответствующей выдачи 1С нет. Это аномалия сверки, но приоритет ниже риска «выдали и не использовали».")
            continue

        if r["reverse_gap"] > 0 and (not np.isfinite(r["reverse_ratio_pct"]) or r["reverse_ratio_pct"] >= CFG.reverse_ratio_high_pct):
            emit(r, "Medium", "ЛИС заметно выше 1С", f"ЛИС больше на {fmt_num(r['reverse_gap'])} ед.", "ЛИС > 1С",
                 "Обратное расхождение. Требует сверки, но не считается главным риском невозврата.")

    cols = ["severity", "lab_key", "Лаборатория", "БИН", "Заказчик", "Контейнер", "Тип аномалии", "Значение", "Период", "Расчетная дельта", "Неотражено_ед", "Неотражено_pct", "Направление", "Почему"]
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=cols)
    result["severity_rank"] = result["severity"].map(SEVERITY_RANK).fillna(0)
    result = result.sort_values(["severity_rank", "Неотражено_ед", "Расчетная дельта"], ascending=[False, False, False]).drop(columns="severity_rank").reset_index(drop=True)
    return result


def build_monthly_anomaly_signals(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["month", "severity", "count"])
    x = (
        df.groupby(["month", "lab_key", "bin", "container_group"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), status=("MatchStatus", lambda s: " | ".join(sorted(set(s.dropna().astype(str))))))
        .reset_index()
    )
    x["util"] = np.where(x["issued"] > 0, x["consumed"] / x["issued"] * 100.0, np.nan)
    def classify(r):
        issued, consumed, util = float(r["issued"] or 0), float(r["consumed"] or 0), r["util"]
        if issued > 0 and consumed == 0: return "Critical"
        if issued > 0 and pd.notna(util) and util <= CFG.critical_utilization_pct: return "Critical"
        if issued > 0 and pd.notna(util) and util <= CFG.high_utilization_pct: return "High"
        if "1C_only" in str(r["status"] or "") and issued > 0: return "High"
        if consumed > 0 and issued == 0: return "Medium"
        if issued > 0 and consumed / issued * 100 >= CFG.reverse_ratio_high_pct: return "Medium"
        return ""
    x["severity"] = x.apply(classify, axis=1)
    x = x[x["severity"] != ""]
    if x.empty:
        return pd.DataFrame(columns=["month", "severity", "count"])
    return x.groupby(["month", "severity"], as_index=False).size().rename(columns={"size":"count"})



# ============================================================
# СВОДНЫЕ ВИТРИНЫ ДЛЯ ОТЧЕТА
# ============================================================

def customer_status_latest(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["lab_key", "bin", "status"])
    latest = df["month"].max()
    d = df[df["month"] == latest].copy()

    def resolve(series: pd.Series) -> str:
        vals = set(series.dropna().astype(str))
        has_lis = "LIS_only" in vals
        has_1c = "1C_only" in vals
        if has_lis and has_1c:
            return "Mixed"
        if has_lis:
            return "LIS_only"
        if has_1c:
            return "1C_only"
        if "Matched" in vals:
            return "Matched"
        return next(iter(vals), "—")

    return d.groupby(["lab_key", "bin"], dropna=False)["MatchStatus"].apply(resolve).rename("status").reset_index()


def summaries(df: pd.DataFrame, anomalies: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    monthly = (
        df.groupby("month", dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
        .reset_index().sort_values("month")
    )
    monthly["period_delta"] = monthly["issued"] - monthly["consumed"]
    monthly["calc_balance"] = monthly["period_delta"].cumsum()
    monthly["utilization_pct"] = np.where(monthly["issued"] > 0, monthly["consumed"] / monthly["issued"] * 100.0, np.nan)

    labs = (
        df.groupby(["lab_key", "lab_display_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), customers=("bin", "nunique"), containers=("container_group", "nunique"))
        .reset_index()
    )
    labs["calc_balance"] = labs["issued"] - labs["consumed"]
    labs["utilization_pct"] = np.where(labs["issued"] > 0, labs["consumed"] / labs["issued"] * 100.0, np.nan)

    customers = (
        df.groupby(["lab_key", "lab_display_name", "bin", "partner_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), containers=("container_group", "nunique"))
        .reset_index()
    )
    customers["calc_balance"] = customers["issued"] - customers["consumed"]
    customers["utilization_pct"] = np.where(customers["issued"] > 0, customers["consumed"] / customers["issued"] * 100.0, np.nan)
    customers = customers.merge(customer_status_latest(df), on=["lab_key", "bin"], how="left")

    if anomalies is not None and not anomalies.empty:
        a = anomalies.copy()
        a["severity_rank"] = a["severity"].map(SEVERITY_RANK).fillna(0)
        ar = a.groupby(["lab_key", "БИН"], as_index=False)["severity_rank"].max().rename(columns={"БИН": "bin"})
        customers = customers.merge(ar, on=["lab_key", "bin"], how="left")
    else:
        customers["severity_rank"] = 0
    customers["severity_rank"] = customers["severity_rank"].fillna(0)
    rev_sev = {v: k for k, v in SEVERITY_RANK.items()}
    customers["risk"] = customers["severity_rank"].map(rev_sev).fillna("")

    materials = (
        df.groupby(["container_group", "container_display_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), customers=("bin", "nunique"), labs=("lab_key", "nunique"))
        .reset_index()
    )
    materials["calc_balance"] = materials["issued"] - materials["consumed"]
    materials["utilization_pct"] = np.where(materials["issued"] > 0, materials["consumed"] / materials["issued"] * 100.0, np.nan)

    return {"monthly": monthly, "labs": labs, "customers": customers, "materials": materials}


def procedure_summary(procedure: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if procedure.empty:
        return pd.DataFrame(), pd.DataFrame()
    p = procedure.copy()
    p["metric_norm"] = p["metric"].astype(str).str.strip().str.upper().str.replace("С", "C", regex=False)
    idx = ["month", "lab_key", "lab_display_name", "procedure_uid", "procedure_room_name", "container_group", "container_display_name"]
    if "MatchStatus" in p.columns:
        idx.append("MatchStatus")
    piv = p.pivot_table(index=idx, columns="metric_norm", values="amount", aggfunc="sum", fill_value=0).reset_index()
    piv.columns.name = None
    lis_col = next((c for c in piv.columns if str(c).upper() == "LIS"), None)
    onec_col = next((c for c in piv.columns if str(c).upper() in {"1C", "1С"}), None)
    piv["LIS"] = piv[lis_col] if lis_col else 0.0
    piv["1C"] = piv[onec_col] if onec_col else 0.0
    piv["Разница"] = piv["1C"] - piv["LIS"]
    monthly = piv.groupby("month", dropna=False)[["1C", "LIS"]].sum().reset_index().sort_values("month")
    monthly["Разница"] = monthly["1C"] - monthly["LIS"]
    return piv, monthly


# ============================================================
# ГРАФИКИ
# ============================================================

def chart_monthly_lines(monthly: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=monthly["month"], y=monthly["issued"], mode="lines+markers", name="Выдано 1С", hovertemplate="Выдано 1С: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Scatter(x=monthly["month"], y=monthly["consumed"], mode="lines+markers", name="Использовано ЛИС", hovertemplate="Использовано ЛИС: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Scatter(x=monthly["month"], y=monthly["calc_balance"], mode="lines+markers", name="Накопительная расчетная дельта*", hovertemplate="Расчетная дельта*: %{y:,.0f} ед.<extra></extra>"))
    fig.update_xaxes(tickformat="%b %Y")
    return add_plotly_defaults(fig, height=385, x_title="Месяц", y_title="Количество, ед.", hovermode="x unified")


def chart_monthly_grouped(monthly: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=monthly["month"], y=monthly["issued"], name="Выдано 1С", hovertemplate="Выдано 1С: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Bar(x=monthly["month"], y=monthly["consumed"], name="Использовано ЛИС", hovertemplate="Использовано ЛИС: %{y:,.0f} ед.<extra></extra>"))
    fig.update_layout(barmode="group")
    fig.update_xaxes(tickformat="%b %Y")
    return add_plotly_defaults(fig, height=370, x_title="Месяц", y_title="Количество, ед.", hovermode="x unified")


def chart_top_bar(
    df: pd.DataFrame,
    label: str,
    value: str,
    *,
    top_n: int = 12,
    x_title: str = "Количество, ед.",
    y_title: str = "",
    max_label: int = 42,
    use_abs_sort: bool = False,
    height: int = 390,
) -> go.Figure:
    if df is None or df.empty or value not in df.columns or label not in df.columns:
        return add_plotly_defaults(go.Figure(), height=height, x_title=x_title, y_title=y_title)
    d = df.copy()
    d["_sort"] = d[value].abs() if use_abs_sort else d[value]
    d = d.nlargest(top_n, "_sort").sort_values("_sort")
    d["_short"] = d[label].map(lambda x: short_label(x, max_label))
    d["_full"] = d[label].astype(str)
    fig = go.Figure(go.Bar(
        x=d[value], y=d["_short"], orientation="h",
        customdata=np.stack([d["_full"], d[value]], axis=-1),
        text=[fmt_num(v) for v in d[value]], textposition="outside", cliponaxis=False,
        hovertemplate="%{customdata[0]}<br>Значение: %{customdata[1]:,.0f}<extra></extra>",
    ))
    return add_plotly_defaults(fig, height=height, x_title=x_title, y_title=y_title, left_margin=175, bottom_margin=62, legend=False)


def chart_dual_lab_bar(labs: pd.DataFrame, top_n: int = 12) -> go.Figure:
    if labs.empty:
        return add_plotly_defaults(go.Figure(), height=380, x_title="Лаборатория", y_title="Количество, ед.")
    d = labs.nlargest(top_n, "issued").sort_values("issued", ascending=False).copy()
    d["label"] = d["lab_display_name"].map(lambda x: short_label(x, 28))
    fig = go.Figure()
    fig.add_trace(go.Bar(x=d["label"], y=d["issued"], name="Выдано 1С", customdata=d[["lab_display_name"]], hovertemplate="%{customdata[0]}<br>Выдано 1С: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Bar(x=d["label"], y=d["consumed"], name="Использовано ЛИС", customdata=d[["lab_display_name"]], hovertemplate="%{customdata[0]}<br>Использовано ЛИС: %{y:,.0f} ед.<extra></extra>"))
    fig.update_layout(barmode="group")
    return add_plotly_defaults(fig, height=385, x_title="Лаборатория", y_title="Количество, ед.", bottom_margin=110, hovermode="closest")


def chart_scatter(customers: pd.DataFrame) -> go.Figure:
    d = customers[customers["issued"] > 0].copy()
    if d.empty:
        return add_plotly_defaults(go.Figure(), height=430, x_title="Объем выдачи по 1С, ед.", y_title="Не отражено в ЛИС от выданного, %")

    d["unaccounted"] = (d["issued"] - d["consumed"]).clip(lower=0)
    d["unaccounted_pct"] = np.where(d["issued"] > 0, d["unaccounted"] / d["issued"] * 100.0, 0.0)
    d["reverse_gap"] = (d["consumed"] - d["issued"]).clip(lower=0)
    d["risk_label"] = np.select(
        [(d["consumed"] == 0) | (d["unaccounted_pct"] >= 80), d["unaccounted_pct"] >= 50, d["reverse_gap"] > 0],
        ["Очень критично", "Критично", "Обратное расхождение"], default="Норма / умеренный риск")
    d["priority_score"] = d["unaccounted"] * (1 + d["unaccounted_pct"] / 100)
    top_labels = set(d.nlargest(min(10, len(d)), "priority_score").index)
    d["text_label"] = [short_label(name, 24) if idx in top_labels else "" for idx, name in zip(d.index, d["partner_name"])]
    color_map = {"Очень критично":"#ef4444", "Критично":"#f59e0b", "Обратное расхождение":"#8b5cf6", "Норма / умеренный риск":"#3b82f6"}

    fig = go.Figure()
    for risk_name in ["Норма / умеренный риск", "Критично", "Очень критично", "Обратное расхождение"]:
        part=d[d["risk_label"]==risk_name]
        if part.empty: continue
        sizes=np.clip(np.sqrt(np.maximum(part["unaccounted"].to_numpy(dtype=float),1))/8+7,7,24)
        fig.add_trace(go.Scatter(
            x=part["issued"], y=part["unaccounted_pct"], mode="markers+text", text=part["text_label"], textposition="top center", textfont=dict(size=9), name=risk_name,
            marker=dict(size=sizes, opacity=.76, color=color_map[risk_name], line=dict(width=1,color="white")),
            customdata=np.stack([part["partner_name"].astype(str),part["bin"].astype(str),part["consumed"],part["unaccounted"],part["calc_balance"],part["reverse_gap"]],axis=-1),
            hovertemplate=("<b>%{customdata[0]}</b><br>БИН: %{customdata[1]}<br>Выдано 1С: %{x:,.0f} ед.<br>Использовано ЛИС: %{customdata[2]:,.0f} ед.<br>Не отражено в ЛИС: %{customdata[3]:,.0f} ед.<br>Не отражено: %{y:.1f}% от выдачи<br>Накопительная расчетная дельта*: %{customdata[4]:,.0f} ед.<br>Обратное расхождение ЛИС>1С: %{customdata[5]:,.0f} ед.<extra></extra>")
        ))
    fig.add_hline(y=50,line_dash="dash",line_color="#f59e0b",annotation_text="50% выданного не отражено в ЛИС",annotation_position="top left")
    fig.add_hline(y=80,line_dash="dash",line_color="#ef4444",annotation_text="80% — очень критично",annotation_position="top left")
    fig.update_yaxes(range=[-3,105],ticksuffix="%")
    return add_plotly_defaults(fig,height=440,x_title="Объем выдачи по 1С, ед.",y_title="Доля выданного объема, не отраженная в ЛИС, %",hovermode="closest",legend=True,bottom_margin=82)


def chart_status_donut(customers: pd.DataFrame) -> go.Figure:
    if customers.empty or "status" not in customers.columns:
        return add_plotly_defaults(go.Figure(), height=330, legend=False)
    d = customers["status"].fillna("—").value_counts().rename_axis("status").reset_index(name="count")
    fig = go.Figure(go.Pie(labels=d["status"], values=d["count"], hole=.62, textinfo="label+percent", hovertemplate="%{label}: %{value} заказчиков (%{percent})<extra></extra>"))
    fig.update_layout(annotations=[dict(text=f"{int(d['count'].sum())}<br><span style='font-size:11px'>заказчиков</span>", x=.5, y=.5, showarrow=False, font=dict(size=20))])
    return add_plotly_defaults(fig, height=330, legend=True, left_margin=15, bottom_margin=58)


def chart_customer_monthly(df_customer: pd.DataFrame) -> go.Figure:
    m = df_customer.groupby("month").agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum")).reset_index().sort_values("month")
    m["balance"] = (m["issued"] - m["consumed"]).cumsum()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=m["month"], y=m["issued"], name="Выдано 1С", hovertemplate="Выдано 1С: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Bar(x=m["month"], y=m["consumed"], name="Использовано ЛИС", hovertemplate="Использовано ЛИС: %{y:,.0f} ед.<extra></extra>"))
    fig.add_trace(go.Scatter(x=m["month"], y=m["balance"], mode="lines+markers", name="Накопительная расчетная дельта*", hovertemplate="Расчетная дельта*: %{y:,.0f} ед.<extra></extra>"))
    fig.update_layout(barmode="group")
    fig.update_xaxes(tickformat="%b %Y")
    return add_plotly_defaults(fig, height=390, x_title="Месяц", y_title="Количество, ед.", hovermode="x unified")


def chart_heatmap_lab_material(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return add_plotly_defaults(go.Figure(), height=360, x_title="Материал", y_title="Лаборатория")
    top_groups = df.groupby("container_group")["OneC_Issued"].sum().nlargest(10).index
    d = df[df["container_group"].isin(top_groups)].groupby(["lab_display_name", "container_group", "container_display_name"], as_index=False).agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    d["util"] = np.where(d["issued"] > 0, d["consumed"] / d["issued"] * 100, np.nan)
    d["delta"] = d["issued"] - d["consumed"]
    d["risk"] = 0
    d.loc[(d["issued"] > 0) & (d["util"] < 75), "risk"] = 1
    d.loc[((d["issued"] > 0) & (d["util"] < CFG.high_utilization_pct)) | ((d["issued"] > 0) & (d["consumed"] == 0)), "risk"] = 2
    d.loc[(d["delta"] < 0) | ((d["consumed"] > 0) & (d["issued"] == 0)), "risk"] = 3
    z = d.pivot_table(index="lab_display_name", columns="container_group", values="risk", aggfunc="max", fill_value=0)
    full_map = d.drop_duplicates("container_group").set_index("container_group")["container_display_name"].to_dict()
    hover = []
    for lab_name in z.index:
        row = []
        for cg in z.columns:
            rr = d[(d["lab_display_name"] == lab_name) & (d["container_group"] == cg)]
            if rr.empty:
                row.append(f"{lab_name}<br>{full_map.get(cg,cg)}<br>Нет данных")
            else:
                r = rr.iloc[0]
                row.append(f"{lab_name}<br>{r['container_display_name']}<br>Выдано: {fmt_num(r['issued'])}<br>Использовано: {fmt_num(r['consumed'])}<br>Доля использования: {fmt_pct(r['util'])}")
        hover.append(row)
    colorscale = [[0.0, "#d9f2e5"], [0.33, "#d9f2e5"], [0.34, "#fde68a"], [0.66, "#fde68a"], [0.67, "#fdba74"], [0.88, "#fdba74"], [0.89, "#fca5a5"], [1.0, "#fca5a5"]]
    fig = go.Figure(go.Heatmap(z=z.values, x=list(z.columns), y=list(z.index), zmin=0, zmax=3, colorscale=colorscale, showscale=False, text=hover, hovertemplate="%{text}<extra></extra>", xgap=2, ygap=2))
    return add_plotly_defaults(fig, height=max(320, 115 + 34 * len(z.index)), x_title="Код материала (полное название — при наведении)", y_title="Лаборатория", bottom_margin=80, legend=False)


def chart_city_monthly_lines(df: pd.DataFrame) -> go.Figure:
    """Компактный line-chart только по лабораториям с наибольшим риском, чтобы не было визуальной каши."""
    if df.empty:
        return add_plotly_defaults(go.Figure(), height=460, x_title="Месяц", y_title="1С − ЛИС, ед.")
    d = (
        df.groupby(["month", "lab_key", "lab_display_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
        .reset_index()
        .sort_values("month")
    )
    d["gap"] = (d["issued"] - d["consumed"]).clip(lower=0)
    lab_rank = d.groupby("lab_display_name", dropna=False)["gap"].sum().sort_values(ascending=False)
    top_labs = list(lab_rank.head(6).index)
    d = d[d["lab_display_name"].isin(top_labs)].copy()
    if d.empty:
        return add_plotly_defaults(go.Figure(), height=460, x_title="Месяц", y_title="1С − ЛИС, ед.")

    fig = go.Figure()
    total = d.groupby("month", dropna=False)["gap"].sum().reset_index()
    fig.add_trace(go.Scatter(
        x=total["month"], y=total["gap"], mode="lines+markers", name="Итого по топ-лабораториям",
        line=dict(width=4, dash="dash"),
        hovertemplate="<b>Итого по топ-лабораториям</b><br>%{x|%Y-%m}<br>Не подтверждено в ЛИС: %{y:,.0f} ед.<extra></extra>"
    ))
    for lab_name, part in d.groupby("lab_display_name", dropna=False):
        fig.add_trace(go.Scatter(
            x=part["month"], y=part["gap"], mode="lines+markers", name=str(lab_name),
            customdata=np.stack([part["issued"], part["consumed"]], axis=-1),
            hovertemplate=(f"<b>{html.escape(str(lab_name))}</b><br>" + "%{x|%Y-%m}<br>Выдано 1С: %{customdata[0]:,.0f} ед.<br>Использовано ЛИС: %{customdata[1]:,.0f} ед.<br>Не подтверждено в ЛИС за месяц: %{y:,.0f} ед.<extra></extra>")
        ))
    fig.update_xaxes(tickformat="%b %Y")
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-.23, x=0, font=dict(size=10)))
    return add_plotly_defaults(fig, height=500, x_title="Месяц", y_title="Выдано 1С минус использовано ЛИС за месяц, ед.", hovermode="closest", legend=True, bottom_margin=155)


def chart_city_monthly_heatmap(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return add_plotly_defaults(go.Figure(), height=430, x_title="Месяц", y_title="Лаборатория / город")
    d = (
        df.groupby(["month", "lab_display_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
        .reset_index()
    )
    d["gap"] = (d["issued"] - d["consumed"]).clip(lower=0)
    order = d.groupby("lab_display_name", dropna=False)["gap"].sum().sort_values(ascending=False).index.tolist()
    p = d.pivot_table(index="lab_display_name", columns="month", values="gap", aggfunc="sum", fill_value=0)
    p = p.reindex(order)
    if p.empty:
        return add_plotly_defaults(go.Figure(), height=430, x_title="Месяц", y_title="Лаборатория / город")
    month_labels = [pd.to_datetime(c).strftime("%Y-%m") if pd.notna(c) else "—" for c in p.columns]
    hover = np.empty(p.shape, dtype=object)
    for i, lab in enumerate(p.index):
        for j, month in enumerate(p.columns):
            hover[i, j] = f"<b>{lab}</b><br>{pd.to_datetime(month).strftime('%Y-%m')}<br>Не подтверждено в ЛИС: {fmt_num(p.iloc[i, j])} ед."
    fig = go.Figure(go.Heatmap(
        z=p.values,
        x=month_labels,
        y=list(p.index),
        colorscale=[[0.0, '#f8fafc'], [0.15, '#dbeafe'], [0.45, '#93c5fd'], [0.7, '#60a5fa'], [1.0, '#2563eb']],
        colorbar=dict(title='Не подтверждено<br>в ЛИС, ед.', thickness=12, len=.72),
        customdata=hover,
        hovertemplate='%{customdata}<extra></extra>',
        xgap=2,
        ygap=2,
    ))
    return add_plotly_defaults(fig, height=max(420, 155 + 28 * len(p.index)), x_title="Месяц", y_title="Лаборатория / город", bottom_margin=85, left_margin=120, legend=False)


def chart_material_monthly(df: pd.DataFrame, container_group: str) -> go.Figure:
    d = df[df["container_group"] == container_group].groupby("month").agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum")).reset_index().sort_values("month")
    d["balance"] = (d["issued"] - d["consumed"]).cumsum()
    return chart_customer_monthly(d.rename(columns={"issued": "OneC_Issued", "consumed": "LIS_Consumed"}) if False else df[df["container_group"] == container_group])


def chart_procedure_monthly(monthly: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if not monthly.empty:
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["1C"], name="Выдано / списано 1С", hovertemplate="1С: %{y:,.0f} ед.<extra></extra>"))
        fig.add_trace(go.Bar(x=monthly["month"], y=monthly["LIS"], name="Использовано ЛИС", hovertemplate="ЛИС: %{y:,.0f} ед.<extra></extra>"))
    fig.update_layout(barmode="group")
    fig.update_xaxes(tickformat="%b %Y")
    return add_plotly_defaults(fig, height=370, x_title="Месяц", y_title="Количество, ед.", hovermode="x unified")


def chart_procedure_top(proc_detail: pd.DataFrame) -> go.Figure:
    if proc_detail.empty:
        return add_plotly_defaults(go.Figure(), height=440, x_title="Расхождение, ед.", y_title="Процедурный кабинет")
    d = proc_detail.groupby(["lab_display_name", "procedure_uid", "procedure_room_name"], as_index=False).agg(onec=("1C", "sum"), lis=("LIS", "sum"))
    d["difference"] = d["onec"] - d["lis"]
    d["unaccounted"] = d["difference"].clip(lower=0)
    d["reverse"] = (-d["difference"]).clip(lower=0)
    d["max_gap"] = d[["unaccounted", "reverse"]].max(axis=1)
    d["full"] = d["lab_display_name"].astype(str) + " — " + d["procedure_room_name"].astype(str)
    d = d.nlargest(12, "max_gap").sort_values("difference")
    d["label"] = d["full"].map(lambda x: short_label(x, 40))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=-d["reverse"], y=d["label"], orientation="h", name="ЛИС > 1С (обратное расхождение)",
        customdata=np.stack([d["full"], d["onec"], d["lis"], d["unaccounted"], d["reverse"]], axis=-1),
        text=[fmt_num(v) if v > 0 else "" for v in d["reverse"]], textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{customdata[0]}</b><br>1С: %{customdata[1]:,.0f} ед.<br>ЛИС: %{customdata[2]:,.0f} ед.<br>ЛИС выше 1С на %{customdata[4]:,.0f} ед.<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=d["unaccounted"], y=d["label"], orientation="h", name="1С > ЛИС (основной риск)",
        customdata=np.stack([d["full"], d["onec"], d["lis"], d["unaccounted"], d["reverse"]], axis=-1),
        text=[fmt_num(v) if v > 0 else "" for v in d["unaccounted"]], textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{customdata[0]}</b><br>1С: %{customdata[1]:,.0f} ед.<br>ЛИС: %{customdata[2]:,.0f} ед.<br>Не подтверждено в ЛИС: %{customdata[3]:,.0f} ед.<extra></extra>",
    ))
    fig.add_vline(x=0, line_color="#64748b", line_width=1)
    fig.update_layout(barmode="overlay")
    return add_plotly_defaults(fig, height=460, x_title="Расхождение, ед. (слева: ЛИС > 1С, справа: 1С > ЛИС)", y_title="Процедурный кабинет", left_margin=220, bottom_margin=70, legend=True)


def chart_procedure_materials(proc_detail: pd.DataFrame) -> go.Figure:
    if proc_detail.empty:
        return add_plotly_defaults(go.Figure(), height=380, x_title="Код материала", y_title="Расхождение, ед.")
    d=proc_detail.groupby(["container_group","container_display_name"],as_index=False).agg(onec=("1C","sum"),lis=("LIS","sum"))
    d["diff"]=d["onec"]-d["lis"]; d["unaccounted"]=d["diff"].clip(lower=0); d["reverse"]=(-d["diff"]).clip(lower=0); d["total_gap"]=d["unaccounted"]+d["reverse"]
    d=d.nlargest(12,"total_gap").sort_values("total_gap",ascending=False); d["label"]=d["container_group"].astype(str)
    fig=go.Figure(); fig.add_trace(go.Bar(x=d["label"],y=d["unaccounted"],name="1С выше ЛИС — основной риск",customdata=d[["container_display_name"]],hovertemplate="<b>%{customdata[0]}</b><br>Выдано больше ЛИС на %{y:,.0f} ед.<extra></extra>")); fig.add_trace(go.Bar(x=d["label"],y=d["reverse"],name="ЛИС выше 1С — контроль сверки",customdata=d[["container_display_name"]],hovertemplate="<b>%{customdata[0]}</b><br>ЛИС выше 1С на %{y:,.0f} ед.<extra></extra>")); fig.update_layout(barmode="group")
    return add_plotly_defaults(fig,height=390,x_title="Код материала (полное название — при наведении)",y_title="Абсолютное расхождение между 1С и ЛИС, ед.",bottom_margin=85,hovermode="closest")


def chart_anomaly_types(anomalies: pd.DataFrame) -> go.Figure:
    if anomalies.empty: return add_plotly_defaults(go.Figure(),height=370,x_title="Количество объектов с сигналом",y_title="Причина")
    d=anomalies.groupby("Тип аномалии").size().reset_index(name="Количество")
    return chart_top_bar(d,"Тип аномалии","Количество",top_n=15,x_title="Количество связок заказчик × материал",y_title="Причина аналитического сигнала",max_label=34,height=410)


def chart_anomaly_trend(month_signals: pd.DataFrame) -> go.Figure:
    if month_signals.empty: return add_plotly_defaults(go.Figure(),height=360,x_title="Месяц",y_title="Количество сигналов")
    piv=month_signals.pivot_table(index="month",columns="severity",values="count",aggfunc="sum",fill_value=0).reset_index().sort_values("month"); fig=go.Figure()
    for sev in ["Critical","High","Medium"]:
        if sev in piv.columns: fig.add_trace(go.Bar(x=piv["month"],y=piv[sev],name=severity_ru(sev),hovertemplate=f"{severity_ru(sev)}: %{{y}} сигналов<extra></extra>"))
    fig.update_layout(barmode="stack"); fig.update_xaxes(tickformat="%b %Y")
    return add_plotly_defaults(fig,height=370,x_title="Месяц",y_title="Количество аналитических сигналов",hovermode="x unified")


def chart_anomaly_heatmap(anomalies: pd.DataFrame) -> go.Figure:
    if anomalies.empty:
        return add_plotly_defaults(go.Figure(), height=330, x_title="Причина сигнала", y_title="Лаборатория / город")
    priority_types=["Выдали, но в ЛИС 0","Очень низкое использование после выдачи","Низкое использование после выдачи","Очень большой накопленный неиспользованный объем","Большой накопленный неиспользованный объем","1C_only: выдача без стороны ЛИС","ЛИС есть, выдачи 1С нет","ЛИС заметно выше 1С"]
    d=anomalies[anomalies["Тип аномалии"].isin(priority_types)].copy()
    if d.empty: d=anomalies.copy()
    p=d.pivot_table(index="Лаборатория",columns="Тип аномалии",values="БИН",aggfunc="count",fill_value=0)
    cols=[c for c in priority_types if c in p.columns]+[c for c in p.columns if c not in priority_types]
    p=p[cols]
    short_cols=[wrap_label(c,18,2) for c in p.columns]
    max_v=max(float(p.to_numpy().max()),1.0)
    hover=np.empty(p.shape,dtype=object)
    for i,lab in enumerate(p.index):
        for j,full in enumerate(p.columns): hover[i,j]=f"<b>{lab}</b><br>{full}<br>Объектов с сигналом: {int(p.iloc[i,j])}"
    fig=go.Figure(go.Heatmap(z=p.values,x=short_cols,y=list(p.index),zmin=0,zmax=max_v,colorscale=[[0.0,"#f8fafc"],[0.01,"#dbeafe"],[0.35,"#fde68a"],[0.70,"#fdba74"],[1.0,"#f87171"]],showscale=True,colorbar=dict(title="Кол-во<br>объектов",thickness=12,len=.7),text=p.values.astype(int).astype(str),texttemplate="%{text}",textfont=dict(size=10),customdata=hover,hovertemplate="%{customdata}<extra></extra>",xgap=2,ygap=2))
    return add_plotly_defaults(fig,height=max(390,150+30*len(p.index)),x_title="Причина аналитического сигнала",y_title="Лаборатория / город",bottom_margin=130,legend=False,left_margin=120)


def chart_quality_funnel(funnel: pd.DataFrame) -> go.Figure:
    if funnel.empty or "stage" not in funnel.columns or "rows" not in funnel.columns:
        return add_plotly_defaults(go.Figure(), height=340, legend=False)
    d = funnel.copy()
    # Если есть несколько источников, показываем суммарно по stage.
    d = d.groupby("stage", as_index=False)["rows"].sum()
    fig = go.Figure(go.Funnel(y=d["stage"], x=d["rows"], textinfo="value+percent initial", hovertemplate="Этап: %{y}<br>Строк: %{x:,.0f}<extra></extra>"))
    return add_plotly_defaults(fig, height=340, x_title="Количество строк", y_title="Этап контроля качества", legend=False, left_margin=95)


def chart_quality_issues(qi: pd.DataFrame) -> go.Figure:
    if qi.empty:
        return add_plotly_defaults(go.Figure(), height=340, x_title="Количество строк", y_title="Тип проблемы", legend=False)
    d = qi.groupby("issue_type", as_index=False)["rows"].sum()
    return chart_top_bar(d, "issue_type", "rows", top_n=12, x_title="Количество затронутых строк", y_title="Тип проблемы качества", max_label=38, height=340)


def chart_mapping_coverage(cov: pd.DataFrame) -> go.Figure:
    if cov.empty:
        return add_plotly_defaults(go.Figure(), height=330, x_title="Источник / область маппинга", y_title="Количество строк")
    d = cov.copy()
    d["label"] = d["source"].astype(str) + " — " + d["mapping_scope"].astype(str)
    fig = px.bar(d, x="label", y="rows", text_auto=".3s", custom_data=["source", "mapping_scope", "quantity", "unique_codes"])
    fig.update_traces(hovertemplate="Источник: %{customdata[0]}<br>Область: %{customdata[1]}<br>Строк: %{y:,.0f}<br>Количество: %{customdata[2]:,.0f}<br>Уникальных кодов: %{customdata[3]:,.0f}<extra></extra>")
    return add_plotly_defaults(fig, height=330, x_title="Источник / область маппинга", y_title="Количество строк", bottom_margin=100, legend=False)


# ============================================================
# HTML ТАБЛИЦЫ / КАРТОЧКИ
# ============================================================

def prepare_table(df: pd.DataFrame, max_rows: int | None = None) -> tuple[pd.DataFrame, int]:
    if df is None:
        return pd.DataFrame(), 0
    total = len(df)
    shown = df.head(max_rows).copy() if max_rows is not None else df.copy()
    for col in shown.columns:
        if pd.api.types.is_datetime64_any_dtype(shown[col]):
            shown[col] = shown[col].dt.strftime("%Y-%m")
        elif pd.api.types.is_float_dtype(shown[col]):
            shown[col] = shown[col].map(lambda x: fmt_num(x, 2) if pd.notna(x) else "")
    return shown, total


def dataframe_html(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df is None or df.empty:
        return '<div class="empty">Нет данных для выбранного раздела</div>'
    shown, total = prepare_table(df, max_rows)
    table = shown.to_html(index=False, escape=True, classes="data-table", border=0)
    suffix = ""
    if max_rows is not None and total > max_rows:
        suffix = f'<div class="table-note">Показано {fmt_num(max_rows)} из {fmt_num(total)} строк. Полная детализация находится в Excel.</div>'
    return f'<div class="table-wrap">{table}</div>{suffix}'


def metric_table(df: pd.DataFrame, rename: dict[str, str], sort_by: str | None = None, ascending: bool = False, max_rows: int = 250) -> str:
    d = df.copy()
    if sort_by and sort_by in d.columns:
        d = d.sort_values(sort_by, ascending=ascending)
    cols = [c for c in rename if c in d.columns]
    d = d[cols].rename(columns=rename)
    return dataframe_html(d, max_rows=max_rows)


def kpi_card(title: str, value: str, subtitle: str = "", trend: str = "", trend_kind: str = "neutral", icon: str = "•") -> str:
    trend_html = f'<div class="kpi-trend {trend_kind}">{html.escape(trend)}</div>' if trend else ""
    return f'''<div class="kpi"><div class="kpi-icon">{html.escape(icon)}</div><div class="kpi-body"><div class="kpi-title">{html.escape(title)}</div><div class="kpi-value">{html.escape(value)}</div><div class="kpi-sub">{html.escape(subtitle)}</div>{trend_html}</div></div>'''


def section(title: str, body: str, subtitle: str = "") -> str:
    sub = f'<div class="section-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    return f'<section class="panel"><div class="section-head"><h2>{html.escape(title)}</h2>{sub}</div>{body}</section>'


def info_strip(items: list[tuple[str, str]]) -> str:
    parts = []
    for label, value in items:
        parts.append(f'<div class="info-item"><span>{html.escape(label)}</span><b>{html.escape(value)}</b></div>')
    return '<div class="info-strip">' + ''.join(parts) + '</div>'


def severity_badge(v: str) -> str:
    css={"Critical":"crit","High":"high","Medium":"med","Low":"low"}.get(str(v),"neutral")
    return f'<span class="badge {css}">{html.escape(severity_ru(v))}</span>'


def anomalies_table_html(anomalies: pd.DataFrame, max_rows: int = 250) -> str:
    if anomalies.empty: return '<div class="empty">Нет аналитических сигналов</div>'
    wanted=["severity","Лаборатория","БИН","Заказчик","Контейнер","Тип аномалии","Значение","Направление","Почему","Период"]
    d=anomalies[[c for c in wanted if c in anomalies.columns]].head(max_rows).copy(); rows=[]
    for _,r in d.iterrows():
        cells=[]
        for c in d.columns:
            val=severity_badge(r[c]) if c=="severity" else html.escape(str(r[c] if pd.notna(r[c]) else "")); cells.append(f"<td>{val}</td>")
        rows.append("<tr>"+''.join(cells)+"</tr>")
    labels={"severity":"Критичность"}; headers=''.join(f"<th>{html.escape(labels.get(c,c))}</th>" for c in d.columns)
    suffix=f'<div class="table-note">Показано {len(d)} из {len(anomalies)} сигналов. Полный перечень — в Excel.</div>' if len(anomalies)>len(d) else ""
    return f'<div class="table-wrap"><table class="data-table"><thead><tr>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{suffix}'



# ============================================================
# CSS / JS
# ============================================================

def report_css() -> str:
    return r'''
:root{--bg:#f4f7fb;--panel:#fff;--text:#10213f;--muted:#667085;--border:#dfe7f1;--accent:#1677ff;--soft:#eaf3ff;--danger:#e5484d;--warn:#f59e0b;--ok:#19a974;--purple:#8b5cf6;--cyan:#0ea5b7}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:Arial,'Segoe UI',sans-serif}.app{display:flex;min-height:100vh}.side{width:245px;background:linear-gradient(180deg,#eef5fc,#f5f9fd);border-right:1px solid var(--border);padding:22px 12px;position:sticky;top:0;height:100vh;z-index:10}.brand{font-size:27px;font-weight:800;margin:0 10px}.brand-sub{font-size:11px;color:var(--muted);margin:2px 10px 22px}.nav-btn{width:100%;border:0;background:transparent;padding:11px 13px;text-align:left;border-radius:8px;color:#31476b;font-size:13px;cursor:pointer;margin:2px 0;transition:.15s}.nav-btn.active,.nav-btn:hover{background:#dceaff;color:#0b57d0;font-weight:700}.main{flex:1;min-width:0;padding:14px 18px 34px;max-width:1800px;margin:0 auto}.top{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:8px}.title h1{margin:0;font-size:25px}.title p{margin:4px 0 0;color:var(--muted);font-size:13px}.updated{text-align:right;font-size:12px;color:var(--muted);white-space:nowrap}.context{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 10px;align-items:end}.context-pill{background:#fff;border:1px solid var(--border);border-radius:8px;padding:7px 10px;font-size:11px;color:#475467}.context-pill b{color:#10213f}.excel-link{display:inline-block;background:#fff;border:1px solid #b9d3fa;color:#0b57d0;text-decoration:none;border-radius:8px;padding:7px 11px;font-size:11px;font-weight:700}.notice{background:#fff7e8;border:1px solid #f4c978;color:#714600;padding:10px 13px;border-radius:9px;font-size:12px;margin:9px 0 12px}.kpis{display:grid;grid-template-columns:repeat(6,minmax(155px,1fr));gap:9px;margin:10px 0}.kpi{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px;min-height:108px;display:flex;gap:10px;align-items:flex-start}.kpi-icon{width:34px;height:34px;border-radius:9px;background:#edf4ff;color:#1769dd;display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:800;flex:0 0 auto}.kpi-body{min-width:0}.kpi-title{font-size:11px;color:#475467;font-weight:700}.kpi-value{font-size:23px;font-weight:800;margin-top:5px;line-height:1.05}.kpi-sub{font-size:10px;color:var(--muted);margin-top:5px}.kpi-trend{font-size:10px;font-weight:700;margin-top:4px}.kpi-trend.up{color:var(--ok)}.kpi-trend.down{color:var(--danger)}.kpi-trend.neutral{color:var(--muted)}.grid-2{display:grid;grid-template-columns:minmax(0,2fr) minmax(340px,1fr);gap:11px}.grid-2-eq{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:11px}.grid-3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}.grid-wide-side{display:grid;grid-template-columns:minmax(0,3fr) minmax(300px,1fr);gap:11px}.panel{background:var(--panel);border:1px solid var(--border);border-radius:10px;margin:10px 0;padding:11px 12px;overflow:hidden;box-shadow:0 1px 1px rgba(16,33,63,.02)}.section-head{display:flex;justify-content:space-between;align-items:baseline;gap:15px;min-height:28px}.section-head h2{font-size:16px;margin:0 0 7px}.section-sub{font-size:10px;color:var(--muted);text-align:right}.tab{display:none}.tab.active{display:block}.table-wrap{overflow:auto;max-width:100%;border:1px solid var(--border);border-radius:7px;background:#fff}.data-table{width:100%;border-collapse:collapse;font-size:11px;background:#fff}.data-table th{position:sticky;top:0;z-index:2;background:#eaf1f8;color:#344054;text-align:left;padding:7px;border-bottom:1px solid var(--border);white-space:nowrap}.data-table td{padding:6px 7px;border-bottom:1px solid #edf1f6;vertical-align:top;line-height:1.3;white-space:normal;min-width:74px;max-width:430px;overflow-wrap:anywhere}.data-table td:nth-child(1),.data-table th:nth-child(1){min-width:95px}.data-table tr:nth-child(even){background:#fbfdff}.data-table tr:hover{background:#f3f8ff}.table-note{font-size:10px;color:var(--muted);padding-top:6px}.empty{padding:25px;text-align:center;color:var(--muted);font-size:12px}.footer{font-size:10px;color:var(--muted);margin:18px 2px}.method p,.method li{font-size:12px;line-height:1.55;color:#344054}.method code{background:#f1f5f9;padding:2px 4px;border-radius:4px}.info-strip{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));background:#fff;border:1px solid var(--border);border-radius:10px;padding:12px;margin:10px 0;gap:8px}.info-item{padding:2px 10px;border-right:1px solid #e8eef5;min-width:0}.info-item:last-child{border-right:0}.info-item span{display:block;font-size:10px;color:var(--muted);margin-bottom:4px}.info-item b{display:block;font-size:12px;overflow-wrap:anywhere}.badge{display:inline-block;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:700}.badge.crit{background:#fee2e2;color:#b42318}.badge.high{background:#ffedd5;color:#c2410c}.badge.med{background:#fef3c7;color:#a16207}.badge.low{background:#dcfce7;color:#15803d}.badge.neutral{background:#edf2f7;color:#475467}.micro-note{font-size:10px;color:var(--muted);margin-top:5px}.subtle-card{background:#f8fbff;border:1px solid #e5edf7;border-radius:8px;padding:10px}.plotly-notifier{font-family:Arial,'Segoe UI',sans-serif!important}.plotly .modebar{opacity:.35}.plotly:hover .modebar{opacity:.9}
.period-filter{display:flex;gap:7px;align-items:end;flex-wrap:wrap;background:#fff;border:1px solid var(--border);border-radius:9px;padding:8px 10px}.period-field{display:flex;flex-direction:column;gap:3px;font-size:10px;color:var(--muted)}.period-field input{border:1px solid #cfd8e6;border-radius:6px;padding:5px 7px;font:inherit;color:#23324d;background:#fff}.period-btn{border:1px solid #b9d3fa;background:#f7fbff;color:#0b57d0;border-radius:6px;padding:6px 9px;font-size:10px;font-weight:700;cursor:pointer}.period-btn:hover{background:#eaf3ff}.period-help{font-size:10px;color:var(--muted);max-width:390px;line-height:1.3}.method h3{font-size:14px;margin:18px 0 6px;color:#17355d}
@media(max-width:1250px){.side{width:205px}.kpis{grid-template-columns:repeat(3,1fr)}.grid-2,.grid-2-eq,.grid-3,.grid-wide-side{grid-template-columns:1fr}.info-strip{grid-template-columns:repeat(3,1fr)}}
@media(max-width:760px){.app{display:block}.side{width:auto;height:auto;position:relative}.main{padding:10px}.kpis{grid-template-columns:1fr 1fr}.nav-btn{display:inline-block;width:auto}.top{display:block}.updated{text-align:left;margin-top:7px}.info-strip{grid-template-columns:1fr 1fr}}
'''


def report_js() -> str:
    return r'''
(function(){
  const buttons=[...document.querySelectorAll('.nav-btn[data-tab]')]; const tabs=[...document.querySelectorAll('.tab')];
  function openTab(id){tabs.forEach(t=>t.classList.toggle('active',t.id===id));buttons.forEach(b=>b.classList.toggle('active',b.dataset.tab===id));window.scrollTo({top:0,behavior:'instant'});setTimeout(()=>window.dispatchEvent(new Event('resize')),80);if(history&&history.replaceState)history.replaceState(null,'','#'+id);}
  buttons.forEach(b=>b.addEventListener('click',()=>openTab(b.dataset.tab))); const requested=(location.hash||'').replace('#',''); openTab(buttons.some(b=>b.dataset.tab===requested)?requested:(buttons[0]?.dataset.tab||tabs[0]?.id));
  const startInput=document.getElementById('period-start'),endInput=document.getElementById('period-end'),allBtn=document.getElementById('period-all'),applyBtn=document.getElementById('period-apply'); const minMonth=document.body.dataset.minMonth||'',maxMonth=document.body.dataset.maxMonth||'';
  function isDateLike(v){if(v===null||v===undefined)return false;const s=String(v);return /^\d{4}-\d{2}/.test(s)||!isNaN(Date.parse(s));}
  function applyPeriod(start,end){document.querySelectorAll('.plotly-graph-div').forEach(gd=>{try{const traces=gd.data||[];const hasDate=traces.some(tr=>Array.isArray(tr.x)&&tr.x.some(isDateLike));if(!hasDate)return;if(start&&end){const endDate=new Date(end+'-01T00:00:00');endDate.setMonth(endDate.getMonth()+1);Plotly.relayout(gd,{'xaxis.autorange':false,'xaxis.range':[start+'-01',endDate.toISOString().slice(0,10)]});}else{Plotly.relayout(gd,{'xaxis.autorange':true});}}catch(e){}});const label=document.getElementById('period-current');if(label)label.textContent=(start&&end)?(start+' — '+end):(minMonth+' — '+maxMonth);}
  if(applyBtn)applyBtn.addEventListener('click',()=>{const s=startInput.value,e=endInput.value;if(s&&e&&s>e){alert('Начальный месяц не может быть позже конечного.');return;}applyPeriod(s,e);});
  if(allBtn)allBtn.addEventListener('click',()=>{startInput.value=minMonth;endInput.value=maxMonth;applyPeriod('','');});
  document.querySelectorAll('[data-period-months]').forEach(btn=>btn.addEventListener('click',()=>{const months=parseInt(btn.dataset.periodMonths||'0',10);if(!maxMonth||!months)return;const d=new Date(maxMonth+'-01T00:00:00');d.setMonth(d.getMonth()-(months-1));const s=d.toISOString().slice(0,7);startInput.value=s;endInput.value=maxMonth;applyPeriod(s,maxMonth);}));
})();
'''



# ============================================================
# СТРАНИЦЫ ОТЧЕТА
# ============================================================

def latest_period_metrics(monthly: pd.DataFrame) -> dict:
    if monthly.empty:
        return {"current": {}, "prev": {}}
    cur = monthly.iloc[-1].to_dict()
    prev = monthly.iloc[-2].to_dict() if len(monthly) > 1 else {}
    return {"current": cur, "prev": prev}


def kpis_overview(monthly: pd.DataFrame, anomalies: pd.DataFrame) -> str:
    p=latest_period_metrics(monthly); c,prev=p["current"],p["prev"]; cards=[]
    for title,key,icon,is_pct in [("Выдано 1С","issued","1С",False),("Использовано ЛИС","consumed","LIS",False),("Разрыв 1С − ЛИС*","period_delta","Δ",False),("Доля использования","utilization_pct","%",True)]:
        trend,kind=fmt_change(c.get(key),prev.get(key),pct=is_pct); value=fmt_pct(c.get(key)) if is_pct else fmt_num(c.get(key)); subtitle="за последний месяц" if key!="period_delta" else "положительное значение — потенциальный риск"; cards.append(kpi_card(title,value,subtitle,trend,kind,icon))
    problems=anomalies[anomalies["severity"].isin(["Critical","High"])]["БИН"].nunique() if not anomalies.empty else 0; critical=int((anomalies["severity"]=="Critical").sum()) if not anomalies.empty else 0
    cards.append(kpi_card("Проблемных заказчиков",fmt_num(problems),"критично + очень критично",icon="!")); cards.append(kpi_card("Очень критичных сигналов",fmt_num(critical),"первый приоритет проверки",icon="!!"))
    return '<div class="kpis">'+''.join(cards)+'</div>'


def page_overview(df: pd.DataFrame, s: dict[str, pd.DataFrame], anomalies: pd.DataFrame) -> str:
    monthly,labs,customers=s["monthly"],s["labs"],s["customers"]
    if len(labs)>1 and not anomalies.empty:
        lab_problem=anomalies[anomalies["severity"].isin(["Critical","High"])].groupby("Лаборатория").size().reset_index(name="signals")
        right=chart_top_bar(lab_problem,"Лаборатория","signals",top_n=10,x_title="Количество критичных сигналов",y_title="Лаборатория / город",max_label=28,height=385); right_title="Лаборатории с наибольшим числом рисков"
    else:
        c=customers.copy(); c["unaccounted"]=(c["issued"]-c["consumed"]).clip(lower=0); right=chart_top_bar(c,"partner_name","unaccounted",top_n=10,x_title="Не подтверждено в ЛИС, ед.",y_title="Заказчик",max_label=32,height=385); right_title="Заказчики с наибольшим объемом, не подтвержденным в ЛИС"
    return f'''
    {kpis_overview(monthly, anomalies)}
    <div class="grid-2">
      {section("Динамика выдачи, использования и расчетной дельты*", fig_html(chart_monthly_lines(monthly)), "Основной риск — когда 1С системно выше ЛИС; обратное расхождение контролируется отдельно")}
      {section(right_title, fig_html(right))}
    </div>
    {section("Лаборатория × материал: зоны внимания", fig_html(chart_heatmap_lab_material(df)), "На оси X короткие коды материалов; полное название доступно при наведении")}
    {section("Что требует внимания", anomalies_table_html(anomalies, 50), "Сначала случаи «выдали, но ЛИС не подтверждает использование», затем остальные расхождения")}
    '''


def page_labs(df: pd.DataFrame, s: dict[str, pd.DataFrame], anomalies: pd.DataFrame) -> str:
    monthly, labs, customers = s["monthly"], s["labs"], s["customers"]
    problem_labs = anomalies[anomalies["severity"].isin(["Critical", "High"])]["lab_key"].nunique() if not anomalies.empty else 0
    total_gap = max(float(df["OneC_Issued"].sum() - df["LIS_Consumed"].sum()), 0.0)
    cards = ''.join([
        kpi_card("Лабораторий / городов", fmt_num(labs["lab_key"].nunique()), "в текущем отчете", icon="L"),
        kpi_card("Выдано 1С", fmt_num(df["OneC_Issued"].sum()), "за весь доступный период", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(df["LIS_Consumed"].sum()), "за весь доступный период", icon="LIS"),
        kpi_card("Не подтверждено в ЛИС*", fmt_num(total_gap), "положительная часть 1С − ЛИС", icon="Δ"),
        kpi_card("Заказчиков", fmt_num(df["bin"].nunique()), "уникальных БИН", icon="C"),
        kpi_card("Проблемных лабораторий", fmt_num(problem_labs), "критично / очень критично", icon="!"),
    ])
    if len(labs) > 1:
        main_chart = chart_dual_lab_bar(labs)
        lab_problem = anomalies[anomalies["severity"].isin(["Critical", "High"])].groupby("Лаборатория").size().reset_index(name="signals") if not anomalies.empty else pd.DataFrame()
        top_problem = chart_top_bar(lab_problem, "Лаборатория", "signals", top_n=10, x_title="Количество критичных сигналов", y_title="Лаборатория / город", max_label=28, height=385)
        table = metric_table(labs, {"lab_display_name": "Лаборатория / город", "issued": "Выдано 1С, ед.", "consumed": "Использовано ЛИС, ед.", "calc_balance": "Расчетная дельта*, ед.", "utilization_pct": "Доля использования, %", "customers": "Заказчиков", "containers": "Материалов"}, sort_by="calc_balance", max_rows=100)
        title = "Выдано и использовано по лабораториям / городам"
    else:
        main_chart = chart_monthly_grouped(monthly)
        c = customers.copy()
        c["unaccounted"] = (c["issued"] - c["consumed"]).clip(lower=0)
        top_problem = chart_top_bar(c, "partner_name", "unaccounted", top_n=10, x_title="Не подтверждено в ЛИС, ед.", y_title="Заказчик", max_label=32, height=385)
        table = metric_table(customers, {"partner_name": "Заказчик", "bin": "БИН", "issued": "Выдано 1С, ед.", "consumed": "Использовано ЛИС, ед.", "calc_balance": "Расчетная дельта*, ед.", "utilization_pct": "Доля использования, %", "risk": "Риск"}, sort_by="calc_balance", max_rows=CFG.html_table_rows)
        title = "Выдано и использовано по месяцам"
    city_dynamics = ""
    if len(labs) > 1:
        city_dynamics = f'''
        <div class="grid-2-eq">{section("Динамика риска: топ-лаборатории линиями", fig_html(chart_city_monthly_lines(df)), "Показаны только лаборатории / города с наибольшим суммарным риском, чтобы график не превращался в кашу")}{section("Динамика риска: все лаборатории тепловой картой", fig_html(chart_city_monthly_heatmap(df)), "Темнее цвет — больше объема 1С, не подтвержденного использованием ЛИС в конкретный месяц")}</div>
        '''
    return f'''
    <div class="kpis">{cards}</div>
    {city_dynamics}
    <div class="grid-2">{section(title, fig_html(main_chart))}{section("Лаборатории / объекты с наибольшим числом критичных сигналов", fig_html(top_problem))}</div>
    {section("Заказчики: где большой объем выдачи не подтвержден в ЛИС", fig_html(chart_scatter(customers)), "Правый верхний угол — главный риск: выдали много и большая доля не отражена в ЛИС.")}
    {section("Детальная таблица", table)}
    '''


def pick_customer(customers: pd.DataFrame) -> pd.Series | None:
    if customers.empty:
        return None
    d = customers.copy()
    d["_risk"] = d.get("severity_rank", 0)
    d["_abs"] = d["calc_balance"].abs()
    return d.sort_values(["_risk", "_abs", "issued"], ascending=[False, False, False]).iloc[0]


def page_customers(df: pd.DataFrame, s: dict[str, pd.DataFrame], anomalies: pd.DataFrame) -> str:
    customers=s["customers"].copy(); total=len(customers); active=int((customers["issued"]+customers["consumed"]>0).sum()); problem=int((customers["severity_rank"]>=3).sum()); matched=int((customers["status"]=="Matched").sum()); avg_util=np.average(customers.loc[customers["issued"]>0,"utilization_pct"],weights=customers.loc[customers["issued"]>0,"issued"]) if (customers["issued"]>0).any() else np.nan; critical_customers=int((customers["severity_rank"]>=4).sum()); customers["unaccounted"]=(customers["issued"]-customers["consumed"]).clip(lower=0)
    cards=''.join([kpi_card("Всего заказчиков",fmt_num(total),"БИН × лаборатория",icon="C"),kpi_card("Активных",fmt_num(active),"есть движение",icon="A"),kpi_card("Проблемных",fmt_num(problem),"критично / очень критично",icon="!"),kpi_card("Matched",fmt_num(matched),"на последнем месяце",icon="M"),kpi_card("Средняя доля использования",fmt_pct(avg_util),"взвешено по выдаче",icon="%"),kpi_card("Очень критичных",fmt_num(critical_customers),"первый приоритет проверки",icon="!!")])
    table=metric_table(customers,{"bin":"БИН","partner_name":"Заказчик","lab_display_name":"Лаборатория","issued":"Выдано 1С, ед.","consumed":"Использовано ЛИС, ед.","unaccounted":"Не подтверждено в ЛИС, ед.","calc_balance":"Расчетная дельта*, ед.","utilization_pct":"Доля использования, %","status":"MatchStatus","risk":"Риск"},sort_by="severity_rank",max_rows=CFG.html_table_rows)
    top=pick_customer(customers); trend_section=""
    if top is not None:
        cust_df=df[(df["lab_key"]==top["lab_key"]) & (df["bin"]==top["bin"])]; trend_section=section(f"Динамика выбранного заказчика: {short_label(top['partner_name'],60)}",fig_html(chart_customer_monthly(cust_df)),"Автоматически выбран заказчик с наивысшим текущим риском")
    return f'''
    <div class="kpis">{cards}</div>
    {section("Карта риска заказчиков: выдача 1С vs неотраженная доля в ЛИС", fig_html(chart_scatter(customers)), "Чем правее и выше точка, тем важнее проверка. Главный сценарий — выдали много, использовали мало/ноль.")}
    <div class="grid-wide-side">{section("Реестр заказчиков", table, "Полные наименования сохранены в таблице")}{section("Распределение по MatchStatus", fig_html(chart_status_donut(customers)))}</div>
    <div class="grid-2-eq">{section("Топ заказчиков по объему, не подтвержденному в ЛИС", fig_html(chart_top_bar(customers,"partner_name","unaccounted",top_n=12,x_title="Не подтверждено в ЛИС, ед.",y_title="Заказчик",max_label=32,height=390)))}{trend_section}</div>
    '''


def page_customer_card(df: pd.DataFrame, s: dict[str, pd.DataFrame], anomalies: pd.DataFrame) -> str:
    customers = s["customers"]
    top = pick_customer(customers)
    if top is None:
        return '<div class="empty">Нет заказчиков для карточки</div>'
    cust_df = df[(df["lab_key"] == top["lab_key"]) & (df["bin"] == top["bin"])].copy()
    cust_anom = anomalies[(anomalies["lab_key"] == top["lab_key"]) & (anomalies["БИН"] == top["bin"])] if not anomalies.empty else pd.DataFrame()
    materials = cust_df.groupby(["container_group", "container_display_name"], as_index=False).agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    materials["calc_balance"] = materials["issued"] - materials["consumed"]
    materials["utilization_pct"] = np.where(materials["issued"] > 0, materials["consumed"] / materials["issued"] * 100, np.nan)
    cm = cust_df.groupby("month").agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), containers=("container_group", "nunique")).reset_index().sort_values("month")
    cm["calc_balance"] = (cm["issued"] - cm["consumed"]).cumsum()
    cm["utilization_pct"] = np.where(cm["issued"] > 0, cm["consumed"] / cm["issued"] * 100, np.nan)
    info = info_strip([
        ("Заказчик", str(top["partner_name"])),
        ("БИН", str(top["bin"])),
        ("Лаборатория", str(top["lab_display_name"])),
        ("MatchStatus", str(top.get("status", "—"))),
        ("Риск", str(top.get("risk", "—") or "Низкий/нет сигнала")),
        ("Последний месяц", cust_df["month"].max().strftime("%Y-%m") if not cust_df.empty else "—"),
    ])
    cards = ''.join([
        kpi_card("Выдано 1С", fmt_num(top["issued"]), "за весь период", icon="1С"),
        kpi_card("Использовано ЛИС", fmt_num(top["consumed"]), "за весь период", icon="LIS"),
        kpi_card("Расчетная дельта*", fmt_num(top["calc_balance"]), "накопительно", icon="Δ"),
        kpi_card("Доля использования", fmt_pct(top["utilization_pct"]), "ЛИС / 1С", icon="%"),
        kpi_card("Материалов", fmt_num(top["containers"]), "в истории", icon="M"),
        kpi_card("Аномалий", fmt_num(len(cust_anom)), "текущих сигналов", icon="!"),
    ])
    bal_chart = chart_top_bar(materials, "container_group", "calc_balance", top_n=10, x_title="Расчетная дельта*, ед.", y_title="Код материала", max_label=20, height=390)
    # Hover по коду дополняем полным наименованием через отдельный более информативный график.
    if not materials.empty:
        d = materials.copy(); d["label"] = d["container_group"].astype(str); d = d.nlargest(10, "calc_balance").sort_values("calc_balance")
        bal_chart = go.Figure(go.Bar(x=d["calc_balance"], y=d["label"], orientation="h", customdata=d[["container_display_name"]], text=[fmt_num(v) for v in d["calc_balance"]], textposition="outside", cliponaxis=False, hovertemplate="%{customdata[0]}<br>Расчетная дельта*: %{x:,.0f} ед.<extra></extra>"))
        bal_chart = add_plotly_defaults(bal_chart, height=390, x_title="Расчетная дельта*, ед.", y_title="Код материала", left_margin=90, legend=False)
    material_table = metric_table(materials, {"container_group":"Код материала","container_display_name":"Материал","issued":"Выдано 1С, ед.","consumed":"Использовано ЛИС, ед.","calc_balance":"Расчетная дельта*, ед.","utilization_pct":"Доля использования, %"}, sort_by="calc_balance", max_rows=100)
    month_table = metric_table(cm, {"month":"Месяц","issued":"Выдано 1С, ед.","consumed":"Использовано ЛИС, ед.","calc_balance":"Накопительная расчетная дельта*, ед.","utilization_pct":"Доля использования, %","containers":"Материалов"}, sort_by="month", ascending=False, max_rows=100)
    return f'''
    <div class="notice">Карточка в автономном HTML автоматически показывает заказчика с наивысшим текущим риском. Полный реестр всех заказчиков находится на странице 03 и в Excel.</div>
    {info}
    <div class="kpis">{cards}</div>
    <div class="grid-2">
      {section("Движение по месяцам", fig_html(chart_customer_monthly(cust_df)))}
      {section("Расчетная дельта* по материалам", fig_html(bal_chart), "На оси используются короткие коды; полное название — при наведении")}
    </div>
    {section("История по материалам", material_table)}
    {section("История по месяцам", month_table)}
    '''


def page_materials(df: pd.DataFrame, s: dict[str, pd.DataFrame], anomalies: pd.DataFrame) -> str:
    materials=s["materials"].copy()
    if materials.empty: return '<div class="empty">Нет данных по материалам</div>'
    selected=materials.sort_values("issued",ascending=False).iloc[0]; cg=selected["container_group"]; md=df[df["container_group"]==cg].copy(); mm=md.groupby("month").agg(issued=("OneC_Issued","sum"),consumed=("LIS_Consumed","sum")).reset_index().sort_values("month"); mm["period_delta"]=mm["issued"]-mm["consumed"]; mm["calc_balance"]=mm["period_delta"].cumsum(); mm["utilization_pct"]=np.where(mm["issued"]>0,mm["consumed"]/mm["issued"]*100,np.nan)
    labs_mat=md.groupby("lab_display_name",as_index=False).agg(issued=("OneC_Issued","sum"),consumed=("LIS_Consumed","sum")); labs_mat["unaccounted"]=(labs_mat["issued"]-labs_mat["consumed"]).clip(lower=0); cust_mat=md.groupby(["partner_name","bin"],as_index=False).agg(issued=("OneC_Issued","sum"),consumed=("LIS_Consumed","sum")); cust_mat["unaccounted"]=(cust_mat["issued"]-cust_mat["consumed"]).clip(lower=0); materials["unaccounted"]=(materials["issued"]-materials["consumed"]).clip(lower=0)
    problem_customers=anomalies[(anomalies["Контейнер"].eq(selected["container_display_name"])) & (anomalies["severity"].isin(["Critical","High"]))]["БИН"].nunique() if not anomalies.empty else 0
    cards=''.join([kpi_card("Выдано 1С",fmt_num(df["OneC_Issued"].sum()),"по всем материалам",icon="1С"),kpi_card("Использовано ЛИС",fmt_num(df["LIS_Consumed"].sum()),"по всем материалам",icon="LIS"),kpi_card("Не подтверждено в ЛИС*",fmt_num(max(df["OneC_Issued"].sum()-df["LIS_Consumed"].sum(),0)),"положительная часть 1С − ЛИС",icon="Δ"),kpi_card("Материалов",fmt_num(materials["container_group"].nunique()),"типов",icon="M"),kpi_card("Лабораторий",fmt_num(df["lab_key"].nunique()),"в отчете",icon="L"),kpi_card("Проблемных заказчиков",fmt_num(problem_customers),f"по материалу {cg}",icon="!")])
    lab_chart=chart_top_bar(labs_mat,"lab_display_name","unaccounted",top_n=10,x_title="Не подтверждено в ЛИС, ед.",y_title="Лаборатория / город",max_label=28,height=390)
    dmat=materials.nlargest(12,"issued").sort_values("issued").copy(); material_chart=go.Figure(go.Bar(x=dmat["issued"],y=dmat["container_group"],orientation="h",customdata=np.stack([dmat["container_display_name"],dmat["consumed"],dmat["unaccounted"]],axis=-1),text=[fmt_num(v) for v in dmat["issued"]],textposition="outside",cliponaxis=False,hovertemplate="<b>%{customdata[0]}</b><br>Код: %{y}<br>Выдано 1С: %{x:,.0f} ед.<br>Использовано ЛИС: %{customdata[1]:,.0f} ед.<br>Не подтверждено в ЛИС: %{customdata[2]:,.0f} ед.<extra></extra>")); material_chart=add_plotly_defaults(material_chart,height=410,x_title="Объем выдачи по 1С, ед.",y_title="Код материала",left_margin=80,legend=False)
    cust_chart=chart_top_bar(cust_mat,"partner_name","unaccounted",top_n=10,x_title="Не подтверждено в ЛИС, ед.",y_title="Заказчик",max_label=32,height=400)
    return f'''
    <div class="kpis">{cards}</div>
    {info_strip([("Выбранный материал для детальной динамики",f"{cg} — {selected['container_display_name']}"),("Принцип выбора","Материал с максимальным объемом выдачи")])}
    <div class="grid-2">{section("Динамика по выбранному материалу",fig_html(chart_monthly_lines(mm)),"Полное название вынесено выше")}{section("Где по выбранному материалу больше всего не подтверждено в ЛИС",fig_html(lab_chart))}</div>
    <div class="grid-2-eq">{section("Топ материалов по объему выдачи",fig_html(material_chart),"На оси только код; полное медицинское название — при наведении")}{section("Топ заказчиков по не подтвержденному в ЛИС объему",fig_html(cust_chart))}</div>
    {section("Материалы",metric_table(materials,{"container_group":"Код","container_display_name":"Материал","issued":"Выдано 1С, ед.","consumed":"Использовано ЛИС, ед.","unaccounted":"Не подтверждено в ЛИС, ед.","calc_balance":"Расчетная дельта*, ед.","utilization_pct":"Доля использования, %","customers":"Заказчиков","labs":"Лабораторий"},sort_by="issued",max_rows=CFG.html_table_rows))}
    '''


def page_procedure(proc: pd.DataFrame, internal: pd.DataFrame) -> str:
    detail, monthly = procedure_summary(proc)
    if detail.empty:
        return '<div class="empty">Нет данных по процедурным кабинетам</div>'
    total_1c = float(detail["1C"].sum())
    total_lis = float(detail["LIS"].sum())
    positive_gap = float((detail["1C"] - detail["LIS"]).clip(lower=0).sum())
    reverse_gap = float((detail["LIS"] - detail["1C"]).clip(lower=0).sum())
    rooms = detail["procedure_uid"].nunique()
    problematic = detail.assign(gap=(detail["1C"] - detail["LIS"]).clip(lower=0)).groupby("procedure_uid")["gap"].sum().gt(0).sum()
    cards = ''.join([
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
    dtable = dtable.sort_values("Не подтверждено в ЛИС", ascending=False).rename(columns={"month": "Месяц", "lab_display_name": "Лаборатория", "procedure_uid": "UID ПК", "procedure_room_name": "Процедурный кабинет", "container_group": "Код материала", "container_display_name": "Материал"})
    internal_html = ""
    if internal is not None and not internal.empty:
        idf = internal.copy().sort_values(["month", "lab_display_name", "metric_sort"] if "metric_sort" in internal.columns else ["month"]).rename(columns={"month": "Месяц", "lab_display_name": "Лаборатория", "metric": "Метрика", "amount": "Количество", "rows": "Строк источника"})
        internal_html = section("Внутреннее потребление 1С", dataframe_html(idf[[c for c in ["Месяц", "Лаборатория", "Метрика", "Количество", "Строк источника"] if c in idf.columns]], 120), "Отдельный поток; не суммируется с партнерскими фактами")
    return f'''
    <div class="kpis">{cards}</div>
    {section("1С vs ЛИС по месяцам", fig_html(chart_procedure_monthly(monthly)), "Общая динамика процедурных кабинетов")}
    {section("Процедурные кабинеты: двустороннее расхождение 1С ↔ ЛИС", fig_html(chart_procedure_top(detail)), "Справа — основной риск: 1С выше ЛИС. Слева — обратное расхождение: ЛИС выше 1С")}
    {section("Расхождение по материалам", fig_html(chart_procedure_materials(detail)), "Основной ряд — 1С выше ЛИС; обратная сторона показана отдельно")}
    {section("Процедурные кабинеты — детальная таблица", dataframe_html(dtable, CFG.html_table_rows), "Таблица расположена внизу на всю ширину")}{internal_html}
    '''


def page_anomalies(df: pd.DataFrame, anomalies: pd.DataFrame, exceptions: pd.DataFrame) -> str:
    critical=int((anomalies["severity"]=="Critical").sum()) if not anomalies.empty else 0; high=int((anomalies["severity"]=="High").sum()) if not anomalies.empty else 0; medium=int((anomalies["severity"]=="Medium").sum()) if not anomalies.empty else 0; primary=int((anomalies["Направление"]=="1С > ЛИС").sum()) if not anomalies.empty and "Направление" in anomalies.columns else 0; reverse=int((anomalies["Направление"]=="ЛИС > 1С").sum()) if not anomalies.empty and "Направление" in anomalies.columns else 0
    cards=''.join([kpi_card("Всего сигналов",fmt_num(len(anomalies)),"текущий снимок",icon="!"),kpi_card("Очень критично",fmt_num(critical),"первый приоритет",icon="!!"),kpi_card("Критично",fmt_num(high),"требует проверки",icon="H"),kpi_card("Внимание",fmt_num(medium),"контроль сверки",icon="M"),kpi_card("Главный риск 1С > ЛИС",fmt_num(primary),"выдали больше, чем отражено",icon="1С"),kpi_card("Обратные ЛИС > 1С",fmt_num(reverse),"аномалия меньшего приоритета",icon="LIS")])
    monthly_signals=build_monthly_anomaly_signals(df); exc=exceptions.copy()
    if not exc.empty: exc=exc.sort_values("amount",ascending=False).rename(columns={"month":"Месяц","lab_display_name":"Лаборатория","bin":"БИН","container_display_name":"Материал","side":"Сторона","party_name":"Участник","amount":"Количество"})
    return f'''
    <div class="notice"><b>Приоритет риска:</b> сначала случаи, где 1С показывает выдачу, а ЛИС показывает мало или ноль использования. ЛИС &gt; 1С остается аномалией сверки, но не главным риском невозврата.</div>
    <div class="kpis">{cards}</div>
    <div class="grid-2-eq">{section("Аномалии по причинам",fig_html(chart_anomaly_types(anomalies)))}{section("Динамика аналитических сигналов",fig_html(chart_anomaly_trend(monthly_signals)),"Очень критично = первый приоритет; критично = второй; внимание = обратные/прочие расхождения")}</div>
    {section("Лаборатория × причина риска",fig_html(chart_anomaly_heatmap(anomalies)),"Число в ячейке — количество связок заказчик × материал с сигналом. Цвет показывает количество.")}
    {section("Реестр аномалий",anomalies_table_html(anomalies,CFG.html_table_rows))}
    {section("Исходные исключения LIS / 1С",dataframe_html(exc[[c for c in ["Месяц","Лаборатория","БИН","Материал","Сторона","Участник","Количество"] if c in exc.columns]],100),"Данные из fact_exceptions; полный список — в Excel")}
    '''


def build_watchlist(df: pd.DataFrame, anomalies: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    latest = df["month"].max()
    base = (
        df.groupby(["lab_key", "lab_display_name", "bin", "partner_name"], dropna=False)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), materials=("container_group", "nunique"))
        .reset_index()
    )
    base["unaccounted"] = (base["issued"] - base["consumed"]).clip(lower=0)
    base["utilization_pct"] = np.where(base["issued"] > 0, base["consumed"] / base["issued"] * 100.0, np.nan)

    latest_df = df[df["month"] == latest].copy()
    lm = latest_df.groupby(["lab_key", "bin"], dropna=False).agg(last_issued=("OneC_Issued", "sum"), last_consumed=("LIS_Consumed", "sum")).reset_index()
    lm["last_gap"] = (lm["last_issued"] - lm["last_consumed"]).clip(lower=0)
    lm["last_utilization_pct"] = np.where(lm["last_issued"] > 0, lm["last_consumed"] / lm["last_issued"] * 100.0, np.nan)
    base = base.merge(lm, on=["lab_key", "bin"], how="left")

    if anomalies is not None and not anomalies.empty:
        ac = anomalies.copy()
        ac["severity_rank"] = ac["severity"].map(SEVERITY_RANK).fillna(0)
        agg = ac.groupby(["lab_key", "БИН"], as_index=False).agg(
            severity_rank=("severity_rank", "max"),
            reasons=("Тип аномалии", uniq_text),
            why=("Почему", uniq_text),
        ).rename(columns={"БИН": "bin"})
        base = base.merge(agg, on=["lab_key", "bin"], how="left")
    else:
        base["severity_rank"] = 0
        base["reasons"] = ""
        base["why"] = ""

    def label(r):
        issued = float(r.get("last_issued") or 0)
        consumed = float(r.get("last_consumed") or 0)
        last_util = r.get("last_utilization_pct")
        total_gap = float(r.get("unaccounted") or 0)
        if issued > 0 and consumed == 0:
            return "Срочно: выдали, а в ЛИС 0"
        if issued > 0 and pd.notna(last_util) and last_util <= CFG.critical_utilization_pct:
            return "Срочно: очень низкое использование после выдачи"
        if issued > 0 and pd.notna(last_util) and last_util <= CFG.high_utilization_pct:
            return "Приоритет: низкое использование после выдачи"
        if total_gap > 0:
            return "Нужно проверить накопленный неиспользованный объем"
        return "Контроль"

    base["priority_label"] = base.apply(label, axis=1)
    base["priority_score"] = base["severity_rank"].fillna(0) * 1_000_000 + base["last_gap"].fillna(0) * 10 + base["unaccounted"].fillna(0)
    work = base[(base["severity_rank"].fillna(0) >= 3) | (base["last_gap"].fillna(0) > 0)].copy()
    work = work.sort_values(["priority_score", "last_gap", "unaccounted"], ascending=[False, False, False])
    return work


def page_watchlist(df: pd.DataFrame, anomalies: pd.DataFrame) -> str:
    work = build_watchlist(df, anomalies)
    if work.empty:
        return '<div class="empty">Нет объектов для оперативной отработки</div>'
    critical_n = int((work["severity_rank"].fillna(0) >= 4).sum())
    high_n = int((work["severity_rank"].fillna(0) >= 3).sum())
    zero_lis = int(((work["last_issued"].fillna(0) > 0) & (work["last_consumed"].fillna(0) == 0)).sum())
    total_gap = float(work["last_gap"].fillna(0).sum())
    cards = ''.join([
        kpi_card("К отработке сейчас", fmt_num(len(work)), "заказчик × лаборатория", icon="!"),
        kpi_card("Очень критично", fmt_num(critical_n), "нужно проверить в первую очередь", icon="!!"),
        kpi_card("Критично и выше", fmt_num(high_n), "первый рабочий контур", icon="H"),
        kpi_card("Выдали, а ЛИС = 0", fmt_num(zero_lis), "самый опасный сценарий", icon="0"),
        kpi_card("Не подтверждено в ЛИС за последний месяц", fmt_num(total_gap), "суммарный месячный объем", icon="Δ"),
        kpi_card("Подход", "ежемесячная отработка", "страница для быстрого обзвона / проверки", icon="✓"),
    ])
    chart_top = chart_top_bar(work, "partner_name", "last_gap", top_n=15, x_title="Не подтверждено в ЛИС за последний месяц, ед.", y_title="Заказчик", max_label=34, height=430)
    city = work.groupby("lab_display_name", as_index=False).agg(last_gap=("last_gap", "sum"), customers=("bin", "nunique"))
    city_chart = chart_top_bar(city, "lab_display_name", "last_gap", top_n=12, x_title="Не подтверждено в ЛИС за последний месяц, ед.", y_title="Лаборатория / город", max_label=28, height=430)
    tbl = work[[c for c in ["lab_display_name", "partner_name", "bin", "last_issued", "last_consumed", "last_gap", "issued", "consumed", "unaccounted", "utilization_pct", "priority_label", "reasons", "why"] if c in work.columns]].copy()
    tbl = tbl.rename(columns={
        "lab_display_name": "Лаборатория / город",
        "partner_name": "Заказчик",
        "bin": "БИН",
        "last_issued": "Выдано 1С за последний месяц, ед.",
        "last_consumed": "Использовано ЛИС за последний месяц, ед.",
        "last_gap": "Не подтверждено в ЛИС за последний месяц, ед.",
        "issued": "Выдано 1С за весь период, ед.",
        "consumed": "Использовано ЛИС за весь период, ед.",
        "unaccounted": "Не подтверждено в ЛИС за весь период, ед.",
        "utilization_pct": "Доля использования за весь период, %",
        "priority_label": "Почему в списке",
        "reasons": "Тип сигнала",
        "why": "Обоснование",
    })
    return f'''
    <div class="notice"><b>Страница для отработки:</b> здесь собраны заказчики, у которых есть текущий месячный риск «1С выдали, а ЛИС не подтверждает использование». Это страница-очередь для ежемесячной проверки, чтобы не просматривать весь отчет вручную.</div>
    <div class="kpis">{cards}</div>
    <div class="grid-2-eq">{section("Топ заказчиков к отработке", fig_html(chart_top), "Главный фокус — где в последнем месяце выдали много, а ЛИС отразила мало или ноль")}{section("Где сосредоточен риск по лабораториям / городам", fig_html(city_chart), "Помогает понять, какие площадки проверять в первую очередь")}</div>
    {section("Реестр заказчиков для отработки", dataframe_html(tbl, CFG.html_table_rows), "Список пригоден для ежемесячного обзвона, проверки возвратов и сверки с площадками")}
    '''


def mapping_kpis(cov: pd.DataFrame, conflicts: pd.DataFrame, qi: pd.DataFrame) -> str:
    cards = []
    for source in ["LIS", "1C"]:
        d = cov[cov["source"].astype(str).str.upper().eq(source.upper())] if not cov.empty and "source" in cov.columns else pd.DataFrame()
        if d.empty:
            continue
        total = d["rows"].sum()
        mapped = d[d["mapping_scope"].astype(str).str.lower().str.contains("mapped", na=False) & ~d["mapping_scope"].astype(str).str.lower().str.contains("unmapped", na=False)]["rows"].sum()
        pct = mapped / total * 100 if total else np.nan
        cards.append(kpi_card(f"{source} mapping", fmt_pct(pct), f"{fmt_num(mapped)} из {fmt_num(total)} строк", icon="M"))
    # Дополнительные карточки из фактических таблиц.
    unmapped_lis_rows = cov[(cov["source"].astype(str).str.upper() == "LIS") & cov["mapping_scope"].astype(str).str.lower().str.contains("unmapped", na=False)]["rows"].sum() if not cov.empty else 0
    unmapped_1c_rows = cov[(cov["source"].astype(str).str.upper() == "1C") & cov["mapping_scope"].astype(str).str.lower().str.contains("unmapped", na=False)]["rows"].sum() if not cov.empty else 0
    cards.extend([
        kpi_card("Unmapped LIS", fmt_num(unmapped_lis_rows), "строк", icon="LIS"),
        kpi_card("Unmapped 1C", fmt_num(unmapped_1c_rows), "строк", icon="1С"),
        kpi_card("Mapping conflicts", fmt_num(len(conflicts)), "записей конфликтов", icon="!"),
        kpi_card("DQ issues", fmt_num(qi["rows"].sum() if "rows" in qi.columns else len(qi)), "затронутых строк", icon="DQ"),
    ])
    return '<div class="kpis">' + ''.join(cards[:6]) + '</div>'


def page_quality(all_data: dict[str, pd.DataFrame]) -> str:
    cov = all_data["mapping_coverage"].copy(); qi = all_data["quality_issues"].copy(); funnel = all_data["quality_funnel"].copy(); conflicts = all_data["mapping_conflicts"].copy(); ul = all_data["unmapped_lis"].copy(); uo = all_data["unmapped_onec"].copy()
    qitable = qi.rename(columns={"source":"Источник","issue_type":"Тип проблемы","severity":"Серьезность","rows":"Строки","quantity":"Количество","details":"Детали"})
    ult = ul.rename(columns={"lis_container_code":"Код LIS","lis_container_name_raw":"Материал LIS","lab_display_name":"Лаборатория","rows_count":"Строк","total_consumed":"Использовано","unique_bins":"БИН","unique_hospitals":"Организаций","months":"Месяцы"})
    uot = uo.rename(columns={"onec_mdm_key":"MDM 1C","onec_nomenclature_name":"Номенклатура 1C","onec_article":"Артикул","lab_display_name":"Лаборатория","onec_warehouse_group":"Складская группа","rows_count":"Строк","total_issued":"Выдано","unique_bins":"БИН","unique_counterparties":"Контрагентов","months":"Месяцы"})
    return f'''
    <div class="notice">В текущих DQ-витринах нет месячного измерения для большинства показателей качества. Поэтому страница показывает фактический снимок качества, а не искусственно придуманную динамику.</div>
    {mapping_kpis(cov, conflicts, qi)}
    <div class="grid-3">
      {section("Quality funnel", fig_html(chart_quality_funnel(funnel)))}
      {section("Проблемы качества по типам", fig_html(chart_quality_issues(qi)))}
      {section("Покрытие маппинга", fig_html(chart_mapping_coverage(cov)))}
    </div>
    <div class="grid-2-eq">
      {section("Unmapped LIS", dataframe_html(ult, 35))}
      {section("Unmapped 1C MDM", dataframe_html(uot, 35))}
    </div>
    {section("Quality issues", dataframe_html(qitable, 200))}
    {section("Конфликты маппинга", dataframe_html(conflicts, 100))}
    '''


def page_method(source_root: Path, scope_label: str, monthly: pd.DataFrame) -> str:
    pmin=monthly["month"].min().strftime("%Y-%m") if not monthly.empty else "—"; pmax=monthly["month"].max().strftime("%Y-%m") if not monthly.empty else "—"
    return f'''
    <section class="panel method"><div class="section-head"><h2>Методика, определения и логика критичности</h2></div>
      <p><b>Источник:</b> {html.escape(str(source_root))}</p><p><b>Область отчета:</b> {html.escape(scope_label)}</p><p><b>Доступный период:</b> {pmin} — {pmax}</p>
      <h3>1. Базовые показатели</h3><ul>
        <li><b>Выдано 1С</b> — объем выдачи/реализации из подготовленной витрины 1С.</li>
        <li><b>Использовано ЛИС</b> — фактическое использование из подготовленной витрины ЛИС.</li>
        <li><b>Дельта месяца*</b> = Выдано 1С − Использовано ЛИС.</li>
        <li><b>Накопительная расчетная дельта*</b> = накопленная выдача − накопленное использование. До подтверждения начальных остатков, возвратов, корректировок и перемещений это не физический остаток.</li>
        <li><b>Не подтверждено в ЛИС</b> = max(1С − ЛИС, 0). Это главный оперативный индикатор риска.</li>
      </ul>
      <h3>2. Логика критичности</h3>
      <p>Главная плохая сторона для УРМ — <b>1С &gt; ЛИС</b>: материалы выданы, но их использование в ЛИС отражено не полностью. Это не доказывает физический невозврат, но такие случаи проверяются первыми.</p>
      <ul>
        <li><b>Очень критично:</b> есть выдача 1С и ЛИС = 0; либо в ЛИС отражено не более {CFG.critical_utilization_pct:.0f}% выданного; либо накопительная расчетная дельта эквивалентна минимум {CFG.critical_stock_months:.0f} месяцам среднего потребления.</li>
        <li><b>Критично:</b> отражено более {CFG.critical_utilization_pct:.0f}%, но не более {CFG.high_utilization_pct:.0f}% выдачи; либо расчетная дельта соответствует {CFG.high_stock_months:.0f}–{CFG.critical_stock_months:.0f} месяцам среднего потребления; либо есть 1C_only.</li>
        <li><b>Внимание:</b> ЛИС показывает использование без выдачи 1С или ЛИС заметно выше 1С. Это аномалия сверки, но не основной сценарий «выдали много — использовали мало/ноль».</li>
      </ul>
      <h3>3. Карта риска заказчиков</h3><ul><li>Ось X — объем выдачи по 1С.</li><li>Ось Y — доля выданного объема, не подтвержденная ЛИС.</li><li>Правый верхний угол — самый высокий приоритет проверки.</li></ul><h3>3.1. Страница «К отработке»</h3><ul><li>В этот список попадают заказчики, у которых в последнем месяце есть положительное значение <code>1С − ЛИС</code> либо уже сформирован критичный / очень критичный сигнал.</li><li>Это не окончательное доказательство невозврата, а очередь на ежемесячную проверку: обзвон, запрос документов, проверка возвратов, корректировок и фактического движения.</li></ul>
      <h3>4. Ограничения</h3><ul><li>Пороговые значения — аналитическая методика v3 и должны быть утверждены бизнесом.</li><li>HTML содержит управленческую аналитику; полная детализация — в Excel.</li><li>Отдельного поля city нет, поэтому уровень «город / лаборатория» определяется через lab_key / lab_display_name.</li><li>Факты разных гранулярностей не суммируются между собой как независимые данные.</li></ul>
    </section>
    '''



# ============================================================
# СБОРКА ОДНОГО HTML
# ============================================================

def build_report_html(
    df: pd.DataFrame,
    all_data: dict[str, pd.DataFrame],
    anomalies_all: pd.DataFrame,
    report_title: str,
    scope_label: str,
    scope_lab_key: str | None,
    source_root: Path,
    excel_href: str,
) -> str:
    anomalies = anomalies_all.copy()
    if scope_lab_key is not None and not anomalies.empty:
        anomalies = anomalies[anomalies["lab_key"] == scope_lab_key].copy()
    s = summaries(df, anomalies)

    proc = all_data["procedure"]
    internal = all_data["internal"]
    exc = all_data["exceptions"]
    if scope_lab_key is not None:
        proc = proc[proc["lab_key"] == scope_lab_key].copy()
        internal = internal[internal["lab_key"] == scope_lab_key].copy()
        exc = exc[exc["lab_key"] == scope_lab_key].copy()

    tabs = [
        ("overview", "01 Общая ситуация", page_overview(df, s, anomalies)),
        ("labs", "02 Города / лаборатории", page_labs(df, s, anomalies)),
        ("customers", "03 Заказчики", page_customers(df, s, anomalies)),
        ("customer-card", "04 Карточка заказчика", page_customer_card(df, s, anomalies)),
        ("materials", "05 Расходные материалы", page_materials(df, s, anomalies)),
        ("procedure", "06 Процедурные кабинеты", page_procedure(proc, internal)),
        ("anomalies", "07 Аномалии", page_anomalies(df, anomalies, exc)),
        ("quality", "08 Качество данных", page_quality(all_data)),
        ("watchlist", "09 К отработке", page_watchlist(df, anomalies)),
        ("method", "10 Методика", page_method(source_root, scope_label, s["monthly"])),
    ]

    nav = ''.join([f'<button class="nav-btn" data-tab="{tid}">{html.escape(label)}</button>' for tid, label, _ in tabs])
    content = ''.join([f'<div id="{tid}" class="tab">{body}</div>' for tid, _, body in tabs])
    latest_month = s["monthly"]["month"].max().strftime("%Y-%m") if not s["monthly"].empty else "—"
    min_month = s["monthly"]["month"].min().strftime("%Y-%m") if not s["monthly"].empty else "—"

    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report_title)}</title>
<style>{report_css()}</style>
<script>{get_plotlyjs()}</script>
<script>{PLOTLY_RU_LOCALE_JS}</script>
</head>
<body data-min-month="{min_month}" data-max-month="{latest_month}">
<div class="app">
  <aside class="side">
    <div class="brand">УРМ</div>
    <div class="brand-sub">Учет расходных материалов</div>
    {nav}
  </aside>
  <main class="main">
    <div class="top">
      <div class="title"><h1>{html.escape(report_title)}</h1><p>{html.escape(scope_label)}</p></div>
      <div class="updated">Последний месяц в данных<br><b>{latest_month}</b></div>
    </div>
    <div class="context">
      <div class="period-filter">
        <label class="period-field">Начало<input id="period-start" type="month" min="{min_month}" max="{latest_month}" value="{min_month}"></label>
        <label class="period-field">Конец<input id="period-end" type="month" min="{min_month}" max="{latest_month}" value="{latest_month}"></label>
        <button id="period-apply" class="period-btn">Применить к динамике</button><button id="period-all" class="period-btn">Весь период</button><button class="period-btn" data-period-months="3">3 мес.</button><button class="period-btn" data-period-months="6">6 мес.</button><button class="period-btn" data-period-months="12">12 мес.</button>
      </div>
      <div class="context-pill">Период графиков: <b id="period-current">{min_month} — {latest_month}</b></div>
      <div class="context-pill">Область: <b>{html.escape(scope_label)}</b></div>
      <a class="excel-link" href="{html.escape(excel_href)}">Открыть Excel с деталями</a>
      <div class="period-help">Фильтр периода меняет все графики с месячной осью. KPI и итоговые рейтинги автономного HTML остаются рассчитанными за весь загруженный пакет.</div>
    </div>
    <div class="notice"><b>Важно:</b> «Расчетная дельта*» — аналитическое приближение. До утверждения бизнес-правил она не является подтвержденным физическим остатком.</div>
    {content}
    <div class="footer">УРМ • автономный HTML-отчет v{VERSION} • исходные CSV не изменялись</div>
  </main>
</div>
<script>{report_js()}</script>
</body>
</html>'''


# ============================================================
# EXCEL
# ============================================================

def export_excel(path: Path, df: pd.DataFrame, all_data: dict[str, pd.DataFrame], anomalies: pd.DataFrame, lab_key: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scope = df.copy()
    scoped_anom = anomalies.copy()
    proc = all_data["procedure"]
    exc = all_data["exceptions"]
    internal = all_data["internal"]
    if lab_key is not None:
        proc = proc[proc["lab_key"] == lab_key].copy()
        exc = exc[exc["lab_key"] == lab_key].copy()
        internal = internal[internal["lab_key"] == lab_key].copy()
        if not scoped_anom.empty:
            scoped_anom = scoped_anom[scoped_anom["lab_key"] == lab_key].copy()
    s = summaries(scope, scoped_anom)
    proc_detail, _ = procedure_summary(proc)

    with pd.ExcelWriter(path, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        s["monthly"].to_excel(writer, sheet_name="01_Динамика", index=False)
        s["labs"].to_excel(writer, sheet_name="02_Лаборатории", index=False)
        s["customers"].sort_values(["severity_rank", "calc_balance"], ascending=[False, False]).to_excel(writer, sheet_name="03_Заказчики", index=False)
        s["materials"].sort_values("issued", ascending=False).to_excel(writer, sheet_name="04_Материалы", index=False)
        scoped_anom.head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="05_Аномалии", index=False)
        exc.head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="06_Исключения", index=False)
        proc_detail.head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="07_ПК", index=False)
        internal.head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="08_Внутр_потребление", index=False)
        scope.head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="09_Детальные_данные", index=False)
        if lab_key is None:
            all_data["quality_issues"].to_excel(writer, sheet_name="10_DQ_issues", index=False)
            all_data["mapping_coverage"].to_excel(writer, sheet_name="11_Mapping", index=False)
            all_data["mapping_conflicts"].to_excel(writer, sheet_name="12_Conflicts", index=False)
            all_data["unmapped_lis"].head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="13_Unmapped_LIS", index=False)
            all_data["unmapped_onec"].head(CFG.excel_max_detail_rows).to_excel(writer, sheet_name="14_Unmapped_1C", index=False)

        wb = writer.book
        fmt_header = wb.add_format({"bold": True, "bg_color": "#EAF2FF", "border": 1, "text_wrap": True, "valign": "top"})
        for ws in writer.sheets.values():
            rowmax = ws.dim_rowmax if ws.dim_rowmax is not None else 0
            colmax = ws.dim_colmax if ws.dim_colmax is not None else 0
            ws.freeze_panes(1, 0)
            if rowmax >= 0 and colmax >= 0:
                ws.autofilter(0, 0, rowmax, colmax)
            ws.set_row(0, 28, fmt_header)
            ws.set_column(0, colmax, 18)
            # Текстовые поля получают больше места; Excel сам позволяет расширить при необходимости.
            if colmax >= 2:
                ws.set_column(1, min(colmax, 4), 26)


# ============================================================
# ГЕНЕРАЦИЯ КОМПЛЕКТА
# ============================================================

def generate(input_root: Path, output_root: Path) -> None:
    missing = validate_structure(input_root)
    if missing:
        print("Не найдены обязательные файлы:")
        for p in missing:
            print(" -", p)
        raise SystemExit(2)

    print(f"URM HTML Report Generator v{VERSION}")
    print("[1/6] Читаю подготовленные витрины...")
    data = load_data(input_root)
    fact = enrich_fact(data["main"])
    anomalies = build_anomalies(fact)

    latest_month = fact["month"].max()
    period_tag = latest_month.strftime("%Y-%m") if pd.notna(latest_month) else "unknown"
    out = output_root / period_tag
    html_dir = out / "HTML"
    city_dir = html_dir / "Города_лаборатории"
    excel_dir = out / "Excel"
    city_excel_dir = excel_dir / "Города_лаборатории"
    meta_dir = out / "_metadata"
    for p in [html_dir, city_dir, excel_dir, city_excel_dir, meta_dir]:
        p.mkdir(parents=True, exist_ok=True)

    print("[2/6] Формирую общий HTML (9 страниц)...")
    general_html = build_report_html(
        fact, data, anomalies,
        report_title="УРМ: Общий аналитический отчет",
        scope_label="Вся компания / все лаборатории",
        scope_lab_key=None,
        source_root=input_root,
        excel_href="../Excel/УРМ_ОБЩИЙ.xlsx",
    )
    (html_dir / "УРМ_ОБЩИЙ.html").write_text(general_html, encoding="utf-8")

    print("[3/6] Формирую общий Excel...")
    export_excel(excel_dir / "УРМ_ОБЩИЙ.xlsx", fact, data, anomalies, None)

    labs = data["lab"].dropna(subset=["lab_key"]).copy()
    print(f"[4/6] Формирую отдельные отчеты по лабораториям: {len(labs)} шт.")
    manifest_rows = []
    for _, r in labs.iterrows():
        lab_key = str(r["lab_key"])
        lab_name = str(r.get("lab_display_name", lab_key))
        scoped = fact[fact["lab_key"] == lab_key].copy()
        if scoped.empty:
            continue
        base_name = safe_name(lab_name)
        html_path = city_dir / f"УРМ_{base_name}.html"
        excel_path = city_excel_dir / f"УРМ_{base_name}.xlsx"
        h = build_report_html(
            scoped, data, anomalies,
            report_title=f"УРМ: {lab_name}",
            scope_label=f"Отдельный отчет: {lab_name}",
            scope_lab_key=lab_key,
            source_root=input_root,
            excel_href=f"../../Excel/Города_лаборатории/{html.escape('УРМ_' + base_name + '.xlsx')}",
        )
        html_path.write_text(h, encoding="utf-8")
        export_excel(excel_path, scoped, data, anomalies, lab_key)
        manifest_rows.append({"lab_key": lab_key, "lab_name": lab_name, "html": str(html_path), "excel": str(excel_path), "rows": len(scoped)})
        print(f"   {lab_name}: HTML + Excel")

    print("[5/6] Сохраняю метаданные сборки...")
    pd.DataFrame(manifest_rows).to_csv(meta_dir / "manifest.csv", index=False, encoding="utf-8-sig")
    fingerprints = []
    for folder, files in REQUIRED_FILES.items():
        for file in files:
            p = input_root / folder / file
            fingerprints.append({"file": str(p), "size_bytes": p.stat().st_size, "sha256": sha256(p)})
    pd.DataFrame(fingerprints).to_csv(meta_dir / "source_fingerprints.csv", index=False, encoding="utf-8-sig")

    build_info = {
        "generator_version": VERSION,
        "input_root": str(input_root),
        "output_root": str(out),
        "latest_month": period_tag,
        "main_rows": int(len(fact)),
        "labs": int(fact["lab_key"].nunique()),
        "customers": int(fact["bin"].nunique()),
        "containers": int(fact["container_group"].nunique()),
        "anomalies": int(len(anomalies)),
        "pages": 9,
        "warning": "Расчетная дельта не является подтвержденным физическим остатком без утвержденной бизнес-логики.",
    }
    (meta_dir / "build_info.json").write_text(json.dumps(build_info, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[6/6] ГОТОВО")
    print("Отчеты:", out)
    print("Общий HTML:", html_dir / "УРМ_ОБЩИЙ.html")
    print("Общий Excel:", excel_dir / "УРМ_ОБЩИЙ.xlsx")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Генератор автономных HTML/Excel отчетов УРМ v3")
    p.add_argument("--input", required=False, help="Папка Витрины_УРМ_по_YYYY-MM")
    p.add_argument("--output", required=False, help="Корневая папка REPORTS")
    p.add_argument("--config", default="config.json", help="JSON config рядом со скриптом")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = script_dir / cfg_path
    cfg = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    input_root = Path(args.input or cfg.get("input_root", "")).expanduser()
    output_root = Path(args.output or cfg.get("output_root", "C:/URM_POC/REPORTS")).expanduser()
    if not str(input_root):
        print("Не указан input_root. Заполните config.json или используйте --input")
        raise SystemExit(2)
    if not input_root.exists():
        print("Папка с витринами не существует:", input_root)
        raise SystemExit(2)
    generate(input_root, output_root)


if __name__ == "__main__":
    main()
