-- app/sql/045_patient_relationship_fields.sql
--
-- "Mapa de Dados Insighta" — Domínio 1/6 (Paciente): hoje capturamos bem
-- o paciente TRANSACIONAL (o que comprou, quando faltou). Falta o
-- paciente RELACIONAL: quem indicou, se pode ser contatado por
-- campanha, que horário prefere, onde mora. Campos de baixíssimo
-- atrito — cabem no cadastro que já existe, sem tela nova (Onda 1 do
-- mapa de dados).
--
-- DECISÃO — referred_by_patient_id sem FOREIGN KEY até core.patients
-- diretamente cross-tenant
-------------------------------------------------------------------------
-- A constraint FK do Postgres não enxerga RLS: sem cuidado, um tenant
-- malicioso poderia gravar o UUID de um paciente de OUTRO tenant aqui
-- (a FK só exige "existe uma linha com esse id em core.patients",
-- não "existe uma linha desse tenant"). Por isso a referência é
-- validada na CAMADA DE SERVIÇO (PatientService.create_patient), que
-- busca o indicador através do MESMO repositório com RLS ativo — se
-- o paciente indicador não aparecer (porque é de outro tenant, ou não
-- existe), o service rejeita com 422 antes de chegar aqui. A FK ainda
-- existe (garante integridade referencial básica e permite JOIN), só
-- não é a única linha de defesa contra vazamento entre tenants.
--
-- DECISÃO — communication_consent BOOLEAN nullable (nunca DEFAULT false)
-------------------------------------------------------------------------
-- Mesmo princípio de "None sobre zero/false inventado" do resto do
-- produto: NULL = "nunca perguntado" (todo cadastro existente e toda
-- ingestão em massa, que não tem como saber isso), FALSE = "perguntou e
-- o paciente recusou" (nunca pode virar campanha automatizada), TRUE =
-- consentiu. Um DEFAULT false bloquearia campanha pra 100% da base
-- histórica sem necessidade; um DEFAULT true inventaria consentimento
-- que ninguém deu — LGPD exige a base legal ser real, não presumida.
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS referred_by_patient_id UUID REFERENCES core.patients(id);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS communication_consent BOOLEAN;
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS preferred_time_window VARCHAR(10);
ALTER TABLE core.patients ADD COLUMN IF NOT EXISTS zip_code VARCHAR(8);

DO $$ BEGIN
  ALTER TABLE core.patients ADD CONSTRAINT patients_preferred_time_window_check
    CHECK (preferred_time_window IS NULL OR preferred_time_window IN ('manha', 'tarde', 'noite'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN core.patients.referred_by_patient_id IS
  'Paciente que indicou este — capturado no cadastro/primeiro agendamento ("quem te indicou?"). NULL = veio por outro canal, ou nunca perguntado. Ver DECISÃO acima sobre validação de tenant na camada de serviço.';
COMMENT ON COLUMN core.patients.communication_consent IS
  'Consentimento explícito (LGPD) para contato de campanha/reengajamento. NULL = nunca perguntado (estado inicial da maioria); FALSE = perguntado e recusado — pré-requisito técnico de qualquer campanha automatizada.';
COMMENT ON COLUMN core.patients.preferred_time_window IS
  'Janela de horário preferida (manha/tarde/noite) — alimenta preenchimento assistido de horário ocioso. NULL = nunca informado.';
COMMENT ON COLUMN core.patients.zip_code IS
  'CEP, só dígitos (8 caracteres) — alimenta estudo de falta × deslocamento e expansão de unidade. NULL = não informado.';
