-- Executado pelo entrypoint oficial somente na inicialização de um volume vazio.
-- O papel da aplicação não recebe privilégios de superusuário.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec

SELECT format('GRANT pg_monitor TO %I', :'app_user')
\gexec

SELECT format('ALTER SCHEMA public OWNER TO %I', :'app_user')
\gexec

DO $$
DECLARE
    item RECORD;
BEGIN
    FOR item IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tableowner = current_user
    LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO %I', item.tablename, 'dvapp');
    END LOOP;
    FOR item IN
        SELECT s.sequencename
        FROM pg_sequences s
        JOIN pg_class c ON c.relname=s.sequencename
        JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname=s.schemaname
        WHERE s.schemaname='public' AND pg_get_userbyid(c.relowner)=current_user
    LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO %I', item.sequencename, 'dvapp');
    END LOOP;
END
$$;

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT ALL ON TABLES TO %I',
    current_user, :'app_user'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT ALL ON SEQUENCES TO %I',
    current_user, :'app_user'
)
\gexec
