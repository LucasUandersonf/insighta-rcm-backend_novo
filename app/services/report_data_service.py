"""
app/services/report_data_service.py

Orquestra tudo que o relatório semanal precisa: reaproveita
ReportingRepository (glosa/ROI/no-show) e CapacityService (utilização de
agenda, já construído para o módulo de Capacidade) — nenhuma lógica de
agregação nova é inventada aqui além do que já existia espalhado nos
outros módulos.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.repositories.capacity_repository import CapacityRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.services.capacity_service import CapacityService
from app.services.report_calculations import average_utilization, compute_roi_pct


@dataclass
class WeeklyReportData:
    tenant_name: str
    period_start: date
    period_end: date
    total_billed: float
    total_value_saved: float
    high_risk_pending_count: int
    marketing_spend_total: float
    marketing_revenue_attributed: float
    marketing_roi_pct: float | None
    avg_capacity_utilization: float | None
    no_show_count: int
    upcoming_high_risk_appointments: int


class ReportDataService:
    def __init__(
        self,
        reporting_repo: ReportingRepository,
        professional_repo: ProfessionalRepository,
        availability_repo: ProfessionalAvailabilityRepository,
        capacity_repo: CapacityRepository,
    ):
        self.reporting_repo = reporting_repo
        self.professional_repo = professional_repo
        self.capacity_service = CapacityService(availability_repo, capacity_repo)

    async def build_weekly_report(self, tenant_name: str, period_start: date, period_end: date) -> WeeklyReportData:
        billing = await self.reporting_repo.billing_summary(period_start, period_end)
        spend = await self.reporting_repo.marketing_spend_total(period_start, period_end)
        revenue = await self.reporting_repo.revenue_from_campaign_patients(period_start, period_end)
        no_show = await self.reporting_repo.no_show_count(period_start, period_end)

        next_week_start = period_end + timedelta(days=1)
        next_week_end = next_week_start + timedelta(days=6)
        upcoming_high_risk = await self.reporting_repo.upcoming_high_risk_appointments_count(
            next_week_start, next_week_end
        )

        # Utilização média entre profissionais ATIVOS com grade configurada.
        # Profissionais sem nenhuma disponibilidade cadastrada são
        # excluídos da média (ver average_utilization) — incluí-los como
        # 0% distorceria a média para baixo por um problema de CADASTRO,
        # não de ocupação real de agenda.
        professionals = await self.professional_repo.list_active()
        utilization_rates = []
        for professional in professionals:
            result = await self.capacity_service.get_utilization(professional.id, period_start, period_end)
            if result.available_minutes > 0:
                utilization_rates.append(result.utilization_rate)

        return WeeklyReportData(
            tenant_name=tenant_name,
            period_start=period_start,
            period_end=period_end,
            total_billed=billing["total_billed"],
            total_value_saved=billing["total_value_saved"],
            high_risk_pending_count=billing["high_risk_pending_count"],
            marketing_spend_total=spend,
            marketing_revenue_attributed=revenue,
            marketing_roi_pct=compute_roi_pct(spend, revenue),
            avg_capacity_utilization=average_utilization(utilization_rates),
            no_show_count=no_show,
            upcoming_high_risk_appointments=upcoming_high_risk,
        )
