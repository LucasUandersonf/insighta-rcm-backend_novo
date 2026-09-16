"""
app/services/cost_entry_service.py

Plano Diretor Insighta, épico F3.1 ("Módulo de custos e margem real")
— ver DECISÃO completa em app/sql/039_cost_entries.sql.
"""
import uuid

from fastapi import HTTPException, status

from app.models.cost_entry import CostEntry
from app.repositories.cost_entry_repository import CostEntryRepository
from app.schemas.cost_entry import CostEntryCreateRequest, CostEntryResponse
from app.schemas.pagination import PaginatedResponse


class CostEntryService:
    def __init__(self, repo: CostEntryRepository):
        self.repo = repo

    async def create_cost_entry(self, tenant_id: str, created_by: uuid.UUID, data: CostEntryCreateRequest) -> CostEntryResponse:
        entry = CostEntry(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            category=data.category,
            description=data.description,
            amount=data.amount,
            period_month=data.period_month,
            professional_id=data.professional_id,
            created_by=created_by,
        )
        saved = await self.repo.add(entry)
        return CostEntryResponse.model_validate(saved)

    async def list_cost_entries(self, *, limit: int, offset: int) -> PaginatedResponse[CostEntryResponse]:
        items, total = await self.repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(
            items=[CostEntryResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset
        )

    async def delete_cost_entry(self, cost_entry_id: uuid.UUID) -> None:
        entry = await self.repo.get_by_id(cost_entry_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lançamento de custo não encontrado neste tenant.")
        await self.repo.delete(entry)
