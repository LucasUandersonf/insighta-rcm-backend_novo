"""app/api/v1/endpoints/tenant.py — Painel do Administrador da Empresa:
gestão centralizada da clínica/organização + leitura do plano/assinatura
do SaaS. Upgrade de plano em si é fluxo comercial (fora do MVP — ver
DECISÃO em app/schemas/tenant.py); aqui só expomos o estado atual."""
from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import AVAILABLE_PLAN_TIERS, NoShowThresholdSuggestionResponse, TenantResponse, TenantUpdateRequest
from app.services.no_show_risk_engine import MIN_SPECIFIC_SAMPLES
from app.services.no_show_risk_engine import suggest_thresholds as suggest_no_show_thresholds
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


@router.get("/no-show-thresholds/suggested", response_model=NoShowThresholdSuggestionResponse)
async def get_suggested_no_show_thresholds(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> NoShowThresholdSuggestionResponse:
    """
    Sugestão de limiar calculada a partir do HISTÓRICO REAL desta
    clínica (ver DECISÃO completa em
    no_show_risk_engine.suggest_thresholds) — não um valor genérico.
    Só sugere leitura (qualquer papel de _CAN_VIEW); aplicar a sugestão
    ainda passa por PATCH /tenant, que continua owner-only.
    """
    rates = await AnalyticsRepository(db).all_patient_no_show_rates(min_sample=MIN_SPECIFIC_SAMPLES)
    suggestion = suggest_no_show_thresholds(rates)
    if suggestion is None:
        return NoShowThresholdSuggestionResponse(low_threshold=None, medium_threshold=None, sample_size=len(rates))
    return NoShowThresholdSuggestionResponse(
        low_threshold=suggestion.low_threshold, medium_threshold=suggestion.medium_threshold, sample_size=suggestion.sample_size
    )
