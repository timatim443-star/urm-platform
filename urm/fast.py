"""Векторные эквиваленты медленных функций генератора.

Правило: логика не меняется, меняется только способ вычисления. Каждая функция
здесь обязана возвращать то же, что оригинал из report_core, — это проверяется
тестом tools/check_equivalence.py на реальных данных.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from urm import report_core as _rc
from urm.report_core import CFG

_ORIGINAL_STATUS = _rc.customer_status_latest


def build_monthly_anomaly_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Эквивалент report_core.build_monthly_anomaly_signals.

    Оригинал классифицирует каждую группу месяц × лаборатория × БИН × материал
    построчным apply: на 1,27 млн строк — около 150 с. Здесь те же правила
    в том же порядке приоритета через np.select — доли секунды.
    """
    if df.empty:
        return pd.DataFrame(columns=["month", "severity", "count"])
    work = df[["month", "lab_key", "bin", "container_group", "OneC_Issued", "LIS_Consumed"]].copy()
    work["has_1c_only"] = (df["MatchStatus"].astype(str) == "1C_only").astype("int8")
    x = (
        work.groupby(["month", "lab_key", "bin", "container_group"], dropna=False, observed=True)
        .agg(issued=("OneC_Issued", "sum"), consumed=("LIS_Consumed", "sum"), one_c=("has_1c_only", "max"))
        .reset_index()
    )
    issued = x["issued"].fillna(0).to_numpy(dtype="float64")
    consumed = x["consumed"].fillna(0).to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        util = np.where(issued > 0, consumed / issued * 100.0, np.nan)
    has_util = ~np.isnan(util)
    positive = issued > 0
    conditions = [
        positive & (consumed == 0),
        positive & has_util & (util <= CFG.critical_utilization_pct),
        positive & has_util & (util <= CFG.high_utilization_pct),
        (x["one_c"].to_numpy() > 0) & positive,
        (consumed > 0) & (issued == 0),
        positive & (util >= CFG.reverse_ratio_high_pct),
    ]
    choices = ["Critical", "Critical", "High", "High", "Medium", "Medium"]
    x["severity"] = np.select(conditions, choices, default="")
    x = x[x["severity"] != ""]
    if x.empty:
        return pd.DataFrame(columns=["month", "severity", "count"])
    return x.groupby(["month", "severity"], as_index=False).size().rename(columns={"size": "count"})


def lab_material_month(df: pd.DataFrame) -> pd.DataFrame:
    """Свёртка до месяц × лаборатория × материал для графиков, которым не нужен БИН.

    chart_heatmap_lab_material, chart_city_monthly_lines и chart_city_monthly_heatmap
    только суммируют выдачу и расход по этим осям: на свёрнутых данных результат
    тот же, а строк в сотни раз меньше.
    """
    if df.empty:
        return df
    keys = ["month", "lab_key", "lab_display_name", "container_group", "container_display_name"]
    return (
        df.groupby(keys, dropna=False, observed=True)[["OneC_Issued", "LIS_Consumed"]]
        .sum()
        .reset_index()
    )


def partner_names(frame: pd.DataFrame) -> pd.Series:
    """Эквивалент report_core.choose_partner_name построчно, но векторно.

    Оба названия есть: совпадают без учёта регистра — 1С, иначе «1C: … | LIS: …».
    Есть одно — оно. Нет ни одного — «БИН <номер>».
    """
    lis = frame["LIS_Hospitals"].fillna("").astype(str).str.strip()
    onec = frame["OneC_Counterparties"].fillna("").astype(str).str.strip()
    both = (lis != "") & (onec != "")
    same = lis.str.casefold() == onec.str.casefold()
    fallback = "БИН " + frame["bin"].fillna("").astype(str)
    name = np.where(
        both,
        np.where(same, onec, "1C: " + onec + " | LIS: " + lis),
        np.where(onec != "", onec, np.where(lis != "", lis, fallback)),
    )
    return pd.Series(name, index=frame.index, dtype="object")


