"""
app/repositories/contract_repository.py

Agora só o CABEÇALHO do contrato (vigência, PDF, status de homologação —
ver DECISÃO em app/sql/007_contract_intelligence.sql). A tabela de
preços em si é ContractItemRepository — find_agreed_price() (usado pelo
motor de glosa) vive lá, não aqui, porque é lá que o preço mora.
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.contract import Contract
from app.models.insurance_plan import InsurancePlan


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

    async def expiring_without_renewal_summary(self, as_of: date, horizon_days: int) -> list[dict]:
        """
        Contrato de repasse (Achado "Evitando perdas" do Raio-X da
        Receita): `Contract.valid_until` sempre existiu no banco, mas
        nenhum insight avisava ANTES do vencimento — só o painel de
        Utilização de Contrato mostrava a data, exigindo que alguém
        abrisse a tela e notasse sozinho.

        Devolve uma linha por contrato HOMOLOGADO cujo `valid_until` cai
        dentro da janela (as_of..as_of+horizon_days) e que ainda NÃO tem
        um contrato sucessor homologado para o MESMO convênio (`valid_from`
        depois do vencimento deste) — ou seja, "vai vencer e ninguém
        cadastrou a renovação ainda". Um contrato cuja renovação já foi
        homologada (mesmo que o faturista ainda não tenha chegado na data)
        não entra aqui: `find_active_for_plan` já resolve esse caso no dia
        seguinte, não é uma perda em risco.

        Ordenado por `valid_until` ascendente — o motor de insight
        (`_contract_expiring_insight`) só usa o primeiro (o que vence
        primeiro), mesmo critério de "só o pior caso vira card" do
        restante do produto.
        """
        horizon = as_of + timedelta(days=horizon_days)
        successor = aliased(Contract)
        has_successor = exists().where(
            successor.insurance_plan_id == Contract.insurance_plan_id,
            successor.status == "homologado",
            successor.valid_from > Contract.valid_until,
        )
        stmt = (
            select(Contract.id, Contract.insurance_plan_id, InsurancePlan.display_name, Contract.valid_until)
            .join(InsurancePlan, InsurancePlan.id == Contract.insurance_plan_id)
            .where(
                Contract.status == "homologado",
                Contract.valid_until.is_not(None),
                Contract.valid_until >= as_of,
                Contract.valid_until <= horizon,
                ~has_successor,
            )
            .order_by(Contract.valid_until.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "contract_id": row.id,
                "insurance_plan_id": row.insurance_plan_id,
                "plan_name": row.display_name,
                "valid_until": row.valid_until,
            }
            for row in rows
        ]

    async def add(self, contract: Contract) -> Contract:
        self.session.add(contract)
        await self.session.flush()
        return contract

    async def save(self, contract: Contract) -> Contract:
        await self.session.flush()
        return contract
