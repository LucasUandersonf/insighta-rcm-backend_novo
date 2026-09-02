-- =====================================================================
-- ARQUIVO: 012_password_reset.sql
-- Cadastro público (self-signup) + recuperação de senha self-service.
-- =====================================================================
--
-- TABELA core.password_reset_tokens — POR QUE SEM RLS
-- ---------------------------------------------------------------------
-- Mesma razão de core.tenants (ver 001_init_schema.sql): o fluxo de
-- "esqueci minha senha" acontece ANTES de existir qualquer contexto de
-- tenant (o usuário só informou um e-mail) — exatamente o mesmo
-- problema "ovo e galinha" que o login resolve com core.resolve_login
-- (ver 002_auth_resolver.sql). Colocar RLS nesta tabela obrigaria o
-- mesmo tipo de função SECURITY DEFINER só para gravar/ler um token
-- opaco e de vida curta; em vez disso, o valor sensível de verdade
-- (o token em si) nunca é armazenado em texto puro — só o HASH SHA-256
-- dele (mesma ideia de senha: o que vaza do banco não é reutilizável).
--
-- FUNÇÃO core.resolve_user_by_email — MESMO PADRÃO DE core.resolve_login
-- ---------------------------------------------------------------------
-- Devolve TODOS os candidatos (o mesmo e-mail pode existir em mais de um
-- tenant — consultor multi-clínica, mesmo cenário já tratado no login,
-- achado F-04). Quem decide o que fazer com mais de um candidato é o
-- serviço de aplicação (um token de reset por conta ativa encontrada),
-- não esta função.
CREATE TABLE IF NOT EXISTS core.password_reset_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
    -- Denormalizado de propósito: core.users tem RLS, então trocar a
    -- senha de verdade exige uma sessão TENANT-AWARE (ver DECISÃO em
    -- app/db/session.py). Guardar tenant_id aqui evita precisar de mais
    -- uma função SECURITY DEFINER só para descobrir "de qual tenant é
    -- esse user_id" no momento da confirmação do reset.
    tenant_id   UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,  -- SHA-256 hex do token enviado por e-mail (64 chars)
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id ON core.password_reset_tokens (user_id);

COMMENT ON TABLE core.password_reset_tokens IS
  'Tokens de uso único para "esqueci minha senha" (self-service). Sem RLS '
  'de propósito — ver cabeçalho deste arquivo. Só o HASH do token é '
  'gravado; o valor em texto puro só existe no e-mail enviado ao usuário.';

DROP FUNCTION IF EXISTS core.resolve_user_by_email(CITEXT);

CREATE FUNCTION core.resolve_user_by_email(p_email CITEXT)
RETURNS TABLE (
    user_id           UUID,
    tenant_id         UUID,
    full_name         VARCHAR,
    is_active         BOOLEAN,
    tenant_is_active  BOOLEAN,
    tenant_trade_name VARCHAR
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    SELECT
        u.id,
        u.tenant_id,
        u.full_name,
        u.is_active,
        t.is_active,
        t.trade_name
    FROM core.users u
    JOIN core.tenants t ON t.id = u.tenant_id
    WHERE u.email = p_email
    ORDER BY t.trade_name;
$$;

COMMENT ON FUNCTION core.resolve_user_by_email IS
  'Ponto autorizado a localizar um usuário por e-mail cross-tenant, para '
  'o fluxo de recuperação de senha — mesmo princípio de core.resolve_login, '
  'sem expor hash de senha (não é usado para autenticar, só para saber '
  'para qual(is) user_id gerar o token de reset).';

-- Mesmos GRANTs de core.resolve_login — DELIBERADAMENTE NÃO aplicados
-- aqui (ver 002_auth_resolver.sql, que segue a mesma convenção com os
-- comandos comentados abaixo): na primeiríssima execução deste arquivo
-- (banco novo), a role `auth_resolver_owner` ainda não existe — ela só é
-- criada depois, por `_ensure_roles()` em app/scripts/bootstrap_db.py.
-- Por isso o ALTER OWNER / GRANT EXECUTE desta função entram em
-- `_ROLES_SQL` (mesmo arquivo), rodado sempre DEPOIS da criação de
-- roles — nunca aqui, para não quebrar o bootstrap do zero.
-- ALTER FUNCTION core.resolve_user_by_email(CITEXT) OWNER TO auth_resolver_owner;
-- REVOKE ALL ON FUNCTION core.resolve_user_by_email(CITEXT) FROM PUBLIC;
-- GRANT EXECUTE ON FUNCTION core.resolve_user_by_email(CITEXT) TO app_runtime;
