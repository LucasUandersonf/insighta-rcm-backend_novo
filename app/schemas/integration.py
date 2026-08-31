from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ApiKeyCreateRequest(BaseModel):
    name: str  # ex: "ERP TotalCare — produção", para o cliente reconhecer qual chave é qual na lista


class ApiKeyResponse(BaseModel):
    """Nunca carrega o segredo — só o suficiente para a UI listar/gerenciar."""

    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Só a resposta de CRIAÇÃO carrega o valor em texto puro — a mesma
    lógica de PasswordResetResponse (schemas/user.py): mostrado uma vez,
    nunca mais recuperável depois disso."""

    api_key: str
