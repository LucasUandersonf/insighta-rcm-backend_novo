"""
app/api/v1/endpoints/reports.py

Disparo sob demanda do mesmo relatório que o cron da Etapa 4 gera
automaticamente — reaproveita app/services/report_send_service.py por
completo (ver DECISÃO lá para o bug corrigido: este endpoint usava
`Tenant.whatsapp_group_id`, um campo já substituído por
`core.report_recipients` desde 009_report_recipients.sql — corrigido
agora para usar a MESMA fonte de destinatários do cron).

DECISÃO — período PADRÃO é "desde segunda até hoje", não a última semana
fechada
-------------------------------------------------------------------------
Achado do usuário sobre lacuna de produto: o relatório automático só sai
uma vez por semana (dado completo, de propósito — ver _last_full_week em
weekly_report_job.py), então um gestor que quer saber "o que está
acontecendo AGORA" tinha que esperar o próximo ciclo ou abrir o
dashboard sozinho. Este endpoint existe justamente para o caso contrário:
quem aciona já sabe que está pedindo um retrato PARCIAL da semana em
andamento — describe o período no próprio nome do arquivo/PDF, então não
há risco de confundir com o fechamento semanal oficial. Continua
aceitando `period_start`/`period_end` explícitos para quem quiser um
histórico específico sob demanda.

O JWT não carrega tenants.report_recipients (só sub/tenant_id/role — ver
core/security.py), então buscamos o tenant diretamente pela MESMA sessão
`db` já injetada pelo endpoint: core.tenants não tem RLS (é a raiz da
árvore de isolamento, ver 001_init_schema.sql), então essa leitura
funciona normalmente mesmo dentro de uma sessão tenant-aware.
"""
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, require_role
from app.models.tenant import Tenant
from app.schemas.report import WeeklyReportRequest, WeeklyReportResponse
from app.services.report_send_service import send_report_to_recipients
from app.services.whatsapp_client import WhatsAppClient, WhatsAppClientError

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/weekly/send", response_model=WeeklyReportResponse)
async def send_weekly_report_now(
    payload: WeeklyReportRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role("admin", "owner")),
) -> WeeklyReportResponse:
    if payload.period_end is not None and payload.period_start is not None and payload.period_end < payload.period_start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="period_end deve ser >= period_start.")

    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    period_start = payload.period_start or this_monday
    period_end = payload.period_end or today

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == UUID(current_user.tenant_id)))
    tenant = tenant_result.scalar_one()  # sempre existe: é o próprio tenant do usuário autenticado

    # client_factory (WhatsAppClient, a própria classe) só é chamado
    # DENTRO de send_report_to_recipients, e só se houver destinatário —
    # ver DECISÃO lá para o motivo de não construir o client aqui antes
    # de saber se há alguém para receber (evita mascarar "sem
    # destinatário cadastrado" atrás de "credenciais ausentes").
    try:
        result = await send_report_to_recipients(db, tenant, period_start, period_end, WhatsAppClient)
    except WhatsAppClientError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    if result.recipients_checked == 0:
        return WeeklyReportResponse(
            period_start=period_start,
            period_end=period_end,
            sent_via_whatsapp=False,
            recipients_checked=0,
            sent=0,
            failed=0,
            detail=(
                "Nenhum destinatário de relatório semanal com WhatsApp cadastrado para este tenant — "
                "cadastre um em Setup > Destinatários de Relatório."
            ),
        )

    return WeeklyReportResponse(
        period_start=period_start,
        period_end=period_end,
        sent_via_whatsapp=result.sent > 0,
        recipients_checked=result.recipients_checked,
        sent=result.sent,
        failed=result.failed,
        detail=(
            f"Relatório enviado a {result.sent} de {result.recipients_checked} destinatário(s)."
            if result.failed == 0
            else f"Relatório enviado a {result.sent} de {result.recipients_checked} destinatário(s) — {result.failed} falha(s)."
        ),
    )
