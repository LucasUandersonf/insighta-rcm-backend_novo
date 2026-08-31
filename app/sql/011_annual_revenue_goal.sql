-- =====================================================================
-- ARQUIVO: 011_annual_revenue_goal.sql
-- Campo MANUAL de meta de faturamento anual em "Minha Clínica" (Painel
-- do Administrador da Empresa — app/schemas/tenant.py). Alimenta o
-- insight de desempenho anual da Sala de Comando (comparação com o
-- faturamento acumulado no ano e a recomendação de CRM/recuperação de
-- pacientes inativos quando abaixo da meta) — ver
-- app/services/smart_insights_engine.py.
--
-- DECISÃO — campo manual, não calculado
-- ---------------------------------------------------------------------
-- Confirmado explicitamente pelo usuário durante a auditoria: a meta é
-- uma decisão de negócio da clínica (quanto ELA quer faturar), não algo
-- que o sistema deveria inferir de dado histórico (ex: "faturamento do
-- ano passado + 10%" seria uma suposição arbitrária escondida atrás de
-- uma aparência de cálculo objetivo).
--
-- DECISÃO — NUMERIC(14, 2), nullable, sem valor default
-- ---------------------------------------------------------------------
-- NULL é o estado inicial genuíno ("a clínica ainda não configurou uma
-- meta") — diferente de 0, que seria uma meta real de "faturar zero".
-- O insight de desempenho anual (Etapa seguinte) trata NULL como "ainda
-- não há meta configurada, não gerar o insight", nunca como 0/indefinido
-- matemático (mesmo princípio de "None sobre zero" já usado em todo o
-- resto do produto para razões/percentuais — ver relatório da Auditoria
-- Go-Live).
--
-- Mesmo padrão de idempotência de 010_ingestion_original_filename.sql:
-- ADD COLUMN IF NOT EXISTS é idempotente por natureza, então este
-- arquivo entra em _POST_UPGRADE_SQL_FILES SEM marcador em
-- _POST_UPGRADE_MARKER_TABLE (ver app/scripts/bootstrap_db.py).
-- =====================================================================

ALTER TABLE core.tenants
    ADD COLUMN IF NOT EXISTS annual_revenue_goal NUMERIC(14, 2);

COMMENT ON COLUMN core.tenants.annual_revenue_goal IS
  'Meta de faturamento anual configurada manualmente pela clínica em Minha Clínica. '
  'NULL = meta ainda não configurada (não é o mesmo que meta = 0).';
