-- app/sql/046_appointment_visit_intent.sql
--
-- "Mapa de Dados Insighta" — Domínio 2/6 (Pós-atendimento): motivo
-- ESTRUTURADO do agendamento, capturado na hora de marcar — alimenta
-- segmentação de campanha de reengajamento ("quem tem retorno pendente"
-- é uma campanha bem diferente de "quem só fez avaliação e nunca
-- voltou"). Complementa Appointment.visit_type (já existente,
-- primeira_consulta/retorno — um corte mais grosso, ADMINISTRATIVO) com
-- o PORQUÊ clínico/comercial da consulta.
ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS visit_intent_tag VARCHAR(20);

DO $$ BEGIN
  ALTER TABLE core.appointments ADD CONSTRAINT appointments_visit_intent_tag_check
    CHECK (visit_intent_tag IS NULL OR visit_intent_tag IN ('rotina', 'retorno', 'avaliacao', 'urgencia'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN core.appointments.visit_intent_tag IS
  'Motivo estruturado do agendamento (rotina/retorno/avaliacao/urgencia) — alimenta segmentação de campanha de reengajamento. NULL = não informado (todo agendamento existente, e todo vindo de ingestão que não distingue isso hoje).';
