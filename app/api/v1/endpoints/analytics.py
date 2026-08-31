"""
app/api/v1/endpoints/analytics.py — Dashboards de Decisão: um endpoint
por visão (Sala de Comando, Agenda & Capacidade, Insights), cada um já
devolvendo os dados agregados e prontos para o gráfico/cartão — em vez de
o frontend montar 5 requisições e cruzar no cliente. Mesma filosofia dos
outros endpoints "de leitura pesada" do projeto (reports.py).

RBAC: mesmo critério de contracts.py — dado financeiro/estratégico não é
de "atendimento" (recepção). auditor entra porque é leitura pura.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.analytics import AgendaMetricsResponse, ExecutiveSummaryResponse, SmartInsightsResponse
from app.services.analytics_service import AnalyticsService

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
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> SmartInsightsResponse:
    start, end = _default_period(date_from, date_to)
    return await _build_service(db).get_smart_insights(start, end, tenant_id=current_user.tenant_id)
