from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InsuranceCompanyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    ans_registry: str | None = None
    # Ver DECISÃO em app/sql/008_denial_appeals.sql — prazo CONTRATUAL,
    # não uma lei federal única. Opcional: quando o tenant ainda não sabe
    # o número exato, o backend usa o fallback genérico ao calcular o
    # prazo de um recurso (nunca fica sem prazo).
    default_appeal_deadline_days: int | None = Field(default=None, gt=0)


class InsuranceCompanyUpdateRequest(BaseModel):
    """PATCH — hoje só existe para corrigir o prazo de recurso depois de
    conferir o contrato real (o cadastro inicial costuma vir sem esse
    dado à mão)."""

    default_appeal_deadline_days: int | None = Field(default=None, gt=0)


class InsuranceCompanyResponse(BaseModel):
    id: UUID
    name: str
    ans_registry: str | None
    default_appeal_deadline_days: int | None
    created_at: datetime

    model_config = {"from_attributes": True}
