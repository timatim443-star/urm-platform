-- URM2. Пустая копия схемы urm2 для локальной разработки и тестов.
-- Повторяет состав колонок и типы из словаря таблиц (проверка 30.09.2026).
-- Это не production-схема: данных нет, расчёта нет. Нужна, чтобы отлаживать
-- приложение, не обращаясь к рабочей базе.

CREATE SCHEMA IF NOT EXISTS urm2;
SET search_path = urm2, public;

-- ------------------------------------------------------------------ входные

CREATE TABLE container_mapping (
    row_id              bigserial PRIMARY KEY,
    container_group     text NOT NULL,
    lis_container_code  text NOT NULL DEFAULT '',
    lis_container_name  text NOT NULL DEFAULT '',
    onec_mdm_key        text NOT NULL DEFAULT '',
    is_active           smallint NOT NULL DEFAULT 1 CHECK (is_active = ANY (ARRAY[0, 1])),
    valid_from          date,
    valid_to            date,
    comment             text NOT NULL DEFAULT '',
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

CREATE TABLE lab_mapping (
    row_id               bigserial PRIMARY KEY,
    lab_key              text NOT NULL,
    lab_display_name     text NOT NULL,
    lis_lab_name         text NOT NULL DEFAULT '',
    onec_warehouse_group text NOT NULL DEFAULT '',
    is_active            smallint NOT NULL DEFAULT 1 CHECK (is_active = ANY (ARRAY[0, 1])),
    confidence           text NOT NULL DEFAULT '',
    comment              text NOT NULL DEFAULT '',
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE lis_excluded_category (
    value      text PRIMARY KEY,
    is_active  boolean NOT NULL DEFAULT true,
    comment    text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE onec_excluded_brand (LIKE lis_excluded_category INCLUDING ALL);
CREATE TABLE onec_internal_excluded_warehouse_group (LIKE lis_excluded_category INCLUDING ALL);
CREATE TABLE lis_procedure_category (LIKE lis_excluded_category INCLUDING ALL);

-- ------------------------------------------------------------------ выходные
-- У выходных таблиц нет PK/FK/UNIQUE: уникальность проверяется ETL до публикации.

CREATE TABLE fact_total (
    lis_consumed                numeric(24,6),
    lis_unique_customers        bigint,
    onec_issued                 numeric(24,6),
    onec_unique_counterparties  bigint,
    delta_issued_minus_consumed numeric(24,6),
    coverage_issued_to_consumed numeric(24,6),
    match_status                text
);

CREATE TABLE fact_month_container (
    month                       date,
    lab_key                     text,
    lab_display_name            text,
    container_group             text,
    lis_consumed                numeric(24,6),
    lis_unique_customers        bigint,
    onec_issued                 numeric(24,6),
    onec_unique_counterparties  bigint,
    delta_issued_minus_consumed numeric(24,6),
    coverage_issued_to_consumed numeric(24,6),
    match_status                text,
    container_display_name      text
);

CREATE TABLE fact_container (LIKE fact_month_container);
ALTER TABLE fact_container DROP COLUMN month;

CREATE TABLE fact_month_container_bin (
    month                       date,
    lab_key                     text,
    lab_display_name            text,
    container_group             text,
    bin                         text,
    lis_consumed                numeric(24,6),
    lis_unique_customers        bigint,
    onec_issued                 numeric(24,6),
    onec_unique_counterparties  bigint,
    delta_issued_minus_consumed numeric(24,6),
    coverage_issued_to_consumed numeric(24,6),
    match_status                text,
    container_display_name      text
);

CREATE TABLE fact_container_bin (LIKE fact_month_container_bin);
ALTER TABLE fact_container_bin DROP COLUMN month;

CREATE TABLE fact_lab_month_metric (
    month            date,
    lab_key          text,
    lab_display_name text,
    match_status     text,
    metric           text,
    metric_sort      bigint,
    amount           numeric(24,6)
);

CREATE TABLE fact_partner_month_metric (
    month            date,
    lab_key          text,
    lab_display_name text,
    bin              text,
    partner_name     text,
    match_status     text,
    metric           text,
    metric_sort      bigint,
    amount           numeric(24,6)
);

CREATE TABLE fact_partner_container_month_metric (
    month                  date,
    lab_key                text,
    lab_display_name       text,
    bin                    text,
    partner_name           text,
    container_group        text,
    container_display_name text,
    match_status           text,
    metric                 text,
    metric_sort            bigint,
    amount                 numeric(24,6)
);

CREATE TABLE fact_lis_customer_container_month (
    month                  date,
    lab_key                text,
    lab_display_name       text,
    bin                    text,
    customer_lis_uid       text,
    customer_lis_code      text,
    customer_lis_name      text,
    container_group        text,
    container_display_name text,
    lis_consumed           numeric(24,6)
);

CREATE TABLE fact_procedure_room_month_metric (
    month               date,
    lab_key             text,
    lab_display_name    text,
    procedure_uid       text,
    procedure_room_name text,
    match_status        text,
    metric              text,
    metric_sort         bigint,
    amount              numeric(24,6)
);

CREATE TABLE fact_procedure_room_container_month_metric (
    month                  date,
    lab_key                text,
    lab_display_name       text,
    procedure_uid          text,
    procedure_room_name    text,
    container_group        text,
    container_display_name text,
    match_status           text,
    metric                 text,
    metric_sort            bigint,
    amount                 numeric(24,6)
);

CREATE TABLE fact_internal_consumption_lab_month (
    month            date,
    lab_key          text,
    lab_display_name text,
    metric           text,
    metric_sort      bigint,
    amount           numeric(24,6),
    rows             bigint
);

CREATE TABLE fact_exceptions (
    month                  date,
    lab_key                text,
    lab_display_name       text,
    bin                    text,
    container_group        text,
    container_display_name text,
    side                   text,
    party_name             text,
    amount                 numeric(24,6)
);

CREATE TABLE bin_parties (
    month               date,
    lab_key             text,
    lab_display_name    text,
    container_group     text,
    bin                 text,
    lis_hospitals       text,
    onec_counterparties text
);

CREATE TABLE dim_container (
    container_group        text,
    container_display_name text,
    container_label        text
);

CREATE TABLE dim_laboratory (
    lab_key          text,
    lab_display_name text
);

CREATE TABLE match_status_filter (
    match_status text,
    sort_order   bigint
);

CREATE TABLE contractless_onec (
    month               date,
    bin                 text,
    onec_client_name    text,
    onec_org_name       text,
    rows_count          bigint,
    issued_qty          numeric(24,6),
    unique_mdm_keys     bigint,
    sample_nomenclature text
);

CREATE TABLE contractless_onec_monthly (
    month                 date,
    rows_count            bigint,
    issued_qty            numeric(24,6),
    unique_bins           bigint,
    unique_counterparties bigint,
    total_rows            bigint,
    total_issued_qty      numeric(24,6),
    rows_pct              numeric(24,6),
    issued_qty_pct        numeric(24,6)
);

CREATE TABLE mapping_coverage_summary (
    source        text,
    mapping_scope text,
    rows          bigint,
    quantity      numeric(24,6),
    unique_codes  bigint
);

CREATE TABLE mapping_conflicts (
    conflict_type text,
    code          text,
    group_count   bigint
);

CREATE TABLE quality_funnel (
    source text,
    stage  text,
    rows   bigint
);

CREATE TABLE quality_issues (
    source     text,
    issue_type text,
    severity   text,
    rows       bigint,
    quantity   numeric(24,6),
    details    text
);

CREATE TABLE unmapped_lis_codes (lis_container_code text);

CREATE TABLE unmapped_lis_details (
    lis_container_code     text,
    lis_container_name_raw text,
    lab_display_name       text,
    rows_count             bigint,
    total_consumed         numeric(24,6),
    unique_bins            bigint,
    unique_hospitals       bigint,
    months                 text
);

CREATE TABLE unmapped_onec_mdm (onec_mdm_key text);

CREATE TABLE unmapped_onec_details (
    onec_mdm_key          text,
    onec_nomenclature_name text,
    onec_article          text,
    lab_display_name      text,
    onec_warehouse_group  text,
    rows_count            bigint,
    total_issued          numeric(24,6),
    unique_bins           bigint,
    unique_counterparties bigint,
    months                text
);

-- --------------------------------------------------------------- технические

CREATE TABLE etl_state (
    id                    smallint PRIMARY KEY CHECK (id = 1),
    last_checked_day      date,
    last_successful_day   date,
    last_successful_at    timestamptz,
    reference_changed     boolean NOT NULL DEFAULT true,
    references_updated_at timestamptz,
    reference_checksum    text,
    last_emailed_month    date,
    last_error            text
);

CREATE TABLE etl_run (
    run_id       uuid PRIMARY KEY,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    period_start date,
    period_end   date,
    status       text NOT NULL
                 CHECK (status = ANY (ARRAY['running', 'success', 'failed', 'no_change'])),
    error        text
);

-- Триггер, помечающий изменение справочников (как в рабочем контуре).
CREATE OR REPLACE FUNCTION urm2.mark_reference_changed() RETURNS trigger AS $$
BEGIN
    UPDATE urm2.etl_state
       SET reference_changed = true, references_updated_at = now()
     WHERE id = 1;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE name text;
BEGIN
    FOREACH name IN ARRAY ARRAY['container_mapping', 'lab_mapping', 'lis_excluded_category',
                                'onec_excluded_brand', 'onec_internal_excluded_warehouse_group',
                                'lis_procedure_category']
    LOOP
        EXECUTE format(
            'CREATE TRIGGER %I_mark_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE '
            'ON urm2.%I FOR EACH STATEMENT EXECUTE FUNCTION urm2.mark_reference_changed()',
            name, name);
    END LOOP;
END $$;

-- Индексы чтения, как в рабочей схеме (не уникальные).
CREATE INDEX ix_fact_month_container_month ON fact_month_container (month);
CREATE INDEX ix_fact_month_container_lab_key ON fact_month_container (lab_key);
CREATE INDEX ix_fact_month_container_container_group ON fact_month_container (container_group);
CREATE INDEX ix_fact_month_container_match_status ON fact_month_container (match_status);
CREATE INDEX ix_fact_month_container_bin_month ON fact_month_container_bin (month);
CREATE INDEX ix_fact_month_container_bin_bin ON fact_month_container_bin (bin);
CREATE INDEX ix_bin_parties_month ON bin_parties (month);
CREATE INDEX ix_fact_exceptions_month ON fact_exceptions (month);

-- В рабочей базе etl_state всегда содержит одну строку id = 1.
INSERT INTO etl_state (id, reference_changed) VALUES (1, false) ON CONFLICT (id) DO NOTHING;
