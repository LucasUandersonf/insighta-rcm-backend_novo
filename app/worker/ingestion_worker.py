"""
app/worker/ingestion_worker.py

Ponto de entrada do worker de ingestão (Etapa 1 do pipeline). Roda como
um PROCESSO SEPARADO da API (container próprio no deploy, mesmo
codebase — "monolito modular" aplicado também ao worker, não só à API).
Iniciar com:  python -m app.worker.ingestion_worker

FLUXO POR MENSAGEM SQS
-------------------------------------------------------------------------
1. Recebe evento de criação de objeto S3 (via SQS, populado por S3 Event
   Notification configurado na infra/Terraform, fora do escopo deste
   arquivo).
2. Resolve tenant_id + formato a partir da chave (s3_key_resolver.py) —
   ainda não confiável.
3. Valida o tenant contra core.tenants usando uma sessão SEM contexto de
   tenant (get_db_no_tenant) — a tabela tenants não tem RLS, então isso é
   seguro e é o único jeito de validar o tenant ANTES de termos um
   tenant_id em que confiar para abrir a sessão "de verdade".
4. Baixa o objeto do S3.
5. Abre uma sessão TENANT-AWARE (get_db_with_tenant — a MESMA função
   usada pela API, reforçando que "SET LOCAL app.current_tenant" é uma
   abstração reutilizável, não algo amarrado ao ciclo de vida de uma
   requisição HTTP) e delega para
   app.services.ingestion_processing_service.process_uploaded_file, que
   faz claim_file() -> parse -> save_raw_rows() ->
   NormalizationService.normalize_rows() -> mark_processed() dentro
   dela — a MESMA função usada por POST /ingestion/upload (novo caminho
   HTTP síncrono, ver app/api/v1/endpoints/ingestion.py), para nenhuma
   lógica de parsing/normalização existir duplicada entre os dois
   caminhos de entrada.
6. Deleta a mensagem da fila SOMENTE após commit bem-sucedido. Qualquer
   exceção não tratada faz a mensagem voltar a ficar visível na fila após
   o visibility timeout — SQS tenta de novo automaticamente; depois de N
   tentativas (configurado na redrive policy da fila, na infra), a
   mensagem cai numa Dead Letter Queue para investigação manual, sem
   travar o processamento dos outros arquivos.
"""
import asyncio
import json
import logging
import signal
import uuid

import aioboto3
import sentry_sdk
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_db_no_tenant, get_db_with_tenant
from app.models.tenant import Tenant
from app.services.ingestion_processing_service import process_uploaded_file
from app.worker.s3_key_resolver import InvalidIngestionKeyError, resolve

logger = logging.getLogger("ingestion_worker")

settings = get_settings()

# Mesmo padrão opcional de app/main.py: sem SENTRY_DSN, nenhuma chamada de
# sentry_sdk.init() acontece e o resto das chamadas sentry_sdk.* abaixo
# vira no-op. Diferente da API (um processo por request, com handler de
# exceção central), o worker é um PROCESSO LONGO RODANDO SOZINHO — se ele
# morrer de uma exceção não prevista fora do try/except do loop principal
# (ex: falha ao conectar no SQS em run()), hoje isso só aparece no log do
# container; com Sentry configurado, também vira um alerta.
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        # Mesma decisão de privacidade de app/main.py: nunca dado de
        # paciente/beneficiário (aqui, inclusive, o CONTEÚDO dos arquivos
        # de lote que o worker processa) vazando para a Sentry.
        send_default_pii=False,
    )

# Configuração de polling. wait_time_seconds=20 usa long polling do SQS
# (reduz custo e latência comparado a polling curto repetido).
_QUEUE_URL = settings.SQS_INGESTION_QUEUE_URL
_WAIT_TIME_SECONDS = 20
_MAX_MESSAGES_PER_POLL = 10
_VISIBILITY_TIMEOUT_SECONDS = 120  # deve ser maior que o tempo esperado de processamento de um arquivo


async def _validate_active_tenant(tenant_id: uuid.UUID) -> bool:
    async for session in get_db_no_tenant():
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
        return result.scalar_one_or_none() is not None
    return False  # pragma: no cover — get_db_no_tenant sempre gera pelo menos um yield


