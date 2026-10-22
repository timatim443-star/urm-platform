"""Ускоренные функции (urm/fast.py) дают ровно то же, что генератор отчётов v4."""
from __future__ import annotations

import pandas as pd
import pytest

from urm import fast
from urm import report_core as rc


def comparable(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.astype(object).where(frame.notna(), None).reset_index(drop=True)


@pytest.fixture(scope="module", params=["весь период", "последние 3 мес."])
def part(request, fact):
    if request.param == "весь период":
        return fact
    months = sorted(fact["month"].unique())
    base = fact[fact["month"] >= months[-3]].drop(columns=["period_delta", "utilization_pct", "calc_balance"])
    return rc.enrich_fact(base)


def test_anomalies(part):
    expected = rc.build_anomalies(part)
    assert len(expected) > 0
    pd.testing.assert_frame_equal(comparable(expected), comparable(fast.build_anomalies(part)), check_dtype=False)


def test_summaries(part):
    anomalies = rc.build_anomalies(part)
    expected, actual = rc.summaries(part, anomalies), fast.summaries(part, anomalies)
    for name in expected:
        pd.testing.assert_frame_equal(comparable(expected[name]), comparable(actual[name]), check_dtype=False)


def test_summaries_restore_original_function(part):
    original = rc.customer_status_latest
    fast.summaries(part, rc.build_anomalies(part))
    assert rc.customer_status_latest is original


def test_watchlist(part):
    anomalies = rc.build_anomalies(part)
    pd.testing.assert_frame_equal(comparable(rc.build_watchlist(part, anomalies)),
                                  comparable(fast.build_watchlist(part, anomalies)), check_dtype=False)


def test_signals(part):
    key = ["month", "severity"]
    a = rc.build_monthly_anomaly_signals(part).sort_values(key).reset_index(drop=True)
    b = fast.build_monthly_anomaly_signals(part).sort_values(key).reset_index(drop=True)
    assert a[key].equals(b[key])
    assert (a["count"].to_numpy() == b["count"].to_numpy()).all()


def test_partner_names():
    frame = pd.DataFrame({
        "LIS_Hospitals": ["Больница", "больница ", None, "", "А"],
        "OneC_Counterparties": ["БОЛЬНИЦА", "ТОО Б", "ТОО В", None, ""],
        "bin": ["1", "2", "3", "4", "5"],
    })
    expected = frame.apply(rc.choose_partner_name, axis=1).tolist()
    assert fast.partner_names(frame).tolist() == expected
