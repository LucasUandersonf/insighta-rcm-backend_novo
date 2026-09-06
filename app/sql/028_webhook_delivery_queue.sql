-- app/sql/028_webhook_delivery_queue.sql
--
-- Fila de retentativa para webhooks OUTBOUND — último item de código
-- pendente das frentes sugeridas nesta semana. Até aqui (025_webhook_subscriptions.sql),
-- dispatch_event() tentava entregar UMA VEZ; se o destino estivesse fora
-- do ar por alguns segundos, aquele evento específico se perdia para
-- sempre. Esta tabela dá o estado necessário para tentar de novo, com
-- backoff, sem travar a operação que disparou o evento.
--
-- DECISÃO — fila em Postgres, não SQS
-------------------------------------------------------------------------
-- O backlog original previa "quando houver fila de mensageria
-- disponível" — hoje não há SQS provisionado (ver Tier 1 do
-- PRODUCAO_CHECKLIST.md), e não é este código que decide isso. Uma
-- tabela com `next_attempt_at` + um job que varre "o que está vencido"
-- é o padrão clássico de fila-em-banco: resolve o problema de verdade
-- (não perder o evento, tentar de novo depois) sem esperar uma decisão
-- de infraestrutura que está fora do alcance de uma sessão de código.
-- Migrar para SQS depois, se o volume justificar, troca só o
-- worker/repositório — o contrato de dado (o que cada linha representa)
-- não muda.
--
-- DECISÃO — tenant_id + RLS normal (diferente das tabelas platform_*)
-------------------------------------------------------------------------
-- Esta fila é dado da CLÍNICA (qual evento dela falhou e por quê), não
-- bookkeeping da própria Insighta — segue a convenção padrão de todo o
-- resto do schema, ao contrário de platform_risk_alerts/
-- platform_announcements.
CREATE TABLE core.webhook_delivery_queue (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    subscription_id UUID NOT NULL REFERENCES core.webhook_subscriptions(id) ON DELETE CASCADE,
    event_type      VARCHAR(120) NOT NULL,
    -- Payload BRUTO (dict original, não o corpo já assinado): a
    -- assinatura HMAC é recalculada a cada tentativa, com o secret
    -- ATUAL da assinatura (se o cliente regenerar o segredo entre a
    -- falha e a retentativa, a próxima tentativa já sai assinada com o
    -- valor novo, correto).
    payload         JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivered', 'failed')),
    attempt_count   INT NOT NULL DEFAULT 1,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice parcial (só linhas pendentes) — é a ÚNICA consulta que o worker
-- roda em volume (varrer "o que está vencido agora"); linhas entregues/
-- desistidas nunca são buscadas por data de novo.
CREATE INDEX ix_webhook_delivery_queue_due ON core.webhook_delivery_queue (next_attempt_at) WHERE status = 'pending';

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['webhook_delivery_queue'])
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
