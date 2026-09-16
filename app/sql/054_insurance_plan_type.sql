-- app/sql/054_insurance_plan_type.sql
--
-- Plano de Ação Insighta — Onda 3 ("particular como cidadão de primeira
-- classe"): core.billing.insurance_plan_id é OBRIGATÓRIO desde
-- 001_init_schema.sql, então hoje uma clínica com paciente particular
-- (sem convênio) só consegue faturar cadastrando um InsurancePlan "de
-- mentira" pra contornar a constraint. Isso funciona por acidente, mas
-- deixa TODA a analytics (PMR, glosa, utilização de contrato) sem jeito
-- de segregar particular de convênio de verdade — fica tudo misturado
-- no mesmo balde.
--
-- default 'convenio' é retrocompatível de propósito: todo InsurancePlan
-- já cadastrado (real ou "de mentira") continua sendo lido como
-- convênio — ninguém reinterpreta cadastro existente sozinho. Quem
-- quiser separar o "de mentira" de verdade precisa editar esse plano
-- explicitamente (PATCH /insurance-companies/plans/{id}) depois desta
-- migration.
ALTER TABLE core.insurance_plans ADD COLUMN IF NOT EXISTS plan_type VARCHAR(20) NOT NULL DEFAULT 'convenio';

DO $$ BEGIN
  ALTER TABLE core.insurance_plans ADD CONSTRAINT insurance_plans_plan_type_check
    CHECK (plan_type IN ('convenio', 'particular'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN core.insurance_plans.plan_type IS
  'convenio (padrão, retrocompatível) ou particular (paciente sem operadora — Contract/ContractItem deste plano vira a tabela de preço PARTICULAR, reaproveitando o mesmo motor de divergência de cobrança do convênio). Nunca NULL.';
