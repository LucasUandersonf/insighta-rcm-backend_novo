"""
app/schemas/lote.py

Lote — ver DECISÃO completa em app/models/lote.py e
app/sql/016_lotes_faturas.sql.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.models.guia import GUIA_TIPOS


class LoteCreateRequest(BaseModel):
    insurance_plan_id: UUID
    tipo: str

    @field_validator("tipo")
    @classmethod
    def validate_tipo(cls, v: str) -> str:
        if v not in GUIA_TIPOS:
            raise ValueError(f"tipo deve ser um de: {', '.join(GUIA_TIPOS)}")
        return v


class LoteResponse(BaseModel):
    id: UUID
    insurance_plan_id: UUID
    tipo: str
    status: str
    fatura_id: UUID | None
    closed_at: datetime | None
    created_at: datetime
    # Achado do Parecer Técnico "Boletim Insighta" (revisão 2): campo
    # calculado (não uma coluna do model Lote), preenchido à mão pelo
    # service a partir de GuiaRepository.count_by_lote_ids — a tela de
    # gestão de Lotes precisa mostrar isso na listagem sem N+1 query.
    guias_count: int = 0

    model_config = {"from_attributes": True}
