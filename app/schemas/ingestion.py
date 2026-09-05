from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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


# --- Mapeador Automático de Coluna (ver app/sql/021_ingestion_column_aliases.sql) ---
# Escopo desta versão: só CSV de Faturamento — ver DECISÃO em
# app/services/column_mapping_service.py.


class ColumnMappingPreviewResponse(BaseModel):
    """POST /ingestion/preview-headers — NÃO processa o arquivo, só lê o
    cabeçalho e devolve uma sugestão. `unresolved_required_fields` vazio
    significa que o arquivo, do jeito que está (padrão + aliases já
    salvos + sugestão desta passada), teria todo campo obrigatório
    coberto — não é garantia de que cada LINHA vai validar (isso só se
    sabe processando de verdade), só que a estrutura de colunas está OK."""

    raw_headers: list[str]
    suggested_mapping: dict[str, str]
    unresolved_required_fields: list[str]


class SaveColumnAliasesRequest(BaseModel):
    """Confirma o mapeamento — normalmente `suggested_mapping` do preview,
    já revisado/corrigido pelo usuário. `canonical_field` precisa ser um
    dos campos reconhecidos (validado no service, não aqui, pra devolver
    uma mensagem que cita o campo problemático)."""

    data_type: str = Field(default="faturamento")
    mapping: dict[str, str] = Field(min_length=1)


class ColumnAliasResponse(BaseModel):
    id: UUID
    data_type: str
    source_header: str
    canonical_field: str
    created_at: datetime

    model_config = {"from_attributes": True}
