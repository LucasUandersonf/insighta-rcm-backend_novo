-- app/sql/022_patient_lgpd_erasure.sql
--
-- Direito de eliminação do titular (LGPD, art. 18, VI) — achado da
-- checklist de produção: "política de retenção/exclusão de dado de
-- paciente" estava listada como pendência sem nenhuma implementação.
--
-- DECISÃO — ANONIMIZAÇÃO, não exclusão física
-------------------------------------------------------------------------
-- Excluir a linha de core.patients fisicamente quebraria a integridade
-- referencial com core.appointments/core.billing (histórico clínico e
-- financeiro que a clínica é OBRIGADA a reter por lei — retenção fiscal/
-- contábil de faturamento, tipicamente 5 anos no Brasil). A própria LGPD
-- (art. 16) permite manter o dado quando necessário para cumprir
-- obrigação legal ou para exercício de direitos em processo
-- administrativo/judicial — por isso o mecanismo aqui é sempre
-- ANONIMIZAR (remover nome/CPF/data de nascimento, preservar o
-- vínculo com o histórico agregado), nunca DELETE. Ver DECISÃO completa
-- em app/services/patient_service.py (anonymize_patient).
--
-- Auto-idempotente (ADD COLUMN IF NOT EXISTS) — roda em todo deploy via
-- bootstrap_db.py, mesmo padrão de 020_no_show_thresholds.sql.
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS anonymized_at TIMESTAMPTZ;

COMMENT ON COLUMN core.patients.anonymized_at IS
  'Preenchido quando o titular exerce o direito de eliminação (LGPD art. 18, VI) — a partir daqui full_name/cpf/birth_date/acquisition_* são substituídos por um placeholder, nunca apagados fisicamente. NULL = paciente ainda com dado pessoal intacto.';
