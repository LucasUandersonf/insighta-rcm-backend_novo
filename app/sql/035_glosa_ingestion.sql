-- app/sql/035_glosa_ingestion.sql
--
-- Terceiro Template de Integração: "Glosa" (demonstrativo de pagamento —
-- ver docstring de RawDenialRow em app/worker/schemas.py e DECISÃO em
-- app/sql/019_agenda_ingestion.sql sobre core.ingestion_files.data_type).
-- core.ingestion_files_data_type_check só aceitava ('faturamento',
-- 'agenda') — sem alargar, todo upload/worker com data_type='glosa'
-- falharia na gravação da landing zone com IntegrityError.
--
-- Mesmo padrão de 013_fix_plan_tier_check.sql: self-idempotente por
-- construção (DROP IF EXISTS + ADD), sem entrar em
-- _POST_UPGRADE_MARKER_TABLE — seguro rodar em todo deploy.
ALTER TABLE core.ingestion_files DROP CONSTRAINT IF EXISTS ingestion_files_data_type_check;
ALTER TABLE core.ingestion_files
    ADD CONSTRAINT ingestion_files_data_type_check CHECK (data_type IN ('faturamento', 'agenda', 'glosa'));
