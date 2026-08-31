"""
app/repositories/webhook_repository.py

Mesma técnica de idempotência do ingestion_repository.py, aplicada a
eventos de webhook: a Meta reenvia um evento se não receber 200 a tempo,
então o mesmo external_event_id pode chegar mais de uma vez.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class WebhookRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_event_if_new(
        self, *, tenant_id: uuid.UUID, source: str, external_event_id: str, payload: dict
    ) -> bool:
        """Retorna True se o evento era novo e foi gravado; False se já existia (duplicata)."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO core.marketing_webhook_events
                    (id, tenant_id, source, external_event_id, payload)
                VALUES
                    (:id, :tenant_id, :source, :external_event_id, CAST(:payload AS JSONB))
                ON CONFLICT (tenant_id, source, external_event_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "source": source,
                "external_event_id": external_event_id,
                "payload": _to_json(payload),
            },
        )
        return result.scalar_one_or_none() is not None


def _to_json(payload: dict) -> str:
    import json

    return json.dumps(payload)
