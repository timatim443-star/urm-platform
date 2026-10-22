"""Точечное редактирование шести входных таблиц.

Правила (разделы 6–9 руководства):
  * только фиксированный allowlist таблиц и колонок, имена — через
    psycopg.sql.Identifier, значения — параметрами драйвера;
  * строка блокируется FOR UPDATE и сверяется со всеми ранее прочитанными
    значениями: иначе правка затрёт чужую;
  * при UPDATE всегда явно ставится updated_at = now() — триггер этого не делает;
  * TRUNCATE и массовое удаление не поддерживаются: полностью пустая входная
    таблица будет автоматически заполнена стартовыми значениями при следующей
    инициализации runner, то есть удаление правил не является устойчивым
    способом их выключить. Для выключения используется is_active.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from urm.config import SCHEMA
from urm.db import DatabaseUnavailable, fetch_rows, rw_connection

MAPPING_TABLES = ("container_mapping", "lab_mapping")
RULE_TABLES = (
    "lis_excluded_category",
    "onec_excluded_brand",
    "onec_internal_excluded_warehouse_group",
    "lis_procedure_category",
)


@dataclass(frozen=True)
class TableSpec:
    name: str
    title: str
    key: str
    fields: tuple[str, ...]
    required: tuple[str, ...]
    active_kind: str  # "smallint" | "boolean"
    hint: str


SPECS: dict[str, TableSpec] = {
    "container_mapping": TableSpec(
        name="container_mapping",
        title="Маппинг контейнеров",
        key="row_id",
        fields=(
            "container_group",
            "lis_container_code",
            "lis_container_name",
            "onec_mdm_key",
            "is_active",
            "valid_from",
            "valid_to",
            "comment",
        ),
        required=("container_group",),
        active_kind="smallint",
        hint=(
            "Коды сопоставляются точным строковым равенством после обрезки краевых пробелов. "
            "Один непустой MDM должен иметь не более одной действующей строки на дату. "
            "Смену маппинга планируйте по границам месяцев: для LIS сравнивается первое число месяца."
        ),
    ),
    "lab_mapping": TableSpec(
        name="lab_mapping",
        title="Маппинг лабораторий",
        key="row_id",
        fields=(
            "lab_key",
            "lab_display_name",
            "lis_lab_name",
            "onec_warehouse_group",
            "is_active",
            "confidence",
            "comment",
        ),
        required=("lab_key", "lab_display_name"),
        active_kind="smallint",
        hint=(
            "Сопоставление точное и регистрозависимое. Запятые в группе склада 1С "
            "разворачиваются в отдельные алиасы — предпочтительна одна группа на строку. "
            "confidence расчёт не использует."
        ),
    ),
    "lis_excluded_category": TableSpec(
        name="lis_excluded_category",
        title="Исключаемые категории заказчиков LIS",
        key="value",
        fields=("value", "is_active", "comment"),
        required=("value",),
        active_kind="boolean",
        hint="Вхождение подстроки без учёта регистра в категорию заказчика LIS. Исключение выполняется до классификации ПК.",
    ),
    "onec_excluded_brand": TableSpec(
        name="onec_excluded_brand",
        title="Исключаемые бренды и организации 1С",
        key="value",
        fields=("value", "is_active", "comment"),
        required=("value",),
        active_kind="boolean",
        hint="Вхождение без учёта регистра в номенклатуру, организацию ИЛИ клиента. Короткий токен исключает лишнее.",
    ),
    "onec_internal_excluded_warehouse_group": TableSpec(
        name="onec_internal_excluded_warehouse_group",
        title="Исключаемые группы складов внутреннего потребления",
        key="value",
        fields=("value", "is_active", "comment"),
        required=("value",),
        active_kind="boolean",
        hint=(
            "Точное регистрозависимое совпадение после обрезки пробелов. "
            "Для пустого склада используйте значение «ПУСТЫЕ СКЛАДЫ»."
        ),
    ),
    "lis_procedure_category": TableSpec(
        name="lis_procedure_category",
        title="Категории процедурных кабинетов LIS",
        key="value",
        fields=("value", "is_active", "comment"),
        required=("value",),
        active_kind="boolean",
        hint=(
            "Вхождение без учёта регистра: строка классифицируется как ПК, а не исключается. "
            "Если активных строк не останется, расчёт применит встроенное значение «Процедурные кабинеты»."
        ),
    ),
}

TEXT_FIELDS = {
    field
    for spec in SPECS.values()
    for field in spec.fields
    if field not in ("is_active", "valid_from", "valid_to")
}


log = logging.getLogger(__name__)


def _guard(func):
    """Серверную ошибку наружу не отдаём: пользователю — короткий текст, подробности — в лог."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ValidationError, StaleRowError, DatabaseUnavailable):
            raise
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction) as exc:
            log.error("Write denied: %s", exc)
            raise DatabaseUnavailable(
                "Недостаточно прав учётной записи на запись в этот справочник. "
                "Обратитесь к администратору PostgreSQL."
            ) from exc
        except psycopg.errors.IntegrityError as exc:
            # Нарушено правило самой базы (обязательное поле, уникальность, ссылка):
            # это ошибка данных, а не сбой — показываем, какое правило и какое поле.
            log.warning("Write rejected by constraint: %s", exc)
            diag = exc.diag
            reasons = {
                psycopg.errors.NotNullViolation: f"поле «{diag.column_name}» обязательно в базе",
                psycopg.errors.UniqueViolation: "такая строка уже есть (нарушена уникальность"
                                                + (f" «{diag.constraint_name}»" if diag.constraint_name else "") + ")",
                psycopg.errors.ForeignKeyViolation: "значение не найдено в связанном справочнике",
                psycopg.errors.CheckViolation: f"не выполнено условие «{diag.constraint_name}»",
            }
            reason = next((text for kind, text in reasons.items() if isinstance(exc, kind)),
                          diag.message_primary or "нарушено ограничение таблицы")
            raise ValidationError(f"База не приняла изменение: {reason}.") from exc
        except psycopg.Error as exc:
            log.error("Write failed: %s", exc)
            raise DatabaseUnavailable(
                "Не удалось сохранить изменение. Повторите попытку позже; "
                "подробности — в журнале приложения."
            ) from exc

    return wrapper


