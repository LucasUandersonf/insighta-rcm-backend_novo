"""
app/api/v1/endpoints/webhooks.py

Webhook do Meta Ads (Etapa 1: "Webhooks para escutar eventos de APIs do
Meta Ads"). Diferente de todo o resto da API, este endpoint NÃO usa JWT
— quem chama é a Meta, não um usuário logado. O tenant vem do PATH
(/webhooks/meta-ads/{tenant_id}), e a autenticidade da chamada é provada
pela assinatura HMAC no header X-Hub-Signature-256, verificada contra o
segredo cadastrado PARA AQUELE TENANT ESPECÍFICO (tela de Setup do
briefing: "configuração de tokens (Meta Ads)").

DECISÃO — dois passos de resolução de sessão, igual ao login
-------------------------------------------------------------------------
1) Sessão SEM tenant (get_db_no_tenant) só para ler
   tenants.meta_ads_webhook_secret — necessário porque, até validarmos a
   assinatura, não sabemos se a chamada é legítima, mas precisamos ler o
   segredo do tenant alegado pela URL para conseguir validar a assinatura
   (mesmo problema de "ovo e galinha" do login, resolvido da mesma forma:
   tenants não tem RLS, então essa leitura pontual é segura).
2) Só DEPOIS de validar a assinatura HMAC é que abrimos uma sessão
   tenant-aware (get_db_with_tenant) para gravar o evento.
Isso garante que ninguém consegue gravar um evento "como se fosse" outro
tenant só por adivinhar/enumerar um tenant_id na URL — sem o segredo
correto, a assinatura nunca bate, e a requisição é rejeitada com 401 antes
de qualquer escrita.
"""
import uuid

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, status
from sqlalchemy import select

from app.core.rate_limit import limiter
from app.core.security import verify_meta_webhook_signature
from app.db.session import get_db_no_tenant, get_db_with_tenant
from app.models.tenant import Tenant
from app.repositories.webhook_repository import WebhookRepository

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _get_tenant_webhook_secret(tenant_id: uuid.UUID) -> str | None:
    async for session in get_db_no_tenant():
        result = await session.execute(
            select(Tenant.meta_ads_webhook_secret).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        )
        return result.scalar_one_or_none()
    return None  # pragma: no cover


@router.get("/meta-ads/{tenant_id}")
async def verify_meta_webhook(
    tenant_id: uuid.UUID = Path(...),
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
) -> int:
    """
    Handshake de verificação exigido pela Meta ao cadastrar a URL do
    webhook: ela chama este GET uma vez, esperando receber de volta o
    valor de hub.challenge SE hub.verify_token bater com o segredo
    configurado para o tenant. Reaproveitamos o mesmo
    meta_ads_webhook_secret como verify_token, evitando um segundo campo
    de configuração na tela de Setup.
    """
    secret = await _get_tenant_webhook_secret(tenant_id)
    if secret is None or hub_mode != "subscribe" or hub_verify_token != secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verificação de webhook falhou.")
    return int(hub_challenge)


@router.post("/meta-ads/{tenant_id}", status_code=status.HTTP_200_OK)
@limiter.limit("60/minute")
async def receive_meta_webhook(
    request: Request,
    tenant_id: uuid.UUID = Path(...),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    raw_body = await request.body()  # bytes CRUS — obrigatório ler antes de qualquer parsing (ver security.py)

    secret = await _get_tenant_webhook_secret(tenant_id)
    if secret is None:
        # Mesma resposta genérica de sempre: não confirmamos se o tenant
        # existe ou não para quem não tem o segredo correto.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Assinatura inválida.")

    if not verify_meta_webhook_signature(payload=raw_body, signature_header=x_hub_signature_256, secret=secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Assinatura inválida.")

    body = await request.json()
    # Estrutura real de payload da Meta é aninhada (entry[].changes[]...);
    # usamos um id estável do evento para dedupe. Em produção, extrair o
    # id específico do formato de evento assinado (ex: entry[0].id +
    # entry[0].time), documentado na API de Webhooks da Meta.
    external_event_id = str(body.get("entry", [{}])[0].get("id", uuid.uuid4()))

    async for tenant_session in get_db_with_tenant(str(tenant_id)):
        repo = WebhookRepository(tenant_session)
        is_new = await repo.save_event_if_new(
            tenant_id=tenant_id, source="meta_ads", external_event_id=external_event_id, payload=body
        )
        if not is_new:
            # Reenvio da Meta (evento duplicado) — 200 OK sem reprocessar,
            # é o comportamento esperado pela própria Meta em um reenvio.
            return {"status": "duplicate_ignored"}

    # A partir daqui, o evento cru está salvo (landing zone, mesmo
    # espírito do worker de arquivos). Traduzir o payload específico do
    # Meta Ads em linhas de core.marketing_spend é trabalho da Etapa 2/3
    # (normalização), fora do escopo deste endpoint de ingestão.
    return {"status": "received"}
