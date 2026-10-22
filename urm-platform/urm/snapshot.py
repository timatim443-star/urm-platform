"""Снимок данных: PostgreSQL → Parquet → DuckDB → готовые таблицы интерфейса.

Зачем. Витрины меняются раз в сутки, а при каждом входе приложение тянуло
всё по сети и заново считало аномалии. На десятках миллионов строк это
минуты и гигабайты памяти. Теперь тяжёлое делается один раз на публикацию.

Слои:
  raw/<таблица>.parquet   — выгрузка из PostgreSQL потоково, порциями
                            (память не зависит от объёма таблицы);
  main.parquet            — свёртка DuckDB до зерна интерфейса:
                            месяц × лаборатория (= город) × материал × БИН;
  anomalies.parquet       — аномалии генератора, посчитанные один раз;
  остальные таблицы       — читаются на странице через DuckDB с фильтром,
                            в память целиком не поднимаются.

Атомарность: снимок пишется в скрытый каталог и переименовывается целиком,
указатель CURRENT меняется одной операцией. Сбой сборки не трогает текущий
снимок. Хранятся два последних — для отката.

Нижний слой заменяемый: сейчас источник — витрины urm2. При переходе на
оригинальную базу меняются только запросы выгрузки (SOURCES) и SQL свёртки
(MAIN_SQL); страницы и расчёты остаются прежними.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
from psycopg import sql

from urm import fast, packs
from urm.config import SCHEMA
from urm.db import ro_connection
from urm.fast import build_anomalies
from urm.loader import NUMERIC, QUERIES, RENAME
from urm.report_core import enrich_fact

log = logging.getLogger(__name__)

ROOT = Path(os.environ.get("URM_SNAPSHOT_DIR", "snapshots")).resolve()
POINTER = ROOT / "CURRENT"
KEEP = max(2, int(os.environ.get("URM_SNAPSHOT_KEEP", "2") or 2))  # сколько снимков хранить (≥ 2)
CHUNK = int(os.environ.get("URM_SNAPSHOT_CHUNK", "200000"))
_LOCK = threading.Lock()
_STATE = {"building": False, "error": "", "started": 0.0, "stage": ""}

SOURCES: list[tuple[str, str, tuple[str, ...]]] = QUERIES

# Таблицы, которые страница читает целиком (они малы) — держатся в памяти.
IN_MEMORY = ("main", "anomalies", "lab", "containers", "statuses")
# Таблицы, которые страница фильтрует через DuckDB (могут быть большими).
ON_DEMAND = (
    "exceptions", "procedure", "internal", "quality_issues", "mapping_conflicts",
    "mapping_coverage", "quality_funnel", "unmapped_lis", "unmapped_onec",
)

MAIN_SQL = r"""
WITH f AS (
    SELECT month, lab_key, container_group, bin,
           any_value(lab_display_name)        AS lab_display_name,
           any_value(container_display_name)  AS container_display_name,
           coalesce(sum(LIS_Consumed), 0)     AS LIS_Consumed,
           coalesce(sum(OneC_Issued), 0)      AS OneC_Issued
    FROM read_parquet($main)
    GROUP BY month, lab_key, container_group, bin
),
p AS (
    SELECT month, lab_key, container_group, bin,
           -- Как uniq_text генератора: пробелы схлопнуты, без повторов, не более 6 имён.
           array_to_string(list_slice(list_sort(list_distinct(list(
               nullif(regexp_replace(trim(LIS_Hospitals), '\s+', ' ', 'g'), '')))), 1, 6), ' | ')
               AS LIS_Hospitals,
           array_to_string(list_slice(list_sort(list_distinct(list(
               nullif(regexp_replace(trim(OneC_Counterparties), '\s+', ' ', 'g'), '')))), 1, 6), ' | ')
               AS OneC_Counterparties
    FROM read_parquet($parties)
    GROUP BY month, lab_key, container_group, bin
)
SELECT f.*,
       -- Статус пересчитывается из сумм, а не наследуется от строки (раздел 4 руководства).
       CASE WHEN f.LIS_Consumed > 0 AND f.OneC_Issued > 0 THEN 'Matched'
            WHEN f.LIS_Consumed > 0 THEN 'LIS_only'
            WHEN f.OneC_Issued  > 0 THEN '1C_only'
            ELSE 'No_activity' END AS MatchStatus,
       p.LIS_Hospitals, p.OneC_Counterparties
