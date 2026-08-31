-- =====================================================================
-- ARQUIVO: 004_capacity_management.sql
-- Capacity & Utilization Management — analytics de agenda
--
-- DECISÃO DE ESCOPO — por que professionals + grade semanal, e por que
-- NÃO salas/recursos nem calendário de exceções nesta rodada
-- ---------------------------------------------------------------------
-- "Capacidade" pode ser modelada em granularidades crescentes:
--   1) Agregada por clínica       -> mais simples, mas quase todo cliente
--      de RCM médico quer abrir "quem está ocioso" por profissional.
--   2) Por profissional (ESTA)    -> desbloqueia a maioria das perguntas
--      de negócio reais (taxa de ocupação do Dr. X, no-show por médico)
--      com uma única entidade nova.
--   3) Por profissional + sala/equipamento -> sistema de reserva de
--      recursos com regras de conflito (duas pessoas não podem usar a
--      mesma sala ao mesmo tempo). Escopo de feature própria, não
--      implementado agora por falta de validação de que é o problema
--      prioritário do cliente.
-- Escolhemos o nível 2. A tabela core.resources (nível 3) pode ser
-- adicionada depois sem quebrar nada daqui — appointments ganharia uma
-- segunda FK opcional, não uma reformulação.
--
-- Também deliberadamente NÃO modelamos exceções de calendário (feriado,
-- férias, licença médica). professional_availability é só uma GRADE
-- SEMANAL RECORRENTE ("Dr. X atende seg-sex, 8h-12h e 14h-18h"). Isso
-- responde à pergunta "qual a capacidade teórica instalada" — que já é
-- o número que a diretoria quer ver num MVP. Exceções pontuais fazem a
-- métrica mais precisa, mas são complexidade real (cada exceção seria
-- uma linha datada, com uma regra de precedência sobre a grade
-- recorrente); ficam para quando houver sinal de que a imprecisão
-- importa na prática.
-- =====================================================================

SET search_path TO core, public;

-- =====================================================================
-- TABELA: professionals
-- =====================================================================
CREATE TABLE IF NOT EXISTS core.professionals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    full_name               VARCHAR(255) NOT NULL,
    professional_registry   VARCHAR(30),      -- CRM/CRO/etc., sem validação de formato por conselho (varia por categoria)
    specialty                VARCHAR(100),
    is_active                BOOLEAN NOT NULL DEFAULT true,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =====================================================================
-- TABELA: professional_availability
-- Grade semanal recorrente. Múltiplas linhas por profissional+dia da
-- semana são permitidas de propósito (ex: manhã e tarde com intervalo
-- de almoço no meio = 2 blocos no mesmo weekday).
-- =====================================================================
CREATE TABLE IF NOT EXISTS core.professional_availability (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    professional_id UUID NOT NULL REFERENCES core.professionals(id) ON DELETE CASCADE,
    weekday         SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0=domingo .. 6=sábado (convenção Python date.weekday()+1 tratada na app)
    start_time      TIME NOT NULL,
    end_time        TIME NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_professional_availability_lookup
    ON core.professional_availability (tenant_id, professional_id, weekday);

-- =====================================================================
-- appointments ganha profissional + duração — necessário para calcular
-- minutos ocupados (antes só sabíamos "aconteceu", não "por quanto tempo").
-- Ambas as colunas são NULLABLE: appointments já existentes continuam
-- válidos, e a Etapa 2 (normalização) hoje não tem como saber o
-- profissional a partir de um arquivo legado que não traz esse dado —
-- fica de fora do cálculo de utilização até ser preenchido.
-- =====================================================================
ALTER TABLE core.appointments
    ADD COLUMN IF NOT EXISTS professional_id UUID REFERENCES core.professionals(id),
    ADD COLUMN IF NOT EXISTS duration_minutes INTEGER CHECK (duration_minutes > 0);

CREATE INDEX IF NOT EXISTS idx_appointments_professional
    ON core.appointments (tenant_id, professional_id, scheduled_at);


-- =====================================================================
-- RLS — mesmo padrão de sempre.
-- =====================================================================
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['professionals','professional_availability'])
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
