-- УРМ, миграция 001: схема платформы и таблица пользователей.
-- Витрины urm2 не трогаются. Повторный запуск безопасен.

CREATE SCHEMA IF NOT EXISTS urm2_app;

CREATE TABLE IF NOT EXISTS urm2_app.schema_version (
    version     integer PRIMARY KEY,
    name        text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS urm2_app.app_user (
    login         text PRIMARY KEY,
    display_name  text NOT NULL DEFAULT '',
    -- Права ролей — в urm/roles.py: city (свои города), network (все города), admin (всё).
    role          text NOT NULL CHECK (role IN ('city', 'network', 'admin')),
    -- lab_key лабораторий (= городов) из urm2.dim_laboratory. Нужны роли city.
    labs          text[] NOT NULL DEFAULT '{}',
    password_hash text NOT NULL,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT city_role_needs_labs CHECK (role <> 'city' OR cardinality(labs) > 0),
    CONSTRAINT login_lowercase CHECK (login = lower(login))
);

-- Чтение для входа: учётной записи на чтение платформы (если она отдельная).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'urm_app_ro') THEN
        GRANT USAGE ON SCHEMA urm2_app TO urm_app_ro;
        GRANT SELECT ON urm2_app.app_user, urm2_app.schema_version TO urm_app_ro;
    END IF;
END $$;

INSERT INTO urm2_app.schema_version (version, name) VALUES (1, 'app_schema')
ON CONFLICT (version) DO NOTHING;