def build_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Эквивалент report_core.build_anomalies.

    Оригинал склеивает статусы сверки лямбдой по каждой группе и обходит
    связки построчно (iterrows): на всей сети с узким периодом — около 20 с.
    Здесь те же правила в том же порядке приоритета, но маской по всем
    связкам сразу; текст «Значение» формируется только для сработавших строк
    теми же функциями fmt_num / fmt_pct.
    """
    from urm.report_core import SEVERITY_RANK, fmt_num, fmt_pct, snapshot_by_grain

    cols = ["severity", "lab_key", "Лаборатория", "БИН", "Заказчик", "Контейнер", "Тип аномалии", "Значение",
            "Период", "Расчетная дельта", "Неотражено_ед", "Неотражено_pct", "Направление", "Почему"]
    if df.empty:
        return pd.DataFrame()
    keys = ["lab_key", "bin", "container_group"]
    latest = df["month"].max()
    latest_df = df.loc[df["month"] == latest, keys + ["OneC_Issued", "LIS_Consumed", "MatchStatus"]].copy()
    # В оригинале статус — склейка уникальных значений; правилу нужно только
    # «встречается ли 1C_only» — это то же самое, что «хоть одно значение его содержит».
    latest_df["one_c"] = latest_df["MatchStatus"].astype("object").map(
        lambda v: isinstance(v, str) and "1C_only" in v).astype(bool)
    snap = snapshot_by_grain(df)
    latest_metrics = (
        latest_df.groupby(keys, dropna=False)
        .agg(latest_issued=("OneC_Issued", "sum"), latest_consumed=("LIS_Consumed", "sum"), one_c=("one_c", "max"))
        .reset_index()
    )
    snap = snap.merge(latest_metrics, on=keys, how="left")
    snap[["latest_issued", "latest_consumed"]] = snap[["latest_issued", "latest_consumed"]].fillna(0.0)
    issued = snap["latest_issued"].to_numpy(dtype="float64")
    consumed = snap["latest_consumed"].to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        util = np.where(issued > 0, consumed / issued * 100.0, np.nan)
        unacc = np.clip(issued - consumed, 0, None)
        unacc_pct = np.where(issued > 0, unacc / issued * 100.0, np.nan)
        reverse_gap = np.clip(consumed - issued, 0, None)
        reverse_ratio = np.where(issued > 0, consumed / issued * 100.0, np.where(consumed > 0, np.inf, np.nan))
    balance = snap["calc_balance"].to_numpy(dtype="float64")
    stock = snap["months_stock"].to_numpy(dtype="float64")
    one_c = snap["one_c"].astype("boolean").fillna(False).to_numpy(dtype=bool)
    has_util = ~np.isnan(util)
    has_stock = ~np.isnan(stock)
    positive = issued > 0

    rules = [
        (positive & (consumed == 0), "Critical", "Выдали, но в ЛИС 0", "1С > ЛИС",
         "Есть выдача по 1С, но в последнем месяце ЛИС не показывает использования. Первый приоритет проверки.",
         lambda i: f"{fmt_num(issued[i])} ед. выдано; 0 в ЛИС"),
        (positive & has_util & (util <= CFG.critical_utilization_pct), "Critical",
         "Очень низкое использование после выдачи", "1С > ЛИС",
         f"После выдачи в ЛИС отражено не более {CFG.critical_utilization_pct:.0f}% объема.",
         lambda i: f"Использовано {fmt_pct(util[i])}; не отражено {fmt_num(unacc[i])} ед."),
        (positive & has_util & (util <= CFG.high_utilization_pct), "High",
         "Низкое использование после выдачи", "1С > ЛИС",
         "Существенная часть выданного объема не подтверждена использованием в ЛИС.",
         lambda i: f"Использовано {fmt_pct(util[i])}; не отражено {fmt_num(unacc[i])} ед."),
        (has_stock & (balance > 0) & (stock >= CFG.critical_stock_months), "Critical",
         "Очень большой накопленный неиспользованный объем", "1С > ЛИС",
         f"Расчетная дельта эквивалентна как минимум {CFG.critical_stock_months:.0f} месяцам среднего потребления.",
         lambda i: f"{float(stock[i]):.1f} мес. среднего потребления".replace(".", ",")),
        (has_stock & (balance > 0) & (stock >= CFG.high_stock_months), "High",
         "Большой накопленный неиспользованный объем", "1С > ЛИС",
         f"Расчетная дельта эквивалентна как минимум {CFG.high_stock_months:.0f} месяцам среднего потребления.",
         lambda i: f"{float(stock[i]):.1f} мес. среднего потребления".replace(".", ",")),
        (one_c & positive, "High", "1C_only: выдача без стороны ЛИС", "1С > ЛИС",
         "В текущем срезе есть сторона 1С, но отсутствует сторона ЛИС.",
         lambda i: f"{fmt_num(issued[i])} ед."),
        ((consumed > 0) & (issued == 0), "Medium", "ЛИС есть, выдачи 1С нет", "ЛИС > 1С",
         "Использование отражено в ЛИС, но соответствующей выдачи 1С нет. Это аномалия сверки, но приоритет ниже риска «выдали и не использовали».",
         lambda i: f"{fmt_num(consumed[i])} ед. в ЛИС"),
        ((reverse_gap > 0) & (~np.isfinite(reverse_ratio) | (reverse_ratio >= CFG.reverse_ratio_high_pct)), "Medium",
         "ЛИС заметно выше 1С", "ЛИС > 1С",
         "Обратное расхождение. Требует сверки, но не считается главным риском невозврата.",
         lambda i: f"ЛИС больше на {fmt_num(reverse_gap[i])} ед."),
    ]
    rule_of = np.full(len(snap), -1)
    for number, rule in reversed(list(enumerate(rules))):  # первое сработавшее правило побеждает
        rule_of[rule[0]] = number
    hit = np.flatnonzero(rule_of >= 0)  # порядок строк — как у iterrows в оригинале
    if len(hit) == 0:
        return pd.DataFrame(columns=cols)

    chosen = rule_of[hit]
    picked = snap.iloc[hit]
    result = pd.DataFrame({
        "severity": [rules[k][1] for k in chosen],
        "lab_key": picked["lab_key"].to_numpy(dtype=object),
        "Лаборатория": picked["lab_display_name"].to_numpy(dtype=object),
        "БИН": picked["bin"].to_numpy(dtype=object),
        "Заказчик": picked["partner_name"].to_numpy(dtype=object),
        "Контейнер": picked["container_display_name"].to_numpy(dtype=object),
        "Тип аномалии": [rules[k][2] for k in chosen],
        "Значение": [rules[k][5](i) for k, i in zip(chosen, hit)],
        "Период": latest.strftime("%Y-%m") if pd.notna(latest) else "",
        "Расчетная дельта": balance[hit],
        "Неотражено_ед": unacc[hit],
        "Неотражено_pct": unacc_pct[hit],
        "Направление": [rules[k][3] for k in chosen],
        "Почему": [rules[k][4] for k in chosen],
    })
    result["severity_rank"] = result["severity"].map(SEVERITY_RANK).fillna(0)
    return (result.sort_values(["severity_rank", "Неотражено_ед", "Расчетная дельта"], ascending=[False, False, False])
            .drop(columns="severity_rank").reset_index(drop=True))


_KNOWN_STATUSES = ("LIS_only", "1C_only", "Matched")


def customer_status_latest(df: pd.DataFrame) -> pd.DataFrame:
    """Эквивалент report_core.customer_status_latest (статус заказчика в последнем месяце).

    LIS_only и 1C_only вместе — Mixed; иначе LIS_only, 1C_only, Matched по порядку;
    иначе любое другое встретившееся значение (в витрине это No_activity); пусто — «—».
    """
    if df.empty:
        return pd.DataFrame(columns=["lab_key", "bin", "status"])
    latest = df["month"].max()
    d = df.loc[df["month"] == latest, ["lab_key", "bin", "MatchStatus"]].copy()
    status = d["MatchStatus"].astype("object")
    present = status.notna()
    text = status.where(present, "").astype(str)
    d["lis"] = text == "LIS_only"
    d["onec"] = text == "1C_only"
    d["matched"] = text == "Matched"
    d["other"] = text.where(present & ~text.isin(_KNOWN_STATUSES))
    g = d.groupby(["lab_key", "bin"], dropna=False, observed=True, sort=True).agg(
        lis=("lis", "max"), onec=("onec", "max"), matched=("matched", "max"), other=("other", "first"))
    lis, onec, matched = g["lis"].to_numpy(bool), g["onec"].to_numpy(bool), g["matched"].to_numpy(bool)
    other = g["other"].astype("object").where(g["other"].notna(), "—").to_numpy()
    result = np.select([lis & onec, lis, onec, matched], ["Mixed", "LIS_only", "1C_only", "Matched"], default=other)
    return g.index.to_frame(index=False).assign(status=result)


def _uniq_text(frame: pd.DataFrame, keys: list[str], column: str) -> pd.Series:
    """Эквивалент report_core.uniq_text в groupby: уникальные значения в порядке
    появления, пробелы схлопнуты, не больше шести, через « | »."""
    values = frame[column].astype("object")
    norm = values.where(values.isna(), values.astype(str).str.replace(r"\s+", " ", regex=True).str.strip())
    work = frame[keys].copy()
    work["v"] = norm
    work = work[work["v"].notna() & (work["v"] != "")].drop_duplicates(keys + ["v"])
    work = work[work.groupby(keys, sort=False).cumcount() < 6]
    return work.groupby(keys, sort=False)["v"].agg(" | ".join)


def build_watchlist(df: pd.DataFrame, anomalies: pd.DataFrame) -> pd.DataFrame:
    """Эквивалент report_core.build_watchlist: склейка причин и метка приоритета —
    без построчных apply."""
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

    latest_df = df[df["month"] == latest]
    lm = latest_df.groupby(["lab_key", "bin"], dropna=False).agg(
        last_issued=("OneC_Issued", "sum"), last_consumed=("LIS_Consumed", "sum")).reset_index()
    lm["last_gap"] = (lm["last_issued"] - lm["last_consumed"]).clip(lower=0)
    lm["last_utilization_pct"] = np.where(lm["last_issued"] > 0, lm["last_consumed"] / lm["last_issued"] * 100.0, np.nan)
    base = base.merge(lm, on=["lab_key", "bin"], how="left")

    if anomalies is not None and not anomalies.empty:
        from urm.report_core import SEVERITY_RANK

        keys = ["lab_key", "БИН"]
        ac = anomalies.copy()
        ac["severity_rank"] = ac["severity"].map(SEVERITY_RANK).fillna(0)
        agg = ac.groupby(keys).agg(severity_rank=("severity_rank", "max"))
        agg["reasons"] = _uniq_text(ac, keys, "Тип аномалии").reindex(agg.index).fillna("")
        agg["why"] = _uniq_text(ac, keys, "Почему").reindex(agg.index).fillna("")
        agg = agg.reset_index().rename(columns={"БИН": "bin"})
        base = base.merge(agg, on=["lab_key", "bin"], how="left")
    else:
        base["severity_rank"] = 0
        base["reasons"] = ""
        base["why"] = ""

    issued = base["last_issued"].fillna(0).to_numpy(dtype="float64")
    consumed = base["last_consumed"].fillna(0).to_numpy(dtype="float64")
    util = base["last_utilization_pct"].to_numpy(dtype="float64")
    gap = base["unaccounted"].fillna(0).to_numpy(dtype="float64")
    has_util = ~np.isnan(util)
    base["priority_label"] = np.select(
        [
            (issued > 0) & (consumed == 0),
            (issued > 0) & has_util & (util <= CFG.critical_utilization_pct),
            (issued > 0) & has_util & (util <= CFG.high_utilization_pct),
            gap > 0,
        ],
        [
            "Срочно: выдали, а в ЛИС 0",
            "Срочно: очень низкое использование после выдачи",
            "Приоритет: низкое использование после выдачи",
            "Нужно проверить накопленный неиспользованный объем",
        ],
        default="Контроль",
    )
    base["priority_score"] = base["severity_rank"].fillna(0) * 1_000_000 + base["last_gap"].fillna(0) * 10 + base["unaccounted"].fillna(0)
    work = base[(base["severity_rank"].fillna(0) >= 3) | (base["last_gap"].fillna(0) > 0)].copy()
    return work.sort_values(["priority_score", "last_gap", "unaccounted"], ascending=[False, False, False])


def summaries(df: pd.DataFrame, anomalies: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """report_core.summaries с ускоренным статусом заказчика.

    Сам расчёт сводок — функция генератора как есть; заменяется только
    вызываемая ею customer_status_latest на эквивалентную векторную.
    """
    # Подмена на время вызова. При одновременных вызовах из нескольких потоков
    # в худшем случае отработает исходная функция — результат тот же.
    _rc.customer_status_latest = customer_status_latest
    try:
        return _rc.summaries(df, anomalies)
    finally:
        _rc.customer_status_latest = _ORIGINAL_STATUS


# Пары «ключ → подпись», у которых подпись должна быть одна на ключ.
CANONICAL = (("bin", "partner_name"), ("lab_key", "lab_display_name"), ("container_group", "container_display_name"))


def canonical_names(frame: pd.DataFrame) -> pd.DataFrame:
    """Одна подпись на ключ: один БИН — одно название заказчика, один lab_key — один город,
    один код материала — одно название.

    В витринах название контрагента берётся из документов месяца и у одного БИН
    бывает записано по-разному. Тогда генератор, группируя по паре БИН + название,
    показывает одного заказчика несколькими строками, а в выпадающих списках
    появляются неотличимые дубликаты. Берётся название с наибольшим объёмом
    (выдача + использование), при равенстве — самое свежее.
    """
    out = frame.copy()
    volume = out["OneC_Issued"].fillna(0).astype("float64") + out["LIS_Consumed"].fillna(0).astype("float64")
    for key, name in CANONICAL:
        if key not in out.columns or name not in out.columns:
            continue
        work = pd.DataFrame({"k": out[key].astype("object"), "n": out[name].astype("object"),
                             "v": volume, "m": out["month"]})
        g = (work.dropna(subset=["n"]).groupby(["k", "n"], dropna=False, sort=False)
             .agg(v=("v", "sum"), m=("m", "max")).reset_index()
             .sort_values(["k", "v", "m", "n"], ascending=[True, False, False, True])
             .drop_duplicates("k"))
        mapping = dict(zip(g["k"], g["n"]))
        out[name] = out[key].astype("object").map(mapping).fillna(out[name].astype("object"))
    return out
