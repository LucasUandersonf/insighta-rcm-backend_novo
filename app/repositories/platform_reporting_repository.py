"""
app/repositories/platform_reporting_repository.py — painel interno de
Customer Success. Mesmo padrão de api_key_repository.py::find_candidates_by_prefix:
chama uma função SQL SECURITY DEFINER via `text()` a partir de uma sessão
SEM tenant (`get_db_no_tenant()`), porque este relatório é, de propósito,
cross-tenant (ver DECISÃO completa em app/sql/026_platform_customer_success.sql).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class TenantUsageRow:
    tenant_id: uuid.UUID
    trade_name: str
    plan_tier: str
    tenant_is_active: bool
    tenant_created_at: datetime
    active_users: int
    last_activity_at: datetime | None
    events_last_30d: int
    patients_total: int
    appointments_last_30d: int
    billings_last_30d: int


class PlatformReportingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_tenant_usage(self) -> list[TenantUsageRow]:
        stmt = text(
            "SELECT tenant_id, trade_name, plan_tier, tenant_is_active, tenant_created_at, "
            "active_users, last_activity_at, events_last_30d, patients_total, "
            "appointments_last_30d, billings_last_30d "
            "FROM core.platform_tenant_usage_summary()"
        )
        result = await self.session.execute(stmt)
        return [
            TenantUsageRow(
                tenant_id=row.tenant_id,
                trade_name=row.trade_name,
                plan_tier=row.plan_tier,
                tenant_is_active=row.tenant_is_active,
                tenant_created_at=row.tenant_created_at,
                active_users=row.active_users,
                last_activity_at=row.last_activity_at,
                events_last_30d=row.events_last_30d,
                patients_total=row.patients_total,
                appointments_last_30d=row.appointments_last_30d,
                billings_last_30d=row.billings_last_30d,
            )
            for row in result.all()
        ]
