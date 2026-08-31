-- =====================================================================
-- ARQUIVO: 006_platform_admin.sql
-- Fundação de "Administração da Plataforma" exigida pelo novo escopo do
-- produto (camada analítica/RCM sobre dados consolidados de ERPs
-- externos): gestão de usuários (RBAC) e canal de integrações (chaves
-- de API para os webhooks que o ERP do cliente vai chamar).
--
-- Mesmo padrão de 003/004: tabela nova -> ENABLE + FORCE ROW LEVEL
-- SECURITY -> policy tenant_isolation_<tabela>. Nenhuma tabela nova
-- aqui é exceção à regra de isolamento por tenant_id.
-- =====================================================================

-- ---------------------------------------------------------------------
-- users: dois campos novos para suportar troca de senha administrada.
--
-- DECISÃO — must_change_password em vez de e-mail de "primeiro acesso"
-- ---------------------------------------------------------------------
-- O MVP ainda não tem provedor de e-mail transacional integrado (ver
-- STATUS_DO_PROJETO). Em vez de bloquear a tela de "Gestão de Usuários"
-- até essa integração existir, o admin/owner cria o usuário ou reseta a
-- senha e recebe uma senha temporária UMA VEZ na resposta da API (nunca
-- persistida em texto puro, nunca reexibida depois). O login com uma
-- senha marcada must_change_password=true continua funcionando
-- normalmente, mas o frontend força a troca antes de liberar o resto da
-- aplicação. Quando o envio de e-mail entrar no roadmap, este campo
-- continua válido — só passamos a entregar a senha temporária por e-mail
-- em vez de na resposta HTTP.
ALTER TABLE core.users
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN core.users.must_change_password IS
  'true logo após criação pelo admin ou reset administrado; a API de troca de senha (self-service) zera este campo.';


-- ---------------------------------------------------------------------
-- TABELA: api_keys (Central de Integrações & Webhooks)
--
-- DECISÃO — chave de API genérica por tenant, além do
-- tenants.meta_ads_webhook_secret específico (Meta Ads permanece à parte
-- por ser webhook de UMA integração third-party fixa, não algo que o
-- cliente "gera"). Esta tabela é o que a tela "Integrações & Webhooks"
-- do briefing usa para o cliente emitir/revogar credenciais que o ERP
-- dele usa para autenticar contra os endpoints de ingestão da nossa
-- plataforma (upload automático / push webhook nativo).
--
-- Só o HASH da chave é armazenado (mesmo princípio de hashed_password em
-- users) — a chave em texto puro só existe uma vez, no corpo da resposta
-- de criação, exatamente como uma senha nunca é reexibida depois de
-- setada. key_prefix (8 primeiros caracteres, não sensível) fica em
-- claro só para a UI conseguir listar "qual chave é qual" sem expor o
-- segredo inteiro de novo.
-- ---------------------------------------------------------------------
CREATE TABLE core.api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    name            VARCHAR(120) NOT NULL,
    key_prefix      VARCHAR(12) NOT NULL,
    key_hash        VARCHAR(255) NOT NULL,
    created_by      UUID REFERENCES core.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX ix_api_keys_tenant_active ON core.api_keys (tenant_id) WHERE revoked_at IS NULL;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['api_keys'])
    LOOP
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE core.%I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation_%1$I ON core.%1$I
            USING (tenant_id = core.current_tenant_id())
            WITH CHECK (tenant_id = core.current_tenant_id());
        $f$, t);
    END LOOP;
END $$;
