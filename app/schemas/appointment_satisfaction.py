from datetime import datetime

from pydantic import BaseModel, field_validator


class SatisfactionLinkResponse(BaseModel):
    """Resposta de POST /appointments/{id}/satisfaction-link — a
    recepção copia `url` e envia manualmente ao paciente (ver DECISÃO em
    052_appointment_satisfaction.sql sobre por que não é automático via
    WhatsApp)."""

    url: str
    expires_at: datetime


class PublicSatisfactionStatusResponse(BaseModel):
    """GET /public/satisfaction/{token} — só o suficiente para o frontend
    decidir se mostra o formulário de avaliação ou uma mensagem de "link
    inválido/expirado". Nenhum dado do paciente/clínica é exposto aqui
    de propósito: o token já prova posse do link, mas isso não deveria
    virar uma forma de descobrir dados de terceiros por tentativa."""

    valid: bool


class PublicSatisfactionSubmitRequest(BaseModel):
    score: int

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("score deve estar entre 1 e 5.")
        return v
