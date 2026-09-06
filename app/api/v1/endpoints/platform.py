"""
app/api/v1/endpoints/platform.py — painel interno de Customer Success
orientado a dados. Ver DECISÃO completa em
app/sql/026_platform_customer_success.sql, app/sql/029_platform_users.sql
e app/api/platform_admin_auth.py sobre por que este é um fluxo de
autenticação TOTALMENTE separado do login de clínica: não existe usuário
de clínica nem tenant nesta sessão, só a conta individual de quem opera
a Insighta.

NUNCA referenciado por nenhum link dentro do produto que um usuário de
clínica usa — só acessível por quem sabe o endereço (frontend: rota
`/plataforma`, fora do fluxo de login normal).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.platform_admin_auth import PlatformAdminDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import create_platform_admin_token, verify_password
from app.db.session import get_db_no_tenant
from app.repositories.platform_audit_log_repository import PlatformAuditLogRepository
from app.repositories.platform_reporting_repository import PlatformReportingRepository
from app.repositories.platform_risk_alert_repository import PlatformRiskAlertRepository
from app.repositories.platform_user_repository import PlatformUserRepository
from app.schemas.platform import (
    PlatformAlertRunResponse,
    PlatformAuditLogEntryResponse,
    PlatformLoginRequest,
    PlatformLoginResponse,
    TenantUsageSummary,
)
from app.services.platform_alert_service import PlatformAlertService
from app.services.platform_reporting_service import PlatformReportingService

router = APIRouter(prefix="/platform", tags=["platform"])
settings = get_settings()

_GENERIC_LOGIN_ERROR = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos.")


@router.post("/login", response_model=PlatformLoginResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)  # mesma proteção de força bruta do login de clínica
async def platform_login(
    request: Request, credentials: PlatformLoginRequest, db: AsyncSession = Depends(get_db_no_tenant)
) -> PlatformLoginResponse:
    user_repo = PlatformUserRepository(db)
    user = await user_repo.get_by_email(credentials.email)
    # Mesma mensagem genérica para "e-mail não existe" e "senha errada" —
    # mesmo critério de app/api/v1/endpoints/auth.py (evita user
    # enumeration). is_active também cai aqui, sem diferenciar: uma conta
    # desativada não deveria nem confirmar que o e-mail existe.
    if user is None or not user.is_active or not verify_password(credentials.password, user.hashed_password):
        raise _GENERIC_LOGIN_ERROR

    now = datetime.now(timezone.utc)
    await user_repo.touch_last_login(user, now=now)
    await PlatformAuditLogRepository(db).record(platform_user_id=user.id, action="login")
    await db.commit()  # get_db_no_tenant não commita sozinha — ver auth_service.py

    return PlatformLoginResponse(access_token=create_platform_admin_token(str(user.id)))


@router.get("/tenants-usage", response_model=list[TenantUsageSummary])
async def list_tenants_usage(identity: PlatformAdminDep, db: AsyncSession = Depends(get_db_no_tenant)) -> list[TenantUsageSummary]:
    """
    Uso agregado por clínica — quem está engajado, quem está em risco.
    Sessão SEM tenant de propósito (get_db_no_tenant): a função SQL por
    trás (core.platform_tenant_usage_summary, SECURITY DEFINER) é o único
    ponto do sistema autorizado a agregar dado cross-tenant. Só leitura —
    não gera entrada em platform_audit_log (ver DECISÃO em
    029_platform_users.sql: o histórico cobre AÇÕES, não visualizações).
    """
    service = PlatformReportingService(PlatformReportingRepository(db))
    return await service.list_tenant_usage()


@router.post("/alerts/run", response_model=PlatformAlertRunResponse)
async def run_risk_alerts(identity: PlatformAdminDep, db: AsyncSession = Depends(get_db_no_tenant)) -> PlatformAlertRunResponse:
    """
    Dispara manualmente a checagem de alertas de risco — útil para testar
    sem esperar o agendador externo (mesmo espírito de
    POST /reports/weekly/send). Em produção, roda periodicamente via
    app/worker/platform_risk_alert_job.py (cron/EventBridge Scheduler).
    Registrado em platform_audit_log — é a primeira ação real (não só
    leitura) que este painel oferece.
    """
    service = PlatformAlertService(
        PlatformReportingService(PlatformReportingRepository(db)),
        PlatformRiskAlertRepository(db),
    )
    result = await service.check_and_send_risk_alerts()
    await PlatformAuditLogRepository(db).record(platform_user_id=identity.platform_user_id, action="alerts_run")
    # get_db_no_tenant não commita sozinha (diferente da sessão
    # tenant-aware) — ver mesma exigência em app/services/auth_service.py.
    await db.commit()
    return result


@router.get("/audit-log", response_model=list[PlatformAuditLogEntryResponse])
async def list_platform_audit_log(identity: PlatformAdminDep, db: AsyncSession = Depends(get_db_no_tenant)) -> list[PlatformAuditLogEntryResponse]:
    """
    "Histórico de quem fez o quê" (login e disparo manual de alertas, por
    ora — ver DECISÃO em 029_platform_users.sql). Volume esperado baixo
    (equipe pequena, ações esporádicas), por isso resolve o e-mail de
    cada ator com uma busca simples em vez de um JOIN — mais direto de
    ler do que otimizar prematuramente um caminho de baixíssimo tráfego.
    """
    entries = await PlatformAuditLogRepository(db).list_recent()
    user_repo = PlatformUserRepository(db)
    email_by_id: dict = {}
    responses = []
    for entry in entries:
        if entry.platform_user_id not in email_by_id:
            actor = await user_repo.get_by_id(entry.platform_user_id)
            email_by_id[entry.platform_user_id] = actor.email if actor else "conta removida"
        responses.append(
            PlatformAuditLogEntryResponse(
                id=entry.id, actor_email=email_by_id[entry.platform_user_id], action=entry.action, created_at=entry.created_at
            )
        )
    return responses
