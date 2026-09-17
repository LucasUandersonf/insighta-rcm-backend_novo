"""
app/services/organization_service.py

Épico F3.2 do Plano Diretor ("Consolidação multi-unidade"). Serviço
PRÓPRIO (não dentro de AnalyticsService), mesmo motivo de
NetworkBenchmarkService: a única fonte de dado deste serviço é uma
sessão SEM tenant (DbSessionNoTenant, ver app/api/deps.py).
"""
from uuid import UUID

from app.repositories.organization_repository import OrganizationRepository
from app.schemas.analytics import OrganizationSummaryResponse, OrganizationUnitSummary

# Mesma janela do Comparativo entre Clínicas seria "90 dias" — aqui 30
# dias de propósito: o consolidado multi-unidade é operacional ("como
# cada unidade está indo AGORA"), não uma tendência de médio prazo.
_WINDOW_DAYS = 30


def _rate_or_none(numerator: int, denominator: int) -> float | None:
    """Mesmo princípio de _denial_risk_pct/overall_no_show_rate em
    analytics_service.py: sem amostra, a taxa é None (indefinida), nunca
    0%/100% inventado."""
    if denominator <= 0:
        return None
    return numerator / denominator


class OrganizationService:
    def __init__(self, repo: OrganizationRepository):
        self.repo = repo

    async def get_units_summary(self, tenant_id: UUID) -> OrganizationSummaryResponse:
        rows = await self.repo.get_units_summary(tenant_id, window_days=_WINDOW_DAYS)

        if not rows:
            # Tenant não pertence a nenhuma organização — estado NORMAL
            # da maioria das clínicas (ver DECISÃO em
            # app/sql/042_organizations.sql: o JOIN da função nunca casa
            # quando requesting_org.organization_id é NULL).
            return OrganizationSummaryResponse(
                belongs_to_organization=False,
                organization_name=None,
                window_days=_WINDOW_DAYS,
                units=[],
                consolidated_total_billed=0.0,
                consolidated_denial_risk_pct=None,
                consolidated_no_show_rate=None,
            )

        units = [
            OrganizationUnitSummary(
                tenant_id=str(row.tenant_id),
                trade_name=row.trade_name,
                is_requesting_tenant=row.is_requesting_tenant,
                total_billed=row.total_billed,
                denial_risk_pct=(row.denial_risk_value / row.total_billed if row.total_billed > 0 else None),
                appointment_count=row.appointment_count,
                no_show_rate=_rate_or_none(row.no_show_count, row.no_show_total),
            )
            for row in rows
        ]

        consolidated_total_billed = sum(row.total_billed for row in rows)
        consolidated_denial_risk_value = sum(row.denial_risk_value for row in rows)
        consolidated_no_show_count = sum(row.no_show_count for row in rows)
        consolidated_no_show_total = sum(row.no_show_total for row in rows)

        return OrganizationSummaryResponse(
            belongs_to_organization=True,
            organization_name=rows[0].organization_name,
            window_days=_WINDOW_DAYS,
            units=units,
            consolidated_total_billed=consolidated_total_billed,
            consolidated_denial_risk_pct=(
                consolidated_denial_risk_value / consolidated_total_billed if consolidated_total_billed > 0 else None
            ),
            consolidated_no_show_rate=_rate_or_none(consolidated_no_show_count, consolidated_no_show_total),
        )
