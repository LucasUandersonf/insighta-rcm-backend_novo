from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class InsurancePlanCreateRequest(BaseModel):
    """Cadastro NOVO exige operadora quando `plan_type == "convenio"` (ver
    DECISÃO em app/sql/007_contract_intelligence.sql) — planos antigos
    sem operadora continuam existindo, mas todo cadastro feito pela tela
    a partir de agora liga o plano à sua operadora. `plan_type ==
    "particular"` é o INVERSO: um paciente particular não tem operadora
    por definição, então `insurance_company_id` deve vir vazio (ver
    DECISÃO completa em 054_insurance_plan_type.sql)."""

    insurance_company_id: UUID | None = None
    display_name: str = Field(min_length=1, max_length=255)
    ans_registry: str | None = None
    plan_type: str = Field(default="convenio", pattern="^(convenio|particular)$")

    @model_validator(mode="after")
    def validate_company_matches_plan_type(self) -> "InsurancePlanCreateRequest":
        if self.plan_type == "convenio" and self.insurance_company_id is None:
            raise ValueError("insurance_company_id é obrigatório para plan_type='convenio'.")
        if self.plan_type == "particular" and self.insurance_company_id is not None:
            raise ValueError("plan_type='particular' não pode ter insurance_company_id (paciente sem operadora).")
        return self


class InsurancePlanUpdateRequest(BaseModel):
    """PATCH parcial — hoje serve para desativar/reativar (`is_active`),
    mesmo padrão de InsuranceCompanyUpdateRequest/ProfessionalUpdateRequest:
    "excluir" um plano cadastrado errado ou duplicado sem quebrar as FKs
    de Contract/Appointment/Billing (ver DECISÃO no model). `plan_type`
    corrige um plano "de mentira" já cadastrado pra virar particular de
    verdade (ver DECISÃO em 054_insurance_plan_type.sql) — nunca
    validado contra `insurance_company_id` aqui: o plano já existe com o
    que tinha, corrigir os dois juntos por PATCH parcial é
    responsabilidade de quem está editando, não deste schema."""

    is_active: bool | None = None
    plan_type: str | None = Field(default=None, pattern="^(convenio|particular)$")


class InsurancePlanResponse(BaseModel):
    id: UUID
    insurance_company_id: UUID | None
    display_name: str
    normalized_key: str
    ans_registry: str | None
    is_active: bool
    plan_type: str
    created_at: datetime

    model_config = {"from_attributes": True}
