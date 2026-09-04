"""
app/services/ingestion_processing_service.py

Núcleo COMPARTILHADO do pipeline de ingestão (Etapas 1+2 do briefing),
extraído de app/worker/ingestion_worker.py para ter DOIS chamadores sobre
a MESMA implementação — nenhuma lógica de parsing/normalização é
duplicada entre eles:

  1. app/worker/ingestion_worker.py — caminho ASSÍNCRONO via SQS
     (SFTP -> S3 Event Notification -> SQS -> worker fazendo polling).
     Continua responsável por TUDO que é específico de SQS (parsing da
     mensagem, visibility timeout, DLQ/retry) — só a parte "processar UM
     arquivo já em mãos" foi movida para cá.
  2. app/api/v1/endpoints/ingestion.py (`POST /ingestion/upload`) —
     caminho SÍNCRONO via HTTP: o usuário sobe um arquivo pela tela do
     produto e recebe o resultado do processamento na MESMA requisição,
     sem precisar de fila nem polling.

DECISÃO — a função recebe bucket/key/version/format/bytes já resolvidos,
nunca "vai buscar" nada sozinha
-------------------------------------------------------------------------
Quem decide DE ONDE vêm os bytes (baixar do S3, ou já tê-los em mãos por
causa de um upload HTTP que acabou de gravar no S3) é responsabilidade do
CHAMADOR, não desta função. Isso mantém `process_uploaded_file` testável
sem precisar de rede/S3 e sem acoplar a lógica de negócio (claim/parse/
save/normalize/mark) à origem do dado.

DECISÃO — a sessão de banco já deve estar TENANT-AWARE ao chegar aqui
-------------------------------------------------------------------------
Esta função NUNCA abre sessão nem decide qual tenant_id usar — ambos os
chamadores já resolveram e VALIDARAM o tenant_id antes de chegar aqui
(o worker contra core.tenants via uma sessão sem tenant, ver
_validate_active_tenant em ingestion_worker.py; o endpoint HTTP a partir
do JWT já assinado, ver app/api/deps.py) e já abriram a sessão certa
(get_db_with_tenant). Misturar "decidir o tenant" com "processar o
arquivo" tornaria esta função perigosa de reutilizar — um terceiro
chamador futuro poderia esquecer a validação e vazar dado entre tenants.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_file import IngestionFile
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.billing_repository import BillingRepository
from app.repositories.contract_item_repository import ContractItemRepository
from app.repositories.guia_repository import GuiaRepository
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.insurance_plan_repository import InsurancePlanRepository
from app.repositories.local_repository import LocalRepository
from app.repositories.patient_repository import PatientRepository
from app.repositories.professional_repository import ProfessionalRepository
from app.services.normalization_service import NormalizationService
from app.worker.parsers import csv_parser, json_parser, xml_parser

_PARSERS = {
    "csv": csv_parser.parse,
    "xml": xml_parser.parse,
    "json": json_parser.parse,
}


class UnknownFileFormatError(Exception):
    """file_format fora de {csv, xml, json}. Na prática, quem chama esta
    função já validou o formato antes (s3_key_resolver.py no caminho SQS;
    detecção por extensão/content-type no endpoint HTTP) — esta checagem
    aqui é só a segunda camada de defesa de sempre, para esta função
    nunca confiar cegamente em quem a chama."""


class FileParsingError(Exception):
    """
    Falha ESTRUTURAL ao parsear o arquivo INTEIRO (ex: XML corrompido
    demais para o parser entender, JSON cuja raiz não é uma lista) —
    diferente de um erro de validação de UMA linha, que fica registrado
    em ingestion_raw_rows.validation_errors sem impedir o resto do
    arquivo (ver RowParseResult.failed).

    Carrega a IngestionFile já reclamada (claim_file já rodou antes do
    parser ser chamado) para quem lida com a exceção poder relatar
    corretamente qual arquivo falhou — mesmo sabendo que, na prática, a
    transação inteira é revertida pelo `session.begin()` de
    get_db_with_tenant quando esta exceção sobe (ver DECISÃO em
    app/db/session.py): nada fica de fato persistido, e uma nova
    tentativa (novo upload HTTP, ou o SQS reentregando a mensagem) pode
    reclamar o mesmo arquivo de novo sem esbarrar em duplicata falsa.
    """

    def __init__(self, message: str, *, ingestion_file: IngestionFile | None = None):
        super().__init__(message)
        self.ingestion_file = ingestion_file


@dataclass
class ProcessingResult:
    ingestion_file: IngestionFile
    already_claimed: bool
    structural_error_count: int = 0
    normalized_count: int = 0
    rejected_count: int = 0


async def process_uploaded_file(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    s3_bucket: str,
    s3_key: str,
    s3_version_id: str | None,
    file_format: str,
    raw_bytes: bytes,
    original_filename: str | None = None,
) -> ProcessingResult:
    """
    Dado bytes já em mãos + tenant_id JÁ VALIDADO, roda o pipeline
    completo — claim_file() -> parse -> save_raw_rows() ->
    NormalizationService.normalize_rows() -> mark_processed() — dentro da
    sessão `db` recebida, que já deve estar tenant-aware (SET LOCAL
    app.current_tenant já aplicado pelo chamador).

    `original_filename` é opcional e existe só para o caminho HTTP (o
    caminho SQS não tem "nome original" — a chave S3 já É o nome do
    arquivo do jeito que o SFTP depositou); fica None para o worker.

    Retorna `ProcessingResult` com `already_claimed=True` quando o
    arquivo (mesma chave de idempotência tenant_id+bucket+key+version)
    já havia sido reclamado antes — o caminho FELIZ de uma entrega
    duplicada (ver IngestionRepository.claim_file), NUNCA uma exceção.
    Levanta `FileParsingError` só quando o arquivo INTEIRO não pôde ser
    parseado (erros por linha não levantam exceção — ficam em
    ingestion_raw_rows.status='rejected', contados em `rejected_count`
    depois da normalização).
    """
    if file_format not in _PARSERS:
        raise UnknownFileFormatError(f"Formato de arquivo não suportado: {file_format!r}")

    repo = IngestionRepository(db)
    ingestion_file = await repo.claim_file(
        tenant_id=tenant_id,
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        s3_version_id=s3_version_id,
        file_format=file_format,
        original_filename=original_filename,
    )
    if ingestion_file is None:
        existing = await repo.get_file_by_idempotency_key(
            tenant_id=tenant_id, s3_bucket=s3_bucket, s3_key=s3_key, s3_version_id=s3_version_id
        )
        assert existing is not None  # claim_file só retorna None se a linha JÁ existir
        return ProcessingResult(ingestion_file=existing, already_claimed=True)

    try:
        parser = _PARSERS[file_format]
        parse_results = parser(raw_bytes)
    except Exception as exc:
        error_message = f"Falha ao parsear arquivo {file_format}: {exc}"
        # Best-effort: registra o motivo da falha na própria linha, mesmo
        # sabendo que a transação inteira será revertida pela exceção que
        # levantamos logo abaixo (ver docstring de FileParsingError) — não
        # tem custo e documenta a intenção para o dia em que este fluxo
        # deixar de rodar dentro de uma única transação tudo-ou-nada.
        await repo.mark_failed(ingestion_file.id, error_message=error_message)
        raise FileParsingError(error_message, ingestion_file=ingestion_file) from exc

    saved_rows = await repo.save_raw_rows(ingestion_file.id, tenant_id, parse_results)
    structural_error_count = sum(1 for r in parse_results if r.errors)

    normalization_service = NormalizationService(
        patient_repo=PatientRepository(db),
        professional_repo=ProfessionalRepository(db),
        appointment_repo=AppointmentRepository(db),
        contract_item_repo=ContractItemRepository(db),
        insurance_plan_repo=InsurancePlanRepository(db),
        billing_repo=BillingRepository(db),
        local_repo=LocalRepository(db),
        guia_repo=GuiaRepository(db),
    )
    summary = await normalization_service.normalize_rows(tenant_id, saved_rows, source_file=s3_key)

    row_count = len(parse_results)
    error_row_count = structural_error_count + summary.rejected
    await repo.mark_processed(ingestion_file.id, row_count=row_count, error_row_count=error_row_count)

    # mark_processed() é um UPDATE via Core (não via ORM), então o objeto
    # `ingestion_file` em memória não reflete sozinho os novos valores de
    # status/row_count/error_row_count/processed_at — refresh explícito
    # para quem chama (ex: o endpoint HTTP, que devolve esses campos na
    # resposta) ver o estado final real, não o estado de quando foi
    # reclamado ("processing", contadores zerados).
    await db.refresh(ingestion_file)

    return ProcessingResult(
        ingestion_file=ingestion_file,
        already_claimed=False,
        structural_error_count=structural_error_count,
        normalized_count=summary.normalized,
        rejected_count=summary.rejected,
    )
