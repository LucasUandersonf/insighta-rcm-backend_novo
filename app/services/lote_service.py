"""
app/services/lote_service.py

Lote — ver DECISÃO completa em app/models/lote.py e
app/sql/016_lotes_faturas.sql. Ciclo de vida aberto -> fechado ->
faturado (este último setado por FaturaService.create_from_lotes, não
aqui).
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.lote import Lote
from app.repositories.guia_repository import GuiaRepository
from app.repositories.lote_repository import LoteRepository
from app.schemas.guia import GuiaResponse
from app.schemas.lote import LoteCreateRequest, LoteResponse
from app.schemas.pagination import PaginatedResponse


class LoteService:
    def __init__(self, lote_repo: LoteRepository, guia_repo: GuiaRepository):
        self.lote_repo = lote_repo
        self.guia_repo = guia_repo

    async def create_lote(self, tenant_id: str, data: LoteCreateRequest) -> LoteResponse:
        lote = Lote(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            insurance_plan_id=data.insurance_plan_id,
            tipo=data.tipo,
        )
        saved = await self.lote_repo.add(lote)
        return LoteResponse.model_validate(saved)

    async def list_lotes_paginated(self, *, limit: int, offset: int) -> PaginatedResponse[LoteResponse]:
        items, total = await self.lote_repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(items=[LoteResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset)

    async def get_lote(self, lote_id: uuid.UUID) -> LoteResponse:
        lote = await self._get_or_404(lote_id)
        return LoteResponse.model_validate(lote)

    async def list_guias_in_lote(self, lote_id: uuid.UUID) -> list[GuiaResponse]:
        await self._get_or_404(lote_id)
        guias = await self.guia_repo.list_by_lote(lote_id)
        return [GuiaResponse.model_validate(g) for g in guias]

    async def add_guia(self, lote_id: uuid.UUID, guia_id: uuid.UUID) -> GuiaResponse:
        """Equivale a "Atribuir ao Lote"/seleção de pacientes de um ERP
        real. Valida o que confirmamos em 3 ERPs do mercado: um lote só
        aceita guias do MESMO convênio e MESMO tipo (ver DECISÃO em
        app/sql/016_lotes_faturas.sql)."""
        lote = await self._get_or_404(lote_id)
        if lote.status != "aberto":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Só é possível adicionar guias a um lote aberto.")

        guia = await self.guia_repo.get_by_id(guia_id)
        if guia is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guia não encontrada neste tenant.")
        if guia.lote_id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Esta guia já pertence a um lote.")
        if guia.insurance_plan_id != lote.insurance_plan_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A guia precisa ser do mesmo convênio do lote.")
        if guia.tipo != lote.tipo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A guia precisa ser do mesmo tipo do lote.")

        guia.lote_id = lote.id
        await self.guia_repo.save(guia)
        return GuiaResponse.model_validate(guia)

    async def remove_guia(self, lote_id: uuid.UUID, guia_id: uuid.UUID) -> GuiaResponse:
        lote = await self._get_or_404(lote_id)
        if lote.status != "aberto":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Só é possível remover guias de um lote aberto.")

        guia = await self.guia_repo.get_by_id(guia_id)
        if guia is None or guia.lote_id != lote.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guia não encontrada neste lote.")

        guia.lote_id = None
        await self.guia_repo.save(guia)
        return GuiaResponse.model_validate(guia)

    async def fechar_lote(self, lote_id: uuid.UUID) -> LoteResponse:
        """Equivale ao "Bloquear" de um ERP real: trava edição, pronto
        para virar fatura. Não pode ficar vazio — fechar um lote sem
        nenhuma guia não tem sentido de negócio."""
        lote = await self._get_or_404(lote_id)
        if lote.status != "aberto":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Só é possível fechar um lote aberto.")

        guias = await self.guia_repo.list_by_lote(lote_id)
        if not guias:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Não é possível fechar um lote sem nenhuma guia.")

        lote.status = "fechado"
        lote.closed_at = datetime.now(timezone.utc)
        await self.lote_repo.save(lote)
        return LoteResponse.model_validate(lote)

    async def _get_or_404(self, lote_id: uuid.UUID) -> Lote:
        lote = await self.lote_repo.get_by_id(lote_id)
        if lote is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lote não encontrado neste tenant.")
        return lote
