"""
app/worker/insight_outcome_reevaluation_job.py

Épico F1.2 do Plano Diretor Insighta ("Ciclo fechado de insight: ação
-> resultado") — "Job periódico que reavalia a métrica-fonte de
insights marcados 'resolvido' N dias depois e registra o delta
realizado." Mesma estrutura de health_score_snapshot_job.py (script de
execução única, processa todos os tenants ativos, termina; agendado
por um cron EXTERNO).

DECISÃO — "a métrica ainda aparece?" em vez de recalcular uma fórmula
por tipo de insight
-------------------------------------------------------------------------
generate_insights() não expõe uma função "recompute só este insight" —
cada um dos 39 mecanismos tem sua própria lógica, e escrever um
dispatcher por `insight_key` seria reimplementar boa parte do motor.
Em vez disso, a reavaliação recomputa a fila de hoje (mesmo cálculo de
AnalyticsService.get_priority_queue, sem o Comparativo cross-tenant —
não vale o custo de sessão extra pra um job em lote) e procura um item
com o MESMO insight_key (mesma função slugify(categoria:título) usada
na criação — ver DECISÃO em insight_outcome_service.py). Se o mesmo
problema ainda aparece, `resolved_metric_value` é o impacto financeiro
ATUAL dele; se sumiu (ou ficou abaixo do piso de materialidade do
motor), `resolved_metric_value = 0.0` — nunca None (None significaria
"não reavaliado ainda", que deixaria de ser verdade).

Executar manualmente:  python -m app.worker.insight_outcome_reevaluation_job
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import sentry_sdk

from app.core.config import get_settings
from app.core.text_utils import slugify
from app.db.session import get_db_with_tenant
from app.models.tenant import Tenant
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.contract_repository import ContractRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.health_score_snapshot_repository import HealthScoreSnapshotRepository
from app.repositories.insight_outcome_repository import InsightOutcomeRepository
from app.repositories.lote_repository import LoteRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.services.analytics_service import AnalyticsService
from app.worker.active_tenants import list_active_tenants

logger = logging.getLogger("insight_outcome_reevaluation_job")
settings = get_settings()

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        send_default_pii=False,
    )

# "N dias depois" que o épico F1.2 pede — 14 dias dá tempo real de uma
# ação (ex: renegociar contrato, treinar recepção) produzir efeito
# mensurável no período seguinte, sem esperar tanto que ninguém mais
# lembra por que o insight foi marcado resolvido.
_REEVALUATION_DELAY_DAYS = 14
# Mesma janela "semana padrão" do resto do produto (ver _default_period
# em analytics.py) — a fila recomputada usa o MESMO recorte de tempo
# que o gestor veria abrindo a aba "Hoje" agora.
_REEVALUATION_WINDOW_DAYS = 7


async def _process_tenant(tenant: Tenant, as_of: datetime) -> int:
    reevaluated_count = 0
    async for session in get_db_with_tenant(str(tenant.id)):
        outcome_repo = InsightOutcomeRepository(session)
        pending = await outcome_repo.list_pending_reevaluation(as_of=as_of, min_days_since_resolved=_REEVALUATION_DELAY_DAYS)
        if not pending:
            return 0

        analytics_service = AnalyticsService(
            AnalyticsRepository(session),
            ReportingRepository(session),
            ProfessionalRepository(session),
            ProfessionalAvailabilityRepository(session),
            CapacityRepository(session),
            DenialAppealRepository(session),
            TenantRepository(session),
            HealthScoreSnapshotRepository(session),
            LoteRepository(session),
            ContractRepository(session),
        )
        window_end = date.today()
        window_start = window_end - timedelta(days=_REEVALUATION_WINDOW_DAYS - 1)
        # Sem network_benchmark aqui de propósito: exigiria uma segunda
        # sessão SEM tenant por tenant processado, custo que não se
        # justifica pra um job em lote — só os itens do Comparativo
        # ficam permanentemente sem reavaliação automática (raro:
        # generate_insights cobre a maior parte, o Comparativo é UM
        # candidato entre muitos).
        current_queue = await analytics_service.get_priority_queue(
            window_start, window_end, tenant_id=str(tenant.id), network_benchmark=None, limit=1000
        )
        current_by_key = {
            slugify(f"{item.category}:{item.title}"): item.financial_impact for item in current_queue.items
        }

        for outcome in pending:
            current_impact = current_by_key.get(outcome.insight_key)
            outcome.resolved_metric_value = current_impact if current_impact is not None else 0.0
            outcome.reevaluated_at = as_of
            await outcome_repo.save(outcome)
            reevaluated_count += 1

    return reevaluated_count


async def run() -> None:
    as_of = datetime.now(timezone.utc)
    tenants = await list_active_tenants()
    logger.info("Reavaliação de ciclo fechado de insight: %d tenant(s) ativo(s)", len(tenants))

    total_reevaluated = 0
    for tenant in tenants:
        try:
            count = await _process_tenant(tenant, as_of)
            total_reevaluated += count
            if count:
                logger.info("tenant=%s (%s): %d outcome(s) reavaliado(s)", tenant.id, tenant.trade_name, count)
        except Exception as exc:
            # Mesmo princípio de isolamento de falha dos outros jobs — um
            # tenant com dado inesperado não pode travar os demais.
            if settings.SENTRY_DSN:
                sentry_sdk.set_tag("tenant_id", str(tenant.id))
                sentry_sdk.capture_exception(exc)
            logger.exception("Falha inesperada ao reavaliar insight_outcomes para tenant=%s", tenant.id)

    logger.info("Reavaliação concluída: %d outcome(s) no total.", total_reevaluated)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
