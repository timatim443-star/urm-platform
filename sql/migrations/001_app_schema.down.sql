-- Откат миграции 001: УДАЛЯЕТ всех пользователей платформы. Витрины urm2 не трогаются.
DROP TABLE IF EXISTS urm2_app.app_user;
DROP TABLE IF EXISTS urm2_app.schema_version;
DROP SCHEMA IF EXISTS urm2_app;