FROM f LEFT JOIN p USING (month, lab_key, container_group, bin)
"""


# ----------------------------------------------------------------- чтение

def current_dir() -> Path | None:
    try:
        name = POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    path = ROOT / name
    return path if (path / "meta.json").exists() else None


def current_meta() -> dict:
    path = current_dir()
    if path is None:
        return {}
    try:
        return json.loads((path / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_memory(path: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_parquet(path / f"{name}.parquet") for name in IN_MEMORY}


def query(path: Path, table: str, where: str = "", params: list | None = None,
          order: str = "", limit: int | None = None) -> pd.DataFrame:
    """Отбор из большой таблицы снимка без загрузки её целиком."""
    if table not in ON_DEMAND:
        raise ValueError(f"Таблица {table} не читается по запросу.")
    statement = "SELECT * FROM read_parquet(?)"  # noqa: S608 — путь передаётся параметром
    if where:
        statement += f" WHERE {where}"
    if order:
        statement += f" ORDER BY {order}"
    if limit:
        statement += f" LIMIT {int(limit)}"
    with duckdb.connect() as con:
        return con.execute(statement, [(path / f"{table}.parquet").as_posix(), *(params or [])]).df()


def status() -> dict:
    return dict(_STATE)


# ----------------------------------------------------------------- выгрузка

def _arrow_type(column: str) -> pa.DataType:
    if column == "month":
        return pa.timestamp("ns")
    if column in NUMERIC:
        return pa.float64()
    return pa.string()


def _extract(conn: psycopg.Connection, name: str, statement: str, target: Path) -> int:
    """Потоковая выгрузка через COPY: PostgreSQL отдаёт CSV, PyArrow разбирает порциями.

    Строки не проходят через Python поштучно — это в разы быстрее курсора.
    Память не зависит от объёма: CSV пишется во временный файл рядом
    и сразу перекладывается в Parquet блоками, после чего удаляется.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM ({statement}) AS q LIMIT 0")  # noqa: S608 — запросы фиксированы в коде
        columns = [RENAME.get(d.name, d.name) for d in cur.description]
    schema = pa.schema([(c, _arrow_type(c)) for c in columns])
    csv_path = target.with_suffix(".csv")
    with conn.cursor() as cur, open(csv_path, "wb") as handle:
        with cur.copy(f"COPY ({statement}) TO STDOUT WITH (FORMAT csv)") as copy:  # noqa: S608
            for chunk in copy:
                handle.write(chunk)

    total = 0
    # Файл открываем сами и закрываем до удаления: на Windows открытый файл
    # удалить нельзя (WinError 32), а читатель PyArrow держит его до сборки мусора.
    with pq.ParquetWriter(target, schema, compression="zstd") as writer, open(csv_path, "rb") as source:
        if csv_path.stat().st_size:
            reader = pacsv.open_csv(
                source,
                read_options=pacsv.ReadOptions(column_names=columns, block_size=64 << 20),
                parse_options=pacsv.ParseOptions(newlines_in_values=True),
                convert_options=pacsv.ConvertOptions(
                    column_types={f.name: f.type for f in schema},
                    # В CSV PostgreSQL NULL — пустое поле без кавычек, пустая строка — "".
                    null_values=[""], strings_can_be_null=True, quoted_strings_can_be_null=False,
                ),
            )
            try:
                for batch in reader:
                    writer.write_batch(batch)
                    total += batch.num_rows
            finally:
                reader.close()
                del reader
        else:
            writer.write_table(schema.empty_table())
    csv_path.unlink(missing_ok=True)
    return total


