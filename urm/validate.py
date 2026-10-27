"""Проверки справочников.

Повторяют 02_reference_checks.sql из комплекта и добавляют то, чего в
опубликованной `mapping_conflicts` нет: пересечения периодов по одному MDM и
расхождения признаков дедупликации LIS. Именно они размножают количество
(раздел 6 руководства), а встроенная диагностика их не ловит.

Ни одна функция ничего не пишет.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from urm.db import fetch_df


@dataclass(frozen=True)
class Check:
    code: str
    title: str
    severity: str  # "Критично" | "Разобрать"
    explanation: str
    sql: str


CHECKS: tuple[Check, ...] = (
    Check(
        "empty_required_container",
        "Пустые обязательные ключи в маппинге контейнеров",
        "Критично",
        "Активная строка без группы или без обеих сторон кода не даёт корректного сопоставления.",
        """
        SELECT row_id, container_group, lis_container_code, onec_mdm_key, comment
        FROM urm2.container_mapping
        WHERE is_active = 1
          AND (btrim(container_group) = ''
               OR (btrim(lis_container_code) = '' AND btrim(onec_mdm_key) = ''))
        """,
    ),
    Check(
        "empty_required_lab",
        "Пустые обязательные ключи в маппинге лабораторий",
        "Критично",
        "Активная строка без ключа, подписи или без обоих алиасов источника.",
        """
        SELECT row_id, lab_key, lab_display_name, lis_lab_name, onec_warehouse_group
        FROM urm2.lab_mapping
        WHERE is_active = 1
          AND (btrim(lab_key) = '' OR btrim(lab_display_name) = ''
               OR (btrim(lis_lab_name) = '' AND btrim(onec_warehouse_group) = ''))
        """,
    ),
    Check(
        "mdm_overlap",
        "Один MDM 1С в нескольких активных строках с пересечением периодов",
        "Критично",
        "Для 1С дедупликации нет: такие строки размножают исходное количество, даже если группа одна.",
        """
        SELECT a.row_id AS row_a, b.row_id AS row_b, a.onec_mdm_key,
               a.container_group AS group_a, b.container_group AS group_b,
               a.valid_from AS from_a, a.valid_to AS to_a,
               b.valid_from AS from_b, b.valid_to AS to_b
        FROM urm2.container_mapping AS a
        JOIN urm2.container_mapping AS b ON a.row_id < b.row_id
         AND btrim(a.onec_mdm_key) = btrim(b.onec_mdm_key)
        WHERE a.is_active = 1 AND b.is_active = 1 AND btrim(a.onec_mdm_key) <> ''
          AND coalesce(a.valid_from, '-infinity'::date) <= coalesce(b.valid_to, 'infinity'::date)
          AND coalesce(b.valid_from, '-infinity'::date) <= coalesce(a.valid_to, 'infinity'::date)
        ORDER BY a.onec_mdm_key
        """,
    ),
    Check(
        "lis_dedup_conflict",
        "LIS-код повторяется с разными признаками дедупликации и перекрытием дат",
        "Критично",
        "Несколько MDM на один LIS-код допустимы только при совпадении группы, имени и периода.",
        """
        SELECT a.row_id AS row_a, b.row_id AS row_b, a.lis_container_code,
               a.container_group AS group_a, b.container_group AS group_b,
               a.lis_container_name AS name_a, b.lis_container_name AS name_b,
               a.valid_from AS from_a, a.valid_to AS to_a,
               b.valid_from AS from_b, b.valid_to AS to_b
        FROM urm2.container_mapping AS a
        JOIN urm2.container_mapping AS b ON a.row_id < b.row_id
         AND btrim(a.lis_container_code) = btrim(b.lis_container_code)
        WHERE a.is_active = 1 AND b.is_active = 1 AND btrim(a.lis_container_code) <> ''
          AND coalesce(a.valid_from, '-infinity'::date) <= coalesce(b.valid_to, 'infinity'::date)
          AND coalesce(b.valid_from, '-infinity'::date) <= coalesce(a.valid_to, 'infinity'::date)
          AND (btrim(a.container_group), btrim(a.lis_container_name), a.valid_from, a.valid_to)
              IS DISTINCT FROM
              (btrim(b.container_group), btrim(b.lis_container_name), b.valid_from, b.valid_to)
        ORDER BY a.lis_container_code
        """,
    ),
    Check(
        "group_many_names",
        "У одной группы контейнеров несколько канонических названий",
        "Разобрать",
        "Подпись группы должна быть согласована для всех её строк.",
        """
        SELECT btrim(container_group) AS container_group,
               array_agg(DISTINCT btrim(lis_container_name)) AS names
        FROM urm2.container_mapping
        WHERE is_active = 1 AND btrim(lis_container_name) <> ''
        GROUP BY btrim(container_group)
        HAVING count(DISTINCT btrim(lis_container_name)) > 1
        """,
    ),
    Check(
        "container_exact_duplicates",
        "Точные бизнес-дубли строк маппинга контейнеров",
        "Разобрать",
        "Технические row_id и updated_at в сравнение не входят.",
        """
        SELECT container_group, lis_container_code, lis_container_name, onec_mdm_key,
               is_active, valid_from, valid_to, count(*) AS duplicate_count
        FROM urm2.container_mapping
        GROUP BY container_group, lis_container_code, lis_container_name, onec_mdm_key,
                 is_active, valid_from, valid_to, comment
        HAVING count(*) > 1
        """,
    ),
    Check(
        "lab_alias_conflict",
        "Алиас лаборатории привязан к разным ключам",
        "Критично",
        "Расчёт может выбрать первую строку, а не остановиться с ошибкой. Порядок строк SQL не гарантирован.",
        """
        WITH aliases AS (
            SELECT btrim(lab_key) AS lab_key, btrim(lab_display_name) AS lab_display_name,
                   btrim(lis_lab_name) AS alias, 'LIS' AS side
            FROM urm2.lab_mapping WHERE is_active = 1
            UNION
            SELECT btrim(lab_key), btrim(lab_display_name), btrim(onec_warehouse_group), '1C'
            FROM urm2.lab_mapping WHERE is_active = 1
            UNION
            SELECT btrim(lab_key), btrim(lab_display_name), btrim(part), '1C'
            FROM urm2.lab_mapping
            CROSS JOIN LATERAL unnest(string_to_array(onec_warehouse_group, ',')) AS t(part)
            WHERE is_active = 1
        )
        SELECT alias, side, array_agg(DISTINCT lab_key) AS keys,
               array_agg(DISTINCT lab_display_name) AS names
        FROM aliases WHERE alias <> ''
        GROUP BY alias, side
        HAVING count(DISTINCT (lab_key, lab_display_name)) > 1
        """,
    ),
    Check(
        "lab_key_many_names",
        "У одного ключа лаборатории несколько подписей",
        "Разобрать",
        "dim_laboratory тоже может получить несколько строк на ключ и размножить JOIN.",
        """
        SELECT btrim(lab_key) AS lab_key, array_agg(DISTINCT btrim(lab_display_name)) AS names
        FROM urm2.lab_mapping WHERE is_active = 1
        GROUP BY btrim(lab_key) HAVING count(DISTINCT btrim(lab_display_name)) > 1
        """,
    ),
    Check(
        "empty_rule_token",
        "Пустой активный токен правила",
        "Критично",
        "Пустая подстрока совпадает с любым текстом: активный пустой бренд исключит всё.",
        """
        WITH rules AS (
            SELECT 'lis_excluded_category' AS rule_table, value, is_active
              FROM urm2.lis_excluded_category
            UNION ALL SELECT 'onec_excluded_brand', value, is_active FROM urm2.onec_excluded_brand
            UNION ALL SELECT 'onec_internal_excluded_warehouse_group', value, is_active
              FROM urm2.onec_internal_excluded_warehouse_group
            UNION ALL SELECT 'lis_procedure_category', value, is_active
              FROM urm2.lis_procedure_category
        )
        SELECT rule_table, value, is_active FROM rules WHERE is_active AND btrim(value) = ''
        """,
    ),
    Check(
        "equivalent_rule_tokens",
        "Эквивалентные токены правил без учёта регистра",
        "Разобрать",
        "Три правила ищут подстроку без учёта регистра; SQL-ключ при этом регистрозависим.",
        """
        WITH rules AS (
            SELECT 'lis_excluded_category' AS rule_table, value
              FROM urm2.lis_excluded_category WHERE is_active
            UNION ALL SELECT 'onec_excluded_brand', value FROM urm2.onec_excluded_brand WHERE is_active
            UNION ALL SELECT 'lis_procedure_category', value
              FROM urm2.lis_procedure_category WHERE is_active
        )
        SELECT rule_table, lower(btrim(value)) AS normalized_value, array_agg(value) AS values
        FROM rules GROUP BY rule_table, lower(btrim(value)) HAVING count(*) > 1
        """,
    ),
    Check(
        "short_brand_token",
        "Очень короткий активный токен бренда 1С",
        "Разобрать",
        "Проверяется вхождением в номенклатуру, организацию и клиента: короткий фрагмент исключает лишнее.",
        """
        SELECT value, length(btrim(value)) AS token_length, comment
        FROM urm2.onec_excluded_brand
        WHERE is_active AND length(btrim(value)) BETWEEN 1 AND 3
        """,
    ),
    Check(
        "dimension_key_duplicates",
        "Неуникальный ключ в опубликованных измерениях",
        "Разобрать",
        "JOIN к такому измерению размножит факт.",
        """
        SELECT 'dim_laboratory' AS dimension, lab_key AS key, count(*) AS rows
        FROM urm2.dim_laboratory GROUP BY lab_key HAVING count(*) > 1
        UNION ALL
        SELECT 'dim_container', container_group, count(*)
        FROM urm2.dim_container GROUP BY container_group HAVING count(*) > 1
        """,
    ),
    Check(
        "fact_bin_format",
        "Структурные огрехи БИН и месяца в фактах",
        "Разобрать",
        "Это проверка формата, а не действительности идентификатора.",
        """
        SELECT month, lab_display_name, container_group, bin
        FROM urm2.fact_month_container_bin
        WHERE bin IS NULL OR bin !~ '^[0-9]{12}$' OR month <> date_trunc('month', month)::date
        LIMIT 200
        """,
    ),
)

CHECKS_BY_CODE = {check.code: check for check in CHECKS}


def run_check(code: str) -> pd.DataFrame:
    return fetch_df(CHECKS_BY_CODE[code].sql)


def run_all() -> list[tuple[Check, pd.DataFrame]]:
    return [(check, fetch_df(check.sql)) for check in CHECKS]


def summary(results: list[tuple[Check, pd.DataFrame]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Проверка": check.title,
                "Важность": check.severity,
                "Найдено строк": len(frame),
                "Пояснение": check.explanation,
            }
            for check, frame in results
        ]
    )
