"""Графики, изменённые по сравнению с генератором отчётов v4.

report_core.py остаётся копией генератора без правок; всё, что меняется
по решению бизнеса, живёт здесь и подключается в views/report.py.

Изменения:
  * chart_monthly_lines — контрастные цвета выдачи 1С и использования ЛИС.
  * chart_heatmap_lab_material — новая шкала цветов (2026-10-10):
      красный  — критически низкая доля использования (≤ 20 % или выдали, а в ЛИС 0);
      оранжевый — низкая (≤ 50 %); жёлтый — умеренная (< 75 %); зелёный — норма (75–100 %);
      фиолетовый — использовано больше, чем выдано (> 100 %): благоприятно, но требует сверки;
      синий — есть использование в ЛИС, а выдачи 1С нет.
    В генераторе > 100 % было красным, критически низкое — оранжевым, а ячейки
    без данных — зелёными. Теперь пустые ячейки остаются пустыми.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from urm.report_core import CFG, add_plotly_defaults, fmt_num, fmt_pct
from urm.report_core import chart_monthly_lines as _core_monthly_lines


def chart_monthly_lines(monthly: pd.DataFrame) -> go.Figure:
    """Не меняем расчёты/шкалу: только две похожие синие линии разделяем по цвету."""
    fig = _core_monthly_lines(monthly)
    for trace in fig.data:
        if trace.name == "Выдано 1С":
            trace.update(line=dict(color="#0072CE"), marker=dict(color="#0072CE"))
        elif trace.name == "Использовано ЛИС":
            trace.update(line=dict(color="#138A65"), marker=dict(color="#138A65"))
        elif trace.name == "Накопительная расчетная дельта*":
            trace.update(line=dict(color="#F03B3B"), marker=dict(color="#F03B3B"))
    return fig


NORMAL_FROM_PCT = 75.0  # нижняя граница «нормы», как в генераторе

# (код, цвет, подпись) — порядок кодов задаёт шкалу.
HEATMAP_LEVELS = (
    (0, "#d9f2e5", "75–100 % — норма"),
    (1, "#fde68a", f"{CFG.high_utilization_pct:.0f}–75 % — умеренно"),
    (2, "#fdba74", f"{CFG.critical_utilization_pct:.0f}–{CFG.high_utilization_pct:.0f} % — низко"),
    (3, "#fca5a5", f"≤ {CFG.critical_utilization_pct:.0f} % или 0 в ЛИС — критично"),
    (4, "#c4b5fd", "> 100 % — использовано больше выданного"),
    (5, "#93c5fd", "ЛИС есть, выдачи 1С нет"),
)


def heatmap_level(issued: np.ndarray, consumed: np.ndarray) -> np.ndarray:
    """Код цвета ячейки по выдаче и использованию. NaN — нет движения."""
    issued = np.asarray(issued, dtype="float64")
    consumed = np.asarray(consumed, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        util = np.where(issued > 0, consumed / issued * 100.0, np.nan)
    positive = issued > 0
    return np.select(
        [
            positive & ((consumed <= 0) | (util <= CFG.critical_utilization_pct)),
            positive & (util <= CFG.high_utilization_pct),
            positive & (util < NORMAL_FROM_PCT),
            positive & (util <= 100.0),
            positive & (util > 100.0),
            (~positive) & (consumed > 0),
        ],
        [3, 2, 1, 0, 4, 5],
        default=np.nan,
    )


def heatmap_legend_html() -> str:
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin:0 14px 4px 0;'
        f'font-size:12px;color:#475569"><span style="width:14px;height:14px;border-radius:3px;'
        f'background:{color};display:inline-block"></span>{label}</span>'
        for _, color, label in HEATMAP_LEVELS
    )
    return f'<div style="display:flex;flex-wrap:wrap;margin:2px 0 10px">{chips}</div>'


def chart_heatmap_lab_material(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return add_plotly_defaults(go.Figure(), height=360, x_title="Материал", y_title="Лаборатория")
    top_groups = df.groupby("container_group")["OneC_Issued"].sum().nlargest(10).index
    d = (
        df[df["container_group"].isin(top_groups)]
        .groupby(["lab_display_name", "container_group", "container_display_name"], as_index=False, observed=True)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"))
    )
    d["util"] = np.where(d["issued"] > 0, d["consumed"] / d["issued"] * 100, np.nan)
    d["level"] = heatmap_level(d["issued"].to_numpy(), d["consumed"].to_numpy())

    labs = sorted(d["lab_display_name"].astype(str).unique())
    groups = list(top_groups)
    z = d.pivot_table(index="lab_display_name", columns="container_group", values="level", aggfunc="max",
                      observed=True).reindex(index=labs, columns=groups)
    cells = d.set_index(["lab_display_name", "container_group"])
    names = d.drop_duplicates("container_group").set_index("container_group")["container_display_name"].to_dict()
    titles = {code: label for code, _, label in HEATMAP_LEVELS}
    hover = []
    for lab_name in z.index:
        row = []
        for cg in z.columns:
            if (lab_name, cg) not in cells.index:
                row.append(f"{lab_name}<br>{names.get(cg, cg)}<br>Нет данных")
                continue
            r = cells.loc[(lab_name, cg)]
            level = r["level"]
            status = titles.get(int(level), "") if pd.notna(level) else "нет движения"
            row.append(f"{lab_name}<br>{r['container_display_name']}<br>Выдано: {fmt_num(r['issued'])}"
                       f"<br>Использовано: {fmt_num(r['consumed'])}<br>Доля использования: {fmt_pct(r['util'])}"
                       f"<br><b>{status}</b>")
        hover.append(row)

    top = len(HEATMAP_LEVELS) - 1
    colorscale = []
    for code, color, _ in HEATMAP_LEVELS:  # дискретная шкала: по полосе на код
        colorscale += [[code / (top + 1), color], [(code + 1) / (top + 1), color]]
    fig = go.Figure(go.Heatmap(
        z=z.to_numpy(dtype="float64") + 0.5, x=list(z.columns), y=list(z.index),
        zmin=0, zmax=top + 1, colorscale=colorscale, showscale=False,
        text=hover, hovertemplate="%{text}<extra></extra>", xgap=2, ygap=2, hoverongaps=False,
    ))
    return add_plotly_defaults(fig, height=max(320, 105 + 25 * len(z.index)),
                               x_title="Код материала (полное название — при наведении)", y_title="Лаборатория",
                               bottom_margin=80, legend=False)


# ---------------------------------------------------------------- пончики (03 Заказчики)

RISK_LEVELS = (  # severity_rank → подпись, цвет
    (4, "Очень критично", "#e5484d"),
    (3, "Критично", "#f59e0b"),
    (2, "Внимание", "#facc15"),
    (0, "Без сигнала", "#b9c6d3"),
)
STATUS_COLORS = {"Matched": "#0088c8", "LIS_only": "#8b5cf6", "1C_only": "#f59e0b",
                 "Mixed": "#94a3b8", "No_activity": "#cbd5e1"}
DONUT_HEIGHT = 192  # только два пончика страницы 03; другие графики не затрагиваются


def donut(labels: list[str], values: list[float], colors: list[str], center: str, unit: str = "всего") -> go.Figure:
    """Небольшой пончик; подписи и доли — списком справа, а не на самом кольце."""
    total = float(sum(values)) or 1.0
    def share(v: float) -> str:  # 3 из 1 378 — это «< 1 %», а не «0 %»
        pct = v / total * 100
        return "< 1 %" if 0 < pct < 0.5 else f"{pct:.0f} %"

    # Подпись в две строки — узкая, помещается и в блоке на ноутбуке; число и доля — вторым, серым.
    legend = [f"{label}<br><span style='color:#5d6b7e;font-size:10.5px'>{fmt_num(v)} · {share(v)}</span>"
              for label, v in zip(labels, values)]
    # Число в центре — заголовок самого кольца (position="middle center"): Plotly ставит его точно
    # в центр отверстия при любой ширине. Аннотация по координатам листа съезжала вбок.
    fig = go.Figure(go.Pie(
        labels=legend, values=values, hole=.68, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color="#ffffff", width=2)), textinfo="none",
        hovertemplate="%{label}<extra></extra>",
        domain=dict(x=[0, .42], y=[0, 1]),
        title=dict(text=f"<b>{center}</b><br><span style='font-size:11px;color:#5d6b7e'>{unit}</span>",
                   position="middle center", font=dict(size=18, color="#13263d")),
    ))
    fig.update_layout(
        # autoexpand=False: широкая подпись не отжимает поля и не сжимает кольцо в точку
        height=DONUT_HEIGHT, margin=dict(l=8, r=8, t=8, b=8, autoexpand=False), paper_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(orientation="v", x=.46, xanchor="left", y=.5, yanchor="middle", tracegroupgap=3,
                    font=dict(size=11, color="#13263d"), itemclick=False, itemdoubleclick=False),
    )
    return fig


def risk_donut(customers: pd.DataFrame) -> go.Figure:
    rank = customers["severity_rank"].fillna(0).astype(int).clip(lower=0).replace({1: 0})
    counts = rank.value_counts()
    rows = [(label, int(counts.get(code, 0)), color) for code, label, color in RISK_LEVELS]
    rows = [r for r in rows if r[1] > 0] or [("Нет данных", 1, "#e2e8f0")]
    return donut([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], fmt_num(len(customers)))


def status_donut(customers: pd.DataFrame) -> go.Figure:
    counts = customers["status"].fillna("—").value_counts()
    labels = list(counts.index)
    return donut(labels, [int(v) for v in counts.values], [STATUS_COLORS.get(x, "#cbd5e1") for x in labels],
                 fmt_num(len(customers)))
