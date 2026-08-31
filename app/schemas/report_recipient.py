"""
app/schemas/report_recipient.py

`report_types` aceita string livre de propósito: hoje o único tipo
conhecido é "weekly_summary" (ver app/worker/weekly_report_job.py), mas
não existe um catálogo/enum de tipos de relatório em nenhum outro lugar
do sistema (grep em app/worker/ e app/api/v1/endpoints/reports.py não
acha nada parecido com uma constante `REPORT_TYPES`). Restringir a um
enum fechado aqui obrigaria a alterar este schema toda vez que um novo
tipo de relatório for criado — pior do que validar só o formato (string
não-vazia) e deixar o catálogo de valores "vivo" no dado em si.
"""
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

_REPORT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _normalize_report_types(values: list[str]) -> list[str]:
    normalized = []
    for v in values:
        v = v.strip().lower()
        if not v:
            continue
        if not _REPORT_TYPE_RE.match(v):
            raise ValueError(
                f"'{v}' não é um tipo de relatório válido — use apenas letras minúsculas, números e '_' (ex: 'weekly_summary')."
            )
        normalized.append(v)
    return normalized


class ReportRecipientCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    phone_whatsapp: str | None = None
    email: str | None = None
    # Vazio = recebe todos os tipos de relatório (ver DECISÃO em
    # app/sql/009_report_recipients.sql).
    report_types: list[str] = Field(default_factory=list)
    active: bool = True

    @field_validator("report_types")
    @classmethod
    def _validate_report_types(cls, v: list[str]) -> list[str]:
        return _normalize_report_types(v)

    @model_validator(mode="after")
    def _require_contact(self) -> "ReportRecipientCreateRequest":
        if not self.phone_whatsapp and not self.email:
            raise ValueError("Informe ao menos um contato: phone_whatsapp ou email.")
        return self


class ReportRecipientUpdateRequest(BaseModel):
    """PATCH — todos os campos opcionais; o que não vier no payload não é
    alterado. A validação "pelo menos um contato" é reaplicada no service
    sobre o ESTADO FINAL (depois de aplicar o patch), não sobre este
    payload isolado — um PATCH que só troca `name` não pode acidentalmente
    ficar impune se phone/email já eram ambos nulos (não deveria ser
    possível, mas defesa em profundidade)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone_whatsapp: str | None = None
    email: str | None = None
    report_types: list[str] | None = None
    active: bool | None = None

    @field_validator("report_types")
    @classmethod
    def _validate_report_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _normalize_report_types(v)


class ReportRecipientResponse(BaseModel):
    id: UUID
    name: str
    phone_whatsapp: str | None
    email: str | None
    report_types: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
