"""
app/services/webhook_subscription_service.py — CRUD da Central de
Integrações & Webhooks (sentido OUTBOUND). Ver DECISÃO completa em
app/sql/025_webhook_subscriptions.sql.
"""
import uuid

from fastapi import HTTPException, status

from app.core.security import generate_webhook_secret
from app.models.webhook_subscription import WebhookSubscription
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.schemas.webhook_subscription import (
    WebhookSubscriptionCreatedResponse,
    WebhookSubscriptionCreateRequest,
    WebhookSubscriptionResponse,
    WebhookSubscriptionUpdateRequest,
)


class WebhookSubscriptionService:
    def __init__(self, repo: WebhookSubscriptionRepository):
        self.repo = repo

    async def list_subscriptions(self) -> list[WebhookSubscriptionResponse]:
        subs = await self.repo.list_all()
        return [WebhookSubscriptionResponse.model_validate(s) for s in subs]

    async def create_subscription(
        self, tenant_id: str, created_by: uuid.UUID, data: WebhookSubscriptionCreateRequest
    ) -> WebhookSubscriptionCreatedResponse:
        secret = generate_webhook_secret()
        subscription = await self.repo.add(
            WebhookSubscription(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                name=data.name,
                url=data.url,
                secret=secret,
                event_types=data.event_types,
                active=data.active,
                created_by=created_by,
            )
        )
        return WebhookSubscriptionCreatedResponse(
            id=subscription.id,
            name=subscription.name,
            url=subscription.url,
            event_types=subscription.event_types,
            active=subscription.active,
            created_at=subscription.created_at,
            secret=secret,
        )

    async def update_subscription(
        self, subscription_id: uuid.UUID, data: WebhookSubscriptionUpdateRequest
    ) -> WebhookSubscriptionResponse:
        subscription = await self._get_or_404(subscription_id)
        if data.name is not None:
            subscription.name = data.name
        if data.url is not None:
            subscription.url = data.url
        if data.event_types is not None:
            subscription.event_types = data.event_types
        if data.active is not None:
            subscription.active = data.active
        await self.repo.save(subscription)
        return WebhookSubscriptionResponse.model_validate(subscription)

    async def delete_subscription(self, subscription_id: uuid.UUID) -> None:
        subscription = await self._get_or_404(subscription_id)
        await self.repo.delete(subscription)

    async def _get_or_404(self, subscription_id: uuid.UUID) -> WebhookSubscription:
        subscription = await self.repo.get_by_id(subscription_id)
        if subscription is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura de webhook não encontrada.")
        return subscription
