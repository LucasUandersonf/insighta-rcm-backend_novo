"""
app/api/v1/endpoints/contracts.py

Diferença em relação a patients/appointments: dado financeiro sensível
(valor de repasse por convênio), então _CAN_WRITE aqui NÃO inclui
'atendimento' — só quem lida com o financeiro da clínica deve poder
cadastrar/alterar tabela de repasse.

Três fluxos de escrita, três grupos de endpoint:
  - Cadastro manual rápido: POST /contracts (sem PDF/IA)
  - Esteira de IA (Parser Inteligente de Contratos): POST /contracts/upload
    -> POST /contracts/{id}/extract -> POST /contracts/{id}/homologate
  - Leitura: GET /contracts/active, GET /contracts/{id}
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.contract_repository import ContractRepository
from app.schemas.contract import (
    ContractCreateRequest,
    ContractResponse,
    ExtractionPreviewResponse,
    HomologateRequest,
)
from app.schemas.pagination import PaginatedResponse
from app.services.contract_intake_service import ContractIntakeService
from app.services.contract_service import ContractService

router = APIRouter(prefix="/contracts", tags=["contracts"])

_CAN_WRITE = ("financeiro", "admin", "owner")
_CAN_READ = ("financeiro", "admin", "owner", "auditor")

# PDF de contrato: limite defensivo — o mesmo motivo documentado em
# AnthropicContractExtractor.extract (pdf_text[:100_000]): um arquivo
# absurdamente grande é sinal de upload errado (não é assim que um
# contrato de tabela de preços se parece), não uma legítima tabela de
# 500 páginas.
_MAX_PDF_BYTES = 20 * 1024 * 1024


def _build_service(db: DbSession) -> ContractService:
    return ContractService(ContractRepository(db), ContractItemRepository(db))


def _build_intake_service(db: DbSession) -> ContractIntakeService:
    return ContractIntakeService(ContractRepository(db), ContractItemRepository(db))


@router.post("", response_model=ContractResponse, status_code=201)
async def create_contract(
    payload: ContractCreateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ContractResponse:
    return await _build_service(db).create_contract(current_user.tenant_id, payload)


@router.get("/active", response_model=PaginatedResponse[ContractResponse])
async def list_active_contracts(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[ContractResponse]:
    """Resposta: `{items: ContractResponse[], total, limit, offset}` —
    mesmo envelope de GET /audit-log, GET /patients e GET /denial-appeals
    (ver app/schemas/pagination.py). QUEBRA o contrato anterior deste
    endpoint, que devolvia `list[ContractResponse]` "nu" (ver
    src/pages/ContractsPage.tsx no frontend, que hoje faz
    `apiClient.get<Contract[]>(...)` — precisa passar a ler `.items`)."""
    items, total = await _build_service(db).list_active_paginated(limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(
    contract_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
) -> ContractResponse:
    return await _build_service(db).get_contract(contract_id)


@router.post("/upload", response_model=ContractResponse, status_code=201)
async def upload_contract_pdf(
    db: DbSession,
    insurance_plan_id: uuid.UUID = Form(...),
    valid_from: date = Form(...),
    valid_until: date | None = Form(None),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ContractResponse:
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Só arquivos PDF são aceitos.")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio.")
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF acima do limite de 20MB.")

    return await _build_intake_service(db).create_draft(
        tenant_id=current_user.tenant_id,
        insurance_plan_id=insurance_plan_id,
        valid_from=valid_from,
        valid_until=valid_until,
        filename=file.filename or "contrato.pdf",
        pdf_bytes=pdf_bytes,
    )


@router.post("/{contract_id}/extract", response_model=ExtractionPreviewResponse)
async def extract_contract(
    contract_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ExtractionPreviewResponse:
    return await _build_intake_service(db).run_extraction(contract_id)


@router.post("/{contract_id}/homologate", response_model=ContractResponse)
async def homologate_contract(
    contract_id: uuid.UUID,
    payload: HomologateRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_WRITE)),
) -> ContractResponse:
    return await _build_intake_service(db).homologate(
        tenant_id=current_user.tenant_id,
        contract_id=contract_id,
        homologated_by=uuid.UUID(current_user.id),
        items=payload.items,
    )
