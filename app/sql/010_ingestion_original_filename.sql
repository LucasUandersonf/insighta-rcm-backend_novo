-- =====================================================================
-- ARQUIVO: 010_ingestion_original_filename.sql
-- Nome de exibição do arquivo enviado via POST /ingestion/upload (novo
-- caminho HTTP síncrono, ver app/api/v1/endpoints/ingestion.py) — o
-- caminho SQS não tinha esse conceito (a própria s3_key já É o nome do
-- arquivo do jeito que o SFTP depositou), mas o usuário que sobe pela
-- tela do produto espera ver o nome ORIGINAL que ele escolheu no
-- computador dele na tela de histórico (GET /ingestion/files), não a
-- chave S3 tenant-scoped construída por baixo dos panos.
--
-- BUG LATENTE FECHADO AQUI DE PASSAGEM — idempotência não pegava
-- quando s3_version_id é NULL (bucket não versionado)
-- ---------------------------------------------------------------------
-- A UNIQUE constraint original de 003_ingestion_tables.sql —
-- UNIQUE (tenant_id, s3_bucket, s3_key, s3_version_id) — nunca dispara
-- quando s3_version_id é NULL para as duas linhas: é semântica padrão do
-- Postgres tratar NULL como "diferente de qualquer coisa, inclusive de
-- outro NULL" em constraints UNIQUE. Isso ficou invisível até agora
-- porque o único chamador de claim_file() (o worker SQS) só reprocessa
-- o MESMO evento S3 com o MESMO version_id de um bucket versionado —
-- nunca exercitou o caso version_id=NULL de verdade. POST
-- /ingestion/upload expõe esse caso na prática: um bucket de ingestão
-- não-versionado (comum, já que versionamento é opcional aqui) sempre
-- grava com s3_version_id=NULL, e reenviar o MESMO arquivo (mesma
-- chave, calculada deterministicamente a partir de tenant+formato+nome
-- em app/services/ingestion_storage_client.py) precisa ser detectado
-- como duplicata — sem este índice parcial, ON CONFLICT nunca dispararia
-- e cada reenvio criaria uma IngestionFile nova, reprocessando o arquivo
-- inteiro de novo (paciente/appointment/billing duplicados). Um índice
-- ÚNICO PARCIAL cobrindo só as linhas WHERE s3_version_id IS NULL resolve
-- isso sem tocar na constraint original (que continua servindo o caso
-- versionado) — ver app/repositories/ingestion_repository.py
-- (claim_file escolhe o alvo de ON CONFLICT certo conforme
-- s3_version_id é ou não None).
--
-- DECISÃO — ADD COLUMN IF NOT EXISTS direto, SEM marcador em
-- bootstrap_db.py._POST_UPGRADE_MARKER_TABLE
-- ---------------------------------------------------------------------
-- Todo outro arquivo em _POST_UPGRADE_SQL_FILES (006-009) precisa de um
-- marcador de tabela em bootstrap_db.py porque contém CREATE TABLE/
-- POLICY sem IF NOT EXISTS — sem o marcador, rodar o arquivo de novo em
-- todo deploy falharia (ou pior, silenciosamente recriaria uma policy).
-- Este arquivo não tem esse problema: `ADD COLUMN IF NOT EXISTS` já é
-- idempotente por natureza — mesma razão pela qual
-- 005_performance_indexes.sql (CREATE INDEX IF NOT EXISTS) TAMBÉM fica
-- DE FORA do mapa de marcadores em bootstrap_db.py. Generalizar o helper
-- de idempotência para checar information_schema.columns só para este
-- único arquivo adicionaria um segundo tipo de checagem para resolver um
-- problema que o próprio Postgres já resolve com uma cláusula padrão —
-- mais superfície de código sem ganho real. Basta adicionar este arquivo
-- a `_POST_UPGRADE_SQL_FILES` e deixá-lo de fora de
-- `_POST_UPGRADE_MARKER_TABLE`, exatamente como já é feito para 005.
-- =====================================================================

ALTER TABLE core.ingestion_files
    ADD COLUMN IF NOT EXISTS original_filename VARCHAR(500);

-- CREATE UNIQUE INDEX ... IF NOT EXISTS também já é idempotente por
-- natureza (mesmo raciocínio de ADD COLUMN IF NOT EXISTS acima e de
-- 005_performance_indexes.sql) — nenhum marcador extra necessário.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_files_idempotency_null_version
    ON core.ingestion_files (tenant_id, s3_bucket, s3_key)
    WHERE s3_version_id IS NULL;
