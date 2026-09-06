from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AnnouncementResponse(BaseModel):
    id: UUID
    title: str
    body: str
    published_at: datetime
    # Calculado por usuário (ver DECISÃO em AnnouncementRepository) —
    # nunca vem direto do model, sempre da query com o LEFT JOIN.
    is_read: bool

    model_config = {"from_attributes": True}


class AnnouncementListResponse(BaseModel):
    items: list[AnnouncementResponse]
    unread_count: int
