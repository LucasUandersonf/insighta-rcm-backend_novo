"""
app/repositories/contract_repository.py

Agora só o CABEÇALHO do contrato (vigência, PDF, status de homologação —
ver DECISÃO em app/sql/007_contract_intelligence.sql). A tabela de
preços em si é ContractItemRepository — find_agreed_price() (usado pelo
motor de glosa) vive lá, não aqui, porque é lá que o preço mora.
"""
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract


class ContractRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_plan(self, insurance_plan_id: uuid.UUID) -> list[Contract]:
        stmt = (
            select(Contract)
            .where(Contract.insurance_plan_id == insurance_plan_id)
            .order_by(Contract.valid_from.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Contract]:
        stmt = select(Contract).order_by(Contract.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_paginated(self, *, limit: int, offset: int) -> tuple[list[Contract], int]:
        count_stmt = select(func.count()).select_from(Contract)
        total = (await self.session.execute(count_stmt)).scalar_one()

        items_stmt = select(Contract).order_by(Contract.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(items_stmt)
        return list(result.scalars().all()), total

    async def get_by_id(self, contract_id: uuid.UUID) -> Contract | None:
        stmt = select(Contract).where(Contract.id == contract_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_for_plan(self, insurance_plan_id: uuid.UUID, as_of: date | None = None) -> Contract | None:
        as_of = as_of or date.today()
        stmt = (
            select(Contract)
            .where(
                Contract.insurance_plan_id == insurance_plan_id,
                Contract.status == "homologado",
                Contract.valid_from <= as_of,
                (Contract.valid_until.is_(None)) | (Contract.valid_until >= as_of),
            )
            .order_by(Contract.valid_from.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def add(self, contract: Contract) -> Contract:
        self.session.add(contract)
        await self.session.flush()
        return contract

    async def save(self, contract: Contract) -> Contract:
        await self.session.flush()
        return contract
