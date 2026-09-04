"""
app/schemas/guia.py

Guia (TISS) — ver DECISÃO completa em app/models/guia.py e
app/sql/015_billing_guia.sql.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.guia import GUIA_TIPOS


class GuiaCreateRequest(BaseModel):
    insurance_plan_id: UUID
    tipo: str
    numero: str | None = None
    senha: str | None = None
    senha_validade: date | None = None
    tabela_procedimento: str | None = None

    @field_validator("tipo")
    @classmethod
    def validate_tipo(cls, v: str) -> str:
        if v not in GUIA_TIPOS:
            raise ValueError(f"tipo deve ser um de: {', '.join(GUIA_TIPOS)}")
        return v


class GuiaUpdateRequest(BaseModel):
    """
    PATCH parcial — só aplica campos explicitamente enviados (mesmo
    padrão de ProfessionalUpdateRequest/InsuranceCompanyUpdateRequest).
    `tipo`/`insurance_plan_id` não são editáveis aqui de propósito: uma
    guia já emitida não deveria "trocar" de tipo ou de convênio — isso é
    cadastro errado, cenário para excluir/recriar, não para editar.
    """

    numero: str | None = None
    senha: str | None = None
    senha_validade: date | None = None
    tabela_procedimento: str | None = None


class GuiaResponse(BaseModel):
    id: UUID
    insurance_plan_id: UUID
    tipo: str
    numero: str | None
    senha: str | None
    senha_validade: date | None
    tabela_procedimento: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
