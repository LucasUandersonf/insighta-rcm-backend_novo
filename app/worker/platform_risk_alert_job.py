"""
app/worker/platform_risk_alert_job.py

Ponto de entrada dos alertas proativos de Customer Success — irmão de
weekly_report_job.py/daily_alert_job.py (script de execução única,
processa e termina), mas diferente dos dois em um ponto central: aqueles
avisam UM TENANT sobre o próprio negócio; este avisa a PRÓPRIA EQUIPE
Insighta sobre o conjunto de clínicas (ver DECISÃO completa em
app/services/platform_alert_service.py e app/sql/027_platform_risk_alerts.sql).

Frequência recomendada: 1x/dia (EventBridge Scheduler ou cron) — "risco"
é definido por 30 dias sem atividade (PlatformReportingService), então
não há ganho em rodar com mais frequência que isso; diferente do alerta
de falta (daily_alert_job.py), aqui não existe uma janela de horas que
justifique rodar de hora em hora.

Executar manualmente:  python -m app.worker.platform_risk_alert_job

DECISÃO — Sentry desde a primeira versão deste arquivo (mesmo padrão dos
outros dois jobs, ver DECISÃO em daily_alert_job.py): uma falha
sistêmica aqui significa a equipe Insighta parar de ser avisada de
clientes em risco, silenciosamente — o pior cenário possível para esta
funcionalidade específica.
"""
import asyncio
import logging

import sentry_sdk

from app.core.config import get_settings
from app.db.session import get_db_no_tenant
from app.repositories.platform_reporting_repository import PlatformReportingRepository
from app.repositories.platform_risk_alert_repository import PlatformRiskAlertRepository
from app.services.platform_alert_service import PlatformAlertService
from app.services.platform_reporting_service import PlatformReportingService

logger = logging.getLogger("platform_risk_alert_job")
settings = get_settings()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )


async def run() -> None:
    async for session in get_db_no_tenant():
        service = PlatformAlertService(
            PlatformReportingService(PlatformReportingRepository(session)),
            PlatformRiskAlertRepository(session),
        )
        try:
            result = await service.check_and_send_risk_alerts()
            await session.commit()
        except Exception as exc:
            if settings.SENTRY_DSN:
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao rodar alertas proativos de Customer Success.")
            return

        logger.info(
            "Alertas de Customer Success: %d novo(s), %d lembrete(s), %d recuperada(s).",
            len(result.new_alerts), len(result.reminders_sent), len(result.recovered),
        )
        if result.new_alerts:
            logger.info("Entraram em risco agora: %s", ", ".join(result.new_alerts))
        if result.reminders_sent:
            logger.info("Lembrete reenviado (ainda em risco): %s", ", ".join(result.reminders_sent))
        if result.recovered:
            logger.info("Saíram do risco: %s", ", ".join(result.recovered))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