class StaleRowError(RuntimeError):
    """Строка изменилась с момента чтения: перечитать и показать расхождение."""


class ValidationError(ValueError):
    pass


def _spec(table: str) -> TableSpec:
    if table not in SPECS:
        raise ValidationError("Таблица не разрешена для редактирования.")
    return SPECS[table]


def validate(table: str, values: dict) -> list[str]:
    """Проверки уровня строки. Бизнес-конфликты всей таблицы — в urm.validate."""
    spec = _spec(table)
    errors: list[str] = []
    for field in spec.fields:
        if field not in values:
            errors.append(f"Не передано поле «{field}».")
    for field in spec.required:
        value = values.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Поле «{field}» обязательно и не может быть пустым.")
    for field in spec.fields:
        if field in TEXT_FIELDS and values.get(field) is not None:
            if not isinstance(values[field], str):
                errors.append(f"Поле «{field}» должно быть текстом, NULL не допускается.")
            elif values[field] != values[field].strip():
                errors.append(f"В поле «{field}» есть краевые пробелы — уберите их.")
    active = values.get("is_active")
    if spec.active_kind == "smallint":
        if not isinstance(active, int) or isinstance(active, bool) or active not in (0, 1):
            errors.append("Маппинг использует целое 0 или 1 в поле is_active.")
    else:
        if not isinstance(active, bool):
            errors.append("Правило использует логическое true/false в поле is_active.")
    if table == "container_mapping":
        for field in ("valid_from", "valid_to"):
            value = values.get(field)
            if value is not None and not isinstance(value, date):
                errors.append(f"Поле «{field}» должно быть датой или пустым.")
        start, end = values.get("valid_from"), values.get("valid_to")
        if isinstance(start, date) and isinstance(end, date) and start > end:
            errors.append("Конец периода действия раньше начала.")
        if not str(values.get("lis_container_code", "")).strip() and not str(
            values.get("onec_mdm_key", "")
        ).strip():
            errors.append(
                "Обе стороны пусты. Пустой ключ способен сопоставиться с пустым ключом источника — "
                "такую строку заводить нельзя."
            )
    if table == "lab_mapping":
        if not str(values.get("lis_lab_name", "")).strip() and not str(
            values.get("onec_warehouse_group", "")
        ).strip():
            errors.append("Нужен хотя бы один алиас: имя лаборатории LIS или группа склада 1С.")
    if table in RULE_TABLES:
        if values.get("is_active") and not str(values.get("value", "")).strip():
            errors.append("Активное правило с пустым значением исключит все данные.")
    return errors


def read_rows(table: str, limit: int = 5000) -> list[dict]:
    """Свежее чтение без кэша: редактор обязан видеть актуальное состояние."""
    spec = _spec(table)
    columns = ", ".join([spec.key, *[f for f in spec.fields if f != spec.key], "updated_at"])
    order = "row_id" if spec.key == "row_id" else "value"
    return fetch_rows(
        f"SELECT {columns} FROM {SCHEMA}.{spec.name} ORDER BY {order} LIMIT %s",  # noqa: S608
        (limit,),
    )


