"""
app/repositories/billing_repository.py

Repositório recebe a `session` já tenant-aware (ver DbSession em deps.py)
e NUNCA precisa escrever `.where(Billing.tenant_id == ...)` manualmente —
essa é justamente a folga que o RLS nos dá: o repositório fica mais
simples e, ao mesmo tempo, mais seguro, porque não depende de um
desenvolvedor lembrar de filtrar por tenant em toda query nova.
"""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Billing


class BillingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_high_risk(self) -> list[Billing]:
        # Sem WHERE tenant_id: o RLS já garante que só vêm linhas do
        # tenant certo. Isso alimenta a Tela B (Painel Anti-Glosa).
        stmt = select(Billing).where(Billing.denial_risk_level == "high")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_high_risk_paginated(self, *, limit: int, offset: int) -> tuple[list[Billing], int]:
        # Mesmo padrão de paginação aplicado a contracts/denial-appeals/
        # patients (ver PaginatedResponse em app/schemas/pagination.py):
        # itens + contagem total, para a UI renderizar "Mostrando X-Y de Z".
        base = select(Billing).where(Billing.denial_risk_level == "high")
        total = (await self.session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        stmt = base.order_by(Billing.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_id(self, billing_id: uuid.UUID) -> Billing | None:
        stmt = select(Billing).where(Billing.id == billing_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, billing: Billing) -> Billing:
        self.session.add(billing)
        await self.session.flush()  # garante que billing.id exista antes do commit implícito
        return billing

    async def save(self, billing: Billing) -> Billing:
        await self.session.flush()
        return billing
