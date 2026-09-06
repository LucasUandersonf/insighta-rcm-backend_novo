"""
app/api/v1/endpoints/platform.py — painel interno de Customer Success
orientado a dados. Ver DECISÃO completa em
app/sql/026_platform_customer_success.sql e app/api/platform_admin_auth.py
sobre por que este é um fluxo de autenticação TOTALMENTE separado do
login de clínica: não existe usuário nem tenant nesta sessão, só a
senha única da equipe que opera a Insighta.

NUNCA referenciado por nenhum link dentro do produto que um usuário de
clínica usa — só acessível por quem sabe o endereço (frontend: rota
`/plataforma`, fora do fluxo de login normal).
"""
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.platform_admin_auth import PlatformAdminDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import create_platform_admin_token
from app.db.session import get_db_no_tenant
from app.repositories.platform_reporting_repository import PlatformReportingRepository
from app.schemas.platform import PlatformLoginRequest, PlatformLoginResponse, TenantUsageSummary
from app.services.platform_reporting_service import PlatformReportingService

router = APIRouter(prefix="/platform", tags=["platform"])
settings = get_settings()


@router.post("/login", response_model=PlatformLoginResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)  # mesma proteção de força bruta do login de clínica
async def platform_login(request: Request, credentials: PlatformLoginRequest) -> PlatformLoginResponse:
    if not settings.PLATFORM_ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Painel interno não configurado (PLATFORM_ADMIN_PASSWORD ausente).",
        )
    # hmac.compare_digest (tempo constante) em vez de `==`, mesmo cuidado
    # de verify_meta_webhook_signature em app/core/security.py — evita
    # vazar por timing quantos caracteres da senha enviada estão corretos.
    if not hmac.compare_digest(credentials.password, settings.PLATFORM_ADMIN_PASSWORD):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Senha inválida.")

    return PlatformLoginResponse(access_token=create_platform_admin_token())


@router.get("/tenants-usage", response_model=list[TenantUsageSummary], dependencies=[PlatformAdminDep])
async def list_tenants_usage(db: AsyncSession = Depends(get_db_no_tenant)) -> list[TenantUsageSummary]:
    """
    Uso agregado por clínica — quem está engajado, quem está em risco.
    Sessão SEM tenant de propósito (get_db_no_tenant): a função SQL por
    trás (core.platform_tenant_usage_summary, SECURITY DEFINER) é o único
    ponto do sistema autorizado a agregar dado cross-tenant.
    """
    service = PlatformReportingService(PlatformReportingRepository(db))
    return await service.list_tenant_usage()
