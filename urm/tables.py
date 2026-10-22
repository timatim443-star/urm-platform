"""Таблицы платформы, изменённые по сравнению с генератором, и выгрузка в Excel.

  * signals_by_customer — сигналы, сведённые до заказчика (город × БИН):
    одна строка на заказчика вместо строки на каждый материал. Используется
    в «Что требует внимания» (01) и «Реестре аномалий» (07). Подробная
    разбивка по материалам остаётся в выгрузке Excel и карточке заказчика.
  * customer_labels — подписи заказчиков для графиков без склейки двух разных
    БИН с одинаковым названием.
  * xlsx — книга Excel из таблиц (для маленьких кнопок «Excel» у таблиц).
"""
from __future__ import annotations

import html as htmllib
import io

import pandas as pd

from urm.report_core import SEVERITY_RANK, fmt_num, severity_badge

KEYS = ["lab_key", "Лаборатория", "БИН", "Заказчик"]


def _join_unique(values: pd.Series, limit: int | None = None) -> str:
    """Уникальные значения через «; ». limit=None — все, без «и ещё N»."""
    seen: list[str] = []
    for value in values.dropna().astype(str):
        value = value.strip()
        if value and value not in seen:
            seen.append(value)
    if limit is None or len(seen) <= limit:
        return "; ".join(seen)
    return "; ".join(seen[:limit]) + f" и ещё {len(seen) - limit}"


def signals_by_customer(anomalies: pd.DataFrame) -> pd.DataFrame:
    """Одна строка на заказчика (город × БИН): суммы по всем его материалам."""
    if anomalies is None or anomalies.empty:
        return pd.DataFrame()
    a = anomalies.copy()
    a["_rank"] = a["severity"].map(SEVERITY_RANK).fillna(0)
    a["_unacc"] = pd.to_numeric(a["Неотражено_ед"], errors="coerce").fillna(0)
    a["_delta"] = pd.to_numeric(a["Расчетная дельта"], errors="coerce").fillna(0)
    a = a.sort_values(["_rank", "_unacc"], ascending=[False, False])
    main = a.drop_duplicates(KEYS)  # самый тяжёлый сигнал заказчика — первая строка
    agg = (
        a.groupby(KEYS, dropna=False, sort=False)
        .agg(materials=("Контейнер", "nunique"), unaccounted=("_unacc", "sum"), delta=("_delta", "sum"),
             reasons=("Тип аномалии", _join_unique), materials_list=("Контейнер", _join_unique),
             directions=("Направление", _join_unique), period=("Период", "max"))
        .reset_index()
    )
    out = main[KEYS + ["severity", "_rank", "Тип аномалии", "Почему"]].merge(agg, on=KEYS, how="left")
    # причины, кроме главной — она уже в своей колонке
    out["other_reasons"] = [
        "; ".join(x for x in str(all_).split("; ") if x and x != str(main_)) or "—"
        for all_, main_ in zip(out["reasons"], out["Тип аномалии"])
    ]
    out = out.sort_values(["_rank", "unaccounted", "delta"], ascending=[False, False, False]).reset_index(drop=True)
    return out.rename(columns={"Тип аномалии": "main_reason", "Почему": "why"})


def signals_display(grouped: pd.DataFrame) -> pd.DataFrame:
    """Колонки для экрана и Excel."""
    if grouped.empty:
        return grouped
    from urm.report_core import severity_ru

    return pd.DataFrame({
        "Критичность": grouped["severity"].map(severity_ru),
        "БИН": grouped["БИН"],
        "Заказчик": grouped["Заказчик"],
        "Лаборатория": grouped["Лаборатория"],
        "Главная причина": grouped["main_reason"],
        "Материалов с сигналом": grouped["materials"],
        "Не подтверждено в ЛИС за последний месяц, ед.": grouped["unaccounted"],
        "Расчетная дельта*, ед.": grouped["delta"],
        "Другие причины": grouped["other_reasons"],
        "Материалы": grouped["materials_list"],
        "Направление": grouped["directions"],
        "Период": grouped["period"],
    })


def signals_html(grouped: pd.DataFrame, max_rows: int = 250) -> str:
    if grouped is None or grouped.empty:
        return '<div class="empty">Нет аналитических сигналов</div>'
    shown = grouped.head(max_rows)
    head = ["Критичность", "БИН", "Заказчик", "Лаборатория", "Главная причина", "Материалов",
            "Не подтверждено в ЛИС, ед.", "Расчетная дельта*, ед.", "Другие причины", "Материалы", "Период"]
    rows = []
    for r in shown.itertuples(index=False):
        cells = [
            severity_badge(r.severity), htmllib.escape(str(r.БИН)), htmllib.escape(str(r.Заказчик)),
            htmllib.escape(str(r.Лаборатория)), htmllib.escape(str(r.main_reason)), fmt_num(r.materials),
            fmt_num(r.unaccounted), fmt_num(r.delta), htmllib.escape(r.other_reasons),
            htmllib.escape(r.materials_list), htmllib.escape(str(r.period)),
        ]
        css = {0: "nowrap", 1: "nowrap", 2: "wide", 4: "wide", 5: "num", 6: "num", 7: "num", 8: "wide", 10: "nowrap"}
        rows.append("<tr>" + "".join(f'<td class="{css[i]}">{c}</td>' if i in css else f"<td>{c}</td>"
                                     for i, c in enumerate(cells)) + "</tr>")
    note = (f'<div class="table-note">Показано {fmt_num(len(shown))} из {fmt_num(len(grouped))} заказчиков. '
            "Полный перечень и разбивка по материалам — кнопка Excel.</div>" if len(grouped) > len(shown) else "")
    header = "".join(f"<th>{htmllib.escape(h)}</th>" for h in head)
    from urm.style import compact

    return compact(f'<div class="table-wrap"><table class="data-table"><thead><tr>{header}</tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table></div>{note}')


def customer_labels(frame: pd.DataFrame, name: str = "partner_name", bin_col: str = "bin") -> pd.Series:
    """Название заказчика; если у разных БИН одинаковое название — с БИН, чтобы столбики не слиплись."""
    names = frame[name].astype(str)
    clash = names.map(names.value_counts()) > 1
    return names.where(~clash, names + " · " + frame[bin_col].astype(str))


def xlsx(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Книга Excel: заголовок закреплён, автофильтр, ширина колонок по содержимому."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        header = writer.book.add_format({"bold": True, "bg_color": "#EAF2FF", "border": 1,
                                         "text_wrap": True, "valign": "top"})
        for raw_name, frame in sheets.items():
            name = (raw_name or "Лист")[:31]
            frame = frame if frame is not None else pd.DataFrame()
            frame = frame.copy()
            for column in frame.columns:
                if isinstance(frame[column].dtype, pd.DatetimeTZDtype):
                    frame[column] = frame[column].dt.tz_localize(None)
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes(1, 0)
            if len(frame.columns):
                sheet.autofilter(0, 0, max(len(frame), 1), len(frame.columns) - 1)
            for i, column in enumerate(frame.columns):
                sample = frame[column].head(200).astype(str).str.len().max() if len(frame) else 0
                width = min(max(len(str(column)) * 0.9, (sample or 0) * 1.1, 8), 60)
                sheet.set_column(i, i, width)
                sheet.write(0, i, str(column), header)
            sheet.set_row(0, 32)
    return buffer.getvalue()
