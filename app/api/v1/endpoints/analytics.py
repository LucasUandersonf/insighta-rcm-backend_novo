"""
app/api/v1/endpoints/analytics.py — Dashboards de Decisão: um endpoint
por visão (Sala de Comando, Agenda & Capacidade, Insights), cada um já
devolvendo os dados agregados e prontos para o gráfico/cartão — em vez de
o frontend montar 5 requisições e cruzar no cliente. Mesma filosofia dos
outros endpoints "de leitura pesada" do projeto (reports.py).

RBAC: mesmo critério de contracts.py — dado financeiro/estratégico não é
de "atendimento" (recepção). auditor entra porque é leitura pura.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, DbSessionNoTenant, require_role
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.contract_price_benchmark_repository import ContractPriceBenchmarkRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.network_benchmark_repository import NetworkBenchmarkRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.analytics import (
    AgendaMetricsResponse,
    ContractUtilizationResponse,
    DenialRiskDistributionResponse,
    ExecutiveSummaryResponse,
    HealthScoreResponse,
    NetworkBenchmarkResponse,
    OportunidadesResponse,
    PlanLossRankingResponse,
    SmartInsightsResponse,
)
from app.services.analytics_service import AnalyticsService
from app.services.network_benchmark_service import NetworkBenchmarkService
from app.services.oportunidades_service import OportunidadesService

router = APIRouter(prefix="/analytics", tags=["analytics"])

_CAN_VIEW = ("owner", "admin", "financeiro", "auditor")


def _default_period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Sem filtro explícito -> últimos 7 dias fechados (a "semana" padrão
    dos cartões de variação percentual)."""
    resolved_end = date_to or date.today()
    resolved_start = date_from or (resolved_end - timedelta(days=6))
    if resolved_start > resolved_end:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="date_from deve ser <= date_to.")
    return resolved_start, resolved_end


def _build_service(db: DbSession) -> AnalyticsService:
    return AnalyticsService(
        AnalyticsRepository(db),
        ReportingRepository(db),
        ProfessionalRepository(db),
        ProfessionalAvailabilityRepository(db),
        CapacityRepository(db),
        DenialAppealRepository(db),
        # Só para ler Tenant.annual_revenue_goal (meta manual) no insight
        # de desempenho anual — ver smart_insights_engine.py::_annual_goal_insight.
        TenantRepository(db),
    )


@router.get("/executive-summary", response_model=ExecutiveSummaryResponse)
async def get_executive_summary(
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> ExecutiveSummaryResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_executive_summary(start, end)


@router.get("/agenda-metrics", response_model=AgendaMetricsResponse)
async def get_agenda_metrics(
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> AgendaMetricsResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_agenda_metrics(start, end)


@router.get("/smart-insights", response_model=SmartInsightsResponse)
async def get_smart_insights(
    db: DbSession,
    db_no_tenant: DbSessionNoTenant,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> SmartInsightsResponse:
    start, end = _default_period(date_from, date_to)
    # db_no_tenant além de db (mesma dupla do endpoint network-benchmark
    # abaixo): o Comparativo entrou como manchete opcional do feed de
    # insights (ver DECISÃO em AnalyticsService.get_smart_insights) e
    # precisa da mesma fonte cross-tenant — duas sessões independentes na
    # mesma rota, sem conflito (cada Depends abre a sua). Só entram pares
    # com cohort suficiente (your_rate e network_median não-None); o
    # resto do critério (desvio mínimo, só o pior caso) é decidido dentro
    # do service/motor, não aqui.
    benchmark = await NetworkBenchmarkService(NetworkBenchmarkRepository(db_no_tenant)).get_benchmark(
        uuid.UUID(current_user.tenant_id)
    )
    network_benchmark = [
        (m.key, m.label, m.your_rate, m.network_median)
        for m in benchmark.metrics
        if m.your_rate is not None and m.network_median is not None
    ]
    return await _build_service(db).get_smart_insights(
        start, end, tenant_id=current_user.tenant_id, network_benchmark=network_benchmark
    )


@router.get("/health-score", response_model=HealthScoreResponse)
async def get_health_score(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> HealthScoreResponse:
    # Sem date_from/date_to de propósito — janela é fixa dentro do
    # service (ver DECISÃO em AnalyticsService.get_health_score).
    return await _build_service(db).get_health_score()


@router.get("/network-benchmark", response_model=NetworkBenchmarkResponse)
async def get_network_benchmark(
    db: DbSessionNoTenant,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> NetworkBenchmarkResponse:
    # DbSessionNoTenant (não DbSession) de propósito — este é o único
    # endpoint da Sala de Comando que precisa escapar do RLS para
    # comparar com outras clínicas (ver DECISÃO em
    # app/sql/032_network_benchmark.sql e app/api/deps.py). current_user
    # continua exigindo autenticação normal — só a sessão de banco em si
    # não carrega tenant.
    service = NetworkBenchmarkService(NetworkBenchmarkRepository(db))
    return await service.get_benchmark(uuid.UUID(current_user.tenant_id))


@router.get("/oportunidades", response_model=OportunidadesResponse)
async def get_oportunidades(
    db: DbSessionNoTenant,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> OportunidadesResponse:
    # DbSessionNoTenant pelo mesmo motivo do Comparativo acima (ver
    # DECISÃO em app/sql/033_network_contract_price_benchmark.sql): esta
    # rota também escapa do RLS de propósito para cruzar preço de
    # contrato entre clínicas.
    service = OportunidadesService(ContractPriceBenchmarkRepository(db))
    return await service.get_oportunidades(uuid.UUID(current_user.tenant_id))


@router.get("/plan-loss-ranking", response_model=PlanLossRankingResponse)
async def get_plan_loss_ranking(
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> PlanLossRankingResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_plan_loss_ranking(start, end)


@router.get("/contract-utilization", response_model=ContractUtilizationResponse)
async def get_contract_utilization(
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> ContractUtilizationResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_contract_utilization(start, end)


@router.get("/denial-risk-distribution", response_model=DenialRiskDistributionResponse)
async def get_denial_risk_distribution(
    db: DbSession,
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> DenialRiskDistributionResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_denial_risk_distribution(start, end)
