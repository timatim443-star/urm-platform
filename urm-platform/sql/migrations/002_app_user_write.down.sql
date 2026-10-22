DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'urm_app_rw') THEN
        REVOKE INSERT, UPDATE ON urm2_app.app_user FROM urm_app_rw;
    END IF;
END $$;
DELETE FROM urm2_app.schema_version WHERE version = 2;
