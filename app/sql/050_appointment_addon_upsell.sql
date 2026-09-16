-- app/sql/050_appointment_addon_upsell.sql
--
-- "Mapa de Dados Insighta" — Domínio Pós-atendimento (Onda 2), pilar
-- "Crescimento ativo/upsell" (3,0 → 8,5): hoje não sabíamos nem QUANTO
-- upsell já é oferecido informalmente pelo profissional/recepção no
-- checkout — só o que vira faturamento de fato. Sem o lado "oferecido"
-- do funil, nunca dá pra medir taxa de conversão, só o resultado final
-- (que já existe como receita, sem contexto).
--
-- DECISÃO — dois campos (o QUE foi oferecido + resultado), não um
-- booleano só
-------------------------------------------------------------------------
-- addon_offered_procedure (texto livre, "o que foi oferecido") separado
-- de addon_declined (o resultado) permite reconstruir o funil completo
-- (oferecido -> aceito/recusado) e ainda documentar QUAL procedimento
-- performa melhor de upsell — um booleano só nunca responderia isso.
ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS addon_offered_procedure TEXT;
ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS addon_declined BOOLEAN;

COMMENT ON COLUMN core.appointments.addon_offered_procedure IS
  '"Mapa de Dados Insighta" — o que foi oferecido a mais no checkout (texto livre). NULL = nada oferecido/não perguntado.';
COMMENT ON COLUMN core.appointments.addon_declined IS
  'Resultado da oferta: NULL = nada oferecido (a maioria); TRUE = oferecido e RECUSADO; FALSE = oferecido e ACEITO. Nunca inventa um resultado sem addon_offered_procedure preenchido.';
