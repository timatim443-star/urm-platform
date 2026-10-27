"""Пакеты: всё, что нужно страницам для одного среза, посчитанное заранее.

Пакет = сводки генератора (summaries), аномалии, «К отработке», динамика
сигналов, свёртка для графиков, итоги процедурных кабинетов.

При ночной сборке снимка пакеты считаются для типовых срезов:
  вся сеть и каждая лаборатория (город) × весь период, 12, 6 и 3 месяца.
Эти срезы открываются мгновенно — страница только читает готовое.
Любой другой срез (заказчик, материал, свой период, несколько городов)
считается тем же make() на лету и кэшируется для всех пользователей.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from urm import fast
from urm.report_core import enrich_fact, procedure_summary

PRESET_MONTHS = tuple(
    int(x) for x in os.environ.get("URM_PRESET_MONTHS", "12,6,3").split(",") if x.strip().isdigit()
)
NAMES = (
    "monthly", "labs", "customers", "materials", "anomalies", "watchlist",
    "signals", "lmc", "proc_detail", "proc_monthly",
)


def window(latest: pd.Timestamp, months: int | None, earliest: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not months:
        return earliest, latest
    start = (latest - pd.DateOffset(months=months - 1)).normalize().replace(day=1)
    return max(start, earliest), latest


def pack_id(lab: str | None, months: int | None) -> str:
    return f"{lab or 'all'}__{months or 'full'}"


def make(fact: pd.DataFrame, anomalies: pd.DataFrame, procedure: pd.DataFrame) -> dict[str, pd.DataFrame]:
    s = fast.summaries(fact, anomalies)
    detail, proc_monthly = procedure_summary(procedure) if not procedure.empty else (pd.DataFrame(), pd.DataFrame())
    return {
        "monthly": s["monthly"],
        "labs": s["labs"],
        "customers": s["customers"],
        "materials": s["materials"],
        "anomalies": anomalies,
        "watchlist": fast.build_watchlist(fact, anomalies),
        "signals": fast.build_monthly_anomaly_signals(fact),
        "lmc": fast.lab_material_month(fact),
        "proc_detail": detail,
        "proc_monthly": proc_monthly,
    }


def scope_fact(main: pd.DataFrame, network_anomalies: pd.DataFrame, lab: str | None,
               start: pd.Timestamp, end: pd.Timestamp, full: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Факт и аномалии среза — так же, как это делает генератор.

    Весь период: сетевые аномалии, отфильтрованные по лаборатории (как в
    отчёте по городу). Узкий период: накопительная дельта и аномалии
    считаются заново — у них меняется «последний месяц».
    """
    part = main if lab is None else main[main["lab_key"] == lab]
    if full:
        anomalies = network_anomalies if lab is None else network_anomalies[network_anomalies["lab_key"] == lab]
        return part, anomalies.reset_index(drop=True)
    part = part[(part["month"] >= start) & (part["month"] <= end)]
    fact = enrich_fact(part.drop(columns=["period_delta", "utilization_pct", "calc_balance"], errors="ignore"))
    return fact, fast.build_anomalies(fact)


def write(pack: dict[str, pd.DataFrame], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        frame = pack.get(name, pd.DataFrame())
        frame = frame.copy()
        for column in frame.columns:
            if frame[column].dtype == "object":
                frame[column] = frame[column].map(lambda v: None if v is None or v != v else str(v))
        frame.to_parquet(folder / f"{name}.parquet", index=False, compression="zstd")


def read(folder: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_parquet(folder / f"{name}.parquet") for name in NAMES}
