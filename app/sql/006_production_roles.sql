-- =====================================================================
-- ARQUIVO: 006_production_roles.sql
--
-- ⚠️  SUPERADO por app/scripts/bootstrap_db.py + entrypoint.py — a
-- criação destas roles agora é AUTOMÁTICA a cada deploy (ver seção
-- "Deploy em produção" no README). Este arquivo fica como referência /
-- caminho manual alternativo, útil só se você estiver rodando fora do
-- padrão de entrypoint único (outra plataforma, outro processo de
-- deploy). Para o fluxo padrão deste projeto, você não precisa rodar
-- isto manualmente.
--
-- CRIAÇÃO DAS ROLES DE PRODUÇÃO — app_runtime e auth_resolver_owner
--
-- POR QUE ISTO É URGENTE
-- ---------------------------------------------------------------------
-- A aplicação, até agora, conecta em produção usando o usuário padrão
-- do Postgres gerenciado (ex: "postgres" no Railway/RDS) — que é
-- SUPERUSUÁRIO. Um superusuário do Postgres IGNORA Row-Level Security
-- por definição do próprio banco, mesmo com FORCE ROW LEVEL SECURITY
-- ativado em toda tabela (ver 001_init_schema.sql). Isso significa que,
-- neste momento, o isolamento entre tenants não está sendo garantido
-- pelo banco — só "por enquanto não apareceu um bug que vazasse dado
-- entre clínicas", que é exatamente a garantia que o RLS existe pra
-- eliminar. Rodar este script e trocar a DATABASE_URL da aplicação é
-- prioridade máxima, antes de qualquer dado real de cliente entrar no
-- sistema.
--
-- MESMO PADRÃO já validado nos testes de integração (tests/conftest.py),
-- incluindo a correção de um bug real que encontramos lá: BYPASSRLS só
-- ignora POLÍTICAS de RLS — não ignora GRANTs básicos de schema/tabela.
-- Por isso auth_resolver_owner recebe USAGE/SELECT explícitos, além do
-- BYPASSRLS.
--
-- DECISÃO — \gexec em vez de DO $$ ... EXECUTE ... $$
-- ---------------------------------------------------------------------
-- A primeira versão deste script usava um bloco DO com EXECUTE
-- concatenando a senha via ||. Isso tem um bug sutil: a substituição de
-- variável do psql (:'var') acontece ANTES do texto virar SQL — quando
-- o valor citado entra numa concatenação de string (||) dentro do
-- PL/pgSQL, as aspas que delimitavam o literal são consumidas na
-- montagem do VALOR da string, e o resultado final passado ao EXECUTE
-- fica SEM aspas ao redor da senha — erro de sintaxe. O idiom correto
-- do psql para "criar algo só se não existir, com um valor dinâmico" é
-- \gexec: a query SELECT abaixo só retorna uma linha (o comando CREATE
-- ROLE pronto, já com quote_literal() aplicado corretamente) quando a
-- role AINDA NÃO existe; \gexec executa o que a query devolveu. Se a
-- role já existe, a query não devolve linha nenhuma, e \gexec não
-- executa nada — idempotente, sem a armadilha do bloco DO.
--
-- COMO USAR
-- ---------------------------------------------------------------------
-- A senha NUNCA fica escrita neste arquivo — é passada por variável do
-- psql na hora de rodar, para este arquivo poder ser commitado no
-- GitHub sem vazar credencial nenhuma.
--
-- 1. Gere uma senha forte: `openssl rand -base64 32`
-- 2. Rode (com a DATABASE_URL de SUPERUSUÁRIO atual, a que a aplicação
--    usa hoje, antes da troca) — a senha vai SEM aspas na linha de
--    comando:
--    psql "$DATABASE_URL" -v app_runtime_password='<senha-gerada-aqui>' -f app/sql/006_production_roles.sql
-- 3. Atualize a variável de ambiente DATABASE_URL do serviço (Railway,
--    etc.) para usar `app_runtime` em vez de `postgres`, com a MESMA
--    senha gerada no passo 1.
-- 4. Redeploy. A aplicação passa a conectar como app_runtime — RLS
--    passa a valer de verdade a partir daqui.
-- =====================================================================

SET search_path TO core, public;

-- ---------------------------------------------------------------------
-- app_runtime — a role que a APLICAÇÃO usa no dia a dia (via DATABASE_URL)
-- ---------------------------------------------------------------------
SELECT 'CREATE ROLE app_runtime LOGIN PASSWORD ' || quote_literal(:'app_runtime_password') || ' NOSUPERUSER NOBYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime') \gexec

GRANT USAGE ON SCHEMA core TO app_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core TO app_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core TO app_runtime;
GRANT EXECUTE ON FUNCTION core.current_tenant_id() TO app_runtime;

-- Tabelas/sequences CRIADAS NO FUTURO (novas migrations) herdam esses
-- mesmos privilégios automaticamente, sem precisar rodar GRANT de novo
-- manualmente a cada nova tabela.
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT USAGE, SELECT ON SEQUENCES TO app_runtime;

-- ---------------------------------------------------------------------
-- auth_resolver_owner — dona da função de login (SECURITY DEFINER)
-- BYPASSRLS já incluído na criação (não precisa de ALTER ROLE separado).
-- ---------------------------------------------------------------------
SELECT 'CREATE ROLE auth_resolver_owner NOLOGIN NOSUPERUSER BYPASSRLS'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_resolver_owner') \gexec

-- IMPORTANTE (bug real encontrado ao validar isto nos testes de
-- integração): BYPASSRLS só ignora POLÍTICAS de RLS — não ignora GRANTs
-- básicos de schema/tabela. A função roda com os privilégios DESTA role
-- (SECURITY DEFINER), então ela precisa, além do BYPASSRLS, de USAGE no
-- schema e SELECT nas tabelas que a função lê internamente.
GRANT USAGE ON SCHEMA core TO auth_resolver_owner;
GRANT SELECT ON core.users, core.tenants TO auth_resolver_owner;

ALTER FUNCTION core.resolve_login(CITEXT) OWNER TO auth_resolver_owner;
REVOKE ALL ON FUNCTION core.resolve_login(CITEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.resolve_login(CITEXT) TO app_runtime;
