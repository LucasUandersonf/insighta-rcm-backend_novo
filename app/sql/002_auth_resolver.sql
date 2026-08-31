-- =====================================================================
-- ARQUIVO: 002_auth_resolver.sql
-- PROBLEMA A RESOLVER: "ovo e galinha" do login sob RLS
-- ---------------------------------------------------------------------
-- O usuário faz login apenas com e-mail + senha. Nesse momento a
-- aplicação AINDA NÃO SABE o tenant_id (é justamente o que precisa
-- descobrir para poder fazer SET LOCAL app.current_tenant depois).
-- Mas a tabela core.users tem RLS: sem app.current_tenant setado,
-- current_tenant_id() retorna NULL e NENHUMA linha é visível — nem
-- para o próprio processo de descobrir quem é o usuário.
--
-- Alternativas descartadas:
--   (a) Dar BYPASSRLS à role de runtime inteira -> resolveria o login,
--       mas destruiria a garantia de isolamento para TODAS as outras
--       queries feitas com essa mesma role. Inaceitável.
--   (b) Guardar credenciais fora do RLS em uma tabela paralela sem
--       tenant_id -> duplica a fonte da verdade de senha/e-mail e cria
--       risco de dessincronia entre "users" e essa tabela espelho.
--
-- Solução adotada: uma função SQL "SECURITY DEFINER".
-- Uma função SECURITY DEFINER roda com os privilégios de quem a
-- CRIOU (o dono da função), não de quem a chama. Se o dono for uma
-- role com BYPASSRLS restrita a essa única finalidade, a função
-- consegue enxergar todos os tenants só para esta consulta pontual e
-- estritamente delimitada (somente e-mail, hash de senha, tenant_id,
-- role e status ativo) — nunca expõe a tabela inteira, nunca permite
-- INSERT/UPDATE, e fica centralizada em UM ponto auditável do schema
-- em vez de espalhar exceções de RLS pela aplicação.
-- =====================================================================

-- Role dona da função, criada apenas para este propósito, sem LOGIN
-- direto pela aplicação (a app não conecta como esta role; apenas a
-- FUNÇÃO roda "vestindo" seus privilégios).
-- CREATE ROLE auth_resolver_owner NOBYPASSRLS NOLOGIN;
-- ALTER ROLE auth_resolver_owner BYPASSRLS;  -- único lugar do sistema com bypass, e só dentro da função abaixo
-- IMPORTANTE — bug real encontrado ao validar isto contra um Postgres de
-- verdade (ver tests/conftest.py): BYPASSRLS só ignora POLÍTICAS de RLS,
-- não ignora GRANTs básicos de schema/tabela. Sem as duas linhas abaixo,
-- a função falha com "permission denied for schema core" mesmo com
-- BYPASSRLS setado, porque SECURITY DEFINER roda com os privilégios
-- REAIS da role dona — que precisa ter USAGE/SELECT concedidos como
-- qualquer outra role, além do BYPASSRLS:
-- GRANT USAGE ON SCHEMA core TO auth_resolver_owner;
-- GRANT SELECT ON core.users, core.tenants TO auth_resolver_owner;

-- CORREÇÃO (Auditoria Go-Live, achado F-04) — `LIMIT 1` sem `ORDER BY`
-- escolhia um tenant ARBITRÁRIO (dependente de plano de execução do
-- Postgres, não determinístico) quando o mesmo e-mail existe em mais de
-- um tenant — cenário real e esperado do schema, documentado para
-- consultores que atendem múltiplas clínicas com o mesmo e-mail de
-- login. Login por e-mail é inerentemente ambíguo nesse caso: a senha
-- sozinha não diz qual das clínicas o usuário quer acessar (podendo
-- inclusive ser a MESMA senha em ambas). A correção move a resolução de
-- "qual tenant" para fora do SQL: a função agora devolve TODOS os
-- candidatos daquele e-mail (com trade_name, para a UI poder listar as
-- opções), e o endpoint de login (app/api/v1/endpoints/auth.py) decide:
-- 0 credencial válida -> erro genérico; exatamente 1 -> login direto;
-- mais de 1 com a mesma senha válida -> exige seleção explícita do
-- tenant antes de emitir o token.
-- CORREÇÃO DE PRODUÇÃO (2026-08-30) — `CREATE OR REPLACE FUNCTION` recusa
-- mudar o formato de retorno de uma função já existente ("cannot change
-- return type of existing function"). A correção do F-04 acima ACRESCENTOU
-- colunas (tenant_is_active, tenant_trade_name) à saída da função — ou
-- seja, o `OR REPLACE` sozinho não é suficiente para levar um banco que já
-- tinha a versão antiga (5 colunas) para a versão nova (7 colunas); ele
-- falha silenciosamente NUNCA — ele dá erro — mas esse erro nunca chegou a
-- acontecer porque este arquivo (002) só rodava uma vez, no primeiro
-- bootstrap (ver _BOOTSTRAP_FILES em app/scripts/bootstrap_db.py), então
-- em todo banco já existente a função ficou para trás na versão antiga,
-- incompatível com auth_repository.py::LoginRecord. Resultado: login
-- quebrando com `NoSuchColumnError: tenant_trade_name` em produção.
-- DROP + CREATE (em vez de só OR REPLACE) torna este arquivo seguro de
-- rodar de novo a qualquer momento, com qualquer formato de retorno
-- anterior — por isso ele também entrou em _POST_UPGRADE_SQL_FILES,
-- deixando de depender de "só roda no primeiro bootstrap".
DROP FUNCTION IF EXISTS core.resolve_login(CITEXT);

CREATE FUNCTION core.resolve_login(p_email CITEXT)
RETURNS TABLE (
    user_id         UUID,
    tenant_id       UUID,
    hashed_password VARCHAR,
    role            core.user_role,
    is_active       BOOLEAN,
    tenant_is_active BOOLEAN,
    tenant_trade_name VARCHAR
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp   -- fixa search_path para evitar sequestro de função (CVE clássico de SECURITY DEFINER)
STABLE
AS $$
    SELECT
        u.id,
        u.tenant_id,
        u.hashed_password,
        u.role,
        u.is_active,
        t.is_active,
        t.trade_name
    FROM core.users u
    JOIN core.tenants t ON t.id = u.tenant_id
    WHERE u.email = p_email
    ORDER BY t.trade_name;  -- ordem determinística; sem efeito na decisão de negócio, só reprodutibilidade
$$;

-- ALTER FUNCTION core.resolve_login(CITEXT) OWNER TO auth_resolver_owner;

-- A role de runtime da aplicação (app_runtime) NÃO recebe SELECT direto
-- em core.users fora do RLS — ela só pode EXECUTAR esta função pontual.
-- REVOKE ALL ON FUNCTION core.resolve_login(CITEXT) FROM PUBLIC;
-- GRANT EXECUTE ON FUNCTION core.resolve_login(CITEXT) TO app_runtime;

COMMENT ON FUNCTION core.resolve_login IS
  'Único ponto do sistema autorizado a ler credenciais cross-tenant. Usado '
  'exclusivamente pelo fluxo de login, antes do contexto de tenant existir. '
  'Retorna o mínimo necessário para autenticar e emitir o JWT.';
