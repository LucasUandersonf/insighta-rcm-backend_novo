"""
app/schemas/webhook_delivery.py — visibilidade operacional da fila de
retentativa (ver app/sql/028_webhook_delivery_queue.sql). Somente
leitura: não existe um "reenviar agora" nesta v1 — o worker já cobre
isso automaticamente dentro da janela de backoff.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class WebhookDeliveryEntryResponse(BaseModel):
    id: UUID
    subscription_id: UUID
    event_type: str
    status: str
    attempt_count: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
