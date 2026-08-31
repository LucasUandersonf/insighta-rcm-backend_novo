"""
app/api/v1/endpoints/report_recipients.py

CRUD de destinatários de relatório (multi-recipient) — ver DECISÃO
completa em app/sql/009_report_recipients.sql. RBAC restrito a
owner/admin: quem recebe relatório financeiro/operacional da clínica é
decisão de gestão, não do dia a dia de atendimento/financeiro.
"""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.report_recipient_repository import ReportRecipientRepository
from app.schemas.report_recipient import (
    ReportRecipientCreateRequest,
    ReportRecipientResponse,
    ReportRecipientUpdateRequest,
)
from app.services.report_recipient_service import ReportRecipientService

router = APIRouter(prefix="/report-recipients", tags=["report-recipients"])

_CAN_MANAGE = ("owner", "admin")


def _build_service(db: DbSession) -> ReportRecipientService:
    return ReportRecipientService(ReportRecipientRepository(db))


@router.post("", response_model=ReportRecipientResponse, status_code=201)
async def create_report_recipient(
    payload: ReportRecipientCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ReportRecipientResponse:
    return await _build_service(db).create_recipient(current_user.tenant_id, payload)


@router.get("", response_model=list[ReportRecipientResponse])
async def list_report_recipients(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> list[ReportRecipientResponse]:
    return await _build_service(db).list_recipients()


@router.get("/{recipient_id}", response_model=ReportRecipientResponse)
async def get_report_recipient(
    recipient_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ReportRecipientResponse:
    return await _build_service(db).get_recipient(recipient_id)


@router.patch("/{recipient_id}", response_model=ReportRecipientResponse)
async def update_report_recipient(
    recipient_id: uuid.UUID,
    payload: ReportRecipientUpdateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ReportRecipientResponse:
    return await _build_service(db).update_recipient(recipient_id, payload)


@router.delete("/{recipient_id}", status_code=204)
async def delete_report_recipient(
    recipient_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> None:
    await _build_service(db).delete_recipient(recipient_id)
