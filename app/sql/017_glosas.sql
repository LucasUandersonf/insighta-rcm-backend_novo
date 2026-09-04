-- =====================================================================
-- ARQUIVO: 017_glosas.sql
-- Glosa REAL — Fase 3 do plano de adequação ao fluxo real de mercado
-- (Agendamento -> Atendimento -> Faturamento). Ver DECISÃO completa em
-- app/sql/015_billing_guia.sql/016_lotes_faturas.sql (Fases 1-2) e
-- MODERNANET_REFERENCIA.md/PLANO_ADEQUACAO_TISS.md (conversa com o
-- usuário) — o módulo "CONCILIAÇÃO E GLOSAS" de um ERP real é processo
-- próprio, distinto de um dashboard.
--
-- DECISÃO — Glosa é uma entidade NOVA, separada de DenialAppeal
-- -------------------------------------------------------------------
-- `core.denial_appeals` (008_denial_appeals.sql) já existe e modela o
-- RECURSO (o expediente de contestação, com prazo e status
-- aberto/protocolado/deferido/indeferido/nip_aberta) — mas nem toda
-- glosa real vira recurso: uma glosa pequena pode simplesmente ser
-- aceita sem contestação. `Glosa` registra o FATO ("a operadora negou/
-- reduziu X"), independente de a clínica decidir recorrer ou não.
-- DenialAppeal continua existindo do jeito que está — sem mudança
-- nesta migration.
--
-- DECISÃO — Glosa é o dado que falta para medir a PRECISÃO do motor de
-- risco preditivo (denial_risk_engine.py)
-- -------------------------------------------------------------------
-- `Billing.denial_risk_level` é uma PREVISÃO calculada no momento da
-- cobrança, antes de qualquer envio. Sem registrar o que a operadora
-- de fato respondeu, nunca dá pra saber se essa previsão está certa —
-- nem quantificar o caso mais perigoso (glosa real em um billing que o
-- motor marcou como risco BAIXO, ou seja, o motor "não viu vir").
-- =====================================================================

CREATE TABLE core.glosas (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    billing_id          UUID NOT NULL REFERENCES core.billing(id),
    -- Código do motivo de glosa (Tabela 27 do padrão TISS/ANS) —
    -- NULLABLE porque nem sempre a operadora devolve o código
    -- estruturado (às vezes só um demonstrativo em texto livre).
    codigo_motivo       VARCHAR(10),
    descricao_motivo    TEXT,
    valor_glosado       NUMERIC(12, 2) NOT NULL,
    data_recebimento    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT glosas_valor_check CHECK (valor_glosado > 0)
);

CREATE INDEX ix_glosas_tenant_billing ON core.glosas (tenant_id, billing_id);

-- ---------------------------------------------------------------------
-- RLS na tabela nova — mesmo padrão de sempre.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['glosas'])
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
