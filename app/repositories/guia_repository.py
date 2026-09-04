import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guia import Guia


class GuiaRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, guia_id: uuid.UUID) -> Guia | None:
        stmt = select(Guia).where(Guia.id == guia_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_numero(self, insurance_plan_id: uuid.UUID, numero: str) -> Guia | None:
        """Usado pela normalização de ingestão (template de Faturamento)
        para agrupar várias linhas com o MESMO número de guia num único
        registro de Guia — o caso real de uma SADT com vários
        procedimentos na mesma guia (ver DECISÃO em
        app/sql/015_billing_guia.sql)."""
        stmt = select(Guia).where(Guia.insurance_plan_id == insurance_plan_id, Guia.numero == numero)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Guia], int]:
        items_stmt = select(Guia).order_by(Guia.created_at.desc()).limit(limit).offset(offset)
        items = list((await self.session.execute(items_stmt)).scalars().all())
        total = (await self.session.execute(select(func.count()).select_from(Guia))).scalar_one()
        return items, total

    async def list_by_lote(self, lote_id: uuid.UUID) -> list[Guia]:
        """Usado por LoteService para montar a lista de guias de um lote
        (tela de gestão) e para validar que um lote não fica vazio ao
        fechar (ver DECISÃO em app/sql/016_lotes_faturas.sql)."""
        stmt = select(Guia).where(Guia.lote_id == lote_id).order_by(Guia.created_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, guia: Guia) -> Guia:
        self.session.add(guia)
        await self.session.flush()
        return guia

    async def save(self, guia: Guia) -> Guia:
        await self.session.flush()
        return guia
