-- app/sql/025_webhook_subscriptions.sql
--
-- Segunda metade de "Integrações genéricas (webhooks/API)" — a primeira
-- (024_api_key_resolver.sql + POST /integrations/ingest) resolveu o
-- sentido INBOUND (ERP/CRM/planilha do cliente empurrando dado pra cá).
-- Esta resolve o sentido OUTBOUND: o cliente cadastra uma URL (Slack
-- incoming webhook, Zapier/Make, o próprio CRM) e a plataforma AVISA
-- quando um evento relevante acontece, sem o cliente precisar ficar
-- consultando a API.
--
-- Mesma estrutura de core.report_recipients (009_report_recipients.sql):
-- N assinaturas por tenant, cada uma opcionalmente restrita a um
-- subconjunto de tipos de evento ('{}' vazio = todos).
CREATE TABLE core.webhook_subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    name            VARCHAR(120) NOT NULL,
    url             TEXT NOT NULL,
    -- Segredo gerado na criação, usado para assinar o corpo de cada
    -- entrega (HMAC-SHA256, mesmo mecanismo que a Meta usa para o
    -- webhook INBOUND que já validamos em verify_meta_webhook_signature
    -- — aqui a plataforma é quem ASSINA, o cliente que VERIFICA). Só é
    -- reexibido uma vez, na criação (mesmo padrão de api_keys.key_hash
    -- vs. o valor puro devolvido só no create) — mas aqui, diferente de
    -- senha/API key, o segredo PRECISA ficar em claro no banco: é a
    -- própria plataforma quem assina cada entrega, não quem verifica.
    secret          VARCHAR(64) NOT NULL,
    -- '{}' (vazio) = recebe todos os tipos de evento — mesma convenção
    -- de report_recipients.report_types.
    event_types     TEXT[] NOT NULL DEFAULT '{}',
    active          BOOLEAN NOT NULL DEFAULT true,
    created_by      UUID REFERENCES core.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_webhook_subscriptions_tenant_active ON core.webhook_subscriptions (tenant_id) WHERE active = true;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['webhook_subscriptions'])
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
