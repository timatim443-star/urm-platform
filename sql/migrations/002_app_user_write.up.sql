-- УРМ, миграция 002: право учётной записи на запись управлять пользователями
-- со страницы «12 Пользователи» и менять собственный пароль. Удаления нет.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'urm_app_rw') THEN
        GRANT USAGE ON SCHEMA urm2_app TO urm_app_rw;
        GRANT SELECT, INSERT, UPDATE ON urm2_app.app_user TO urm_app_rw;
    END IF;
END $$;

INSERT INTO urm2_app.schema_version (version, name) VALUES (2, 'app_user_write')
ON CONFLICT (version) DO NOTHING;
