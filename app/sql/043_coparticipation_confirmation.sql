-- app/sql/043_coparticipation_confirmation.sql
--
-- Épico F4.2 do Plano Diretor ("Fechar lacunas operacionais") — a peça
-- que faltava do módulo de coparticipação: hoje o sistema sabe QUANTO
-- foi COBRADO de coparticipação (Billing.coparticipation_value, ver
-- 036_billing_appointment_extended_fields.sql) mas nunca se esse valor
-- foi de fato RECEBIDO do paciente no momento do atendimento — um valor
-- "cobrado" só no papel pode nunca ter entrado no caixa (recepção
-- esqueceu de cobrar, paciente prometeu pagar depois e sumiu, etc.).
-- Isso é uma categoria de vazamento de receita DIFERENTE de glosa
-- (nunca chega a ser cobrado do convênio, é dinheiro que deveria ter
-- sido cobrado do PRÓPRIO paciente).
--
-- DECISÃO — BOOLEAN nullable (nunca DEFAULT false)
-------------------------------------------------------------------------
-- Mesmo princípio de "None sobre zero/false inventado" usado em todo o
-- produto: NULL = "ainda não confirmado" (a esmagadora maioria dos
-- lançamentos existentes e de toda ingestão em massa, que não tem como
-- saber isso), FALSE = "confirmado que NÃO foi recebido" (um vazamento
-- de receita provado, não presumido), TRUE = confirmado recebido. Um
-- DEFAULT false inventaria "não recebido" pra todo lançamento antigo,
-- o oposto do que aconteceu de verdade (a maioria provavelmente FOI
-- recebida, só nunca foi confirmada no sistema).
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS coparticipation_received BOOLEAN;
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS coparticipation_confirmed_at TIMESTAMPTZ;
ALTER TABLE core.billing ADD COLUMN IF NOT EXISTS coparticipation_confirmed_by UUID REFERENCES core.users(id);

COMMENT ON COLUMN core.billing.coparticipation_received IS
  'Confirmação de que a coparticipação (coparticipation_value) foi de fato recebida do paciente. NULL = ainda não confirmado (estado inicial da maioria); FALSE = confirmado que NÃO foi recebida (vazamento de receita provado).';
COMMENT ON COLUMN core.billing.coparticipation_confirmed_at IS
  'Quando a confirmação foi registrada — ver POST /billing/{id}/confirm-coparticipation.';
COMMENT ON COLUMN core.billing.coparticipation_confirmed_by IS
  'Usuário que registrou a confirmação.';
