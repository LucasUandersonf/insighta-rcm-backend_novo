"""
app/repositories/network_benchmark_repository.py — Comparativo entre
clínicas (Sala de Comando 2.0). Mesmo padrão de
platform_reporting_repository.py: chama uma função SQL SECURITY DEFINER
via `text()` a partir de uma sessão SEM tenant (get_db_no_tenant, ver
app/api/deps.py::DbSessionNoTenant), porque este comparativo é, de
propósito, cross-tenant — ver DECISÃO completa em
app/sql/032_network_benchmark.sql.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class NetworkBenchmarkRow:
    your_denial_rate: float | None
    your_denial_sample: int
    network_denial_median: float | None  # None = cohort abaixo do mínimo, nunca "0%"
    denial_cohort_size: int
    # "Mapa de Dados Insighta" — pilar Comparativo & rede: True quando o
    # cohort foi filtrado pela MESMA Tenant.specialty do solicitante
    # (ver DECISÃO completa em 048_network_benchmark_specialty_segment.sql);
    # False = usou o cohort geral (sem specialty própria, ou cohort
    # segmentado abaixo do mínimo).
    denial_cohort_is_segmented: bool
    your_no_show_rate: float | None
    your_no_show_sample: int
    network_no_show_median: float | None
    no_show_cohort_size: int
    no_show_cohort_is_segmented: bool


@dataclass
class NetworkChurnRow:
    """"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente,
    mecanismo 4) — mesmo formato de NetworkBenchmarkRow acima, ver
    DECISÃO completa em app/sql/053_network_churn_benchmark.sql."""

    your_churn_rate: float | None
    your_churn_sample: int
    network_churn_median: float | None  # None = cohort abaixo do mínimo, nunca "0%"
    churn_cohort_size: int
    churn_cohort_is_segmented: bool


@dataclass
class NetworkRevenueGrowthRow:
    your_trailing_12mo_total: float
    your_prior_12mo_total: float
    your_growth_rate: float | None  # None = sem faturamento nos 12 meses anteriores (base indefinida)
    network_growth_median: float | None  # None = cohort abaixo do mínimo
    growth_cohort_size: int


class NetworkBenchmarkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_revenue_growth(self, tenant_id: UUID, *, min_cohort: int = 5) -> NetworkRevenueGrowthRow:
        """Épico F3.3 do Plano Diretor ("Metas e cenários orientados a
        dados") — ver DECISÃO completa em
        app/sql/041_network_revenue_growth_benchmark.sql."""
        stmt = text("SELECT * FROM core.network_revenue_growth_benchmark(:tenant_id, :min_cohort)")
        result = await self.session.execute(stmt, {"tenant_id": str(tenant_id), "min_cohort": min_cohort})
        row = result.one()
        return NetworkRevenueGrowthRow(
            your_trailing_12mo_total=float(row.your_trailing_12mo_total),
            your_prior_12mo_total=float(row.your_prior_12mo_total),
            your_growth_rate=float(row.your_growth_rate) if row.your_growth_rate is not None else None,
            network_growth_median=float(row.network_growth_median) if row.network_growth_median is not None else None,
            growth_cohort_size=int(row.growth_cohort_size),
        )

    async def get_benchmark(self, tenant_id: UUID, *, window_days: int = 90, min_cohort: int = 5) -> NetworkBenchmarkRow:
        stmt = text(
            "SELECT * FROM core.network_glosa_no_show_benchmark(:tenant_id, :window_days, :min_cohort)"
        )
        result = await self.session.execute(
            stmt, {"tenant_id": str(tenant_id), "window_days": window_days, "min_cohort": min_cohort}
        )
        row = result.one()
        return NetworkBenchmarkRow(
            your_denial_rate=float(row.your_denial_rate) if row.your_denial_rate is not None else None,
            your_denial_sample=int(row.your_denial_sample),
            network_denial_median=float(row.network_denial_median) if row.network_denial_median is not None else None,
            denial_cohort_size=int(row.denial_cohort_size),
            denial_cohort_is_segmented=bool(row.denial_cohort_is_segmented),
            your_no_show_rate=float(row.your_no_show_rate) if row.your_no_show_rate is not None else None,
            your_no_show_sample=int(row.your_no_show_sample),
            network_no_show_median=float(row.network_no_show_median) if row.network_no_show_median is not None else None,
            no_show_cohort_size=int(row.no_show_cohort_size),
            no_show_cohort_is_segmented=bool(row.no_show_cohort_is_segmented),
        )

    async def get_churn_benchmark(self, tenant_id: UUID, *, min_cohort: int = 5) -> NetworkChurnRow:
        """"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente,
        mecanismo 4) — ver DECISÃO completa em
        app/sql/053_network_churn_benchmark.sql. Sem window_days de
        propósito: churn precoce é sempre "a partir de agora" (mesmo
        espírito de AnalyticsService.get_early_churn_risk), nunca uma
        janela de período."""
        stmt = text("SELECT * FROM core.network_churn_benchmark(:tenant_id, :min_cohort)")
        result = await self.session.execute(stmt, {"tenant_id": str(tenant_id), "min_cohort": min_cohort})
        row = result.one()
        return NetworkChurnRow(
            your_churn_rate=float(row.your_churn_rate) if row.your_churn_rate is not None else None,
            your_churn_sample=int(row.your_churn_sample),
            network_churn_median=float(row.network_churn_median) if row.network_churn_median is not None else None,
            churn_cohort_size=int(row.churn_cohort_size),
            churn_cohort_is_segmented=bool(row.churn_cohort_is_segmented),
        )
