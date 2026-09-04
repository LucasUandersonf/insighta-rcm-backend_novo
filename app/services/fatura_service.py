"""
app/services/fatura_service.py

Fatura — ver DECISÃO completa em app/models/fatura.py e
app/sql/016_lotes_faturas.sql. Agrupa um ou mais Lotes FECHADOS do
MESMO convênio; a baixa (recebimento) acontece aqui, não no lote.
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.fatura import Fatura
from app.repositories.fatura_repository import FaturaRepository
from app.repositories.lote_repository import LoteRepository
from app.schemas.fatura import FaturaCreateRequest, FaturaResponse, FaturaSettleRequest
from app.schemas.pagination import PaginatedResponse


class FaturaService:
    def __init__(self, fatura_repo: FaturaRepository, lote_repo: LoteRepository):
        self.fatura_repo = fatura_repo
        self.lote_repo = lote_repo

    async def create_from_lotes(self, tenant_id: str, data: FaturaCreateRequest) -> FaturaResponse:
        lotes = await self.lote_repo.get_many_by_ids(data.lote_ids)
        if len(lotes) != len(set(data.lote_ids)):
            # RLS já filtra lote de outro tenant "para fora" do resultado
            # — a mesma mensagem cobre "não existe" e "não é meu".
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Um ou mais lotes não foram encontrados neste tenant.")

        first_plan_id = lotes[0].insurance_plan_id
        for lote in lotes:
            if lote.status != "fechado":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Todos os lotes precisam estar fechados antes de gerar a fatura.",
                )
            if lote.insurance_plan_id != first_plan_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Todos os lotes de uma fatura precisam ser do mesmo convênio.",
                )

        fatura = Fatura(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            insurance_plan_id=first_plan_id,
            serie=data.serie,
            numero=data.numero,
        )
        saved = await self.fatura_repo.add(fatura)

        for lote in lotes:
            lote.status = "faturado"
            lote.fatura_id = saved.id
            await self.lote_repo.save(lote)

        return FaturaResponse.model_validate(saved)

    async def list_faturas_paginated(self, *, limit: int, offset: int) -> PaginatedResponse[FaturaResponse]:
        items, total = await self.fatura_repo.list_paginated(limit=limit, offset=offset)
        return PaginatedResponse(items=[FaturaResponse.model_validate(i) for i in items], total=total, limit=limit, offset=offset)

    async def get_fatura(self, fatura_id: uuid.UUID) -> FaturaResponse:
        fatura = await self._get_or_404(fatura_id)
        return FaturaResponse.model_validate(fatura)

    async def settle_fatura(self, fatura_id: uuid.UUID, data: FaturaSettleRequest) -> FaturaResponse:
        """Baixa da fatura — mesmo princípio de BillingService.settle_billing,
        um nível acima: registra quanto a operadora REALMENTE pagou pela
        fatura inteira."""
        fatura = await self._get_or_404(fatura_id)
        fatura.valor_recebido = round(data.valor_recebido, 2)
        fatura.data_recebimento = datetime.now(timezone.utc)
        fatura.status = "parcialmente_paga" if data.is_partial else "paga"
        await self.fatura_repo.save(fatura)
        return FaturaResponse.model_validate(fatura)

    async def _get_or_404(self, fatura_id: uuid.UUID) -> Fatura:
        fatura = await self.fatura_repo.get_by_id(fatura_id)
        if fatura is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fatura não encontrada neste tenant.")
        return fatura
