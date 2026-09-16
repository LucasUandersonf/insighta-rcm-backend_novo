-- app/sql/038_insight_outcomes.sql
--
-- Plano Diretor Insighta — épicos F1.2 (Ciclo fechado de insight: ação
-- -> resultado) e F1.3 (Atribuição e workflow), na MESMA tabela de
-- propósito (o próprio roadmap já sugere isso: "Campo assigned_to +
-- due_date em insight_outcomes, mesma tabela da F1.2").
--
-- DECISÃO — insight_key é uma STRING (slug), não um ID de linha de
-- outra tabela
-------------------------------------------------------------------------
-- generate_insights() nunca persiste os insights que produz — eles são
-- computados na hora, a cada request, a partir do estado atual do
-- banco (ver DECISÃO em smart_insights_engine.py: motor puro, sem
-- banco). Não existe "o insight nº42" com id estável pra referenciar.
-- `insight_key` é um slug determinístico (categoria + título
-- normalizado — ver app/core/text_utils.slugify, já usado em
-- contract_extraction_service) que identifica "este MESMO tipo de
-- alerta" entre execuções, o suficiente pra saber se ele ainda
-- aparece na fila quando o job de reavaliação rodar depois.
--
-- DECISÃO — snapshot de título/mensagem/impacto, não referência viva
-------------------------------------------------------------------------
-- Título e mensagem mudam a cada período (nomes de convênio, valores).
-- Gravamos o texto de QUANDO o gestor marcou o insight, pra tela
-- "Insights que valeram a pena" mostrar o que foi dito na época, sem
-- depender do insight ainda existir/ser recalculável depois.
CREATE TABLE core.insight_outcomes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    insight_key             TEXT NOT NULL,
    source                  TEXT NOT NULL CHECK (source IN ('insight', 'raiox')),
    category                TEXT NOT NULL,
    severity                TEXT NOT NULL,
    title                   TEXT NOT NULL,
    message                 TEXT NOT NULL,
    financial_impact_snapshot NUMERIC(14,2),
    status                  TEXT NOT NULL DEFAULT 'pendente'
                                CHECK (status IN ('pendente', 'em_andamento', 'resolvido', 'ignorado')),
    -- F1.3 — atribuição e prazo.
    assigned_to             UUID REFERENCES core.users(id) ON DELETE SET NULL,
    due_date                DATE,
    -- F1.2 — reavaliação: preenchidos só quando status vira 'resolvido'
    -- e o job de reavaliação (app/worker/insight_outcome_reevaluation_job.py)
    -- roda depois. resolved_metric_value NULL = ainda não reavaliado,
    -- nunca um 0 inventado.
    resolution_note         TEXT,
    resolved_at             TIMESTAMPTZ,
    resolved_metric_value   NUMERIC(14,2),
    reevaluated_at          TIMESTAMPTZ,
    created_by              UUID NOT NULL REFERENCES core.users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_insight_outcomes_tenant_status ON core.insight_outcomes (tenant_id, status);
CREATE INDEX ix_insight_outcomes_tenant_assigned ON core.insight_outcomes (tenant_id, assigned_to) WHERE assigned_to IS NOT NULL;
CREATE INDEX ix_insight_outcomes_reevaluation_pending ON core.insight_outcomes (resolved_at)
    WHERE status = 'resolvido' AND reevaluated_at IS NULL;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['insight_outcomes'])
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

COMMENT ON TABLE core.insight_outcomes IS
  'Ciclo de vida de um insight que o gestor decidiu acompanhar (F1.2) '
  'e/ou delegar (F1.3): pendente -> em_andamento -> resolvido/ignorado. '
  'insight_key identifica o TIPO de alerta (slug), não uma linha de '
  'outra tabela — generate_insights() nunca persiste instâncias.';
