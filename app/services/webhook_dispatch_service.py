"""
app/services/webhook_dispatch_service.py — Motor de disparo dos webhooks
OUTBOUND (ver app/sql/025_webhook_subscriptions.sql e
app/services/webhook_subscription_service.py, que é só o CRUD).

DECISÃO — catálogo de eventos ainda não é fechado
-------------------------------------------------------------------------
`event_types` aceita qualquer string no formato "dominio.evento" (ver
app/schemas/webhook_subscription.py) porque o conjunto de eventos que a
plataforma dispara ainda está crescendo. Eventos ligados até agora, todos
disparados a partir de um ÚNICO registro criado/alterado via endpoint
normal (nunca de um path de ingestão em lote — ver DECISÃO em
AppointmentService.webhook_repo sobre por que):

  - "billing.held_for_review" (BillingService.create_billing) — um
    faturamento que o motor de risco de glosa decidiu segurar para
    revisão manual é algo que o cliente quer saber IMEDIATAMENTE numa
    ferramenta que ele já usa, não só ao abrir o painel depois.
  - "denial_appeal.resolved" (DenialAppealService.resolve_appeal) —
    dispara nas três transições possíveis (deferido/indeferido/
    nip_aberta), não só nas terminais.
  - "no_show_risk.high" (AppointmentService.create_appointment) — só no
    nível "alto", não em todo agendamento criado.

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

DECISÃO — fila de retentativa com backoff exponencial (ver
app/sql/028_webhook_delivery_queue.sql)
-------------------------------------------------------------------------
`dispatch_event()` ainda tenta entregar UMA VEZ, na hora — isso não
mudou (a operação que disparou o evento não pode esperar retries dentro
da mesma requisição). O que mudou: quando essa tentativa imediata falha,
em vez de só logar e esquecer, o evento é ENFILEIRADO
(core.webhook_delivery_queue) para o worker (webhook_retry_job.py)
tentar de novo mais tarde, com backoff crescente — 1m, 5m, 30m, 2h, 6h
(`_RETRY_BACKOFF_SECONDS`), 6 tentativas no total contando a imediata
(`_MAX_ATTEMPTS`). Depois de esgotar as tentativas, desiste (linha vira
`status='failed'`) e reporta ao Sentry — nunca fica reagendando pra
sempre uma URL que claramente não existe mais.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import sentry_sdk

from app.core.config import get_settings
from app.core.security import sign_webhook_payload
from app.models.webhook_delivery_queue import WebhookDeliveryQueueEntry
from app.repositories.webhook_delivery_queue_repository import WebhookDeliveryQueueRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository

logger = logging.getLogger(__name__)
settings = get_settings()

_DELIVERY_TIMEOUT_SECONDS = 5.0

# Delay ANTES de cada retentativa (índice 0 = antes da 2ª tentativa,
# índice 1 = antes da 3ª, ...). _MAX_ATTEMPTS = len(...) + 1 porque a
# primeira tentativa (a imediata, dentro de dispatch_event) não gasta
# nenhum item desta lista.
_RETRY_BACKOFF_SECONDS = [60, 300, 1800, 7200, 21600]  # 1m, 5m, 30m, 2h, 6h
_MAX_ATTEMPTS = len(_RETRY_BACKOFF_SECONDS) + 1


def _next_retry_delay_seconds(attempt_count: int) -> int:
    """`attempt_count` = quantas tentativas já foram feitas (contando a
    que acabou de falhar). Só é chamado quando `attempt_count < _MAX_ATTEMPTS`
    — o chamador decide desistir antes de chegar aqui fora desse caso."""
    return _RETRY_BACKOFF_SECONDS[attempt_count - 1]


def _build_delivery(event_type: str, payload: dict, secret: str) -> tuple[bytes, dict[str, str]]:
    # default=str cobre UUID/Decimal/datetime sem exigir que o chamador
    # pré-serialize o payload — mesma preocupação prática de outros pontos
    # do sistema que serializam para fora do ORM.
    body_bytes = json.dumps({"event_type": event_type, "data": payload}, default=str, sort_keys=True).encode("utf-8")
    signature = sign_webhook_payload(payload=body_bytes, secret=secret)
    headers = {"Content-Type": "application/json", "X-Insighta-Signature": signature}
    return body_bytes, headers


def _json_safe(payload: dict) -> dict:
    """`payload` pode carregar UUID/Decimal/datetime (o chamador nunca
    precisa pré-serializar, ver _build_delivery acima) — mas a coluna
    JSONB de core.webhook_delivery_queue exige tipos nativos de JSON.
    Um round-trip json.dumps(default=str)/json.loads resolve isso sem
    duplicar a lógica de serialização em dois lugares."""
    return json.loads(json.dumps(payload, default=str, sort_keys=True))


async def _attempt_delivery(client: httpx.AsyncClient, url: str, body_bytes: bytes, headers: dict[str, str]) -> str | None:
    """None em sucesso; mensagem de erro curta em falha. Nunca lança —
    quem chama decide o que fazer com a falha (enfileirar, reagendar,
    desistir)."""
    try:
        response = await client.post(url, content=body_bytes, headers=headers)
        if response.status_code >= 400:
            return f"status {response.status_code}"
        return None
    except Exception as exc:  # httpx lança tipos variados de erro de rede/timeout/DNS
        return str(exc)


async def dispatch_event(repo: WebhookSubscriptionRepository, *, event_type: str, payload: dict) -> None:
    """Entrega `payload` a toda assinatura ATIVA elegível para `event_type`
    neste tenant (RLS da sessão do `repo` já garante isso). Nunca lança —
    ver DECISÃO acima. Falha na tentativa imediata enfileira retentativa,
    nunca perde o evento silenciosamente."""
    subscriptions = await repo.list_active_for_event(event_type)
    if not subscriptions:
        return

    queue_repo = WebhookDeliveryQueueRepository(repo.session)
    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
        for subscription in subscriptions:
            body_bytes, headers = _build_delivery(event_type, payload, subscription.secret)
            error = await _attempt_delivery(client, subscription.url, body_bytes, headers)
            if error is None:
                continue

            logger.warning(
                "Entrega de webhook falhou na tentativa imediata (reagendada): subscription=%s event=%s erro=%s",
                subscription.id, event_type, error,
            )
            await queue_repo.enqueue(
                tenant_id=subscription.tenant_id,
                subscription_id=subscription.id,
                event_type=event_type,
                payload=_json_safe(payload),
                next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=_next_retry_delay_seconds(1)),
                error=error,
            )


async def process_due_retries(
    queue_repo: WebhookDeliveryQueueRepository,
    subscription_repo: WebhookSubscriptionRepository,
    *,
    now: datetime | None = None,
) -> list[WebhookDeliveryQueueEntry]:
    """Chamado pelo worker (app/worker/webhook_retry_job.py), uma vez por
    tenant (a sessão por trás dos dois repositórios é tenant-aware — RLS
    garante que só vê a fila do tenant setado nela). Retorna as entradas
    processadas nesta chamada, só para o job logar um resumo."""
    now = now or datetime.now(timezone.utc)
    due = await queue_repo.list_due(now=now)
    if not due:
        return []

    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
        for entry in due:
            subscription = await subscription_repo.get_by_id(entry.subscription_id)
            if subscription is None or not subscription.active:
                # Assinatura removida/desativada entre a falha original e
                # agora — não há mais para onde entregar; desiste sem
                # gastar mais tentativas numa URL que ninguém mais quer.
                await queue_repo.mark_failed(entry, error="assinatura removida ou desativada", now=now)
                continue

            body_bytes, headers = _build_delivery(entry.event_type, entry.payload, subscription.secret)
            error = await _attempt_delivery(client, subscription.url, body_bytes, headers)
            if error is None:
                await queue_repo.mark_delivered(entry, now=now)
                continue

            new_attempt_count = entry.attempt_count + 1
            if new_attempt_count >= _MAX_ATTEMPTS:
                logger.error(
                    "Desistindo da entrega de webhook após %d tentativas: subscription=%s event=%s erro=%s",
                    new_attempt_count, subscription.id, entry.event_type, error,
                )
                if settings.SENTRY_DSN:
                    sentry_sdk.set_tag("webhook_subscription_id", str(subscription.id))
                    sentry_sdk.capture_message(
                        f"Webhook desistido após {new_attempt_count} tentativas: {subscription.url} ({entry.event_type})",
                        level="error",
                    )
                await queue_repo.mark_failed(entry, error=error, now=now, attempt_count=new_attempt_count)
            else:
                delay = _next_retry_delay_seconds(new_attempt_count)
                await queue_repo.mark_retry(entry, next_attempt_at=now + timedelta(seconds=delay), error=error, now=now)

    return due
