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

DECISÃO — Sentry também aqui, não só na API e no ingestion_worker.py
-------------------------------------------------------------------------
BUG CORRIGIDO (achado na rodada de monitoramento/alertas): este script
nunca chamava `sentry_sdk.init()` — resultado prático, se o job inteiro
quebrasse antes do laço por tenant (ex: `WhatsAppClient()` levantando
`WhatsAppClientError` porque a credencial expirou, ou qualquer bug novo
introduzido aqui), o processo simplesmente morria com uma stack trace no
log do container, e NINGUÉM era avisado — silêncio indistinguível de
"rodou certo e não tinha nada para enviar". Mesmo padrão de
`app/worker/ingestion_worker.py`: com `SENTRY_DSN` configurada, o SDK
instala um hook global que captura qualquer exceção não tratada que
mate o processo, sem precisar de um `try/except` extra ao redor de
`run()` — e cada falha POR TENANT dentro do laço (linha ~/except abaixo)
também passa a ser reportada individualmente, não só logada.
"""
import asyncio
import logging
from datetime import date, timedelta

import sentry_sdk

from app.core.config import get_settings
from app.db.session import get_db_with_tenant
from app.models.tenant import Tenant
from app.services.report_send_service import send_report_to_recipients
from app.services.whatsapp_client import WhatsAppClient
from app.worker.active_tenants import list_active_tenants

logger = logging.getLogger("weekly_report_job")
settings = get_settings()

# Mesmo padrão opcional de app/main.py/ingestion_worker.py: sem
# SENTRY_DSN, nenhuma chamada de sentry_sdk.init() acontece e as
# chamadas sentry_sdk.* abaixo viram no-op — zero mudança de
# comportamento para quem ainda não configurou Sentry.
if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        # Mesma decisão de privacidade do resto do projeto: nunca dado de
        # paciente/beneficiário vazando para a Sentry.
        send_default_pii=False,
    )


def _last_full_week() -> tuple[date, date]:
    """Segunda a domingo da semana anterior à data de execução — evita
    reportar uma semana ainda em andamento (dado incompleto)."""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday, last_sunday


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
    tenants = await list_active_tenants()
    logger.info("Relatório semanal: %d tenant(s) ativo(s), período %s a %s", len(tenants), period_start, period_end)

    client = WhatsAppClient()  # levanta WhatsAppClientError cedo se credenciais não configuradas

    for tenant in tenants:
        try:
            await _process_tenant(tenant, period_start, period_end, client)
        except Exception as exc:
            # Um tenant com dado inesperado não pode travar o relatório
            # dos demais — mesmo princípio de isolamento de falha do
            # ingestion_worker.py (uma mensagem problemática não trava a fila).
            # O reporte à Sentry é só observabilidade, não muda esse
            # comportamento — só garante que alguém é avisado em vez de
            # só aparecer no log do container.
            if settings.SENTRY_DSN:
                sentry_sdk.set_tag("tenant_id", str(tenant.id))
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao gerar relatório para tenant=%s", tenant.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
