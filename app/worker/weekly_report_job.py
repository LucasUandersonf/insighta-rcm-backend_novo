"""
app/worker/weekly_report_job.py

Ponto de entrada da Etapa 4. Diferente do ingestion_worker.py (loop
contínuo de polling em SQS), este é um SCRIPT DE EXECUÇÃO ÚNICA: roda,
processa todos os tenants ativos, e termina. Rodar semanalmente, disparado
por um agendador EXTERNO (cron do host, ou AWS EventBridge Scheduler
invocando este processo/container) — não implementamos um loop
"dorme até sexta-feira" dentro da aplicação, porque isso manteria um
container ligado 24/7 só para acordar uma vez por semana, sem necessidade.
Provisionamento do agendador é infraestrutura (Terraform), fora deste repo,
mesmo tratamento dado ao bucket S3 e à fila SQS da Etapa 1.

Executar manualmente:  python -m app.worker.weekly_report_job
"""
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.db.session import get_db_no_tenant, get_db_with_tenant
from app.models.tenant import Tenant
from app.services.report_send_service import send_report_to_recipients
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger("weekly_report_job")


def _last_full_week() -> tuple[date, date]:
    """Segunda a domingo da semana anterior à data de execução — evita
    reportar uma semana ainda em andamento (dado incompleto)."""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday, last_sunday


async def _active_tenants() -> list[Tenant]:
    """Antes filtrava por `Tenant.whatsapp_group_id.is_not(None))` — o
    destino único de outrora. Agora o destino é dado por
    `core.report_recipients` (N por tenant, ver DECISÃO em
    app/sql/009_report_recipients.sql), então a elegibilidade não é mais
    decidida aqui: qualquer tenant ativo é candidato, e
    `_process_tenant` simplesmente não envia nada (e não é contado como
    falha) se não houver nenhum destinatário elegível para
    `report_send_service.REPORT_TYPE`."""
    async for session in get_db_no_tenant():
        result = await session.execute(select(Tenant).where(Tenant.is_active.is_(True)))
        return list(result.scalars().all())
    return []  # pragma: no cover


async def _process_tenant(tenant: Tenant, period_start: date, period_end: date, client: WhatsAppClient) -> None:
    async for session in get_db_with_tenant(str(tenant.id)):
        # client_factory devolve o MESMO client já construído (ver
        # DECISÃO em send_report_to_recipients) — o cron quer falhar
        # rápido UMA VEZ se as credenciais da plataforma faltarem (ver
        # run() abaixo), não reconstruir/revalidar por tenant.
        result = await send_report_to_recipients(session, tenant, period_start, period_end, lambda: client)

    if result.recipients_checked == 0:
        logger.info("Nenhum destinatário de WhatsApp elegível para tenant=%s (%s) — pulando.", tenant.id, tenant.trade_name)
        return

    logger.info(
        "Relatório semanal processado: tenant=%s (%s) — %d enviado(s), %d falha(s) de %d destinatário(s).",
        tenant.id, tenant.trade_name, result.sent, result.failed, result.recipients_checked,
    )


async def run() -> None:
    period_start, period_end = _last_full_week()
    tenants = await _active_tenants()
    logger.info("Relatório semanal: %d tenant(s) ativo(s), período %s a %s", len(tenants), period_start, period_end)

    client = WhatsAppClient()  # levanta WhatsAppClientError cedo se credenciais não configuradas

    for tenant in tenants:
        try:
            await _process_tenant(tenant, period_start, period_end, client)
        except Exception:
            # Um tenant com dado inesperado não pode travar o relatório
            # dos demais — mesmo princípio de isolamento de falha do
            # ingestion_worker.py (uma mensagem problemática não trava a fila).
            logger.exception("Falha inesperada ao gerar relatório para tenant=%s", tenant.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
