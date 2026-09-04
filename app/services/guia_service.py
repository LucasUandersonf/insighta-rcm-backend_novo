import uuid

from fastapi import HTTPException, status

from app.models.guia import Guia
from app.repositories.guia_repository import GuiaRepository
from app.schemas.guia import GuiaCreateRequest, GuiaResponse, GuiaUpdateRequest
from app.schemas.pagination import PaginatedResponse


class GuiaService:
    def __init__(self, repo: GuiaRepository):
        self.repo = repo

    async def create_guia(self, tenant_id: str, data: GuiaCreateRequest) -> GuiaResponse:
        guia = Guia(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            insurance_plan_id=data.insurance_plan_id,
            tipo=data.tipo,
            numero=data.numero,
            senha=data.senha,
            senha_validade=data.senha_validade,
            tabela_procedimento=data.tabela_procedimento,
        )
        saved = await self.repo.add(guia)
        return GuiaResponse.model_validate(saved)

    async def list_guias_paginated(self, *, limit: int, offset: int) -> PaginatedResponse[GuiaResponse]:
        items, total = await self.repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(items=[GuiaResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset)

    async def get_guia(self, guia_id: uuid.UUID) -> GuiaResponse:
        guia = await self.repo.get_by_id(guia_id)
        if guia is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guia não encontrada neste tenant.")
        return GuiaResponse.model_validate(guia)

    async def update_guia(self, guia_id: uuid.UUID, data: GuiaUpdateRequest) -> GuiaResponse:
        guia = await self.repo.get_by_id(guia_id)
        if guia is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guia não encontrada neste tenant.")
        if data.numero is not None:
            guia.numero = data.numero
        if data.senha is not None:
            guia.senha = data.senha
        if data.senha_validade is not None:
            guia.senha_validade = data.senha_validade
        if data.tabela_procedimento is not None:
            guia.tabela_procedimento = data.tabela_procedimento
        await self.repo.save(guia)
        return GuiaResponse.model_validate(guia)
