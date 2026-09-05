"""
app/worker/daily_alert_job.py

Ponto de entrada do "Alerta diário de risco de falta" — irmão do
weekly_report_job.py, mesma estrutura (script de execução única,
processa todos os tenants ativos, termina), mas pensado para rodar com
frequência MUITO maior (ex: a cada 1-2h via EventBridge Scheduler), já
que o objetivo é pegar os agendamentos de risco alto DENTRO da janela
de 24h à frente enquanto ainda dá tempo de ligar para o paciente — ver
DECISÃO completa em app/services/report_send_service.py
(send_daily_risk_alert) sobre por que isso reaproveita o mecanismo de
envio do relatório semanal em vez de um tipo de mensagem novo.

Rodar semanalmente não faz sentido aqui (a janela de 24h andaria junto
com o relógio, não com o calendário) — por isso, diferente de
`_last_full_week()` no relatório semanal, este job não tem "período":
é sempre "agora" (`datetime.now(timezone.utc)`) na hora em que roda.

Executar manualmente:  python -m app.worker.daily_alert_job

DECISÃO — Sentry desde a primeira versão deste arquivo
-------------------------------------------------------------------------
Este job roda com frequência muito maior que o relatório semanal (a
cada 1-2h, não 1x/semana) — se ele quebrar de forma sistêmica (ex: a
mesma `WhatsAppClientError` de credencial expirada que afetaria o
relatório semanal), o SILÊNCIO se repetiria a cada ciclo, não uma vez
por semana, tornando ainda mais fácil não notar. Mesmo mecanismo
adotado em `weekly_report_job.py` na mesma rodada (ver DECISÃO lá para
o detalhe de por que isso faltava e o que corrige): `sentry_sdk.init()`
guardado por `SENTRY_DSN`, sem mudança de comportamento para quem ainda
não configurou Sentry.
"""
import asyncio
import logging
from datetime import datetime, timezone

import sentry_sdk

from app.core.config import get_settings
from app.db.session import get_db_with_tenant
from app.models.tenant import Tenant
from app.services.report_send_service import send_daily_risk_alert
from app.services.whatsapp_client import WhatsAppClient
from app.worker.active_tenants import list_active_tenants

logger = logging.getLogger("daily_alert_job")
settings = get_settings()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )


async def _process_tenant(tenant: Tenant, as_of: datetime, client: WhatsAppClient) -> None:
    async for session in get_db_with_tenant(str(tenant.id)):
        # client_factory devolve o MESMO client já construído — mesmo
        # raciocínio de weekly_report_job.py: falhar rápido UMA VEZ se
        # as credenciais da plataforma faltarem (ver run() abaixo), não
        # reconstruir/revalidar por tenant.
        result = await send_daily_risk_alert(session, tenant, as_of, lambda: client)

    if result.recipients_checked == 0:
        logger.info(
            "Nenhum destinatário de alerta de risco elegível para tenant=%s (%s) — pulando.", tenant.id, tenant.trade_name
        )
        return

    if result.high_risk_appointments_found == 0:
        logger.info(
            "Nenhum agendamento de risco alto nas próximas 24h para tenant=%s (%s) — nada a alertar.",
            tenant.id, tenant.trade_name,
        )
        return

    logger.info(
        "Alerta de risco processado: tenant=%s (%s) — %d agendamento(s) de risco alto, "
        "%d enviado(s), %d falha(s) de %d destinatário(s).",
        tenant.id, tenant.trade_name, result.high_risk_appointments_found, result.sent, result.failed, result.recipients_checked,
    )


async def run() -> None:
    as_of = datetime.now(timezone.utc)
    tenants = await list_active_tenants()
    logger.info("Alerta diário de risco: %d tenant(s) ativo(s), janela a partir de %s", len(tenants), as_of.isoformat())

    client = WhatsAppClient()  # levanta WhatsAppClientError cedo se credenciais não configuradas

    for tenant in tenants:
        try:
            await _process_tenant(tenant, as_of, client)
        except Exception as exc:
            # Mesmo princípio de isolamento de falha do weekly_report_job.py
            # (e do ingestion_worker.py): um tenant com dado inesperado não
            # pode travar o alerta dos demais. Reporte à Sentry é só
            # observabilidade — mesmo raciocínio de weekly_report_job.py.
            if settings.SENTRY_DSN:
                sentry_sdk.set_tag("tenant_id", str(tenant.id))
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao gerar alerta de risco para tenant=%s", tenant.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