def count_active(table: str) -> tuple[int, int]:
    spec = _spec(table)
    rows = fetch_rows(
        f"SELECT count(*) AS total, count(*) FILTER (WHERE is_active::int = 1) AS active "  # noqa: S608
        f"FROM {SCHEMA}.{spec.name}"
    )
    row = rows[0]
    return int(row["total"]), int(row["active"])


def _identifier(table: str) -> sql.Identifier:
    return sql.Identifier(SCHEMA, table)


@_guard
def update_row(table: str, expected: dict, changes: dict) -> dict:
    """UPDATE одной строки с проверкой всех ранее прочитанных значений."""
    spec = _spec(table)
    if not changes:
        raise ValidationError("Нет изменений.")
    if not set(changes).issubset(spec.fields):
        raise ValidationError("Передано недопустимое поле.")
    if spec.key == "value" and "value" in changes:
        raise ValidationError(
            "Ключ правила меняется отдельной согласованной операцией: заведите новое значение "
            "и выключите старое."
        )
    merged = {**{field: expected.get(field) for field in spec.fields}, **changes}
    errors = validate(table, merged)
    if errors:
        raise ValidationError("; ".join(errors))
    required = set(spec.fields) | {spec.key, "updated_at"}
    missing = required - set(expected)
    if missing:
        raise ValidationError(f"Нет исходных значений строки: {', '.join(sorted(missing))}.")

    identifier = _identifier(spec.name)
    with rw_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE {} = %s FOR UPDATE").format(
                        identifier, sql.Identifier(spec.key)
                    ),
                    (expected[spec.key],),
                )
                current = cur.fetchone()
                if current is None:
                    raise StaleRowError("Строка удалена другим пользователем. Перечитайте справочник.")
                diverged = [
                    field for field in required if current.get(field) != expected.get(field)
                ]
                if diverged:
                    raise StaleRowError(
                        "Строка изменена другим пользователем: " + ", ".join(sorted(diverged))
                    )
                assignments = [
                    sql.SQL("{} = %s").format(sql.Identifier(field)) for field in changes
                ]
                assignments.append(sql.SQL("updated_at = now()"))
                cur.execute(
                    sql.SQL("UPDATE {} SET {} WHERE {} = %s RETURNING *").format(
                        identifier,
                        sql.SQL(", ").join(assignments),
                        sql.Identifier(spec.key),
                    ),
                    [*changes.values(), expected[spec.key]],
                )
                updated = cur.fetchone()
                if updated is None:
                    raise StaleRowError("Не удалось обновить строку. Перечитайте справочник.")
                return dict(updated)


@_guard
def insert_row(table: str, values: dict) -> dict:
    spec = _spec(table)
    errors = validate(table, values)
    if errors:
        raise ValidationError("; ".join(errors))
    fields = [field for field in spec.fields]
    identifier = _identifier(spec.name)
    with rw_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                if spec.key == "value":
                    cur.execute(
                        sql.SQL("SELECT 1 FROM {} WHERE {} = %s").format(
                            identifier, sql.Identifier(spec.key)
                        ),
                        (values["value"],),
                    )
                    if cur.fetchone():
                        raise ValidationError("Такое значение правила уже заведено.")
                cur.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                        identifier,
                        sql.SQL(", ").join(sql.Identifier(field) for field in fields),
                        sql.SQL(", ").join(sql.Placeholder() * len(fields)),
                    ),
                    [values[field] for field in fields],
                )
                created = cur.fetchone()
                return dict(created or {})


@_guard
def delete_row(table: str, expected: dict) -> None:
    """Удаление одной строки. Таблицу нельзя опустошать полностью."""
    spec = _spec(table)
    total, _ = count_active(table)
    if total <= 1:
        raise ValidationError(
            "Нельзя удалить последнюю строку: пустая входная таблица будет автоматически "
            "заполнена стартовыми значениями при следующей инициализации. Выключите строку (is_active)."
        )
    identifier = _identifier(spec.name)
    with rw_connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE {} = %s FOR UPDATE").format(
                        identifier, sql.Identifier(spec.key)
                    ),
                    (expected[spec.key],),
                )
                current = cur.fetchone()
                if current is None:
                    raise StaleRowError("Строка уже удалена.")
                diverged = [
                    field
                    for field in set(spec.fields) | {"updated_at"}
                    if current.get(field) != expected.get(field)
                ]
                if diverged:
                    raise StaleRowError(
                        "Строка изменена другим пользователем: " + ", ".join(sorted(diverged))
                    )
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                        identifier, sql.Identifier(spec.key)
                    ),
                    (expected[spec.key],),
                )


__all__ = [
    "DatabaseUnavailable",
    "MAPPING_TABLES",
    "RULE_TABLES",
    "SPECS",
    "StaleRowError",
    "TableSpec",
    "ValidationError",
    "count_active",
    "delete_row",
    "insert_row",
    "read_rows",
    "update_row",
    "validate",
]
