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
    your_no_show_rate: float | None
    your_no_show_sample: int
    network_no_show_median: float | None
    no_show_cohort_size: int


class NetworkBenchmarkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

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
            your_no_show_rate=float(row.your_no_show_rate) if row.your_no_show_rate is not None else None,
            your_no_show_sample=int(row.your_no_show_sample),
            network_no_show_median=float(row.network_no_show_median) if row.network_no_show_median is not None else None,
            no_show_cohort_size=int(row.no_show_cohort_size),
        )
