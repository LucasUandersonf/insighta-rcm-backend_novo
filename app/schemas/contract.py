"""
app/schemas/contract.py

Dado financeiro sensível (valor acordado por procedimento) — por isso os
endpoints correspondentes usam require_role restrito a
financeiro/admin/owner, nunca 'atendimento' (ver app/api/v1/endpoints/contracts.py).

ContractCreateRequest agora carrega uma LISTA de itens (a hierarquia
Convênio -> Plano -> Contrato -> Itens do briefing), não mais um único
procedure_code/agreed_value por chamada — ver DECISÃO em
app/sql/007_contract_intelligence.sql.
"""
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ContractItemInput(BaseModel):
    tuss_code: str = Field(min_length=1, max_length=20)
    procedure_name: str | None = None
    agreed_price: float = Field(gt=0)


class ContractCreateRequest(BaseModel):
    """Cadastro MANUAL rápido (sem PDF/IA) — para quando o faturista já
    sabe os 1-2 procedimentos que quer lançar e não vale a pena passar
    pelo fluxo de upload. Cria o contrato já HOMOLOGADO (foi um humano
    quem digitou, não uma extração pendente de revisão)."""

    insurance_plan_id: UUID
    valid_from: date
    valid_until: date | None = None
    items: list[ContractItemInput] = Field(min_length=1)

    @model_validator(mode="after")
    def check_date_range(self) -> "ContractCreateRequest":
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until deve ser posterior a valid_from.")
        return self


class ContractItemResponse(BaseModel):
    id: UUID
    tuss_code: str
    procedure_name: str | None
    agreed_price: float

    model_config = {"from_attributes": True}


class ContractResponse(BaseModel):
    id: UUID
    insurance_plan_id: UUID
    valid_from: date
    valid_until: date | None
    status: str
    pdf_s3_key: str | None
    items: list[ContractItemResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ContractDraftCreateRequest(BaseModel):
    """multipart/form-data — ver ContractIntakeService.create_draft.
    O PDF em si vai como UploadFile no endpoint, não neste schema (Pydantic
    não modela arquivo binário no corpo multipart junto de campos comuns
    sem Form(...) explícito por campo — ver app/api/v1/endpoints/contracts.py)."""

    insurance_plan_id: UUID
    valid_from: date
    valid_until: date | None = None


class ExtractedItemResponse(BaseModel):
    """Um item como a IA devolveu — ainda NÃO persistido em
    contract_items (só existe depois do POST .../homologate). `warning`
    é o alerta específico daquela linha (ex: "código TUSS fora do padrão
    esperado") para a Tela de Conferência destacar visualmente."""

    tuss_code: str
    procedure_name: str | None
    agreed_price: float
    warning: str | None = None


class ExtractionPreviewResponse(BaseModel):
    """POST /contracts/{id}/extract — resultado bruto da IA para a Tela
    de Conferência (lado a lado com o PDF). `warnings` são alertas do
    LOTE inteiro (ex: "nenhum item extraído"), distintos de warning por
    item."""

    contract_id: UUID
    status: str
    items: list[ExtractedItemResponse]
    warnings: list[str]


class HomologateRequest(BaseModel):
    """POST /contracts/{id}/homologate — a Tela de Conferência sempre
    manda a lista COMPLETA e final revisada pelo faturista (não um diff
    incremental contra a extração da IA) — ver DECISÃO em
    ContractItemRepository.replace_items."""

    items: list[ContractItemInput] = Field(min_length=1)
