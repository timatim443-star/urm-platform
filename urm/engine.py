"""Срез данных под фильтры и права. Расчёты — функции генератора отчётов.

Источник — снимок (urm/snapshot.py):
  * свёрнутый факт держится в памяти одной копией на все сессии
    (строки — в формате PyArrow: втрое меньше памяти, чем обычные строки pandas);
  * типовые срезы — вся сеть и каждый город × весь период / 12 / 6 / 3 мес. —
    посчитаны при сборке снимка (urm/packs.py) и только читаются;
  * остальные срезы считаются тем же кодом на лету и кэшируются для всех;
  * большие вспомогательные таблицы читаются DuckDB с фильтром.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from urm import packs, snapshot
from urm.loader import etl_state, publication_key

STRING_COLUMNS = ("lab_key", "lab_display_name", "container_group", "container_display_name",
                  "bin", "partner_name", "MatchStatus")


@dataclass(frozen=True)
class Scope:
    month_from: str  # YYYY-MM-01
    month_to: str
    labs: tuple[str, ...] = ()  # lab_key; пусто — все
    bins: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()  # container_group
    network_quality: bool = True


@st.cache_resource(show_spinner="Открываю снимок данных…", max_entries=2)
def _memory(path: str) -> dict[str, pd.DataFrame]:
    mem = snapshot.read_memory(Path(path))
    main = mem["main"]
    for column in STRING_COLUMNS:
        if column in main.columns:
            main[column] = main[column].astype("string[pyarrow]")
    return mem


@st.cache_resource(show_spinner=False, max_entries=64)
def _pack(path: str, pid: str) -> dict[str, pd.DataFrame] | None:
    folder = Path(path) / "packs" / pid
    return packs.read(folder) if folder.exists() else None


@st.cache_data(ttl=60, show_spinner=False)
def _publication() -> str:
    return publication_key()


def current() -> tuple[str, dict[str, pd.DataFrame], dict]:
    """Текущий снимок. Если витрины обновились — пересборка в фоне."""
    snapshot.ensure(_publication(), background=True)
    path = snapshot.current_dir()
    if path is None:
        raise RuntimeError(snapshot.status().get("error") or "Снимок данных ещё не собран.")
    return str(path), _memory(str(path)), snapshot.current_meta()


def state() -> pd.Series:
    return etl_state(_publication())


def presets(main: pd.DataFrame) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Окна периода, для которых посчитаны пакеты: подпись → (начало, конец)."""
    earliest, latest = main["month"].min(), main["month"].max()
    out = {"Весь период": (earliest, latest)}
    for months in packs.PRESET_MONTHS:
        start, end = packs.window(latest, months, earliest)
        if start > earliest:
            out[f"{months} мес."] = (start, end)
    return out


def _matching_pack(main: pd.DataFrame, scope: Scope) -> str | None:
    if scope.bins or scope.materials or len(scope.labs) > 1:
        return None
    earliest, latest = main["month"].min(), main["month"].max()
    start, end = pd.Timestamp(scope.month_from), pd.Timestamp(scope.month_to)
    lab = scope.labs[0] if scope.labs else None
    if end != latest:
        return None
    if start <= earliest:
        return packs.pack_id(lab, None)
    for months in packs.PRESET_MONTHS:
        if packs.window(latest, months, earliest)[0] == start:
            return packs.pack_id(lab, months)
    return None


def _filter(frame: pd.DataFrame, scope: Scope, bins=True, materials=True) -> pd.DataFrame:
    out = frame
    if "month" in out.columns:
        out = out[(out["month"] >= pd.Timestamp(scope.month_from)) & (out["month"] <= pd.Timestamp(scope.month_to))]
    if scope.labs and "lab_key" in out.columns:
        out = out[out["lab_key"].isin(scope.labs)]
    if bins and scope.bins and "bin" in out.columns:
        out = out[out["bin"].isin(scope.bins)]
    if materials and scope.materials and "container_group" in out.columns:
        out = out[out["container_group"].isin(scope.materials)]
    return out


