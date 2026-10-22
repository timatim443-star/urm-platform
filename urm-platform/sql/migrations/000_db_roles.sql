-- УРМ, миграция 000 (необязательная, выполняет администратор PostgreSQL вручную).
-- Отдельные учётные записи для платформы вместо общего логина URM:
--   urm_app_ro — только чтение (все страницы отчёта, сборка снимка, вход пользователей);
--   urm_app_rw — запись только в шесть таблиц маппинга, etl_state-флаг и таблицу пользователей.
--
-- Запуск (пароли вводятся в команде и в git не попадают):
--   psql -h <хост> -p <порт> -d URM -U <администратор> \
--        -v ro_password='…' -v rw_password='…' -f sql/migrations/000_db_roles.sql
-- Если используется одна учётка URM — этот файл не нужен.

CREATE ROLE urm_app_ro LOGIN PASSWORD :'ro_password';
GRANT CONNECT ON DATABASE "URM" TO urm_app_ro;
GRANT USAGE ON SCHEMA urm2 TO urm_app_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA urm2 TO urm_app_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA urm2 GRANT SELECT ON TABLES TO urm_app_ro;
ALTER ROLE urm_app_ro SET default_transaction_read_only = on;

CREATE ROLE urm_app_rw LOGIN PASSWORD :'rw_password';
GRANT CONNECT ON DATABASE "URM" TO urm_app_rw;
GRANT USAGE ON SCHEMA urm2 TO urm_app_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA urm2 TO urm_app_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA urm2 GRANT SELECT ON TABLES TO urm_app_rw;

GRANT INSERT, UPDATE, DELETE ON
    urm2.container_mapping,
    urm2.lab_mapping,
    urm2.lis_excluded_category,
    urm2.onec_excluded_brand,
    urm2.onec_internal_excluded_warehouse_group,
    urm2.lis_procedure_category
TO urm_app_rw;
GRANT USAGE, SELECT ON SEQUENCE urm2.container_mapping_row_id_seq, urm2.lab_mapping_row_id_seq TO urm_app_rw;

-- Триггер mark_reference_changed() пишет в urm2.etl_state правами вызывающего.
-- Вариант А: точечный UPDATE (ниже). Вариант Б (лучше, делает сопровождающий URM2):
--   ALTER FUNCTION urm2.mark_reference_changed() SECURITY DEFINER;  и убрать GRANT ниже.
GRANT UPDATE (reference_changed, references_updated_at) ON urm2.etl_state TO urm_app_rw;

-- Откат: REVOKE ALL … ; DROP ROLE urm_app_rw; DROP ROLE urm_app_ro;
