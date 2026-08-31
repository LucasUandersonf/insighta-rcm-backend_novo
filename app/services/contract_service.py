"""
app/services/contract_service.py

Cadastro MANUAL de contrato (sem PDF/IA) — ver DECISÃO em
app/schemas/contract.py::ContractCreateRequest. Cria o cabeçalho já
HOMOLOGADO (foi um humano quem digitou os itens) e grava os itens na
mesma transação via ContractItemRepository.replace_items (mesmo método
usado pela homologação da esteira de IA — "gravar o conjunto final de
itens" é a mesma operação nos dois fluxos, só muda quem originou os
dados).
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.contract import Contract
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.contract_repository import ContractRepository
from app.schemas.contract import ContractCreateRequest, ContractItemResponse, ContractResponse


class ContractService:
    def __init__(self, repo: ContractRepository, item_repo: ContractItemRepository):
        self.repo = repo
        self.item_repo = item_repo

    async def create_contract(self, tenant_id: str, data: ContractCreateRequest) -> ContractResponse:
        tenant_uuid = uuid.UUID(tenant_id)
        contract = Contract(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            insurance_plan_id=data.insurance_plan_id,
            valid_from=data.valid_from,
            valid_until=data.valid_until,
            status="homologado",
            homologated_at=datetime.now(timezone.utc),
        )
        saved = await self.repo.add(contract)

        items = await self.item_repo.replace_items(
            tenant_id=tenant_uuid,
            contract_id=saved.id,
            items=[item.model_dump() for item in data.items],
        )
        return self._to_response(saved, items)

    async def list_active(self) -> list[ContractResponse]:
        contracts = await self.repo.list_all()
        result = []
        for contract in contracts:
            items = await self.item_repo.list_by_contract(contract.id)
            result.append(self._to_response(contract, items))
        return result

    async def list_active_paginated(self, *, limit: int, offset: int) -> tuple[list[ContractResponse], int]:
        contracts, total = await self.repo.list_paginated(limit=limit, offset=offset)
        result = []
        for contract in contracts:
            items = await self.item_repo.list_by_contract(contract.id)
            result.append(self._to_response(contract, items))
        return result, total

    async def get_contract(self, contract_id: uuid.UUID) -> ContractResponse:
        contract = await self.repo.get_by_id(contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contrato não encontrado neste tenant.")
        items = await self.item_repo.list_by_contract(contract.id)
        return self._to_response(contract, items)

    @staticmethod
    def _to_response(contract: Contract, items: list) -> ContractResponse:
        return ContractResponse(
            id=contract.id,
            insurance_plan_id=contract.insurance_plan_id,
            valid_from=contract.valid_from,
            valid_until=contract.valid_until,
            status=contract.status,
            pdf_s3_key=contract.pdf_s3_key,
            items=[ContractItemResponse.model_validate(i) for i in items],
            created_at=contract.created_at,
        )
