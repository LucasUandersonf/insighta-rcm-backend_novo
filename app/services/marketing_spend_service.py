"""
app/services/marketing_spend_service.py

Achado do Dossiê Insighta RCM — Onda 2 do Plano de Ação: fecha o
pipeline de escrita de core.marketing_spend (ver DECISÃO completa em
app/schemas/marketing_spend.py).
"""
import uuid

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.models.marketing_spend import MarketingSpend
from app.repositories.marketing_spend_repository import MarketingSpendRepository
from app.schemas.marketing_spend import MarketingSpendCreateRequest, MarketingSpendResponse
from app.schemas.pagination import PaginatedResponse


class MarketingSpendService:
    def __init__(self, repo: MarketingSpendRepository):
        self.repo = repo

    async def create_marketing_spend(self, tenant_id: str, data: MarketingSpendCreateRequest) -> MarketingSpendResponse:
        entry = MarketingSpend(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            source=data.source,
            campaign_id=data.campaign_id,
            campaign_name=data.campaign_name,
            spend_date=data.spend_date,
            amount_spent=data.amount_spent,
            impressions=data.impressions,
            clicks=data.clicks,
        )
        try:
            saved = await self.repo.add(entry)
        except IntegrityError:
            # UNIQUE (tenant_id, source, campaign_id, spend_date) — ver
            # 001_init_schema.sql. Erro do usuário (lançamento duplicado
            # do mesmo dia/campanha), nunca um 500: a exceção propaga
            # daqui pra fora do `session.begin()` de get_db_with_tenant,
            # que já cuida do rollback automático (ver DECISÃO lá).
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Já existe um lançamento de gasto para esta campanha nesta data.",
            )
        return MarketingSpendResponse.model_validate(saved)

    async def list_marketing_spend(self, *, limit: int, offset: int) -> PaginatedResponse[MarketingSpendResponse]:
        items, total = await self.repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(
            items=[MarketingSpendResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset
        )

    async def delete_marketing_spend(self, marketing_spend_id: uuid.UUID) -> None:
        entry = await self.repo.get_by_id(marketing_spend_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lançamento de gasto não encontrado neste tenant.")
        await self.repo.delete(entry)
