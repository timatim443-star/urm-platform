"""Общие данные для тестов: синтетический факт в формате снимка, без базы."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("URM_SNAPSHOT_DIR", os.path.join(os.path.dirname(__file__), ".snapshots"))


def make_fact(seed: int = 7, labs: int = 4, bins: int = 60, groups: int = 6, months: int = 8) -> pd.DataFrame:
    """Факт на зерне месяц × лаборатория × материал × БИН со всеми видами расхождений."""
    rng = np.random.default_rng(seed)
    rows = []
    month_list = pd.date_range("2025-03-01", periods=months, freq="MS")
    for lab in range(labs):
        for b in range(bins):
            if rng.random() < 0.5:
                continue
            for g in range(groups):
                if rng.random() < 0.4:
                    continue
                for month in month_list:
                    if rng.random() < 0.25:
                        continue
                    issued = float(rng.choice([0, 0, rng.integers(1, 400)]))
                    pattern = rng.random()
                    if pattern < 0.15:
                        consumed = 0.0
                    elif pattern < 0.3:
                        consumed = issued * rng.uniform(0.05, 0.4)
                    elif pattern < 0.45:
                        consumed = issued * rng.uniform(1.1, 2.5) + rng.integers(0, 5)
                    else:
                        consumed = issued * rng.uniform(0.6, 1.0)
                    consumed = float(round(consumed))
                    if issued > 0 and consumed > 0:
                        status = "Matched"
                    elif issued > 0:
                        status = "1C_only"
                    elif consumed > 0:
                        status = "LIS_only"
                    else:
                        status = "No_activity"
                    rows.append({
                        "month": month, "lab_key": f"lab{lab}", "lab_display_name": f"Город {lab}",
                        "container_group": f"G{g}", "container_display_name": f"Материал {g}",
                        "bin": f"{b:012d}", "partner_name": f"Заказчик  {b}" if b % 5 else f"ТОО Клиент {b}",
                        "OneC_Issued": issued, "LIS_Consumed": consumed, "MatchStatus": status,
                    })
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def fact() -> pd.DataFrame:
    from urm.report_core import enrich_fact

    return enrich_fact(make_fact())
