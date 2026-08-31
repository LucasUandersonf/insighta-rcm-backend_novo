-- =====================================================================
-- ARQUIVO: 003_ingestion_tables.sql
-- ETAPA 1 do pipeline: Ingestão de Dados (SFTP -> S3 -> fila -> worker)
--
-- DECISÃO ARQUITETURAL #1 — S3 Event Notification -> SQS -> worker que
-- faz polling, em vez de AWS Lambda
-- ---------------------------------------------------------------------
-- Lambda seria a escolha "serverless óbvia" para reagir a um evento de
-- S3. Optamos por um worker Python de longa duração consumindo uma fila
-- SQS pelos seguintes motivos, coerentes com a decisão de "monolito
-- modular" do projeto:
--   1) O worker reusa DIRETAMENTE o mesmo código de app/db/session.py,
--      app/models/ e app/repositories/ que a API usa — nada de duplicar
--      lógica de conexão/RLS em runtime Lambda separado com cold start.
--   2) SQS já nos dá, de graça: at-least-once delivery, retry automático
--      via visibility timeout, e uma Dead Letter Queue (DLQ) para
--      arquivos que falham repetidamente — sem reimplementar isso.
--   3) Continua sendo "consumo assíncrono" (requisito do briefing): o
--      worker roda em paralelo à API, como um processo/container
--      separado do mesmo deploy, e escala adicionando réplicas que
--      competem pela mesma fila.
-- Fluxo: SFTP deposita em S3 -> S3 Event Notification publica em SQS ->
-- worker (app/worker/ingestion_worker.py) faz long-polling na fila.
--
-- DECISÃO #2 — Convenção de chave S3 para resolver o tenant
-- ---------------------------------------------------------------------
-- tenants/{tenant_id}/incoming/{csv|xml|json}/{arquivo}
-- O tenant_id embutido no PATH (não no conteúdo do arquivo) é o que
-- permite ao worker chamar core.current_tenant_id()-equivalente (aqui,
-- get_db_with_tenant(tenant_id) em Python) ANTES de tocar em qualquer
-- tabela protegida por RLS. Tratamos esse tenant_id como não confiável
-- até validar contra core.tenants (ver ingestion_repository.py) — um
-- prefixo de S3 malformado ou adulterado nunca deve conseguir gravar
-- dado em um tenant que não existe ou está inativo.
--
-- DECISÃO #3 — Landing zone (ingestion_raw_rows) em vez de já gravar
-- direto em patients/appointments/billing
-- ---------------------------------------------------------------------
-- Etapa 1 (ingestão) e Etapa 2 (normalização/limpeza) são conceitualmente
-- separadas no briefing do produto. Fazemos o worker desta etapa parar
-- em uma "landing zone" (linha crua em JSONB, com status
-- pending_normalization) em vez de já tentar criar Patient/Appointment/
-- Billing finais. Isso porque dado de sistema legado de clínica costuma
-- vir sujo (convênio escrito de 5 formas diferentes, CPF com máscara
-- inconsistente) — misturar "parsing do arquivo" com "resolução de
-- entidades de negócio" no mesmo worker tornaria os dois muito mais
-- difíceis de testar e de reprocessar isoladamente caso a regra de
-- normalização mude no futuro (bastaria reprocessar ingestion_raw_rows,
-- sem precisar re-baixar e reparsear os arquivos do S3).
-- =====================================================================

SET search_path TO core, public;

-- Segredo do webhook do Meta Ads é por tenant (cada clínica conecta sua
-- própria conta de anúncios na tela de Setup do briefing). Em produção,
-- esta coluna deve ser criptografada em repouso via pgcrypto ou, melhor
-- ainda, armazenada fora do Postgres em um Secrets Manager e referenciada
-- aqui só por um ID/ARN — mantido como VARCHAR simples neste MVP para
-- não acoplar a stack de secrets ainda.
ALTER TABLE core.tenants
    ADD COLUMN IF NOT EXISTS meta_ads_webhook_secret VARCHAR(255);