async def _process_s3_object(*, bucket: str, key: str, version_id: str | None) -> None:
    try:
        resolved = resolve(key)
    except InvalidIngestionKeyError as exc:
        # Chave fora do padrão: não sabemos nem de qual tenant é. Logamos
        # e descartamos — não há "para onde" escalar isso com segurança.
        logger.warning("Ignorando objeto S3 com chave inválida: %s", exc)
        return

    if not await _validate_active_tenant(resolved.tenant_id):
        logger.warning("tenant_id %s da chave S3 não existe ou está inativo: %s", resolved.tenant_id, key)
        return

    session_config = aioboto3.Session()
    async with session_config.client("s3") as s3_client:
        obj = await s3_client.get_object(Bucket=bucket, Key=key, **({"VersionId": version_id} if version_id else {}))
        raw_bytes = await obj["Body"].read()

    async for tenant_session in get_db_with_tenant(str(resolved.tenant_id)):
        result = await process_uploaded_file(
            tenant_session,
            resolved.tenant_id,
            s3_bucket=bucket,
            s3_key=key,
            s3_version_id=version_id,
            file_format=resolved.file_format,
            raw_bytes=raw_bytes,
            data_type=resolved.data_type,
        )

        if result.already_claimed:
            # Já reclamado por outra execução/réplica -> idempotência em
            # ação. Não é erro, é o caminho feliz de uma entrega duplicada.
            logger.info("Arquivo já processado anteriormente, ignorando: %s", key)
            return

        logger.info(
            "Arquivo processado: %s (tenant=%s, linhas=%d, erro_estrutural=%d, normalizadas=%d, rejeitadas_na_normalizacao=%d)",
            key,
            resolved.tenant_id,
            result.ingestion_file.row_count,
            result.structural_error_count,
            result.normalized_count,
            result.rejected_count,
        )

async def _handle_message(body: str) -> None:
    """
    Parseia o corpo da mensagem SQS (formato de S3 Event Notification) e
    processa cada registro. Um evento S3 pode conter múltiplos "Records"
    em lote; iteramos por segurança, embora na prática 1 evento = 1 objeto.
    """
    event = json.loads(body)
    for record in event.get("Records", []):
        s3_info = record.get("s3", {})
        bucket = s3_info.get("bucket", {}).get("name")
        key = s3_info.get("object", {}).get("key")
        version_id = s3_info.get("object", {}).get("versionId")
        if not bucket or not key:
            logger.warning("Mensagem SQS sem bucket/key reconhecíveis, ignorando: %s", body[:200])
            continue
        await _process_s3_object(bucket=bucket, key=key, version_id=version_id)


async def run() -> None:
    if not _QUEUE_URL:
        raise RuntimeError("SQS_INGESTION_QUEUE_URL não configurada — ver app/core/config.py")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    session_config = aioboto3.Session()
    async with session_config.client("sqs") as sqs_client:
        logger.info("Worker de ingestão iniciado, aguardando mensagens em %s", _QUEUE_URL)
        while not stop_event.is_set():
            response = await sqs_client.receive_message(
                QueueUrl=_QUEUE_URL,
                MaxNumberOfMessages=_MAX_MESSAGES_PER_POLL,
                WaitTimeSeconds=_WAIT_TIME_SECONDS,
                VisibilityTimeout=_VISIBILITY_TIMEOUT_SECONDS,
            )
            for message in response.get("Messages", []):
                try:
                    await _handle_message(message["Body"])
                except Exception as exc:
                    # Não deletamos a mensagem: ela volta a ficar visível
                    # após VisibilityTimeout e o SQS tenta de novo. Não
                    # relançamos para não matar o loop inteiro por causa
                    # de um arquivo problemático.
                    #
                    # O reporte ao Sentry abaixo é SÓ observabilidade — não
                    # muda em nada o comportamento de retry/DLQ acima: a
                    # mensagem já ia voltar a ficar visível de qualquer
                    # jeito, o SQS já ia tentar de novo de qualquer jeito.
                    # A diferença é só que agora alguém é avisado, em vez
                    # de descobrir só quando a mensagem cair na DLQ (ou
                    # nem isso, se ficar reprocessando com sucesso
                    # eventualmente e ninguém nunca olhar o log).
                    if settings.SENTRY_DSN:
                        sentry_sdk.capture_exception(exc)
                    logger.exception("Falha ao processar mensagem SQS, será reprocessada pelo SQS")
                else:
                    await sqs_client.delete_message(QueueUrl=_QUEUE_URL, ReceiptHandle=message["ReceiptHandle"])

    logger.info("Worker de ingestão encerrado.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
