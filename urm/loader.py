"""Запросы к витринам PostgreSQL в том же контракте, что ждёт report_core.

Генератор отчётов читал CSV-выгрузки витрин. Здесь источником становится база,
а имена колонок приводятся к тому же виду (LIS_Consumed, OneC_Issued,
MatchStatus и т. д.), поэтому вся аналитика и все графики работают без правок.

Данные читаются целиком одним согласованным снимком и кэшируются по ключу
последней публикации: расчёт ночной, в течение дня витрины не меняются.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from urm.config import SETTINGS
from urm.db import fetch_df

# PostgreSQL → контракт отчёта
RENAME = {
    "lis_consumed": "LIS_Consumed",
    "lis_unique_customers": "LIS_UniqueCustomers",
    "onec_issued": "OneC_Issued",
    "onec_unique_counterparties": "OneC_UniqueCounterparties",
    "delta_issued_minus_consumed": "Delta_IssuedMinusConsumed",
    "coverage_issued_to_consumed": "Coverage_Issued_to_Consumed",
    "match_status": "MatchStatus",
    "lis_hospitals": "LIS_Hospitals",
    "onec_counterparties": "OneC_Counterparties",
}

NUMERIC = (
    "LIS_Consumed",
    "LIS_UniqueCustomers",
    "OneC_Issued",
    "OneC_UniqueCounterparties",
    "Delta_IssuedMinusConsumed",
    "Coverage_Issued_to_Consumed",
    "amount",
    "rows",
    "metric_sort",
    "quantity",
    "unique_codes",
    "group_count",
    "rows_count",
    "total_consumed",
    "total_issued",
    "unique_bins",
    "unique_hospitals",
    "unique_counterparties",
)


def _shape(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.rename(columns=RENAME)
    if "month" in out.columns:
        out["month"] = pd.to_datetime(out["month"], errors="coerce")
    for column in out.columns:
        if column in NUMERIC:
            out[column] = pd.to_numeric(out[column], errors="coerce")
    for column in ("bin", "lab_key", "container_group", "MatchStatus", "procedure_uid"):
        if column in out.columns:
            out[column] = out[column].fillna("").astype(str).str.strip()
    return out


# Версия формата снимка: при изменении расчёта снимка увеличить — снимок пересоберётся сам.
SNAPSHOT_FORMAT = 2


def publication_key() -> str:
    """Меняется при каждой успешной публикации витрин — это и есть ключ кэша."""
    frame = fetch_df(
        "SELECT last_successful_at, reference_changed FROM urm2.etl_state WHERE id = 1"
    )
    if frame.empty:
        return f"no-state|f{SNAPSHOT_FORMAT}"
    row = frame.iloc[0]
    return f"{row['last_successful_at']}|{row['reference_changed']}|f{SNAPSHOT_FORMAT}"


@st.cache_data(ttl=SETTINGS.cache_ttl, show_spinner=False)
def etl_state(key: str) -> pd.Series:
    """key — ключ публикации: участвует в ключе кэша."""
    frame = fetch_df(
        """
        SELECT last_checked_day, last_successful_day,
               last_successful_at AT TIME ZONE 'Asia/Almaty' AS calculated_at_local,
               reference_changed,
               references_updated_at AT TIME ZONE 'Asia/Almaty' AS references_changed_at_local,
               last_error
        FROM urm2.etl_state WHERE id = 1
        """
    )
    if frame.empty:
        return pd.Series(dtype="object")
    return frame.iloc[0]


QUERIES: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "main",
        """
        SELECT month, lab_key, lab_display_name, container_group, container_display_name,
               bin, lis_consumed, lis_unique_customers, onec_issued,
               onec_unique_counterparties, delta_issued_minus_consumed,
               coverage_issued_to_consumed, match_status
        FROM urm2.fact_month_container_bin
        """,
        ("fact_month_container_bin",),
    ),
    (
        "parties",
        """
        SELECT month, lab_key, lab_display_name, container_group, bin,
               lis_hospitals, onec_counterparties
        FROM urm2.bin_parties
        """,
        ("bin_parties",),
    ),
    ("lab", "SELECT lab_key, lab_display_name FROM urm2.dim_laboratory", ("dim_laboratory",)),
    (
        "containers",
        "SELECT container_group, container_display_name, container_label FROM urm2.dim_container",
        ("dim_container",),
    ),
    (
        "statuses",
        "SELECT match_status, sort_order FROM urm2.match_status_filter ORDER BY sort_order",
        ("match_status_filter",),
    ),
    (
        "exceptions",
        """
        SELECT month, lab_key, lab_display_name, bin, container_group,
               container_display_name, side, party_name, amount
        FROM urm2.fact_exceptions
        """,
        ("fact_exceptions",),
    ),
    (
        "procedure",
        """
        SELECT month, lab_key, lab_display_name, procedure_uid, procedure_room_name,
               container_group, container_display_name, match_status, metric,
               metric_sort, amount
        FROM urm2.fact_procedure_room_container_month_metric
        """,
        ("fact_procedure_room_container_month_metric",),
    ),
    (
        "internal",
        """
        SELECT month, lab_key, lab_display_name, metric, metric_sort, amount, rows
        FROM urm2.fact_internal_consumption_lab_month
        """,
        ("fact_internal_consumption_lab_month",),
    ),
    (
        "quality_issues",
        "SELECT source, issue_type, severity, rows, quantity, details FROM urm2.quality_issues",
        ("quality_issues",),
    ),
    (
        "mapping_conflicts",
        "SELECT conflict_type, code, group_count FROM urm2.mapping_conflicts",
        ("mapping_conflicts",),
    ),
    (
        "mapping_coverage",
        "SELECT source, mapping_scope, rows, quantity, unique_codes FROM urm2.mapping_coverage_summary",
        ("mapping_coverage_summary",),
    ),
    ("quality_funnel", "SELECT source, stage, rows FROM urm2.quality_funnel", ("quality_funnel",)),
    (
        "unmapped_lis",
        """
        SELECT lis_container_code, lis_container_name_raw, lab_display_name, rows_count,
               total_consumed, unique_bins, unique_hospitals, months
        FROM urm2.unmapped_lis_details
        """,
        ("unmapped_lis_details",),
    ),
    (
        "unmapped_onec",
        """
        SELECT onec_mdm_key, onec_nomenclature_name, onec_article, lab_display_name,
               onec_warehouse_group, rows_count, total_issued, unique_bins,
               unique_counterparties, months
        FROM urm2.unmapped_onec_details
        """,
        ("unmapped_onec_details",),
    ),
]


def row_counts(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Витрина": name, "Строк": len(frame)} for name, frame in data.items()]
    ).sort_values("Строк", ascending=False)
