"""app/api/v1/endpoints/tenant.py — Painel do Administrador da Empresa:
gestão centralizada da clínica/organização + leitura do plano/assinatura
do SaaS. Upgrade de plano em si é fluxo comercial (fora do MVP — ver
DECISÃO em app/schemas/tenant.py); aqui só expomos o estado atual."""
from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import AVAILABLE_PLAN_TIERS, TenantResponse, TenantUpdateRequest
from app.services.tenant_service import TenantService

router = APIRouter(prefix="/tenant", tags=["tenant"])

# Qualquer papel autenticado pode VER os dados da própria clínica (ex:
# "financeiro" precisa saber o plano contratado para falar de limites);
# só owner edita cadastro.
_CAN_VIEW = ("owner", "admin", "financeiro", "atendimento", "auditor")
_CAN_EDIT = ("owner",)


@router.get("", response_model=TenantResponse)
async def get_tenant(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> TenantResponse:
    service = TenantService(TenantRepository(db))
    return await service.get_own_tenant(current_user.tenant_id)


@router.patch("", response_model=TenantResponse)
async def update_tenant(
    payload: TenantUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_EDIT)),
) -> TenantResponse:
    service = TenantService(TenantRepository(db))
    return await service.update_own_tenant(current_user.tenant_id, payload)


@router.get("/plans/available", response_model=list[str])
async def list_available_plans(
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> list[str]:
    return list(AVAILABLE_PLAN_TIERS)