-- =====================================================================
-- TABELA: ingestion_files
-- Rastreia cada arquivo recebido: garante idempotência (o mesmo arquivo
-- do S3 não é processado duas vezes, mesmo com a entrega at-least-once
-- do SQS) e dá observabilidade (quantas linhas vieram, quantas falharam).
-- =====================================================================
CREATE TABLE IF NOT EXISTS core.ingestion_files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    s3_bucket       VARCHAR(255) NOT NULL,
    s3_key          VARCHAR(1024) NOT NULL,
    s3_version_id   VARCHAR(255),              -- versão do objeto S3, se bucket versionado
    file_format     VARCHAR(10) NOT NULL CHECK (file_format IN ('csv','xml','json')),
    status          VARCHAR(20) NOT NULL DEFAULT 'received'
                        CHECK (status IN ('received','processing','processed','failed')),
    row_count       INTEGER DEFAULT 0,
    error_row_count INTEGER DEFAULT 0,
    error_message   TEXT,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ,
    -- Chave de idempotência: mesmo (tenant, bucket, key, version) nunca é
    -- reclamado duas vezes por dois workers concorrentes (ver
    -- ingestion_repository.claim_file, que faz INSERT ... ON CONFLICT).
    UNIQUE (tenant_id, s3_bucket, s3_key, s3_version_id)
);

-- =====================================================================
-- TABELA: ingestion_raw_rows (landing zone / staging)
-- Uma linha por registro do arquivo original, ainda "crua". A Etapa 2
-- (normalização, não implementada neste worker) consome
-- status='pending_normalization' e promove para patients/appointments/
-- billing de fato.
-- =====================================================================
CREATE TABLE IF NOT EXISTS core.ingestion_raw_rows (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    ingestion_file_id    UUID NOT NULL REFERENCES core.ingestion_files(id) ON DELETE CASCADE,
    row_number          INTEGER NOT NULL,       -- posição no arquivo original, útil para debug com o cliente
    payload             JSONB NOT NULL,          -- linha já normalizada para o schema canônico (RawBillingRow)
    validation_errors   JSONB,                   -- preenchido quando a linha falha a validação estrutural
    status               VARCHAR(30) NOT NULL DEFAULT 'pending_normalization'
                            CHECK (status IN ('pending_normalization','normalized','rejected')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_raw_rows_pending
    ON core.ingestion_raw_rows (tenant_id, status)
    WHERE status = 'pending_normalization';


-- =====================================================================
-- TABELA: marketing_webhook_events
-- Dedupe de eventos do webhook do Meta Ads (a Meta pode reenviar o mesmo
-- evento em caso de timeout na resposta). Guardamos o event_id que a
-- própria Meta envia; um segundo POST com o mesmo event_id é descartado
-- sem reprocessar.
-- =====================================================================
CREATE TABLE IF NOT EXISTS core.marketing_webhook_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    source          VARCHAR(30) NOT NULL DEFAULT 'meta_ads',
    external_event_id  VARCHAR(255) NOT NULL,
    payload         JSONB NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source, external_event_id)
);


-- =====================================================================
-- RLS — mesmo padrão de 001_init_schema.sql: FORCE + policy baseada em
-- core.current_tenant_id(). O worker chama get_db_with_tenant(tenant_id)
-- (mesma função já usada pela API) antes de tocar nestas tabelas, então
-- a policy funciona idêntica para tráfego vindo da API ou do worker.
-- =====================================================================
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT unnest(ARRAY['ingestion_files','ingestion_raw_rows','marketing_webhook_events'])
    LOOP
        EXECUTE format('ALTER TABLE core.%I ENABLE ROW LEVEL SECURITY;', t);
        EXECUTE format('ALTER TABLE core.%I FORCE ROW LEVEL SECURITY;', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation_%1$I ON core.%1$I
            USING (tenant_id = core.current_tenant_id())
            WITH CHECK (tenant_id = core.current_tenant_id());
        $f$, t);
    END LOOP;
END $$;
