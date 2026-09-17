-- app/sql/040_tenant_calibration_fields.sql
--
-- Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
-- generaliza o padrão já existente em 020_no_show_thresholds.sql (limiar
-- configurável por tenant, NULL = usa o default do módulo) para o motor
-- de risco de glosa (smart_insights_engine.py) e a Nota de Saúde
-- Financeira (health_score_engine.py). Ver DECISÃO completa em
-- app/services/threshold_calibration.py sobre por que a calibração é
-- pelo histórico REAL da própria clínica, não por uma tabela de
-- benchmark "por especialidade" fabricada.
--
-- Mesmo padrão de 020_no_show_thresholds.sql: NULL é o estado inicial
-- genuíno, nunca 0. Auto-idempotente (ADD COLUMN IF NOT EXISTS) — roda em
-- todo deploy via bootstrap_db.py, sem entrar em _POST_UPGRADE_MARKER_TABLE.
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS specialty VARCHAR(100);
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS denial_risk_warning_threshold NUMERIC(5, 2);
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS denial_risk_critical_threshold NUMERIC(5, 2);
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS health_score_denial_ceiling NUMERIC(5, 4);
ALTER TABLE core.tenants ADD COLUMN IF NOT EXISTS health_score_no_show_ceiling NUMERIC(5, 4);

COMMENT ON COLUMN core.tenants.specialty IS
  'Especialidade predominante da clínica, informada manualmente (texto curto, ex: "odontologia"). Metadado descritivo/contexto — NULL = ainda não informado. Ver DECISÃO em app/services/threshold_calibration.py.';
COMMENT ON COLUMN core.tenants.denial_risk_warning_threshold IS
  'Percentual (0-100) de faturamento em risco médio/alto a partir do qual o insight de risco de glosa vira "atenção". NULL = usa o default do módulo (15.0). Ver smart_insights_engine.py.';
COMMENT ON COLUMN core.tenants.denial_risk_critical_threshold IS
  'Percentual (0-100) a partir do qual o insight de risco de glosa vira "crítico". NULL = usa o default do módulo (40.0). Ver smart_insights_engine.py.';
COMMENT ON COLUMN core.tenants.health_score_denial_ceiling IS
  'Taxa de glosa (fração 0-1) a partir da qual o componente de glosa da Nota de Saúde Financeira já é a pior nota possível. NULL = usa o default do módulo (0.25). Ver health_score_engine.py.';
COMMENT ON COLUMN core.tenants.health_score_no_show_ceiling IS
  'Taxa de falta (fração 0-1) a partir da qual o componente de falta da Nota de Saúde Financeira já é a pior nota possível. NULL = usa o default do módulo (0.40). Ver health_score_engine.py.';
