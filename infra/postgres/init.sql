-- init.sql — Inicializacao do banco PostgreSQL
-- Executado uma unica vez na criacao do container

-- Extensoes uteis
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Configuracoes de seguranca da conexao
ALTER SYSTEM SET log_connections = 'on';
ALTER SYSTEM SET log_disconnections = 'on';
ALTER SYSTEM SET log_duration = 'off';
ALTER SYSTEM SET log_min_duration_statement = '1000';  -- loga queries > 1s

-- Revoga acesso publico padrao
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO dvapp;
GRANT ALL ON SCHEMA public TO dvapp;

SELECT pg_reload_conf();
