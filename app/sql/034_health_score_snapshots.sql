-- app/sql/034_health_score_snapshots.sql
--
-- Tendência da Nota de Saúde Financeira (Sala de Comando 2.0): até
-- aqui, o anel da Nota de Saúde só mostrava o número ATUAL — sem
-- histórico, não tinha como responder "isso está melhorando ou
-- piorando?". Esta tabela guarda uma FOTOGRAFIA da nota por tenant, uma
-- vez por mês (ver app/worker/health_score_snapshot_job.py), para o
-- endpoint de saúde poder comparar a nota de hoje contra a de ~3 meses
-- atrás.
--
-- DECISÃO — granularidade MENSAL, não diária
-------------------------------------------------------------------------
-- A própria nota já usa uma janela fixa de 90 dias (ver
-- _HEALTH_SCORE_WINDOW_DAYS em analytics_service.py) — ela não muda o
-- suficiente de um dia para o outro para justificar uma linha por dia
-- (só ruído de armazenamento). Uma fotografia por mês já é granularidade
-- de sobra para uma tendência de "isso está melhorando?" que o próprio
-- indicador foi desenhado para responder devagar.
--
-- DECISÃO — tenant_id + RLS normal (dado da clínica, não bookkeeping
-- interno da Insighta)
-------------------------------------------------------------------------
-- Mesmo raciocínio de webhook_delivery_queue (028_webhook_delivery_queue.sql):
-- isto é "o histórico de saúde financeira DA CLÍNICA", segue a
-- convenção padrão do resto do schema — nunca lido cross-tenant, ao
-- contrário de network_benchmark/network_contract_price_benchmark.
CREATE TABLE core.health_score_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    -- Sempre o dia 1 do mês do snapshot (ex: 2026-09-01) — normaliza a
    -- chave de upsert independente de que dia do mês o job rodou.
    snapshot_month  DATE NOT NULL,
    -- NULLABLE — mesmo critério de HealthScoreResponse.score: None
    -- quando a amostra do período era insuficiente em TODOS os
    -- componentes (nunca grava um 0 inventado).
    score           NUMERIC(5,2),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, snapshot_month)
);

CREATE INDEX ix_health_score_snapshots_tenant_month ON core.health_score_snapshots (tenant_id, snapshot_month DESC);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['health_score_snapshots'])
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

COMMENT ON TABLE core.health_score_snapshots IS
  'Fotografia mensal da Nota de Saúde Financeira por tenant — usada só '
  'para calcular tendência (nota de hoje vs. ~90 dias atrás). Uma linha '
  'por (tenant_id, snapshot_month); score NULL quando a amostra do mês '
  'era insuficiente.';