def _where(scope: Scope, bins=True, materials=True, month=True) -> tuple[str, list]:
    clauses, params = [], []
    if month:
        clauses.append("month BETWEEN ? AND ?")
        params += [pd.Timestamp(scope.month_from), pd.Timestamp(scope.month_to)]
    if scope.labs:
        clauses.append(f"lab_key IN ({','.join('?' * len(scope.labs))})")
        params += list(scope.labs)
    if bins and scope.bins:
        clauses.append(f"bin IN ({','.join('?' * len(scope.bins))})")
        params += list(scope.bins)
    if materials and scope.materials:
        clauses.append(f"container_group IN ({','.join('?' * len(scope.materials))})")
        params += list(scope.materials)
    return " AND ".join(clauses), params


@st.cache_resource(show_spinner="Считаю срез…", max_entries=24, ttl=6 * 3600)
def compute(snapshot_path: str, scope: Scope) -> dict:
    """Срез + расчёт. Кэш общий для всех пользователей: ключ — снимок и фильтры."""
    started = time.time()
    path = Path(snapshot_path)
    mem = _memory(snapshot_path)
    main = mem["main"]
    fact = _filter(main, scope)

    pid = _matching_pack(main, scope)
    pack = _pack(snapshot_path, pid) if pid else None
    source = "готовый пакет"
    if pack is None:
        source = "расчёт на лету"
        full = (pd.Timestamp(scope.month_from) <= main["month"].min()
                and pd.Timestamp(scope.month_to) >= main["month"].max())
        if full:
            anomalies = mem["anomalies"]
            if scope.labs:
                anomalies = anomalies[anomalies["lab_key"].isin(scope.labs)]
            if scope.bins:
                anomalies = anomalies[anomalies["БИН"].isin(scope.bins)]
            if scope.materials:
                names = set(fact["container_display_name"].dropna().astype(str))
                anomalies = anomalies[anomalies["Контейнер"].astype(str).isin(names)]
            anomalies = anomalies.reset_index(drop=True)
        else:
            from urm.fast import build_anomalies
            from urm.report_core import enrich_fact

            fact = enrich_fact(fact.drop(columns=["period_delta", "utilization_pct", "calc_balance"],
                                         errors="ignore"))
            anomalies = build_anomalies(fact)
        where, params = _where(scope, bins=False)
        procedure = snapshot.query(path, "procedure", where, params)
        pack = packs.make(fact, anomalies, procedure)

    where, params = _where(scope)
    where_lab, params_lab = _where(scope, bins=False, materials=False)
    lab_names = sorted(set(fact["lab_display_name"].dropna().astype(str))) if scope.labs else []
    name_where = f"lab_display_name IN ({','.join('?' * len(lab_names))})" if lab_names else ""
    empty = pd.DataFrame()
    network = scope.network_quality
    q = snapshot.query
    data = {
        "exceptions": q(path, "exceptions", where, params, order="amount DESC", limit=5000),
        "internal": q(path, "internal", where_lab, params_lab),
        "unmapped_lis": q(path, "unmapped_lis", name_where, lab_names),
        "unmapped_onec": q(path, "unmapped_onec", name_where, lab_names),
        "mapping_coverage": q(path, "mapping_coverage") if network else empty,
        "mapping_conflicts": q(path, "mapping_conflicts") if network else empty,
        "quality_issues": q(path, "quality_issues") if network else empty,
        "quality_funnel": q(path, "quality_funnel") if network else empty,
    }
    for frame in data.values():
        if "month" in frame.columns:
            frame["month"] = pd.to_datetime(frame["month"])
    for name in ("monthly", "proc_monthly", "proc_detail", "signals", "lmc"):
        frame = pack.get(name)
        if frame is not None and "month" in frame.columns:
            frame["month"] = pd.to_datetime(frame["month"])

    return {
        "fact": fact,
        "anomalies": pack["anomalies"],
        "s": {k: pack[k] for k in ("monthly", "labs", "customers", "materials")},
        "watchlist": pack["watchlist"],
        "signals": pack["signals"],
        "lmc": pack["lmc"],
        "proc_detail": pack["proc_detail"],
        "proc_monthly": pack["proc_monthly"],
        "data": data,
        "source": source,
        "seconds": round(time.time() - started, 2),
    }

