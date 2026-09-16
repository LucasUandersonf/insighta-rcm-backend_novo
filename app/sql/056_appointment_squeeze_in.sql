-- app/sql/056_appointment_squeeze_in.sql
--
-- Onda 5 do Plano de Ação, item 15 ("agenda avançada") — hoje não há
-- nenhum jeito de marcar que um agendamento foi um ENCAIXE (paciente
-- atendido fora da grade normal, espremido entre horários já
-- ocupados). Sem isso, a clínica não consegue responder "em quais dias
-- da semana a gente mais espreme a agenda" — sinal de sobrecarga
-- operacional que hoje só existe na memória de quem trabalha na
-- recepção.
--
-- Mesmo padrão booleano nullable de addon_declined
-- (050_appointment_addon_upsell.sql): NULL nunca é inventado como
-- "não foi encaixe" — é "não informado", categoria bem diferente de
-- FALSE (informado explicitamente que não foi encaixe).
ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS is_squeeze_in BOOLEAN;

COMMENT ON COLUMN core.appointments.is_squeeze_in IS
  'Onda 5 do Plano de Ação, item 15 — TRUE quando o agendamento foi um "encaixe" (fora da grade normal). NULL = não informado (todo agendamento antigo, e todo vindo de ingestão que não distingue isso hoje); FALSE = marcado explicitamente como NÃO encaixe.';
