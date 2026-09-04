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
    """PATCH parcial — só aplica os campos explicitamente enviados (ver
    DECISÃO em InsuranceCompanyService.update_company). `is_active=false`
    é a forma de "excluir" uma operadora cadastrada errado/duplicada sem
    quebrar as FKs de Contract/Appointment/Billing (ver DECISÃO no
    model) — mesmo padrão de UserUpdateRequest/ProfessionalUpdateRequest."""

    default_appeal_deadline_days: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


class InsuranceCompanyResponse(BaseModel):
    id: UUID
    name: str
    ans_registry: str | None
    default_appeal_deadline_days: int | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
