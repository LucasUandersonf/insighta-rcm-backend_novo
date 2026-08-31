"""
app/api/v1/endpoints/denial_appeals.py

Recurso de Glosa (conformidade ANS) — ver DECISÃO completa em
app/sql/008_denial_appeals.sql. Mesmo RBAC de contracts.py/billing.py:
dado financeiro/jurídico sensível, 'atendimento' fora.
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.billing_repository import BillingRepository
from app.repositories.denial_appeal_attachment_repository import DenialAppealAttachmentRepository
from app.repositories.denial_appeal_repository import DenialAppealRepository
from app.repositories.insurance_company_repository import InsuranceCompanyRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.schemas.denial_appeal import (
    DenialAppealAttachmentResponse,
    DenialAppealCreateRequest,
    DenialAppealFileRequest,
    DenialAppealResolveRequest,
    DenialAppealResponse,
)
from app.schemas.pagination import PaginatedResponse
from app.services.denial_appeal_service import DenialAppealService

router = APIRouter(prefix="/denial-appeals", tags=["denial-appeals"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = ("financeiro", "admin", "owner", "auditor")

# Mesmo limite defensivo documentado em contracts.py — um anexo de
# comprovante/protocolo não deveria ter dezenas de MB.
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _build_service(db: DbSession) -> DenialAppealService:
    return DenialAppealService(
        DenialAppealRepository(db),
        DenialAppealAttachmentRepository(db),
        BillingRepository(db),
        InsurancePlanRepository(db),
        InsuranceCompanyRepository(db),
    )


@router.post("", response_model=DenialAppealResponse, status_code=201)
async def create_denial_appeal(
    payload: DenialAppealCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> DenialAppealResponse:
    return await _build_service(db).create_appeal(current_user.tenant_id, uuid.UUID(current_user.id), payload)


@router.get("", response_model=PaginatedResponse[DenialAppealResponse])
async def list_denial_appeals(
    db: DbSession,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> PaginatedResponse[DenialAppealResponse]:
    """Resposta: `{items: DenialAppealResponse[], total, limit, offset}` —
    mesmo envelope de GET /audit-log, GET /patients e
    GET /contracts/active (ver app/schemas/pagination.py). QUEBRA o
    contrato anterior deste endpoint, que devolvia
    `list[DenialAppealResponse]` "nu" (ver
    src/pages/DenialAppealsPage.tsx no frontend — precisa passar a ler
    `.items`)."""
    items, total = await _build_service(db).list_appeals_paginated(limit=limit, offset=offset, status_filter=status_filter)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{appeal_id}", response_model=DenialAppealResponse)
async def get_denial_appeal(
    appeal_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> DenialAppealResponse:
    return await _build_service(db).get_appeal(appeal_id)


@router.post("/{appeal_id}/file", response_model=DenialAppealResponse)
async def file_denial_appeal(
    appeal_id: uuid.UUID,
    payload: DenialAppealFileRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> DenialAppealResponse:
    return await _build_service(db).file_appeal(appeal_id, payload.filed_at)


@router.post("/{appeal_id}/resolve", response_model=DenialAppealResponse)
async def resolve_denial_appeal(
    appeal_id: uuid.UUID,
    payload: DenialAppealResolveRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> DenialAppealResponse:
    return await _build_service(db).resolve_appeal(appeal_id, payload)


@router.post("/{appeal_id}/attachments", response_model=DenialAppealAttachmentResponse, status_code=201)
async def upload_denial_appeal_attachment(
    appeal_id: uuid.UUID,
    db: DbSession,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> DenialAppealAttachmentResponse:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio.")
    if len(content) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Anexo acima do limite de 20MB.")

    return await _build_service(db).upload_attachment(
        tenant_id=current_user.tenant_id,
        appeal_id=appeal_id,
        uploaded_by=uuid.UUID(current_user.id),
        filename=file.filename or "anexo",
        content=content,
        content_type=file.content_type or "application/octet-stream",
    )


@router.get("/{appeal_id}/attachments", response_model=list[DenialAppealAttachmentResponse])
async def list_denial_appeal_attachments(
    appeal_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> list[DenialAppealAttachmentResponse]:
    return await _build_service(db).list_attachments(appeal_id)
