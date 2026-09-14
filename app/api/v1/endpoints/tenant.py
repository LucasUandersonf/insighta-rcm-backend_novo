"""app/api/v1/endpoints/tenant.py — Painel do Administrador da Empresa:
gestão centralizada da clínica/organização + leitura do plano/assinatura
do SaaS. Upgrade de plano em si é fluxo comercial (fora do MVP — ver
DECISÃO em app/schemas/tenant.py); aqui só expomos o estado atual."""
from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import (
    AVAILABLE_PLAN_TIERS,
    DenialRiskThresholdSuggestionResponse,
    HealthScoreCeilingSuggestionResponse,
    NoShowThresholdSuggestionResponse,
    TenantResponse,
    TenantUpdateRequest,
)
from app.services.analytics_service import monthly_denial_risk_pcts, monthly_no_show_rates
from app.services.health_score_engine import suggest_denial_rate_ceiling, suggest_no_show_rate_ceiling
from app.services.no_show_risk_engine import MIN_SPECIFIC_SAMPLES
from app.services.no_show_risk_engine import suggest_thresholds as suggest_no_show_thresholds
from app.services.smart_insights_engine import suggest_denial_risk_thresholds
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


@router.get("/denial-risk-thresholds/suggested", response_model=DenialRiskThresholdSuggestionResponse)
async def get_suggested_denial_risk_thresholds(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> DenialRiskThresholdSuggestionResponse:
    """
    Épico F2.1 do Plano Diretor ("Calibração por especialidade/porte") —
    sugestão calculada a partir do HISTÓRICO MENSAL REAL desta clínica
    (ver DECISÃO completa em smart_insights_engine.suggest_denial_risk_thresholds
    e threshold_calibration.py). Mesmo padrão do endpoint de no-show
    acima: só leitura, aplicar ainda passa por PATCH /tenant.
    """
    monthly_pcts = await monthly_denial_risk_pcts(AnalyticsRepository(db))
    suggestion = suggest_denial_risk_thresholds(monthly_pcts)
    if suggestion is None:
        return DenialRiskThresholdSuggestionResponse(
            warning_threshold=None, critical_threshold=None, sample_size=len(monthly_pcts)
        )
    return DenialRiskThresholdSuggestionResponse(
        warning_threshold=suggestion.warning_threshold,
        critical_threshold=suggestion.critical_threshold,
        sample_size=suggestion.sample_size,
    )


@router.get("/health-score-ceilings/suggested", response_model=HealthScoreCeilingSuggestionResponse)
async def get_suggested_health_score_ceilings(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_VIEW)),
) -> HealthScoreCeilingSuggestionResponse:
    """
    Épico F2.1 do Plano Diretor — mesma calibração pelo histórico
    mensal real, para os DOIS tetos da Nota de Saúde Financeira (ver
    DECISÃO completa em health_score_engine.py). Cada teto tem sua
    própria amostra (meses com faturamento calculável vs. meses com
    atendimento resolvido) — por isso cada um pode aparecer com
    sample_size diferente, e um pode ter sugestão enquanto o outro ainda
    não tem histórico suficiente.
    """
    repo = AnalyticsRepository(db)
    monthly_pcts = await monthly_denial_risk_pcts(repo)
    monthly_no_show = await monthly_no_show_rates(repo)
    denial_result = suggest_denial_rate_ceiling([pct / 100 for pct in monthly_pcts])
    no_show_result = suggest_no_show_rate_ceiling(monthly_no_show)
    return HealthScoreCeilingSuggestionResponse(
        denial_ceiling=denial_result[0] if denial_result is not None else None,
        denial_ceiling_sample_size=denial_result[1] if denial_result is not None else len(monthly_pcts),
        no_show_ceiling=no_show_result[0] if no_show_result is not None else None,
        no_show_ceiling_sample_size=no_show_result[1] if no_show_result is not None else len(monthly_no_show),
    )
