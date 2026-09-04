from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RejectedRowResponse(BaseModel):
    id: int
    ingestion_file_id: UUID
    row_number: int
    payload: dict
    reason: str | None
    raw_value: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResolveInsurancePlanRequest(BaseModel):
    insurance_plan_id: UUID


class ResolveInsurancePlanResponse(BaseModel):
    row_id: int
    resolved: bool
    additionally_resolved_count: int


class IngestionFileResponse(BaseModel):
    """Uma linha da tela de histórico de upload (GET /ingestion/files) —
    tanto arquivos vindos do caminho SQS quanto do caminho HTTP de upload
    aparecem aqui, indistintamente (mesma tabela core.ingestion_files)."""

    id: UUID
    original_filename: str | None
    file_format: str
    data_type: str
    status: str
    row_count: int
    error_row_count: int
    error_message: str | None
    received_at: datetime
    processed_at: datetime | None

    model_config = {"from_attributes": True}


class UploadIngestionFileResponse(BaseModel):
    """Resposta de POST /ingestion/upload. `already_processed=True`
    (HTTP 200) quando o mesmo arquivo já havia sido processado antes —
    caminho feliz de idempotência, nunca um erro (ver
    IngestionRepository.claim_file); `already_processed=False` (HTTP 201)
    quando o upload de fato disparou um processamento novo."""

    id: UUID
    file_format: str
    data_type: str
    status: str
    row_count: int
    error_row_count: int
    received_at: datetime
    already_processed: bool = False
    message: str | None = None

    model_config = {"from_attributes": True}
