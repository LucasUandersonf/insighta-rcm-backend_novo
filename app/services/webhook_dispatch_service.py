"""
app/services/webhook_dispatch_service.py — Motor de disparo dos webhooks
OUTBOUND (ver app/sql/025_webhook_subscriptions.sql e
app/services/webhook_subscription_service.py, que é só o CRUD).

DECISÃO — catálogo de eventos ainda não é fechado
-------------------------------------------------------------------------
`event_types` aceita qualquer string no formato "dominio.evento" (ver
app/schemas/webhook_subscription.py) porque o conjunto de eventos que a
plataforma dispara ainda está crescendo. O primeiro evento real ligado a
este motor é "billing.held_for_review" (BillingService.create_billing) —
um faturamento que o motor de risco de glosa decidiu segurar para revisão
manual é, por natureza, algo que o cliente quer saber IMEDIATAMENTE numa
ferramenta que ele já usa (Slack, o próprio CRM/ERP), não só ao abrir o
painel depois.

DECISÃO — falha de entrega NUNCA quebra a operação que disparou o evento
-------------------------------------------------------------------------
Criar um faturamento não pode falhar porque o Slack do cliente está fora
do ar, ou porque a URL cadastrada mudou e agora só retorna 404.
`dispatch_event()` captura toda exceção POR ASSINATURA individualmente
(uma URL problemática não impede a entrega às demais) e nunca propaga
para o chamador — mesmo espírito de support_request_service.py (e-mail
best-effort) e do monitoramento via Sentry já usado no restante do
sistema (ver app/worker/daily_alert_job.py).

DECISÃO — nunca PII/dado clínico no corpo
-------------------------------------------------------------------------
O payload de cada evento carrega só identificadores e metadados
operacionais (ids, status, valores agregados) — nunca nome de paciente,
CPF etc. Mesma regra de AuditLogRepository.record (diff nunca carrega
PII/financeiro sensível) — aqui o motivo é ainda mais forte: o corpo
trafega para um servidor de TERCEIROS escolhido pelo cliente, fora do
nosso controle.

DECISÃO — timeout curto, sem retry
-------------------------------------------------------------------------
Um webhook não pode travar a operação que o disparou por muito além do
razoável, e um provedor fora do ar agora provavelmente continua fora do
ar num retry imediato dentro da MESMA requisição — fila/backoff de retry
de verdade fica fora do escopo desta rodada (ver PRODUCAO_CHECKLIST.md).
"""
import json
import logging

import httpx
import sentry_sdk

from app.core.config import get_settings
from app.core.security import sign_webhook_payload
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository

logger = logging.getLogger(__name__)
settings = get_settings()

_DELIVERY_TIMEOUT_SECONDS = 5.0


async def dispatch_event(repo: WebhookSubscriptionRepository, *, event_type: str, payload: dict) -> None:
    """Entrega `payload` a toda assinatura ATIVA elegível para `event_type`
    neste tenant (RLS da sessão do `repo` já garante isso). Nunca lança —
    ver DECISÃO acima."""
    subscriptions = await repo.list_active_for_event(event_type)
    if not subscriptions:
        return

    # default=str cobre UUID/Decimal/datetime sem exigir que o chamador
    # pré-serialize o payload — mesma preocupação prática de outros pontos
    # do sistema que serializam para fora do ORM.
    body_bytes = json.dumps({"event_type": event_type, "data": payload}, default=str, sort_keys=True).encode("utf-8")

    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
        for subscription in subscriptions:
            signature = sign_webhook_payload(payload=body_bytes, secret=subscription.secret)
            headers = {"Content-Type": "application/json", "X-Insighta-Signature": signature}
            try:
                response = await client.post(subscription.url, content=body_bytes, headers=headers)
                if response.status_code >= 400:
                    logger.warning(
                        "Entrega de webhook recusada pelo destino (status %s): subscription=%s event=%s",
                        response.status_code,
                        subscription.id,
                        event_type,
                    )
            except Exception as exc:  # httpx lança tipos variados de erro de rede/timeout/DNS
                logger.warning(
                    "Falha ao entregar webhook: subscription=%s event=%s erro=%s", subscription.id, event_type, exc
                )
                if settings.SENTRY_DSN:
                    sentry_sdk.set_tag("webhook_subscription_id", str(subscription.id))
                    sentry_sdk.capture_exception(exc)
