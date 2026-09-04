import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.local import Local


class LocalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active(self) -> list[Local]:
        """Alimenta seletores operacionais (ex.: campo "Local" em Nova
        Consulta) — mesmo critério de ProfessionalRepository.list_active."""
        stmt = select(Local).where(Local.is_active.is_(True)).order_by(Local.nome)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Local]:
        """Ativos e inativos — usado pela tela de gestão (precisa ver e
        poder reativar quem foi desativado)."""
        stmt = select(Local).order_by(Local.nome)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, local_id: uuid.UUID) -> Local | None:
        stmt = select(Local).where(Local.id == local_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, local: Local) -> Local:
        self.session.add(local)
        await self.session.flush()
        return local

    async def save(self, local: Local) -> Local:
        await self.session.flush()
        return local
