"""
app/schemas/denial_appeal.py

Ver DECISÃO completa em app/sql/008_denial_appeals.sql. RBAC dos
endpoints correspondentes segue o mesmo critério de contracts.py/
billing.py: dado financeiro sensível, 'atendimento' fora.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class DenialAppealCreateRequest(BaseModel):
    """Abre o expediente de contestação a partir da negativa formal
    recebida da operadora — billing_id aponta para o atendimento/lote
    que originou a glosa. `deadline_at` é OPCIONAL: se omitido, o
    backend calcula a partir de `default_appeal_deadline_days` da
    operadora do plano (ou do fallback genérico) — ver
    appeal_deadline_calculator.py."""

    billing_id: UUID
    appeal_type: str = Field(pattern="^(tecnica|administrativa|medica)$")
    operator_denial_reason: str | None = None
    denied_at: date
    deadline_at: date | None = None

    @model_validator(mode="after")
    def check_deadline_after_denial(self) -> "DenialAppealCreateRequest":
        if self.deadline_at and self.deadline_at <= self.denied_at:
            raise ValueError("deadline_at deve ser posterior a denied_at.")
        return self


class DenialAppealFileRequest(BaseModel):
    """POST /denial-appeals/{id}/file — marca que o recurso foi de fato
    protocolado junto à operadora (aberto -> protocolado)."""

    filed_at: datetime | None = None


class DenialAppealResolveRequest(BaseModel):
    """POST /denial-appeals/{id}/resolve — resposta final da operadora
    nesta instância (protocolado -> deferido/indeferido), ou a atualização
    de uma NIP já aberta."""

    status: str = Field(pattern="^(deferido|indeferido|nip_aberta)$")
    resolution_notes: str | None = None


class DenialAppealAttachmentResponse(BaseModel):
    id: UUID
    filename: str
    created_at: datetime

    model_config = {"from_attributes": True}


class DenialAppealResponse(BaseModel):
    id: UUID
    billing_id: UUID
    appeal_type: str
    operator_denial_reason: str | None
    denied_at: date
    deadline_at: date
    status: str
    filed_at: datetime | None
    resolution_notes: str | None
    resolved_at: datetime | None
    created_at: datetime
    attachments: list[DenialAppealAttachmentResponse] = []

    model_config = {"from_attributes": True}
