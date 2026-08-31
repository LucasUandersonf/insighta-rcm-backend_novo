from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: int
    actor_user_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID
    diff: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogFilters(BaseModel):
    """Não usado como corpo de request (os filtros chegam por query
    param no endpoint) — existe só para documentar, num único lugar, o
    conjunto de filtros aceitos por `AuditLogRepository.list_paginated`."""

    entity_type: str | None = None
    action: str | None = None
    date_from: date | None = None
    date_to: date | None = None
