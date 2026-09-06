"""
app/schemas/webhook_subscription.py

`event_types` segue a MESMA decisão de report_recipient.py: string livre
validada só no formato (não um enum fechado) — o catálogo de eventos
que a plataforma pode disparar ainda está crescendo (ver DECISÃO em
app/services/webhook_dispatch_service.py) e travar num enum aqui
obrigaria mexer neste schema a cada evento novo.
"""
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.]*$")


def _normalize_event_types(values: list[str]) -> list[str]:
    normalized = []
    for v in values:
        v = v.strip().lower()
        if not v:
            continue
        if not _EVENT_TYPE_RE.match(v):
            raise ValueError(
                f"'{v}' não é um tipo de evento válido — use apenas letras minúsculas, números, '_' e '.' (ex: 'billing.held_for_review')."
            )
        normalized.append(v)
    return normalized


def _validate_https_url(v: str) -> str:
    # Só HTTPS: o corpo de cada entrega carrega dado operacional (nunca
    # PII/clínico, ver DECISÃO em webhook_dispatch_service.py) assinado
    # por HMAC — a assinatura prova AUTENTICIDADE, não CONFIDENCIALIDADE;
    # HTTP puro deixaria o corpo visível em trânsito.
    if not v.startswith("https://"):
        raise ValueError("A URL do webhook precisa ser HTTPS.")
    return v


class WebhookSubscriptionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1)
    # Vazio = recebe todos os tipos de evento (mesma convenção de
    # report_recipients.report_types).
    event_types: list[str] = Field(default_factory=list)
    active: bool = True

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return _validate_https_url(v)

    @field_validator("event_types")
    @classmethod
    def _validate_event_types(cls, v: list[str]) -> list[str]:
        return _normalize_event_types(v)


class WebhookSubscriptionUpdateRequest(BaseModel):
    """PATCH — todos os campos opcionais; o que não vier no payload não é
    alterado. Não permite trocar o `secret` (regenerar é sempre um
    recurso à parte, não um PATCH silencioso de um campo entre outros)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=1)
    event_types: list[str] | None = None
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        return None if v is None else _validate_https_url(v)

    @field_validator("event_types")
    @classmethod
    def _validate_event_types(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _normalize_event_types(v)


class WebhookSubscriptionResponse(BaseModel):
    """Nunca carrega `secret` — mesma lógica de ApiKeyResponse (nunca
    reexibe o segredo depois da criação)."""

    id: UUID
    name: str
    url: str
    event_types: list[str]
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookSubscriptionCreatedResponse(WebhookSubscriptionResponse):
    """Só a resposta de CRIAÇÃO carrega o segredo em texto puro — o
    cliente precisa dele UMA VEZ, para configurar a verificação de
    assinatura do próprio lado (ex: no Zapier/Make, ou no código que
    recebe o webhook)."""

    secret: str
