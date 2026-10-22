"""Проверка: ускоренные функции дают тот же результат, что генератор.

    python tools/check_equivalence.py                 # на текущем снимке
    python tools/check_equivalence.py path/main.parquet

Сравнивает:
  * аномалии, сводки (помесячно, города, заказчики, материалы) и «К отработке»
    на всём периоде и на последних 3 месяцах по всей сети: urm.fast против
    report_core — построчно, все колонки;
  * динамику сигналов: urm.fast против report_core (построчный оригинал);
  * графики по свёрнутым данным (месяц × лаборатория × материал) против
    тех же графиков по полному факту — по значениям, а не по картинке.
Выход с кодом 1, если хоть что-то расходится.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _admin_env  # noqa: E402,F401 — путь к проекту и тихий streamlit

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from urm import charts, fast, snapshot  # noqa: E402
from urm import report_core as rc


def _figure_values(fig) -> list:
    out = []
    for trace in fig.data:
        for attr in ("x", "y", "z"):
            value = getattr(trace, attr, None)
            if value is not None:
                arr = np.asarray(value, dtype=object)
                out.append((trace.name, attr, arr.tolist()))
    return out


def _same_figure(a, b) -> bool:
    va, vb = _figure_values(a), _figure_values(b)
    if len(va) != len(vb):
        return False
    for (na, aa, xa), (nb, ab, xb) in zip(va, vb):
        if na != nb or aa != ab:
            return False
        xa, xb = np.asarray(xa, dtype=object), np.asarray(xb, dtype=object)
        if xa.shape != xb.shape:
            return False
        for p, q in zip(xa.ravel(), xb.ravel()):
            if isinstance(p, (float, int, np.floating)) and isinstance(q, (float, int, np.floating)):
                if not np.isclose(float(p), float(q), equal_nan=True):
                    return False
            elif str(p) != str(q):
                return False
    return True


def main() -> int:
    if len(sys.argv) > 1:
        fact = pd.read_parquet(sys.argv[1])
    else:
        path = snapshot.current_dir()
        if path is None:
            print("Снимка нет. Соберите его: python tools/build_snapshot.py")
            return 2
        fact = pd.read_parquet(path / "main.parquet")
    print(f"Строк факта: {len(fact):,}".replace(",", " "))
    ok = True

    t = time.time()
    original = rc.build_monthly_anomaly_signals(fact)
    t_orig = time.time() - t
    t = time.time()
    quick = fast.build_monthly_anomaly_signals(fact)
    t_fast = time.time() - t
    key = ["month", "severity"]
    a = original.sort_values(key).reset_index(drop=True)
    b = quick.sort_values(key).reset_index(drop=True)
    same = a[key].equals(b[key]) and (a["count"].to_numpy() == b["count"].to_numpy()).all()
    ok &= same
    print(f"Динамика сигналов: {'совпадает' if same else 'РАСХОДИТСЯ'} "
          f"(оригинал {t_orig:.1f} с, ускоренная {t_fast:.2f} с, строк {len(a)})")

    def comparable(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.astype(object).where(frame.notna(), None).reset_index(drop=True)

    base = fact.drop(columns=["period_delta", "utilization_pct", "calc_balance"], errors="ignore")
    months = sorted(fact["month"].dropna().unique())
    for label, part in (("весь период", base), ("последние 3 мес.", base[base["month"] >= months[-3]])):
        enriched = rc.enrich_fact(part)
        t = time.time()
        original = rc.build_anomalies(enriched)
        t_orig = time.time() - t
        t = time.time()
        quick = fast.build_anomalies(enriched)
        t_fast = time.time() - t
        try:
            pd.testing.assert_frame_equal(comparable(original), comparable(quick), check_dtype=False)
            same = True
        except AssertionError:
            same = False
        ok &= same
        print(f"Аномалии, {label}: {'совпадают' if same else 'РАСХОДЯТСЯ'} "
              f"(оригинал {t_orig:.1f} с, ускоренная {t_fast:.2f} с, строк {len(original)})")

        checks = {
            "Сводки": (lambda: rc.summaries(enriched, original), lambda: fast.summaries(enriched, original)),
            "К отработке": (lambda: rc.build_watchlist(enriched, original), lambda: fast.build_watchlist(enriched, original)),
        }
        for name, (slow_fn, fast_fn) in checks.items():
            t = time.time()
            slow = slow_fn()
            t_orig = time.time() - t
            t = time.time()
            quick = fast_fn()
            t_fast = time.time() - t
            pairs = slow.items() if isinstance(slow, dict) else [("", slow)]
            same = True
            for part_name, frame in pairs:
                other = quick[part_name] if part_name else quick
                try:
                    pd.testing.assert_frame_equal(comparable(frame), comparable(other), check_dtype=False)
                except AssertionError as exc:
                    same = False
                    print(f"  {part_name or name}: {str(exc)[:300]}")
            ok &= same
            print(f"{name}, {label}: {'совпадают' if same else 'РАСХОДЯТСЯ'} "
                  f"(оригинал {t_orig:.1f} с, ускоренная {t_fast:.2f} с)")

    lmc = fast.lab_material_month(fact)
    for name, fn in (
        ("chart_heatmap_lab_material", charts.chart_heatmap_lab_material),
        ("chart_city_monthly_lines", rc.chart_city_monthly_lines),
        ("chart_city_monthly_heatmap", rc.chart_city_monthly_heatmap),
    ):
        same = _same_figure(fn(fact), fn(lmc))
        ok &= same
        print(f"{name}: {'совпадает' if same else 'РАСХОДИТСЯ'} на свёрнутых данных "
              f"({len(lmc):,} строк вместо {len(fact):,})".replace(",", " "))
    print("ИТОГ:", "всё совпадает" if ok else "есть расхождения")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
