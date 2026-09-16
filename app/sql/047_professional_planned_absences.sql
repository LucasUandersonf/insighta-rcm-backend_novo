-- app/sql/047_professional_planned_absences.sql
--
-- "Mapa de Dados Insighta" — Domínio 3/6 (Profissional): grade semanal
-- (core.professional_availability, já existente) diz a capacidade
-- TEÓRICA recorrente; nunca soube de férias/ausência FUTURA planejada —
-- toda projeção de capacidade (ex.: base do épico F3.4, "Decisões de
-- capital") só conseguia olhar pra TRÁS. Tabela própria, não uma
-- exceção dentro da grade semanal: ausência planejada é um intervalo de
-- DATAS (início/fim), não um dia-da-semana recorrente — modelo
-- diferente, tabela diferente.
--
-- CREATE TABLE IF NOT EXISTS — auto-idempotente, não precisa de
-- _POST_UPGRADE_MARKER_TABLE (mesmo raciocínio de 042_organizations.sql).
CREATE TABLE IF NOT EXISTS core.professional_planned_absences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES core.tenants(id),
    professional_id UUID NOT NULL REFERENCES core.professionals(id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT professional_planned_absences_date_order_check CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_professional_planned_absences_professional
    ON core.professional_planned_absences (professional_id, start_date);

COMMENT ON TABLE core.professional_planned_absences IS
  '"Mapa de Dados Insighta" — Domínio Profissional. Ausência futura planejada (férias, licença) por intervalo de datas — alimenta previsão de capacidade futura, não só olhando pra trás.';

-- RLS — mesmo padrão de sempre (ver 039_cost_entries.sql). CREATE POLICY
-- NÃO é idempotente (Postgres não tem "IF NOT EXISTS" pra policy) — por
-- isso este arquivo entra em _POST_UPGRADE_MARKER_TABLE
-- (bootstrap_db.py), marcado por esta própria tabela: só roda uma vez.
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['professional_planned_absences'])
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
