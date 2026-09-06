-- app/sql/024_api_key_resolver.sql
--
-- Problema do "ovo e galinha" da autenticação por API key (mesma
-- categoria de 002_auth_resolver.sql/012_password_reset.sql): um
-- cliente de integração (ERP, Zapier, script próprio) chama um endpoint
-- informando SÓ a chave de API — nem o cliente nem a aplicação sabem
-- ainda de qual tenant ela é. `core.api_keys` tem RLS normal (isolamento
-- por tenant); sem o tenant já setado, NENHUMA linha é visível, nem
-- para o próprio processo de descobrir "de quem é essa chave".
--
-- BUG CORRIGIDO — chaves emitidas, NUNCA verificadas
-- -------------------------------------------------------------------
-- POST /integrations/api-keys já emite chaves desde 006_platform_admin.sql
-- (ApiKeyService.verify_raw_key_against_hash já existia, com o comentário
-- "usado pelo endpoint de ingestão (futuro)") — mas nenhum endpoint jamais
-- chamava essa verificação. Um cliente podia gerar uma chave e ela nunca
-- servia para autenticar nada. Esta função é a peça que faltava (ver
-- POST /integrations/ingest em app/api/v1/endpoints/integrations.py).
--
-- Mesma solução de 002_auth_resolver.sql: função SECURITY DEFINER,
-- reaproveitando a MESMA role `auth_resolver_owner` já criada para o
-- login (ver _ROLES_SQL em app/scripts/bootstrap_db.py) — não cria uma
-- role nova só para isto. Devolve só os candidatos que batem no
-- key_prefix (índice em claro, não sensível — ver DECISÃO em
-- app/core/security.py::generate_api_key); a aplicação faz o
-- verify_password() de verdade contra o(s) candidato(s), exatamente
-- como o login faz com senha (aqui pode haver mais de 1 candidato em
-- tese — prefixo não é garantidamente único — por isso devolve uma
-- TABELA, não uma linha só).
DROP FUNCTION IF EXISTS core.resolve_api_key_candidates(VARCHAR);

CREATE FUNCTION core.resolve_api_key_candidates(p_key_prefix VARCHAR)
RETURNS TABLE (
    id          UUID,
    tenant_id   UUID,
    key_hash    VARCHAR,
    revoked_at  TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = core, pg_temp
STABLE
AS $$
    SELECT id, tenant_id, key_hash, revoked_at
    FROM core.api_keys
    WHERE key_prefix = p_key_prefix;
$$;

COMMENT ON FUNCTION core.resolve_api_key_candidates IS
  'Único ponto do sistema autorizado a ler core.api_keys cross-tenant. '
  'Usado exclusivamente para autenticar chamadas de integração (API key) '
  'antes do contexto de tenant existir. Devolve só key_hash/revoked_at — '
  'nunca expõe a tabela inteira nem outras colunas.';
