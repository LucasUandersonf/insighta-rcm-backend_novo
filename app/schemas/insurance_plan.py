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


class InsurancePlanUpdateRequest(BaseModel):
    """PATCH parcial — hoje só serve para desativar/reativar
    (`is_active`), mesmo padrão de InsuranceCompanyUpdateRequest/
    ProfessionalUpdateRequest: "excluir" um plano cadastrado errado ou
    duplicado sem quebrar as FKs de Contract/Appointment/Billing (ver
    DECISÃO no model)."""

    is_active: bool | None = None


class InsurancePlanResponse(BaseModel):
    id: UUID
    insurance_company_id: UUID | None
    display_name: str
    normalized_key: str
    ans_registry: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
