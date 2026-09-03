"""
app/repositories/contract_item_repository.py

A tabela de preços em si. `find_agreed_price` substitui o antigo
`ContractRepository.find_agreed_value` — mesma semântica de vigência
(joga o JOIN contra o cabeçalho Contract para checar valid_from/
valid_until e status='homologado'), só que agora o preço mora na linha
de item, não no cabeçalho.
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.contract_item import ContractItem


class ContractItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_agreed_price(
        self, insurance_plan_id: uuid.UUID, tuss_code: str, as_of: date | None = None
    ) -> ContractItem | None:
        """
        Usado pelo motor de regras (denial_risk_engine.assess) e pela
        normalização de ingestão (Etapa 2) para comparar valor cobrado vs.
        valor acordado — o "cruzamento de contrato" do briefing. Só
        considera contratos HOMOLOGADOS: um contrato ainda em rascunho/
        revisão (extração de IA ainda não confirmada por humano) nunca
        vira "a verdade" para o motor de glosa.
        """
        as_of = as_of or date.today()
        stmt = (
            select(ContractItem)
            .join(Contract, Contract.id == ContractItem.contract_id)
            .where(
                Contract.insurance_plan_id == insurance_plan_id,
                Contract.status == "homologado",
                Contract.valid_from <= as_of,
                (Contract.valid_until.is_(None)) | (Contract.valid_until >= as_of),
                ContractItem.tuss_code == tuss_code,
            )
            .order_by(Contract.valid_from.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_by_contract(self, contract_id: uuid.UUID) -> list[ContractItem]:
        stmt = select(ContractItem).where(ContractItem.contract_id == contract_id).order_by(ContractItem.tuss_code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_items_for_previous_homologated_contract(
        self, insurance_plan_id: uuid.UUID, exclude_contract_id: uuid.UUID
    ) -> list[ContractItem]:
        """
        Tabela de preços do contrato HOMOLOGADO mais recente do mesmo
        plano (por `valid_from`), excluindo o contrato que está sendo
        extraído agora — usado por
        contract_extraction_service.detect_price_anomalies para comparar
        o preço recém-extraído contra o que já valia antes, procedimento
        a procedimento (mesmo tuss_code).

        Diferente de `find_agreed_price`: não filtra por `as_of` (data de
        hoje) — aqui a pergunta é "o que valia na tabela anterior a
        esta", não "o que vale hoje". Devolve lista vazia quando não há
        nenhum contrato homologado anterior (ex: primeira tabela deste
        plano) — sem histórico, não há o que comparar.
        """
        previous_id_stmt = (
            select(Contract.id)
            .where(
                Contract.insurance_plan_id == insurance_plan_id,
                Contract.status == "homologado",
                Contract.id != exclude_contract_id,
            )
            .order_by(Contract.valid_from.desc())
            .limit(1)
        )
        previous_contract_id = (await self.session.execute(previous_id_stmt)).scalar_one_or_none()
        if previous_contract_id is None:
            return []
        return await self.list_by_contract(previous_contract_id)

    async def replace_items(self, tenant_id: uuid.UUID, contract_id: uuid.UUID, items: list[dict]) -> list[ContractItem]:
        """
        Homologação: substitui TODOS os itens do contrato pelo conjunto
        revisado pelo humano (não um upsert incremental) — a Tela de
        Conferência sempre manda a lista completa e final, então "apagar
        tudo e regravar" é mais simples e mais correto do que tentar
        diffar item a item (evita item órfão de uma extração anterior
        sobrevivendo silenciosamente numa homologação seguinte).
        """
        existing = await self.list_by_contract(contract_id)
        for item in existing:
            await self.session.delete(item)
        await self.session.flush()

        saved = []
        for item in items:
            row = ContractItem(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                contract_id=contract_id,
                tuss_code=item["tuss_code"],
                procedure_name=item.get("procedure_name"),
                agreed_price=item["agreed_price"],
            )
            self.session.add(row)
            saved.append(row)
        await self.session.flush()
        return saved
