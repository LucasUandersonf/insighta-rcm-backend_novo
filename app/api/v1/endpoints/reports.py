"""
app/api/v1/endpoints/reports.py

Disparo sob demanda do mesmo relatório que o cron da Etapa 4 gera
automaticamente — reaproveita ReportDataService, report_pdf_builder.py e
WhatsAppClient por completo. Útil para: (a) a clínica pedir um relatório
fora do ciclo semanal, (b) testar geração/envio sem esperar o agendador
externo disparar.

O JWT não carrega tenants.whatsapp_group_id (só sub/tenant_id/role — ver
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
from app.repositories.capacity_repository import CapacityRepository
from app.repositories.professional_availability_repository import ProfessionalAvailabilityRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.repositories.reporting_repository import ReportingRepository
from app.schemas.report import WeeklyReportRequest, WeeklyReportResponse
from app.services.report_data_service import ReportDataService
from app.services.report_pdf_builder import build_weekly_report_pdf
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
    period_end = payload.period_end or (today - timedelta(days=today.weekday() + 1))
    period_start = payload.period_start or (period_end - timedelta(days=6))

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == UUID(current_user.tenant_id)))
    tenant = tenant_result.scalar_one()  # sempre existe: é o próprio tenant do usuário autenticado

    if not tenant.whatsapp_group_id:
        return WeeklyReportResponse(
            period_start=period_start,
            period_end=period_end,
            sent_via_whatsapp=False,
            detail="Número de WhatsApp de destino não configurado na tela de Setup para este tenant.",
        )

    data_service = ReportDataService(
        ReportingRepository(db),
        ProfessionalRepository(db),
        ProfessionalAvailabilityRepository(db),
        CapacityRepository(db),
    )
    report_data = await data_service.build_weekly_report(tenant.trade_name, period_start, period_end)
    pdf_bytes = build_weekly_report_pdf(report_data)
    filename = f"relatorio_semanal_{period_start.isoformat()}_a_{period_end.isoformat()}.pdf"

    try:
        client = WhatsAppClient()
        await client.send_weekly_report(to_phone_number=tenant.whatsapp_group_id, pdf_bytes=pdf_bytes, filename=filename)
    except WhatsAppClientError as exc:
        return WeeklyReportResponse(
            period_start=period_start, period_end=period_end, sent_via_whatsapp=False, detail=str(exc)
        )

    return WeeklyReportResponse(
        period_start=period_start,
        period_end=period_end,
        sent_via_whatsapp=True,
        detail="Relatório enviado com sucesso.",
    )
