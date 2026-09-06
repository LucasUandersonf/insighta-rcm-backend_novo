"""
app/repositories/webhook_subscription_repository.py

Repositório recebe a `session` já tenant-aware — RLS garante isolamento
(mesmo padrão de report_recipient_repository.py, que esta classe espelha
de propósito: mesma forma "N assinaturas por tenant, evento vazio =
todos").
"""
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_subscription import WebhookSubscription


class WebhookSubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self) -> list[WebhookSubscription]:
        stmt = select(WebhookSubscription).order_by(WebhookSubscription.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, subscription_id: uuid.UUID) -> WebhookSubscription | None:
        stmt = select(WebhookSubscription).where(WebhookSubscription.id == subscription_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_for_event(self, event_type: str) -> list[WebhookSubscription]:
        """Assinaturas ATIVAS elegíveis para `event_type` — mesma lógica
        de ReportRecipientRepository.list_for_report_type: `event_types
        = '{}'` (vazio) é o curinga "todos os eventos"."""
        stmt = select(WebhookSubscription).where(
            WebhookSubscription.active.is_(True),
            or_(
                WebhookSubscription.event_types == [],
                WebhookSubscription.event_types.any(event_type),
            ),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, subscription: WebhookSubscription) -> WebhookSubscription:
        self.session.add(subscription)
        await self.session.flush()
        return subscription

    async def save(self, subscription: WebhookSubscription) -> WebhookSubscription:
        await self.session.flush()
        return subscription

    async def delete(self, subscription: WebhookSubscription) -> None:
        await self.session.delete(subscription)
        await self.session.flush()
