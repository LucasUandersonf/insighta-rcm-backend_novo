"""
app/repositories/organization_repository.py

Épico F3.2 do Plano Diretor ("Consolidação multi-unidade") — mesmo
padrão de network_benchmark_repository.py: chama uma função SQL
SECURITY DEFINER via `text()` a partir de uma sessão SEM tenant, porque
o dashboard consolidado é, de propósito, cross-tenant — ver DECISÃO
completa em app/sql/042_organizations.sql.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class OrganizationUnitRow:
    tenant_id: UUID
    trade_name: str
    organization_name: str
    is_requesting_tenant: bool
    total_billed: float
    denial_risk_value: float
    appointment_count: int
    no_show_count: int
    no_show_total: int


class OrganizationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_units_summary(self, tenant_id: UUID, *, window_days: int = 30) -> list[OrganizationUnitRow]:
        """Uma linha por unidade da MESMA organização do tenant
        solicitante (ele incluso) — lista VAZIA quando o tenant não
        pertence a nenhuma organização (organization_id NULL), nunca um
        erro (ver DECISÃO no JOIN da função SQL: requesting_org.organization_id
        NULL nunca casa com nada)."""
        stmt = text("SELECT * FROM core.organization_units_summary(:tenant_id, :window_days)")
        result = await self.session.execute(stmt, {"tenant_id": str(tenant_id), "window_days": window_days})
        return [
            OrganizationUnitRow(
                tenant_id=row.tenant_id,
                trade_name=row.trade_name,
                organization_name=row.organization_name,
                is_requesting_tenant=row.is_requesting_tenant,
                total_billed=float(row.total_billed),
                denial_risk_value=float(row.denial_risk_value),
                appointment_count=int(row.appointment_count),
                no_show_count=int(row.no_show_count),
                no_show_total=int(row.no_show_total),
            )
            for row in result.all()
        ]
