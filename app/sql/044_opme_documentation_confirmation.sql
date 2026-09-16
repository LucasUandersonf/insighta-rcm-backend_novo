-- app/sql/044_opme_documentation_confirmation.sql
--
-- Épico F2.3 do Plano Diretor ("Auditoria documental leve: prontuário ×
-- conta") — versão RESTRITA explicitamente pedida no roadmap: "checar
-- presença de registro de prescrição/evolução para procedimentos de
-- alto valor (OPME), sem NLP semântico". O produto não lê nem
-- interpreta prontuário nenhum — só registra que um HUMANO conferiu (ou
-- não) que a prescrição/evolução clínica existe pra sustentar a cobrança
-- de OPME antes da guia ir pro convênio, mesma lacuna de "cobrado no
-- papel não é o mesmo que confirmado de verdade" que
-- 043_coparticipation_confirmation.sql já resolveu para coparticipação.
--
-- Por que em Billing, não em Appointment
-------------------------------------------------------------------------
-- OPME é identificado por Billing.item_type = 'material_opme' (ver
-- ITEM_TYPE_VALUES, app/models/billing.py) — a mesma granularidade já
-- usada pelo insight de concentração de OPME
-- (_opme_concentration_insight). Prescrição/evolução sustentam o
-- MATERIAL cobrado, não a consulta inteira; uma guia com várias linhas
-- pode ter só uma delas sendo OPME.
--
-- DECISÃO — BOOLEAN nullable (nunca DEFAULT false), mesmo princípio de
-- 043_coparticipation_confirmation.sql
-------------------------------------------------------------------------
-- NULL = "ainda não conferido" (estado inicial de toda linha OPME
-- existente e de toda ingestão em massa, que não tem como saber isso),
-- FALSE = "conferido e o registro NÃO foi encontrado" (risco de glosa
-- documental PROVADO, não presumido), TRUE = conferido e encontrado. Um
-- DEFAULT false inventaria "documentação ausente" pra toda linha OPME
-- antiga, o oposto do que provavelmente aconteceu (a maioria
-- provavelmente TEM o registro, só nunca foi conferida no sistema).
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS clinical_documentation_confirmed BOOLEAN;
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS clinical_documentation_confirmed_at TIMESTAMPTZ;
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS clinical_documentation_confirmed_by UUID REFERENCES core.users(id);

COMMENT ON COLUMN core.billing.clinical_documentation_confirmed IS
  'Épico F2.3 — auditoria documental leve (versão restrita, sem NLP): confirmação de que existe registro de prescrição/evolução sustentando este item OPME. NULL = ainda não conferido (estado inicial da maioria); FALSE = conferido e o registro NÃO foi encontrado (risco de glosa documental provado).';
COMMENT ON COLUMN core.billing.clinical_documentation_confirmed_at IS
  'Quando a conferência foi registrada — ver POST /billing/{id}/confirm-clinical-documentation.';
COMMENT ON COLUMN core.billing.clinical_documentation_confirmed_by IS
  'Usuário que registrou a conferência.';
