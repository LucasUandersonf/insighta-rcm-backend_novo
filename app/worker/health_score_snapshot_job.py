"""
app/worker/health_score_snapshot_job.py

Ponto de entrada do snapshot mensal da Nota de Saúde Financeira — mesmo
espírito de weekly_report_job.py (script de execução única, processa
todos os tenants ativos e termina; agendado por um cron EXTERNO, ver
DECISÃO em weekly_report_job.py sobre por que não há um loop "dorme até
o dia 1" dentro da aplicação).

Frequência recomendada: 1x/mês (dia 1, ou qualquer dia fixo — a chave de
upsert é por MÊS, não pelo dia exato em que o job roda, ver
app/sql/034_health_score_snapshots.sql). Rodar mais de uma vez no mesmo
mês é seguro (idempotente, sobrescreve a mesma linha).

Executar manualmente:  python -m app.worker.health_score_snapshot_job

DECISÃO — Sentry desde a primeira versão (mesmo padrão dos outros jobs)
-------------------------------------------------------------------------
Uma falha sistêmica aqui é silenciosa por natureza: ninguém "sente falta"
de um snapshot que não foi salvo até 3 meses depois, quando a tendência
do anel de saúde simplesmente não aparece. Mesmo raciocínio de
weekly_report_job.py — sem alguém observando, o processo morreria sem
aviso.
"""
import asyncio
import logging
from datetime import date

import sentry_sdk

from app.core.config import get_settings
from app.db.session import get_db_with_tenant
from app.models.tenant import Tenant
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.health_score_snapshot_repository import HealthScoreSnapshotRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.analytics_service import AnalyticsService
from app.worker.active_tenants import list_active_tenants

logger = logging.getLogger("health_score_snapshot_job")
settings = get_settings()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )


def _first_day_of_current_month() -> date:
    return date.today().replace(day=1)


async def _process_tenant(tenant: Tenant, snapshot_month: date) -> None:
    async for session in get_db_with_tenant(str(tenant.id)):
        service = AnalyticsService(
            AnalyticsRepository(session),
            ReportingRepository(session),
            ProfessionalRepository(session),
            ProfessionalAvailabilityRepository(session),
            CapacityRepository(session),
            DenialAppealRepository(session),
            TenantRepository(session),
            HealthScoreSnapshotRepository(session),
        )
        health_score = await service.get_health_score()
        await HealthScoreSnapshotRepository(session).upsert_snapshot(
            tenant.id, snapshot_month=snapshot_month, score=health_score.score
        )

    logger.info(
        "Snapshot de saúde gravado: tenant=%s (%s) — score=%s, mês=%s",
        tenant.id, tenant.trade_name, health_score.score, snapshot_month,
    )


async def run() -> None:
    snapshot_month = _first_day_of_current_month()
    tenants = await list_active_tenants()
    logger.info("Snapshot mensal de saúde: %d tenant(s) ativo(s), mês %s", len(tenants), snapshot_month)

    for tenant in tenants:
        try:
            await _process_tenant(tenant, snapshot_month)
        except Exception as exc:
            # Mesmo princípio de isolamento de falha do weekly_report_job.py
            # — um tenant com dado inesperado não pode travar o snapshot
            # dos demais.
            if settings.SENTRY_DSN:
                sentry_sdk.set_tag("tenant_id", str(tenant.id))
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao gravar snapshot de saúde para tenant=%s", tenant.id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
