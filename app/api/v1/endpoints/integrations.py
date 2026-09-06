"""app/api/v1/endpoints/integrations.py — Central de Operações e Dados:
Tela de Integrações & Webhooks. Permite ao cliente emitir/revogar chaves
de API que o ERP dele usa para autenticar contra os endpoints de
ingestão desta plataforma, sem customização manual por tenant.

BUG CORRIGIDO — chaves emitidas, NUNCA verificadas em lugar nenhum
-------------------------------------------------------------------------
POST /api-keys existe desde 006_platform_admin.sql, mas nenhum endpoint
jamais autenticava uma chamada usando a chave gerada — um cliente podia
emitir uma chave na tela de Setup e ela não servia para nada. POST
/integrations/ingest (abaixo) é a peça que faltava: permite ao ERP/CRM/
script do próprio cliente empurrar um arquivo de Faturamento ou Agenda
SEM precisar de um usuário logado — só a API key, que não expira como um
JWT. Ver DECISÃO completa em app/api/api_key_auth.py e
app/sql/024_api_key_resolver.sql."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.api_key_auth import ApiKeyDbSession, ApiKeyIdentityDep
from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.webhook_delivery_queue_repository import WebhookDeliveryQueueRepository
from app.repositories.webhook_subscription_repository import WebhookSubscriptionRepository
from app.schemas.integration import ApiKeyCreatedResponse, ApiKeyCreateRequest, ApiKeyResponse
from app.schemas.ingestion import UploadIngestionFileResponse
from app.schemas.webhook_delivery import WebhookDeliveryEntryResponse
from app.schemas.webhook_subscription import (
    WebhookSubscriptionCreatedResponse,
    WebhookSubscriptionCreateRequest,
    WebhookSubscriptionResponse,
    WebhookSubscriptionUpdateRequest,
)
from app.services.api_key_service import ApiKeyService
from app.services.ingestion_processing_service import FileParsingError, process_uploaded_file
from app.services.ingestion_storage_client import IngestionStorageClient, IngestionStorageError, build_upload_key
from app.services.webhook_subscription_service import WebhookSubscriptionService

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Emitir/revogar credencial de integração (API key) ou cadastrar para
# onde a plataforma manda webhooks é decisão administrativa (dado
# sensível: uma controla quem PODE EMPURRAR dado de faturamento para
# dentro do tenant, a outra para ONDE a plataforma manda dado de fora) —
# mesmo critério de _CAN_WRITE em contracts.py.
_CAN_MANAGE = ("owner", "admin")

# Reaproveitados de ingestion.py DE PROPÓSITO (import cross-endpoint, não
# uma cópia) — a validação de formato/data_type de um upload é a MESMA
# regra não importa a porta de entrada (JWT ou API key); duplicar a
# lógica aqui divergiria silenciosamente da primeira vez que alguém
# mudasse só um dos dois lugares. Reextrair as duas rotas para um único
# service foi avaliado e descartado nesta rodada: exigiria mexer no
# endpoint JWT já testado (POST /ingestion/upload) só para acomodar este
# caminho novo — risco maior que o ganho de estilo.
from app.api.v1.endpoints.ingestion import _MAX_UPLOAD_BYTES, _VALID_DATA_TYPES, _detect_file_format  # noqa: E402


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> list[ApiKeyResponse]:
    service = ApiKeyService(ApiKeyRepository(db))
    return await service.list_keys()


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ApiKeyCreatedResponse:
    service = ApiKeyService(ApiKeyRepository(db))
    return await service.create_key(current_user.tenant_id, current_user.id, payload)


@router.delete("/api-keys/{api_key_id}", response_model=ApiKeyResponse)
async def revoke_api_key(
    api_key_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ApiKeyResponse:
    service = ApiKeyService(ApiKeyRepository(db))
    return await service.revoke_key(api_key_id)


@router.get("/webhooks", response_model=list[WebhookSubscriptionResponse])
async def list_webhooks(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> list[WebhookSubscriptionResponse]:
    service = WebhookSubscriptionService(WebhookSubscriptionRepository(db))
    return await service.list_subscriptions()


@router.post("/webhooks", response_model=WebhookSubscriptionCreatedResponse, status_code=201)
async def create_webhook(
    payload: WebhookSubscriptionCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> WebhookSubscriptionCreatedResponse:
    service = WebhookSubscriptionService(WebhookSubscriptionRepository(db))
    return await service.create_subscription(current_user.tenant_id, UUID(current_user.id), payload)


@router.get("/webhooks/deliveries", response_model=list[WebhookDeliveryEntryResponse])
async def list_webhook_deliveries(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> list[WebhookDeliveryEntryResponse]:
    """
    Visibilidade operacional da fila de retentativa (ver
    app/sql/028_webhook_delivery_queue.sql) — as 50 entregas mais
    recentes, pendentes/entregues/desistidas juntas, para o cliente
    diagnosticar "por que meu Slack não recebeu aquele aviso" sem
    precisar abrir um chamado de suporte. Rota declarada ANTES de
    /webhooks/{subscription_id} de propósito: senão "deliveries" seria
    interpretado como um subscription_id.
    """
    entries = await WebhookDeliveryQueueRepository(db).list_recent()
    return [WebhookDeliveryEntryResponse.model_validate(e) for e in entries]


@router.patch("/webhooks/{subscription_id}", response_model=WebhookSubscriptionResponse)
async def update_webhook(
    subscription_id: UUID,
    payload: WebhookSubscriptionUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> WebhookSubscriptionResponse:
    """PATCH parcial — ver DECISÃO em WebhookSubscriptionUpdateRequest
    (nunca troca `secret`; regenerar seria um recurso à parte)."""
    service = WebhookSubscriptionService(WebhookSubscriptionRepository(db))
    return await service.update_subscription(subscription_id, payload)


@router.delete("/webhooks/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    subscription_id: UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> None:
    service = WebhookSubscriptionService(WebhookSubscriptionRepository(db))
    await service.delete_subscription(subscription_id)


@router.post("/ingest", response_model=UploadIngestionFileResponse, status_code=201)
async def ingest_via_api_key(
    identity: ApiKeyIdentityDep,
    db: ApiKeyDbSession,
    file: UploadFile = File(...),
    data_type: str = Form("faturamento"),
) -> UploadIngestionFileResponse:
    """
    Mesmo pipeline de POST /ingestion/upload (claim -> parse -> save ->
    normalize — ver app/services/ingestion_processing_service.py), mas
    autenticado por `X-API-Key` em vez de JWT — pensado para o ERP/CRM/
    script do próprio cliente chamar sozinho, sem sessão de usuário
    (ver DECISÃO no topo do arquivo e em app/api/api_key_auth.py).

    - 201: arquivo novo, processado agora.
    - 200 nunca acontece aqui por design simples: diferente do endpoint
      JWT, este não reexpõe o corpo "já processado antes" com 200 — um
      reenvio idempotente do MESMO arquivo ainda assim retorna 201 com
      `already_processed=True`, porque quem chama é uma integração
      automatizada, não uma pessoa lendo o código de status na tela.
    - 401: X-API-Key ausente, inválida ou revogada.
    - 400: data_type/formato não reconhecido, arquivo vazio ou acima do limite.
    - 422: arquivo reconhecido mas estruturalmente ilegível.
    - 503: bucket de ingestão não configurado, ou falha ao falar com o S3.
    """
    if data_type not in _VALID_DATA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"data_type deve ser um de: {', '.join(_VALID_DATA_TYPES)}.",
        )

    filename = file.filename or "arquivo"
    file_format = _detect_file_format(filename, file.content_type)
    if file_format is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de arquivo não reconhecido. Envie um arquivo .csv, .xml ou .json.",
        )
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio.")
    if len(raw_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo acima do limite de 20MB.")

    try:
        storage = IngestionStorageClient()
    except IngestionStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    s3_key = build_upload_key(identity.tenant_id, data_type, file_format, filename)
    try:
        version_id = await storage.upload_bytes(key=s3_key, raw_bytes=raw_bytes)
    except Exception as exc:  # boto3 lança tipos variados de erro de rede/credencial
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Falha ao enviar o arquivo para o armazenamento: {exc}",
        ) from exc

    try:
        result = await process_uploaded_file(
            db,
            UUID(identity.tenant_id),
            s3_bucket=storage.bucket,
            s3_key=s3_key,
            s3_version_id=version_id,
            file_format=file_format,
            raw_bytes=raw_bytes,
            original_filename=filename,
            data_type=data_type,
        )
    except FileParsingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    # Marca a chave como usada só depois do processamento ter dado certo
    # — mesma sessão tenant-aware já aberta (ApiKeyDbSession), agora com
    # o tenant corretamente setado (ver DECISÃO em api_key_auth.py sobre
    # por que isto não podia acontecer na própria dependency de auth).
    api_key = await ApiKeyRepository(db).get_by_id(UUID(identity.api_key_id))
    if api_key is not None:
        api_key.last_used_at = datetime.now(timezone.utc)
        await ApiKeyRepository(db).save(api_key)

    ingestion_file = result.ingestion_file
    return UploadIngestionFileResponse(
        id=ingestion_file.id,
        file_format=ingestion_file.file_format,
        data_type=ingestion_file.data_type,
        status=ingestion_file.status,
        row_count=ingestion_file.row_count,
        error_row_count=ingestion_file.error_row_count,
        received_at=ingestion_file.received_at,
        already_processed=result.already_claimed,
        message=(
            "Este arquivo já havia sido enviado e processado anteriormente — nenhum dado novo foi importado."
            if result.already_claimed
            else None
        ),
    )
