from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.text_utils import normalize_item_type, sanitize_member_card_value


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
    # Achado do Dicionário de Dados: multiplica ContractItem.agreed_price
    # na comparação de risco (ver denial_risk_engine.assess). default=1
    # cobre o caso comum (uma unidade do procedimento).
    quantity: int = Field(default=1, gt=0, le=1000)
    member_card_number: str | None = None
    item_type: str | None = None
    coparticipation_value: float | None = Field(default=None, ge=0)

    @field_validator("charged_value")
    @classmethod
    def limit_precision(cls, v: float) -> float:
        # Sanitização/validação estrita: evita valores com precisão maluca
        # (ex: 150.999999999) chegando ao banco.
        return round(v, 2)

    @field_validator("member_card_number")
    @classmethod
    def sanitize_member_card(cls, v: str | None) -> str | None:
        # Achado 10 da Auditoria de Templates e Insights (alto) — este
        # endpoint grava a MESMA coluna (Billing.member_card_number) que
        # o Template de Faturamento (ingestão em massa) já normaliza
        # desde o Achado 1. Sem isso, um lançamento manual com a
        # carteirinha formatada diferente nunca casaria com um
        # demonstrativo de Glosa que chegasse depois — a mesma falha
        # silenciosa do Achado 1, só que pela porta manual.
        if v is None or v == "":
            return None
        return sanitize_member_card_value(v)

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, v: str | None) -> str | None:
        # Achado 11 da Auditoria (baixo) — sem isso, um valor fora do
        # vocabulário fechado (ver ITEM_TYPE_VALUES, app/models/billing.py)
        # passava batido pelo Pydantic e só era pego pelo CHECK constraint
        # do banco, virando um 500 opaco em vez de um 422 claro. Mesma
        # função que o Template de Faturamento usa — um vocabulário só,
        # nunca duas listas que podem divergir. Nome diferente do import
        # (`normalize_item_type`) de propósito, para não sombrear a
        # função do módulo dentro da classe.
        return normalize_item_type(v)


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


class BillingSearchItem(BaseModel):
    """Linha de resultado de GET /billing/search — só os campos que
    ajudam um humano a reconhecer QUAL faturamento é o certo (paciente,
    procedimento, convênio, valor) sem precisar saber o UUID de cor.
    Ver DECISÃO em BillingRepository.search."""

    id: UUID
    patient_name: str
    procedure_code: str | None
    insurance_plan_name: str
    charged_value: float
    status: str
    denial_risk_level: str
    created_at: datetime


class BillingSettleRequest(BaseModel):
    """Liquidação do lote — Módulo de Taxas, Custos e Repasses: registra
    quanto a operadora efetivamente pagou, para o dashboard cruzar contra
    o valor contratado (Divergência de Recebimento)."""

    received_value: float = Field(gt=0, description="Valor efetivamente repassado pela operadora")

    @field_validator("received_value")
    @classmethod
    def limit_precision(cls, v: float) -> float:
        return round(v, 2)
