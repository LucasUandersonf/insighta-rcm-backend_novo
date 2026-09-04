"""
app/api/v1/endpoints/ingestion.py

Dois grupos de rota:
  - Tela de Setup: lista linhas de importação que a Etapa 1/2 não
    conseguiu promover sozinhas, e permite ao humano resolver
    manualmente (hoje só o caso 'unknown_insurance_plan' — CID ausente
    ou valor inválido, por exemplo, são erros de dado no arquivo de
    origem, não resolvíveis por um mapeamento; ficam fora de escopo
    deste endpoint).
  - Upload HTTP de arquivo operacional (POST /ingestion/upload) e
    histórico (GET /ingestion/files): o caminho SÍNCRONO que faltava no
    produto — até aqui, a ÚNICA forma de um arquivo de lote (CSV/XML/
    JSON) entrar no sistema era via SFTP -> S3 Event Notification -> SQS
    -> app/worker/ingestion_worker.py, um processo separado sem nenhuma
    porta HTTP. Aqui o usuário sobe o arquivo pela tela do produto e
    recebe o resultado do processamento na MESMA requisição — a
    validação estrutural, o parsing e a normalização são exatamente os
    MESMOS que o worker usa (ver app/services/ingestion_processing_service.py),
    nenhuma lógica duplicada.
"""
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_role
from app.models.ingestion_raw_row import IngestionRawRow
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.guia_repository import GuiaRepository
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.repositories.local_repository import LocalRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.schemas.ingestion import (
    IngestionFileResponse,
    RejectedRowResponse,
    ResolveInsurancePlanRequest,
    ResolveInsurancePlanResponse,
    UploadIngestionFileResponse,
)
from app.schemas.pagination import PaginatedResponse
from app.services.ingestion_processing_service import FileParsingError, process_uploaded_file
from app.services.ingestion_storage_client import IngestionStorageClient, IngestionStorageError, build_upload_key
from app.services.normalization_service import NormalizationService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

_CAN_MANAGE = ("admin", "owner", "financeiro")
# Leitura do histórico de upload também é permitida para auditor
# (read-only) — mesmo padrão de app/api/v1/endpoints/audit_log.py e
# app/api/v1/endpoints/analytics.py: dado sensível, mas visibilidade sem
# poder de ação é papel legítimo de auditoria.
_CAN_READ = (*_CAN_MANAGE, "auditor")

# Mesmo limite defensivo documentado em contracts.py/denial_appeals.py —
# um arquivo de lote diário de uma clínica não deveria chegar perto disso.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_EXTENSION_TO_FORMAT = {".csv": "csv", ".xml": "xml", ".json": "json"}
# Fallback por content-type para quando a extensão do arquivo não bate
# (cliente HTTP genérico, ou nome de arquivo sem extensão) — mesma lista
# de formatos que app/worker/s3_key_resolver.py já reconhece (csv/xml/json).
_CONTENT_TYPE_TO_FORMAT = {
    "text/csv": "csv",
    "application/csv": "csv",
    "application/vnd.ms-excel": "csv",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/json": "json",
    "text/json": "json",
}


def _detect_file_format(filename: str, content_type: str | None) -> str | None:
    """Extensão manda; content-type é só um fallback (navegadores e
    clientes HTTP variam bastante no Content-Type que mandam para
    .csv/.xml, então a extensão do nome do arquivo é o sinal mais
    confiável aqui)."""
    ext = Path(filename).suffix.lower()
    if ext in _EXTENSION_TO_FORMAT:
        return _EXTENSION_TO_FORMAT[ext]
    if content_type in _CONTENT_TYPE_TO_FORMAT:
        return _CONTENT_TYPE_TO_FORMAT[content_type]
    return None


def _to_response(row: IngestionRawRow) -> RejectedRowResponse:
    # BUG CORRIGIDO — `validation_errors` (JSONB) tem DUAS formas
    # possíveis, dependendo de QUEM rejeitou a linha: um dict
    # {"reason": ..., "raw_value": ...} quando a Etapa 2 (normalização)
    # rejeita por convênio desconhecido (ver
    # NormalizationService.normalize_row), ou uma LISTA de mensagens de
    # erro (`RowParseResult.failed`, ver app/worker/schemas.py) quando a
    # Etapa 1 (parsing estrutural do CSV/XML/JSON) já rejeita a linha
    # antes disso — ex: data em formato inválido, moeda sem nenhum
    # dígito. Esta função assumia sempre a forma de dict — `errors.get(...)`
    # quebrava com AttributeError na forma de lista, derrubando com 500
    # o endpoint INTEIRO (GET /ingestion/rejected lista as duas juntas)
    # assim que UMA linha estruturalmente inválida existisse no tenant.
    errors = row.validation_errors
    if isinstance(errors, list):
        reason = "validation_error"
        raw_value = "; ".join(str(e) for e in errors) if errors else None
    else:
        errors = errors or {}
        reason = errors.get("reason")
        raw_value = errors.get("raw_value")
    return RejectedRowResponse(
        id=row.id,
        ingestion_file_id=row.ingestion_file_id,
        row_number=row.row_number,
        payload=row.payload,
        reason=reason,
        raw_value=raw_value,
        created_at=row.created_at,
    )