def build(key: str) -> Path:
    started = time.time()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ROOT.mkdir(parents=True, exist_ok=True)
    work = ROOT / f".{stamp}.tmp"
    if work.exists():
        shutil.rmtree(work)
    (work / "raw").mkdir(parents=True)

    # 1. Выгрузка одним согласованным снимком базы.
    _STATE["stage"] = "выгрузка из PostgreSQL"
    rows: dict[str, int] = {}
    lock_tables = list(dict.fromkeys(t for _, _, tables in SOURCES for t in tables))
    with ro_connection(statement_timeout_ms=6 * 3600 * 1000) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.transaction():
            conn.execute(
                sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(
                    sql.SQL(", ").join(sql.Identifier(SCHEMA, t) for t in lock_tables)
                )
            )
            for name, statement, _ in SOURCES:
                rows[f"raw.{name}"] = _extract(conn, name, statement, work / "raw" / f"{name}.parquet")
    extracted = time.time()

    # 2. Свёртка до зерна интерфейса — DuckDB, без подъёма сырых строк в память.
    _STATE["stage"] = "свёртка"
    raw = work / "raw"
    with duckdb.connect() as con:
        main = con.execute(
            MAIN_SQL, {"main": (raw / "main.parquet").as_posix(), "parties": (raw / "parties.parquet").as_posix()}
        ).df()
        for name in ON_DEMAND + ("lab", "containers", "statuses"):
            # Путь в COPY — литерал SQL: прямые слэши и экранированные кавычки,
            # чтобы работали пути Windows с пробелами и кириллицей.
            target = (work / f"{name}.parquet").as_posix().replace("'", "''")
            con.execute(
                f"COPY (SELECT * FROM read_parquet(?)) TO '{target}' (FORMAT parquet, COMPRESSION zstd)",
                [(raw / f"{name}.parquet").as_posix()],
            )
    aggregated = time.time()

    # 3. Расчёты генератора на свёрнутом зерне.
    _STATE["stage"] = "расчёт аномалий"
    main["month"] = pd.to_datetime(main["month"])
    for column in ("bin", "lab_key", "container_group"):
        main[column] = main[column].fillna("").astype(str)
    main["partner_name"] = fast.partner_names(main)
    main = main.drop(columns=["LIS_Hospitals", "OneC_Counterparties"])
    main = fast.canonical_names(main)  # один БИН — одно название (иначе дубликаты заказчиков)
    fact = enrich_fact(main)
    anomalies = build_anomalies(fact)
    fact.to_parquet(work / "main.parquet", index=False, compression="zstd")
    anomalies.to_parquet(work / "anomalies.parquet", index=False, compression="zstd")
    rows["main"] = len(fact)
    rows["anomalies"] = len(anomalies)
    computed = time.time()

    # 4. Пакеты типовых срезов: вся сеть и каждый город × весь период и 12/6/3 мес.
    _STATE["stage"] = "пакеты срезов"
    earliest, latest = fact["month"].min(), fact["month"].max()
    procedure_path = (work / "procedure.parquet").as_posix()
    built = 0
    with duckdb.connect() as con:
        for lab in [None, *sorted(fact["lab_key"].dropna().unique())]:
            for months in (None, *packs.PRESET_MONTHS):
                start, end = packs.window(latest, months, earliest)
                full = months is None or start <= earliest
                if months is not None and full:
                    continue  # период не короче всей истории — это тот же «весь период»
                part, part_anomalies = packs.scope_fact(fact, anomalies, lab, start, end, full)
                where = "month BETWEEN ? AND ?" + (" AND lab_key = ?" if lab else "")
                procedure = con.execute(
                    f"SELECT * FROM read_parquet(?) WHERE {where}",  # noqa: S608
                    [procedure_path, start, end, *([lab] if lab else [])],
                ).df()
                packs.write(packs.make(part, part_anomalies, procedure),
                            work / "packs" / packs.pack_id(lab, months))
                built += 1
    rows["packs"] = built
    packed = time.time()

    shutil.rmtree(raw, ignore_errors=True)  # сырой слой нужен только для свёртки
    meta = {
        "publication_key": key,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": {
            "extract": round(extracted - started, 1),
            "aggregate": round(aggregated - extracted, 1),
            "compute": round(computed - aggregated, 1),
            "packs": round(packed - computed, 1),
            "total": round(time.time() - started, 1),
        },
        "rows": rows,
        "size_mb": round(sum(f.stat().st_size for f in work.rglob("*.parquet")) / 1e6, 1),
        "preset_months": list(packs.PRESET_MONTHS),
    }
    (work / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    final = ROOT / stamp
    os.replace(work, final)
    pointer = ROOT / "CURRENT.tmp"
    pointer.write_text(stamp, encoding="utf-8")
    os.replace(pointer, POINTER)
    _cleanup()
    log.info("Снимок %s: %s", stamp, meta["seconds"])
    return final


def _cleanup() -> None:
    current = current_dir()
    snapshots = sorted(p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith("."))
    for path in snapshots[:-KEEP]:
        if path != current:
            shutil.rmtree(path, ignore_errors=True)
    for path in ROOT.glob(".*.tmp"):
        if path.is_dir() and time.time() - path.stat().st_mtime > 3600:
            shutil.rmtree(path, ignore_errors=True)


def ensure(key: str, background: bool = True) -> None:
    """Пересобрать снимок, если он отстал от публикации витрин.

    Снимка нет — сборка синхронная. Снимок устарел — сборка в фоне,
    до её окончания пользователи видят предыдущий.
    """
    if current_meta().get("publication_key") == key:
        return
    if current_dir() is None:
        background = False
    if background:
        if not _STATE["building"]:
            threading.Thread(target=_guarded, args=(key,), daemon=True).start()
        return
    _guarded(key)
    if _STATE["error"] and current_dir() is None:
        raise RuntimeError(_STATE["error"])


def _guarded(key: str) -> None:
    if not _LOCK.acquire(blocking=False):
        with _LOCK:  # сборка уже идёт — дождаться
            return
    try:
        if current_meta().get("publication_key") == key:
            return
        _STATE.update(building=True, error="", started=time.time(), stage="")
        build(key)
    except Exception as exc:  # noqa: BLE001
        log.exception("Сборка снимка не удалась")
        _STATE["error"] = str(exc)
    finally:
        _STATE.update(building=False, stage="")
        _LOCK.release()
