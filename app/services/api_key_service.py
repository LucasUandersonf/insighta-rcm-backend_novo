import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.core.security import generate_api_key, hash_password, verify_password
from app.models.api_key import ApiKey
from app.repositories.api_key_repository import ApiKeyRepository
from app.schemas.integration import ApiKeyCreatedResponse, ApiKeyCreateRequest, ApiKeyResponse


class ApiKeyService:
    """Central de Integrações & Webhooks — chaves de API que o ERP/sistema
    do cliente usa para autenticar contra os endpoints de ingestão desta
    plataforma. Ver DECISÃO em app/sql/006_platform_admin.sql."""

    def __init__(self, repo: ApiKeyRepository):
        self.repo = repo

    async def list_keys(self) -> list[ApiKeyResponse]:
        keys = await self.repo.list_all()
        return [ApiKeyResponse.model_validate(k) for k in keys]

    async def create_key(self, tenant_id: str, created_by: str, data: ApiKeyCreateRequest) -> ApiKeyCreatedResponse:
        raw_key, prefix = generate_api_key()
        key = await self.repo.add(
            ApiKey(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                name=data.name,
                key_prefix=prefix,
                key_hash=hash_password(raw_key),
                created_by=uuid.UUID(created_by),
            )
        )
        return ApiKeyCreatedResponse(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
            api_key=raw_key,
        )

    async def revoke_key(self, api_key_id: uuid.UUID) -> ApiKeyResponse:
        key = await self.repo.get_by_id(api_key_id)
        if key is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chave de API não encontrada.")
        if key.revoked_at is None:
            key.revoked_at = datetime.now(timezone.utc)
            await self.repo.save(key)
        return ApiKeyResponse.model_validate(key)

    @staticmethod
    def verify_raw_key_against_hash(raw_key: str, key_hash: str) -> bool:
        """Usado pelo endpoint de ingestão (futuro) que autentica requests
        do ERP do cliente via API key em vez de JWT — reaproveita o mesmo
        verify_password() do argon2, já que a chave foi hasheada com o
        mesmo esquema (ver create_key acima)."""
        return verify_password(raw_key, key_hash)
