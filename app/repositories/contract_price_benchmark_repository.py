"""
app/repositories/contract_price_benchmark_repository.py — Oportunidades
(Sala de Comando 2.0). Mesmo padrão de
network_benchmark_repository.py: chama uma função SQL SECURITY DEFINER
via `text()` a partir de uma sessão SEM tenant (get_db_no_tenant, ver
app/api/deps.py::DbSessionNoTenant), porque este ranking é, de
propósito, cross-tenant — ver DECISÃO completa em
app/sql/033_network_contract_price_benchmark.sql.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ContractPriceBenchmarkRow:
    insurance_plan_id: UUID
    plan_display_name: str
    tuss_code: str
    procedure_name: str | None
    your_price: float
    network_median_price: float
    network_cohort_size: int
    monthly_volume: float


class ContractPriceBenchmarkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_benchmark(
        self, tenant_id: UUID, *, min_cohort: int = 3, volume_window_days: int = 90
    ) -> list[ContractPriceBenchmarkRow]:
        stmt = text(
            "SELECT * FROM core.network_contract_price_benchmark(:tenant_id, :min_cohort, :volume_window_days)"
        )
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": str(tenant_id),
                "min_cohort": min_cohort,
                "volume_window_days": volume_window_days,
            },
        )
        return [
            ContractPriceBenchmarkRow(
                insurance_plan_id=row.insurance_plan_id,
                plan_display_name=row.plan_display_name,
                tuss_code=row.tuss_code,
                procedure_name=row.procedure_name,
                your_price=float(row.your_price),
                network_median_price=float(row.network_median_price),
                network_cohort_size=int(row.network_cohort_size),
                monthly_volume=float(row.monthly_volume),
            )
            for row in result.all()
        ]
