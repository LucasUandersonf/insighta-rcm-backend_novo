-- app/sql/039_tracked_alerts.sql
--
-- Memória contínua dia-a-dia (Roadmap "Rumo à Nota 9", Fase 3) — pedido
-- direto do usuário: "se ele mencione algo na agenda de um paciente ele
-- no dia foi ajustado, o sistema manda a atualização de novos dados...
-- no outro dia a ia já menciona que o erro foi corrigido". Até esta
-- rodada, cada geração de narrativa (executive_narrative_service.py)
-- olhava só o PRESENTE — não existia nenhuma memória do que já tinha
-- sido sinalizado, então o sistema nunca conseguia dizer "aquele
-- problema de ontem já foi resolvido".
--
-- DECISÃO — uma linha por SITUAÇÃO rastreada, não um snapshot diário
-- em bloco (JSON por dia)
-------------------------------------------------------------------------
-- Um snapshot-por-dia (uma linha grande por tenant/dia com a lista
-- inteira de alertas abertos) exigiria reprocessar e comparar dois
-- blobs a cada checagem. Uma linha por situação (identificada por
-- `fact_key`, ver DECISÃO em tracked_alert_repository.py sobre como essa
-- chave é derivada) transforma "o que mudou desde ontem" numa pergunta
-- direta: toda linha ainda sem `resolved_date` que sumiu da lista de
-- hoje vira resolvida HOJE; toda chave nova vira uma linha nova. Nunca
-- é apagada — fica como histórico de quando cada problema apareceu e
-- foi corrigido.
--
-- DECISÃO — tenant_id + RLS normal (dado da clínica), mesmo raciocínio
-- de executive_narratives (038) e health_score_snapshots (034).
CREATE TABLE core.tracked_alerts (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    -- Identificador estável da SITUAÇÃO (não do texto exibido, que muda
    -- de dia para dia com os números) — ver DECISÃO em
    -- tracked_alert_repository.py::derive_fact_key.
    fact_key             TEXT NOT NULL,
    category             TEXT NOT NULL,
    -- Título do insight na última vez em que esteve ativo — guardado só
    -- para a narrativa poder citar "o problema de [título]" sem precisar
    -- recalcular o insight original (que pode nem existir mais).
    title                TEXT NOT NULL,
    first_detected_date  DATE NOT NULL,
    last_detected_date   DATE NOT NULL,
    -- NULL = ainda ativo. Preenchida na primeira sincronização em que a
    -- situação deixa de aparecer entre os insights ativos do tenant.
    resolved_date         DATE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, fact_key)
);

CREATE INDEX ix_tracked_alerts_tenant_resolved ON core.tracked_alerts (tenant_id, resolved_date);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['tracked_alerts'])
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

COMMENT ON TABLE core.tracked_alerts IS
  'Memória contínua dia-a-dia: uma linha por situação sinalizada pelos insights, '
  'com resolved_date preenchida quando a situação some da lista de insights ativos — '
  'permite a narrativa (Camada 3) dizer "isso que eu avisei já foi corrigido".';
