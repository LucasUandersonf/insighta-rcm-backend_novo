-- =====================================================================
-- ARQUIVO: 018_locais_tipo_paciente.sql
-- Local de Atendimento (Unidade/Setor) + Tipo de Paciente — Fase 4 do
-- plano de adequação ao fluxo real de mercado (Agendamento ->
-- Atendimento -> Faturamento). Ver DECISÃO completa em
-- app/sql/015_billing_guia.sql/016_lotes_faturas.sql/017_glosas.sql
-- (Fases 1-3) e MODERNANET_REFERENCIA.md/PLANO_ADEQUACAO_TISS.md
-- (conversa com o usuário) — "Local de Atendimento"/"Unidade"/"Setor" e
-- "Tipo de Paciente" (Amb/Int/PS) aparecem como filtro ou coluna em
-- praticamente TODA tela pesquisada nos 3 ERPs do mercado (Moderna,
-- Feegow, iClinic), e não existiam de forma nenhuma no nosso modelo.
--
-- DECISÃO — Local é CATÁLOGO próprio (como Professional/InsuranceCompany),
-- Tipo de Paciente é campo FIXO validado (como Guia.tipo)
-- -------------------------------------------------------------------
-- "Local de Atendimento" é algo que a clínica CADASTRA previamente
-- (ex.: "Pronto Socorro Adulto", "Recepção Central") — um catálogo por
-- tenant, com o mesmo padrão de desativação (não exclusão) já usado em
-- Professional/InsuranceCompany/InsurancePlan. "Tipo de Paciente" é um
-- vocabulário FECHADO e universal entre clínicas (Ambulatorial/
-- Internação/Pronto-Socorro) — não faz sentido cadastro próprio por
-- tenant, é só um CHECK, igual a Guia.tipo/Appointment.status.
--
-- DECISÃO — os dois campos ficam em Appointment, não em Billing
-- -------------------------------------------------------------------
-- Mesmo critério já usado para procedure_code/cid_code: são fatos do
-- ATENDIMENTO clínico, não da cobrança. Billing já referencia
-- appointment_id — quem precisar de local/tipo_paciente num
-- relatório de faturamento faz JOIN, exatamente como já acontece hoje
-- com procedimento/CID.
-- =====================================================================

CREATE TABLE core.locais (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    nome        VARCHAR(255) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_locais_tenant ON core.locais (tenant_id);

ALTER TABLE core.appointments
    ADD COLUMN local_id UUID REFERENCES core.locais(id),
    ADD COLUMN tipo_paciente VARCHAR(20),
    ADD CONSTRAINT appointments_tipo_paciente_check
        CHECK (tipo_paciente IN ('ambulatorial', 'internacao', 'pronto_socorro'));

COMMENT ON COLUMN core.appointments.local_id IS
  'Local de Atendimento/Unidade/Setor (ver core.locais) — NULLABLE: todo agendamento existente antes desta migration, e todo agendamento vindo da ingestão em massa hoje, não tem essa informação.';
COMMENT ON COLUMN core.appointments.tipo_paciente IS
  'Ambulatorial/Internação/Pronto-Socorro — NULLABLE pelo mesmo motivo de local_id.';

-- ---------------------------------------------------------------------
-- RLS na tabela nova — mesmo padrão de sempre.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['locais'])
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
