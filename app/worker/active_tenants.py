"""
app/worker/active_tenants.py

Lista de tenants ativos — compartilhada entre os jobs agendados
(weekly_report_job.py, daily_alert_job.py) que precisam iterar "todo
tenant ativo" antes de decidir, POR TENANT, se há algo a fazer (ver
DECISÃO em cada job sobre por que a elegibilidade real é decidida
depois, não aqui).
"""
from sqlalchemy import select

from app.db.session import get_db_no_tenant
from app.models.tenant import Tenant


async def list_active_tenants() -> list[Tenant]:
    async for session in get_db_no_tenant():
        result = await session.execute(select(Tenant).where(Tenant.is_active.is_(True)))
        return list(result.scalars().all())
    return []  # pragma: no cover — get_db_no_tenant sempre gera pelo menos um yield
