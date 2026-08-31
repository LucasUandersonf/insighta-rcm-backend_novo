from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InsurancePlanCreateRequest(BaseModel):
    """Cadastro NOVO exige operadora (ver DECISÃO em
    app/sql/007_contract_intelligence.sql) — planos antigos sem operadora
    continuam existindo, mas todo cadastro feito pela tela a partir de
    agora liga o plano à sua operadora."""

    insurance_company_id: UUID
    display_name: str = Field(min_length=1, max_length=255)
    ans_registry: str | None = None


class InsurancePlanResponse(BaseModel):
    id: UUID
    insurance_company_id: UUID | None
    display_name: str
    normalized_key: str
    ans_registry: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
