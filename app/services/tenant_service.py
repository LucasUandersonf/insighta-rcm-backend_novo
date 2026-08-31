import uuid

from fastapi import HTTPException, status

from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import TenantResponse, TenantUpdateRequest


class TenantService:
    """Painel do Administrador da Empresa — dados cadastrais e plano do
    tenant. Escopo do MVP: leitura + edição de dados cadastrais. Troca de
    plano (upgrade/downgrade) fica fora deste serviço de propósito — ver
    DECISÃO em app/schemas/tenant.py (fluxo comercial, não self-service)."""

    def __init__(self, repo: TenantRepository):
        self.repo = repo

    async def get_own_tenant(self, tenant_id: str) -> TenantResponse:
        tenant = await self.repo.get_by_id(uuid.UUID(tenant_id))
        if tenant is None:
            # Não deveria acontecer para um JWT válido (tenant_id vem de um
            # tenant que existia no login) — 404 aqui sinaliza um estado
            # inconsistente (ex: tenant apagado depois do token emitido).
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clínica não encontrada.")
        return TenantResponse.model_validate(tenant)

    async def update_own_tenant(self, tenant_id: str, data: TenantUpdateRequest) -> TenantResponse:
        tenant = await self.repo.get_by_id(uuid.UUID(tenant_id))
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clínica não encontrada.")
        if data.legal_name is not None:
            tenant.legal_name = data.legal_name
        if data.trade_name is not None:
            tenant.trade_name = data.trade_name
        if data.annual_revenue_goal is not None:
            tenant.annual_revenue_goal = data.annual_revenue_goal
        await self.repo.save(tenant)
        return TenantResponse.model_validate(tenant)