@router.get("/rejected", response_model=list[RejectedRowResponse])
async def list_rejected_rows(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
    reason: str | None = Query(default=None, description="Ex: unknown_insurance_plan"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> list[RejectedRowResponse]:
    repo = IngestionRepository(db)
    rows = await repo.list_rejected(reason=reason, limit=limit, offset=offset)
    return [_to_response(r) for r in rows]


@router.post("/rejected/{row_id}/resolve-insurance-plan", response_model=ResolveInsurancePlanResponse)
async def resolve_insurance_plan(
    row_id: int,
    payload: ResolveInsurancePlanRequest,
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> ResolveInsurancePlanResponse:
    ingestion_repo = IngestionRepository(db)
    insurance_plan_repo = InsurancePlanRepository(db)

    target_row = await ingestion_repo.get_by_id(row_id)
    if target_row is None or target_row.status != "rejected":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linha rejeitada não encontrada neste tenant.")

    plan = await insurance_plan_repo.get_by_id(payload.insurance_plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Convênio não encontrado neste tenant.")

    raw_value = (target_row.validation_errors or {}).get("raw_value")
    matching_rows = await ingestion_repo.list_rejected_by_raw_value(raw_value) if raw_value else []

    normalization_service = NormalizationService(
        patient_repo=PatientRepository(db),
        professional_repo=ProfessionalRepository(db),
        appointment_repo=AppointmentRepository(db),
        contract_item_repo=ContractItemRepository(db),
        insurance_plan_repo=insurance_plan_repo,
        billing_repo=BillingRepository(db),
        local_repo=LocalRepository(db),
        guia_repo=GuiaRepository(db),
    )
    summary = await normalization_service.resolve_unknown_insurance_plan(
        tenant_id=UUID(current_user.tenant_id),
        target_row=target_row,
        insurance_plan_id=plan.id,
        also_resolve_matching_rows=matching_rows,
        source_file=None,
    )

    return ResolveInsurancePlanResponse(
        row_id=row_id,
        resolved=summary.target_resolved,
        additionally_resolved_count=summary.additionally_resolved,
    )


@router.post("/upload", response_model=UploadIngestionFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_ingestion_file(
    db: DbSession,
    response: Response,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role(*_CAN_MANAGE)),
) -> UploadIngestionFileResponse:
    """
    Caminho HTTP SÍNCRONO que faltava no produto: sobe um CSV/XML/JSON
    operacional (billing/agenda/repasse) direto para o bucket de
    ingestão e roda o MESMO pipeline (claim/parse/save/normalize) que o
    worker SQS usa — ver app/services/ingestion_processing_service.py.
    Diferente do worker (assíncrono, sem resposta HTTP), aqui o usuário
    recebe o resultado do processamento na mesma requisição.

    - 201: arquivo novo, processado agora.
    - 200: MESMO arquivo (mesma chave de idempotência) já havia sido
      processado antes — caminho feliz de idempotência, não um erro.
    - 400: formato não reconhecido, arquivo vazio, ou acima do limite.
    - 422: arquivo reconhecido mas estruturalmente ilegível (CSV/XML/JSON
      malformado) — erro por LINHA não cai aqui, vira `error_row_count`.
    - 503: bucket de ingestão (AWS_S3_INGEST_BUCKET) não configurado, ou
      falha ao falar com o S3.
    """
    filename = file.filename or "arquivo"
    file_format = _detect_file_format(filename, file.content_type)
    if file_format is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato de arquivo não reconhecido. Envie um arquivo .csv, .xml ou .json.",
        )

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio.")
    if len(raw_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo acima do limite de 20MB.")

    try:
        storage = IngestionStorageClient()
    except IngestionStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    s3_key = build_upload_key(current_user.tenant_id, file_format, filename)
    try:
        version_id = await storage.upload_bytes(key=s3_key, raw_bytes=raw_bytes)
    except Exception as exc:  # boto3 lança tipos variados de erro de rede/credencial
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Falha ao enviar o arquivo para o armazenamento: {exc}",
        ) from exc

    tenant_id = UUID(current_user.tenant_id)
    try:
        result = await process_uploaded_file(
            db,
            tenant_id,
            s3_bucket=storage.bucket,
            s3_key=s3_key,
            s3_version_id=version_id,
            file_format=file_format,
            raw_bytes=raw_bytes,
            original_filename=filename,
        )
    except FileParsingError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if result.already_claimed:
        response.status_code = status.HTTP_200_OK
        existing = result.ingestion_file
        return UploadIngestionFileResponse(
            id=existing.id,
            file_format=existing.file_format,
            status=existing.status,
            row_count=existing.row_count,
            error_row_count=existing.error_row_count,
            received_at=existing.received_at,
            already_processed=True,
            message="Este arquivo já havia sido enviado e processado anteriormente — nenhum dado novo foi importado.",
        )

    ingestion_file = result.ingestion_file
    return UploadIngestionFileResponse(
        id=ingestion_file.id,
        file_format=ingestion_file.file_format,
        status=ingestion_file.status,
        row_count=ingestion_file.row_count,
        error_row_count=ingestion_file.error_row_count,
        received_at=ingestion_file.received_at,
        already_processed=False,
    )


@router.get("/files", response_model=PaginatedResponse[IngestionFileResponse])
async def list_ingestion_files(
    db: DbSession,
    current_user: CurrentUser = Depends(require_role(*_CAN_READ)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[IngestionFileResponse]:
    """Histórico de arquivos processados (upload HTTP e SQS, misturados —
    mesma tabela), mais recente primeiro. Resposta:
    `{items: IngestionFileResponse[], total, limit, offset}` — mesmo
    envelope de GET /contracts/active, GET /denial-appeals e
    GET /audit-log (ver app/schemas/pagination.py)."""
    repo = IngestionRepository(db)
    files, total = await repo.list_files_paginated(limit=limit, offset=offset)
    items = [IngestionFileResponse.model_validate(f) for f in files]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
