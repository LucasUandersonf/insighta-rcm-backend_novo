-- =====================================================================
-- ARQUIVO: 036_billing_appointment_extended_fields.sql
-- Campos novos do Dicionário de Dados dos Templates de Integração
-- (achado desta rodada de auditoria BI/Dados): coparticipação/franquia,
-- número de carteirinha, tipo de item (destaca OPME), quantidade
-- (Faturamento) + data de marcação, tipo de consulta, motivo de
-- cancelamento, canal de agendamento (Agenda).
--
-- DECISÃO — sem tabela nova, só ALTER TABLE em Billing/Appointment
-- -------------------------------------------------------------------
-- Nenhum destes campos é uma entidade própria (diferente de Guia/Lote/
-- Fatura/Glosa nas fases anteriores) — são atributos adicionais do
-- mesmo atendimento/cobrança que já existe. Mesmo critério de
-- procedure_code/cid_code: local_atendimento e tipo_paciente já
-- seguiram esse padrão em 018_locais_tipo_paciente.sql.
--
-- DECISÃO — quantity com DEFAULT 1, nunca NULL
-- -------------------------------------------------------------------
-- Todo billing existente antes desta migration é, por definição, 1
-- unidade do procedimento (o schema de ingestão nunca teve outro jeito
-- de representar quantidade > 1 antes de hoje) — DEFAULT 1 preserva
-- esse fato para os dados antigos sem reescrever nada, e é o valor que
-- o motor de risco (denial_risk_engine.assess) usa para multiplicar
-- ContractItem.agreed_price na comparação de valor.
--
-- DECISÃO — booking_channel é campo PRÓPRIO em Appointment, não
-- reaproveita Patient.acquisition_source
-- -------------------------------------------------------------------
-- acquisition_source mede a origem de MARKETING do paciente (ROI de
-- campanha, um conceito por PACIENTE, setado uma vez); booking_channel
-- mede por qual canal ESTE agendamento específico foi marcado (um
-- conceito por CONSULTA, pode mudar a cada reagendamento). Reaproveitar
-- o mesmo campo sobrescreveria dado de ROI de marketing com dado
-- operacional toda vez que o mesmo paciente marcasse por outro canal.
-- =====================================================================

ALTER TABLE core.billing
    ADD COLUMN IF NOT EXISTS quantity INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS member_card_number VARCHAR(50),
    ADD COLUMN IF NOT EXISTS item_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS coparticipation_value NUMERIC(12, 2);

ALTER TABLE core.billing DROP CONSTRAINT IF EXISTS billing_quantity_check;
ALTER TABLE core.billing
    ADD CONSTRAINT billing_quantity_check CHECK (quantity > 0);

ALTER TABLE core.billing DROP CONSTRAINT IF EXISTS billing_item_type_check;
ALTER TABLE core.billing
    ADD CONSTRAINT billing_item_type_check
        CHECK (item_type IS NULL OR item_type IN ('procedimento', 'material_opme', 'taxa', 'diaria', 'medicamento'));

COMMENT ON COLUMN core.billing.quantity IS
  'Unidades do mesmo procedimento cobradas nesta linha — DEFAULT 1 para todo billing anterior a esta migration. Multiplica ContractItem.agreed_price na comparação de risco de glosa.';
COMMENT ON COLUMN core.billing.member_card_number IS
  'Número da carteirinha do paciente NO CONVÊNIO desta linha — NULLABLE, nem todo ERP de origem exporta isso.';
COMMENT ON COLUMN core.billing.item_type IS
  'procedimento/material_opme/taxa/diaria/medicamento — NULLABLE. OPME é destacado à parte por ser fonte frequente de glosa de alto valor.';
COMMENT ON COLUMN core.billing.coparticipation_value IS
  'Valor cobrado DIRETO do paciente (coparticipação/franquia), à parte do que o convênio paga — NULLABLE, nunca confundido com charged_value.';

ALTER TABLE core.appointments
    ADD COLUMN IF NOT EXISTS booked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS visit_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS cancellation_reason TEXT,
    ADD COLUMN IF NOT EXISTS booking_channel VARCHAR(50);

ALTER TABLE core.appointments DROP CONSTRAINT IF EXISTS appointments_visit_type_check;
ALTER TABLE core.appointments
    ADD CONSTRAINT appointments_visit_type_check
        CHECK (visit_type IS NULL OR visit_type IN ('primeira_consulta', 'retorno'));

COMMENT ON COLUMN core.appointments.booked_at IS
  'Quando o agendamento foi de fato marcado no sistema de origem — diferente de created_at (quando esta linha entrou no nosso banco). NULLABLE.';
COMMENT ON COLUMN core.appointments.visit_type IS
  'primeira_consulta/retorno — NULLABLE, nem todo ERP de origem distingue isso hoje.';
COMMENT ON COLUMN core.appointments.cancellation_reason IS
  'Motivo de cancelamento/remarcação em texto livre — NULLABLE.';
COMMENT ON COLUMN core.appointments.booking_channel IS
  'Canal por onde ESTE agendamento foi marcado (telefone/whatsapp/site/presencial) — NULLABLE. Campo próprio, não reaproveita Patient.acquisition_source (ver DECISÃO no topo do arquivo).';
