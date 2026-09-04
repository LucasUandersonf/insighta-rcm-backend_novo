-- =====================================================================
-- ARQUIVO: 019_agenda_ingestion.sql
-- Template de Integração "Agenda" — ver conversa/PLANO_ADEQUACAO_TISS.md.
--
-- CONTEXTO — pivô de estratégia de produto
-- ---------------------------------------------------------------------
-- Correção de rumo do usuário: "nosso sistema não é um ERP, é um
-- sistema que pega dados destes sistemas". Insighta não compete com
-- Moderna/Feegow/iClinic — é uma camada de RCM/BI que INGERE dados que
-- esses ERPs já produzem. A forma certa de "entrar dado" não é uma tela
-- de CRUD manual (Guia/Lote/Fatura das Fases 1-3 continuam válidas como
-- MODELO de dado, mas raramente serão criadas à mão pelo usuário), é o
-- cliente exportar um arquivo no formato que A INSIGHTA definir e subir
-- pela tela de ingestão — daí "templates prontos": um para Faturamento
-- (já existia, estendido em cima deste commit) e um para Agenda (novo,
-- esta migration).
--
-- Diferença fundamental entre os dois templates: uma linha de
-- Faturamento é sempre um atendimento JÁ OCORRIDO com valor cobrado —
-- vira Appointment(status='completed') + Billing, uma vez, no passado.
-- Uma linha de Agenda é um agendamento que MUDA DE ESTADO com o tempo
-- (agendado -> confirmado -> atendido/faltou) e é tipicamente
-- REEXPORTADO várias vezes conforme o status muda — não gera Billing
-- nenhum (cobrança só existe depois do atendimento), e precisa de uma
-- chave estável para UPSERT em vez de sempre criar um registro novo.
--
-- DECISÃO — external_id em core.appointments é a chave dessa chave estável
-- ---------------------------------------------------------------------
-- O "código do agendamento" do sistema de origem (ex: campo
-- "codigo_agendamento" do CSV). Sem isso, cada reimportação do mesmo
-- relatório de Agenda criaria um agendamento duplicado a cada execução.
-- NULLABLE (agendamento manual e todo agendamento vindo do template de
-- Faturamento não têm essa chave) e ÚNICO por tenant apenas quando
-- presente (índice parcial) — mesmo padrão de unicidade condicional já
-- usado por outras chaves de negócio deste projeto.
--
-- DECISÃO — data_type em core.ingestion_files, não uma tabela nova
-- ---------------------------------------------------------------------
-- Um arquivo de Agenda passa pelo MESMO pipeline de ingestão (claim_file
-- -> parse -> save_raw_rows -> normalize -> mark_processed) que um
-- arquivo de Faturamento — só o SCHEMA de colunas esperado e o que a
-- normalização faz com cada linha mudam. Uma coluna nova basta para
-- diferenciar; duplicar toda a infraestrutura de idempotência/histórico
-- numa segunda tabela seria repetir código sem necessidade.
-- =====================================================================

ALTER TABLE core.appointments ADD COLUMN IF NOT EXISTS external_id VARCHAR(100);

COMMENT ON COLUMN core.appointments.external_id IS
  'Código do agendamento no sistema de origem (Template de Integração "Agenda") — chave de upsert para reimportações do mesmo relatório. NULL para agendamento manual e para todo agendamento vindo do template de Faturamento.';

CREATE UNIQUE INDEX IF NOT EXISTS ix_appointments_tenant_external_id
    ON core.appointments (tenant_id, external_id)
    WHERE external_id IS NOT NULL;

ALTER TABLE core.ingestion_files ADD COLUMN IF NOT EXISTS data_type VARCHAR(20) NOT NULL DEFAULT 'faturamento';

COMMENT ON COLUMN core.ingestion_files.data_type IS
  'Template de integração que o arquivo segue: "faturamento" (padrão, retrocompatível) ou "agenda". Independente de file_format (csv/xml/json é o container; data_type é o esquema de colunas esperado dentro dele).';

ALTER TABLE core.ingestion_files DROP CONSTRAINT IF EXISTS ingestion_files_data_type_check;
ALTER TABLE core.ingestion_files
    ADD CONSTRAINT ingestion_files_data_type_check CHECK (data_type IN ('faturamento', 'agenda'));

-- Todas as ALTERs acima são idempotentes por construção (ADD COLUMN IF
-- NOT EXISTS + índice/constraint IF NOT EXISTS/DROP IF EXISTS) — mesmo
-- padrão de 013_fix_plan_tier_check.sql/014_insurance_is_active.sql:
-- roda em todo deploy via bootstrap_db.py, sem precisar de
-- _POST_UPGRADE_MARKER_TABLE.
