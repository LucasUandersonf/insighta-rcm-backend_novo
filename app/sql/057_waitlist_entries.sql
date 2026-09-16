-- app/sql/057_waitlist_entries.sql
--
-- Onda 5 do Plano de Ação, item 16 ("agenda avançada") — hoje, quando a
-- agenda está cheia, não existe onde anotar "este paciente quer vaga
-- assim que abrir" — a recepção depende de caderno/planilha paralela,
-- ou simplesmente perde o paciente. `core.waitlist_entries` é essa
-- lista de espera de verdade, dentro do produto.
--
-- DECISÃO — 3 estados só, sem "notificado"/"em contato"
-------------------------------------------------------------------------
-- Mesmo raciocínio de core.lotes (Fase 2) e core.contracts: um estado
-- intermediário como "paciente foi avisado, aguardando confirmação"
-- exigiria um canal de notificação que o produto não tem hoje (mesmo
-- limite já documentado em AppointmentUpdateRequest sobre "confirmado").
-- 'aguardando' -> 'agendado' (converteu em consulta real,
-- resolved_appointment_id aponta pra ela) ou 'cancelado' (desistiu/não
-- respondeu) é o suficiente pra fila funcionar.
--
-- DECISÃO — professional_id e preferred_time_window OPCIONAIS
-------------------------------------------------------------------------
-- Muito paciente de lista de espera aceita "qualquer profissional,
-- qualquer horário" (só quer a vaga mais cedo possível) — exigir os
-- dois inventaria uma preferência que não existe. Quando preenchidos,
-- ajudam a recepção a filtrar a lista na hora de oferecer uma vaga
-- específica.
CREATE TABLE core.waitlist_entries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    patient_id              UUID NOT NULL REFERENCES core.patients(id) ON DELETE CASCADE,
    professional_id         UUID REFERENCES core.professionals(id) ON DELETE SET NULL,
    procedure_code          TEXT,
    preferred_time_window   TEXT CHECK (preferred_time_window IS NULL OR preferred_time_window IN ('manha', 'tarde', 'noite')),
    notes                   TEXT,
    status                  TEXT NOT NULL DEFAULT 'aguardando' CHECK (status IN ('aguardando', 'agendado', 'cancelado')),
    resolved_appointment_id UUID REFERENCES core.appointments(id) ON DELETE SET NULL,
    created_by              UUID NOT NULL REFERENCES core.users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at             TIMESTAMPTZ
);

CREATE INDEX ix_waitlist_entries_tenant_status ON core.waitlist_entries (tenant_id, status);

DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['waitlist_entries'])
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

COMMENT ON TABLE core.waitlist_entries IS
  'Onda 5 do Plano de Ação, item 16 — lista de espera de verdade: '
  'paciente quer vaga assim que abrir. status aguardando->agendado '
  '(resolved_appointment_id aponta pra consulta real criada) ou '
  '->cancelado (desistiu/não respondeu).';
