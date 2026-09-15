-- app/sql/051_professional_contract_commission.sql
--
-- "Mapa de Dados Insighta" — Domínio Profissional (Onda 2), pilar
-- Rentabilidade por profissional: hoje a receita líquida por hora de
-- agenda (já calculada, ver DECISÃO em app/services/profitability_service.py)
-- não distingue um profissional CLT (custo fixo, já capturado via
-- CostEntry) de um PJ/cooperado remunerado por COMISSÃO sobre o que
-- fatura — sem o TIPO de contrato e o PERCENTUAL de comissão, o "custo
-- real" desse segundo grupo fica invisível em qualquer relatório de
-- margem.
--
-- DECISÃO — vocabulário FECHADO para contract_type
-------------------------------------------------------------------------
-- "clt"/"pj"/"autonomo"/"cooperado" — os 4 arranjos de contratação mais
-- comuns entre profissionais de saúde no Brasil (mesmo raciocínio de
-- TIPO_PACIENTE_VALUES: vocabulário fechado e universal, não um texto
-- livre que cada clínica preenche diferente). commission_rate (percentual
-- 0-100) só faz sentido para pj/autonomo/cooperado na prática, mas a
-- coluna não impõe isso — a mesma clínica pode remunerar um CLT com um
-- bônus percentual variável, não cabe ao banco decidir isso por ela.
ALTER TABLE core.professionals ADD COLUMN IF NOT EXISTS contract_type TEXT;
ALTER TABLE core.professionals ADD COLUMN IF NOT EXISTS commission_rate NUMERIC(5, 2);

DO $$ BEGIN
  ALTER TABLE core.professionals ADD CONSTRAINT professionals_contract_type_check
    CHECK (contract_type IS NULL OR contract_type IN ('clt', 'pj', 'autonomo', 'cooperado'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE core.professionals ADD CONSTRAINT professionals_commission_rate_check
    CHECK (commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 100));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN core.professionals.contract_type IS
  '"Mapa de Dados Insighta" — clt/pj/autonomo/cooperado. NULL = não informado ainda.';
COMMENT ON COLUMN core.professionals.commission_rate IS
  '"Mapa de Dados Insighta" — percentual de comissão sobre faturamento (0-100), quando aplicável. NULL = não se aplica/não informado.';
