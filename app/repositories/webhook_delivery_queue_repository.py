"""
app/repositories/webhook_delivery_queue_repository.py — ver DECISÃO
completa em app/sql/028_webhook_delivery_queue.sql. Sessão sempre
tenant-aware (RLS normal, diferente dos repositórios platform_*).
"""
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_delivery_queue import WebhookDeliveryQueueEntry


class WebhookDeliveryQueueRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(
        self,
        *,
        tenant_id: uuid.UUID,
        subscription_id: uuid.UUID,
        event_type: str,
        payload: dict,
        next_attempt_at: datetime,
        error: str,
    ) -> WebhookDeliveryQueueEntry:
        entry = WebhookDeliveryQueueEntry(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempt_count=1,
            next_attempt_at=next_attempt_at,
            last_error=error,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_due(self, *, now: datetime) -> list[WebhookDeliveryQueueEntry]:
        stmt = select(WebhookDeliveryQueueEntry).where(
            WebhookDeliveryQueueEntry.status == "pending",
            WebhookDeliveryQueueEntry.next_attempt_at <= now,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_recent(self, *, limit: int = 50) -> list[WebhookDeliveryQueueEntry]:
        """Alimenta a visibilidade operacional (GET /integrations/webhooks/deliveries)
        — mais recentes primeiro, sem filtro de status: pendente, entregue
        e desistido são todos relevantes para quem está diagnosticando
        um problema de integração."""
        stmt = select(WebhookDeliveryQueueEntry).order_by(WebhookDeliveryQueueEntry.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_delivered(self, entry: WebhookDeliveryQueueEntry, *, now: datetime) -> None:
        entry.status = "delivered"
        entry.last_error = None
        entry.updated_at = now
        await self.session.flush()

    async def mark_retry(self, entry: WebhookDeliveryQueueEntry, *, next_attempt_at: datetime, error: str, now: datetime) -> None:
        entry.attempt_count += 1
        entry.next_attempt_at = next_attempt_at
        entry.last_error = error
        entry.updated_at = now
        await self.session.flush()

    async def mark_failed(self, entry: WebhookDeliveryQueueEntry, *, error: str, now: datetime, attempt_count: int | None = None) -> None:
        """Desiste do episódio depois de esgotar as tentativas (ver
        _MAX_ATTEMPTS em webhook_dispatch_service.py) OU porque a
        assinatura foi removida/desativada no meio do caminho.
        `attempt_count` só é passado no primeiro caso — no segundo, a
        contagem de tentativas fica como estava (nenhuma tentativa nova
        foi feita)."""
        entry.status = "failed"
        entry.last_error = error
        entry.updated_at = now
        if attempt_count is not None:
            entry.attempt_count = attempt_count
        await self.session.flush()
