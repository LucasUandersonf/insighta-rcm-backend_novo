-- app/sql/055_patient_outreach_log.sql
--
-- Onda 4 do Plano de Ação ("CRM de verdade: ação, não só leitura"),
-- item 12 — as listas de reativação (InactivePatients, RFM
-- action_items) hoje só APONTAM quem precisa de contato; não existe
-- nenhum registro de que a clínica de fato ligou/mandou mensagem. Sem
-- isso, a recepção religa pro mesmo paciente toda semana sem saber que
-- já tentou, e ninguém sabe se a campanha de reativação está
-- funcionando.
--
-- DECISÃO — log de contato, não uma fila de tarefa com status
-- -------------------------------------------------------------------
-- Não modela "tarefa pendente atribuída" (isso já existe pra outro
-- domínio — ver InsightAssignment em 038_insight_outcomes.sql/F1.3).
-- Aqui é mais simples: cada linha é um FATO já acontecido ("liguei pro
-- paciente X em tal dia, por tal canal, resultado Y") — mesmo espírito
-- de core.cost_entries (lançamento manual de um evento real, não uma
-- regra automática). `outcome` fecha o ciclo: sem ele, o log seria só
-- "eu tentei", nunca "e funcionou?".
CREATE TABLE core.patient_outreach_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    patient_id  UUID NOT NULL REFERENCES core.patients(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL CHECK (channel IN ('telefone', 'whatsapp', 'sms', 'email', 'presencial')),
    outcome     TEXT NOT NULL CHECK (outcome IN ('contatado', 'sem_resposta', 'agendou', 'recusou')),
    notes       TEXT,
    created_by  UUID NOT NULL REFERENCES core.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice pro caso de uso real: "qual foi o ÚLTIMO contato deste
-- paciente" (anotação em InactivePatients/RfmResponse.action_items,
-- ver AnalyticsRepository.latest_outreach_by_patient_ids) — DESC já
-- serve o MAX(created_at) por patient_id sem sort extra.
CREATE INDEX ix_patient_outreach_log_patient_created ON core.patient_outreach_log (patient_id, created_at DESC);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['patient_outreach_log'])
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

COMMENT ON TABLE core.patient_outreach_log IS
  'Registro manual de contato de reativação/recontato com um paciente '
  '(ligou, mandou WhatsApp...) e o resultado — fecha o ciclo das listas '
  'de reativação (InactivePatients, RFM action_items), que antes só '
  'apontavam quem contatar, sem saber quem já foi contatado nem se '
  'funcionou.';
