from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class BillingCreateRequest(BaseModel):
    """
    Schema de ENTRADA. Note que `tenant_id` NÃO existe aqui — o tenant
    nunca vem do corpo da requisição, sempre do JWT (ver deps.py). Isso
    impede o ataque clássico de mass assignment "faturar em nome de outro
    tenant" mesmo que o cliente tente enviar esse campo (ele seria
    silenciosamente ignorado pelo Pydantic por não estar declarado).
    """

    appointment_id: UUID
    insurance_plan_id: UUID
    charged_value: float = Field(gt=0, description="Valor cobrado, deve ser positivo")
    # Guia TISS à qual este lançamento pertence (ver app/models/guia.py) —
    # opcional: nem todo fluxo de faturamento manual já tem guia gerada
    # no momento da cobrança.
    guia_id: UUID | None = None

    @field_validator("charged_value")
    @classmethod
    def limit_precision(cls, v: float) -> float:
        # Sanitização/validação estrita: evita valores com precisão maluca
        # (ex: 150.999999999) chegando ao banco.
        return round(v, 2)


class BillingResponse(BaseModel):
    """Schema de SAÍDA — expõe só o que o frontend precisa, nunca o ORM cru."""

    id: UUID
    appointment_id: UUID
    charged_value: float
    status: str
    denial_risk_level: str
    denial_reasons: list[str]
    value_saved_by_correction: float
    received_value: float | None
    settled_at: datetime | None
    guia_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}  # permite construir a partir do ORM model


class BillingSettleRequest(BaseModel):
    """Liquidação do lote — Módulo de Taxas, Custos e Repasses: registra
    quanto a operadora efetivamente pagou, para o dashboard cruzar contra
    o valor contratado (Divergência de Recebimento)."""

    received_value: float = Field(gt=0, description="Valor efetivamente repassado pela operadora")

    @field_validator("received_value")
    @classmethod
    def limit_precision(cls, v: float) -> float:
        return round(v, 2)
