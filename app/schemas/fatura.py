"""
app/schemas/fatura.py

Fatura — ver DECISÃO completa em app/models/fatura.py e
app/sql/016_lotes_faturas.sql.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FaturaCreateRequest(BaseModel):
    """
    Gera uma fatura a partir de um ou mais lotes já FECHADOS do MESMO
    convênio — mesma tela "Gera Arquivo - TISS"/"Faturamento Emitido" de
    um ERP real, onde Lote e Fatura são seleções relacionadas mas
    independentes (uma fatura pode juntar lotes de tipos diferentes do
    mesmo convênio, fechados na mesma janela de envio).
    """

    lote_ids: list[UUID] = Field(min_length=1)
    serie: str | None = None
    numero: str | None = None


class FaturaSettleRequest(BaseModel):
    """Baixa da fatura — o outro lado do cruzamento: quanto a operadora
    de fato pagou. `is_partial=True` marca status='parcialmente_paga' em
    vez de 'paga' (glosa parcial é comum — a operadora paga menos do que
    a fatura pedia)."""

    valor_recebido: float = Field(gt=0)
    is_partial: bool = False


class FaturaResponse(BaseModel):
    id: UUID
    insurance_plan_id: UUID
    serie: str | None
    numero: str | None
    status: str
    data_emissao: datetime
    valor_recebido: float | None
    data_recebimento: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
