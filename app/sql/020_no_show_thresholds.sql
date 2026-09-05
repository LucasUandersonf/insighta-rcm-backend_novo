-- app/sql/020_no_show_thresholds.sql
--
-- Limiares de risco de falta (no-show) configuráveis por tenant — achado
-- da conversa com o usuário sobre lacunas do módulo de Agenda: os cortes
-- 10%/30% (baixo/médio/alto) do MVP eram um valor de partida razoável,
-- nunca uma calibração validada com dado real, e cada especialidade tem
-- um perfil de falta bem diferente. Agora cada clínica pode ajustar o
-- próprio corte em "Minha Clínica" — ver app/services/no_show_risk_engine.py.
--
-- Mesmo padrão de 011_annual_revenue_goal.sql: NULL é o estado inicial
-- genuíno ("ainda não configurado, usa o default do módulo"), nunca 0.
-- NUMERIC(5,4) guarda uma FRAÇÃO (0.0-1.0), mesmo tipo/escala já usado em
-- core.appointments.no_show_risk_score.
--
-- Auto-idempotente (ADD COLUMN IF NOT EXISTS) — roda em todo deploy via
-- bootstrap_db.py, sem entrar em _POST_UPGRADE_MARKER_TABLE, mesmo
-- padrão de 013_fix_plan_tier_check.sql/014_insurance_is_active.sql.
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS no_show_low_threshold NUMERIC(5, 4);
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS no_show_medium_threshold NUMERIC(5, 4);

COMMENT ON COLUMN core.tenants.no_show_low_threshold IS
  'Taxa de falta (fração 0-1) abaixo da qual um paciente é classificado "baixo risco". NULL = usa o default do módulo (0.10).';
COMMENT ON COLUMN core.tenants.no_show_medium_threshold IS
  'Taxa de falta (fração 0-1) abaixo da qual um paciente é classificado "médio risco" (acima vira "alto"). NULL = usa o default do módulo (0.30).';
