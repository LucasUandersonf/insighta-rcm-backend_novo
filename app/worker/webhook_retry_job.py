"""
app/worker/webhook_retry_job.py

Processa a fila de retentativa de webhooks OUTBOUND (ver DECISÃO
completa em app/sql/028_webhook_delivery_queue.sql e
app/services/webhook_dispatch_service.py::process_due_retries). Mesma
estrutura dos demais jobs (script de execução única, itera tenants
ativos, termina).

Frequência recomendada: a cada 1-5 min (EventBridge Scheduler ou cron) —
a menor janela de backoff é de 1 minuto (_RETRY_BACKOFF_SECONDS[0]), então
rodar com MENOS frequência que isso atrasaria a primeira retentativa sem
necessidade; rodar com MUITO mais frequência só gasta ciclo de CPU
varrendo uma fila que provavelmente está vazia na maior parte do tempo.

Executar manualmente:  python -m app.worker.webhook_retry_job

DECISÃO — por tenant, não uma varredura cross-tenant
-------------------------------------------------------------------------
Diferente dos jobs de platform_* (que são bookkeeping da própria
Insighta, sem RLS), core.webhook_delivery_queue é dado NORMAL de cada
clínica — RLS de verdade, mesma convenção do resto do schema. Por isso
este job segue o MESMO padrão de daily_alert_job.py/weekly_report_job.py:
itera cada tenant ativo, abre uma sessão tenant-aware para ele, processa
só a fila daquele tenant.

DECISÃO — Sentry desde a primeira versão (mesmo padrão dos outros jobs)
-------------------------------------------------------------------------
Uma falha sistêmica aqui significa webhooks nunca mais sendo
reentregues, silenciosamente — pelo motivo oposto de sempre ser bom
detectar rápido.
"""
import asyncio
import logging

import sentry_sdk

from app.core.config import get_settings
from app.db.session import get_db_with_tenant
from app.models.tenant import Tenant
from app.repositories.webhook_delivery_queue_repository import WebhookDeliveryQueueRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.services.webhook_dispatch_service import process_due_retries
from app.worker.active_tenants import list_active_tenants

logger = logging.getLogger("webhook_retry_job")
settings = get_settings()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )


async def _process_tenant(tenant: Tenant) -> int:
    async for session in get_db_with_tenant(str(tenant.id)):
        processed = await process_due_retries(
            WebhookDeliveryQueueRepository(session),
            WebhookSubscriptionRepository(session),
        )
    # get_db_with_tenant já commita ao sair do bloco `session.begin()`
    # sem exceção — ver DECISÃO em app/db/session.py.
    return len(processed)


async def run() -> None:
    tenants = await list_active_tenants()
    total_processed = 0

    for tenant in tenants:
        try:
            count = await _process_tenant(tenant)
            if count:
                logger.info("Fila de webhooks: %d entrega(s) reprocessada(s) para tenant=%s (%s).", count, tenant.id, tenant.trade_name)
            total_processed += count
        except Exception as exc:
            # Mesmo princípio de isolamento de falha dos outros jobs: um
            # tenant com dado inesperado não pode travar os demais.
            if settings.SENTRY_DSN:
                sentry_sdk.set_tag("tenant_id", str(tenant.id))
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao processar fila de webhooks para tenant=%s", tenant.id)

    logger.info("Fila de webhooks: %d entrega(s) reprocessada(s) no total, %d tenant(s) ativo(s) verificados.", total_processed, len(tenants))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
