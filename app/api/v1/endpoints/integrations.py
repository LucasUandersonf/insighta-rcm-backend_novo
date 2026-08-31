"""app/api/v1/endpoints/integrations.py — Central de Operações e Dados:
Tela de Integrações & Webhooks. Permite ao cliente emitir/revogar chaves
de API que o ERP dele usa para autenticar contra os endpoints de
ingestão desta plataforma, sem customização manual por tenant."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.api_key_repository import ApiKeyRepository
from app.schemas.integration import ApiKeyCreatedResponse, ApiKeyCreateRequest, ApiKeyResponse
from app.services.api_key_service import ApiKeyService

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Emitir/revogar credencial de integração é decisão administrativa (dado
# sensível: quem tem essa chave pode empurrar dados de faturamento para
# dentro do tenant) — mesmo critério de _CAN_WRITE em contracts.py.
_CAN_MANAGE = ("owner", "admin")


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
