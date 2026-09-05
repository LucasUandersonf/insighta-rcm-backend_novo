"""
app/services/ingestion_storage_client.py

Client S3 fino para o upload HTTP SÍNCRONO de um arquivo operacional de
lote (CSV/XML/JSON), feito pelo usuário através da tela do produto —
POST /ingestion/upload (app/api/v1/endpoints/ingestion.py). Mesmo
estilo/formato de app/services/contract_storage_client.py: um client
pequeno, direto, sem fila, porque aqui o usuário sobe o arquivo e espera
a resposta na mesma requisição (ver app/services/ingestion_processing_service.py
para o motivo de o PROCESSAMENTO em si ser compartilhado com o worker
SQS — só o transporte/armazenamento é diferente).

DECISÃO — reaproveita AWS_S3_INGEST_BUCKET, o MESMO bucket do pipeline
SFTP -> S3 Event Notification -> SQS (app/worker/ingestion_worker.py),
em vez de um bucket dedicado ao upload HTTP
-------------------------------------------------------------------------
Diferente do PDF de contrato (AWS_S3_CONTRACTS_BUCKET) ou do anexo de
recurso de glosa (AWS_S3_APPEALS_BUCKET) — que são artefatos de natureza
e retenção DIFERENTES do CSV/XML/JSON de faturamento em lote — aqui o
arquivo enviado por HTTP É exatamente o MESMO tipo de objeto que o
worker já processa, só chegando por um caminho diferente (upload direto
em vez de SFTP). Reaproveitar o bucket já existente e a MESMA convenção
de chave de app/worker/s3_key_resolver.py (tenants/{tenant_id}/incoming/
{formato}/{arquivo}) significa que, se um dia quisermos voltar a rotear
esse tráfego pela fila SQS em vez de processar sincronamente, o objeto
já está exatamente onde o S3 Event Notification esperaria encontrá-lo —
nenhuma migração de dado ou de convenção necessária. Um bucket HTTP
separado duplicaria a mesma convenção de chave sem nenhum ganho real de
isolamento (mesmo tipo de dado, mesma política de retenção).

Um bucket faltando (AWS_S3_INGEST_BUCKET não configurado) NUNCA deve
falhar silenciosamente — o endpoint que usa este client converte
`IngestionStorageError` em HTTP 503 com mensagem explícita (mesmo padrão
de app/services/contract_storage_client.py).
"""
import aioboto3

from app.core.aws_s3 import s3_client_kwargs
from app.core.config import get_settings

settings = get_settings()


class IngestionStorageError(Exception):
    pass


def build_upload_key(tenant_id: str, data_type: str, file_format: str, filename: str) -> str:
    """
    MESMA convenção de app/worker/s3_key_resolver.py:
        tenants/{tenant_id}/incoming/{csv|xml|json}/{arquivo}                (faturamento)
        tenants/{tenant_id}/incoming/agenda/{csv|xml|json}/{arquivo}         (agenda)
    Isso importa de verdade: é essa convenção que permite o mesmo objeto
    ser roteado pelo caminho SQS sem qualquer mudança (ver DECISÃO
    acima) — inclusive para o template de Agenda agora, que o worker SQS
    já reconhece (ver s3_key_resolver.py). `data_type="faturamento"` NÃO
    acrescenta segmento nenhum, de propósito: mantém a chave de todo
    upload de Faturamento (formato ou template) idêntica a antes deste
    parâmetro existir — nenhum arquivo já enviado muda de chave.
    """
    safe_filename = (filename or "arquivo").replace("/", "_").strip() or "arquivo"
    prefix = f"{data_type}/" if data_type != "faturamento" else ""
    return f"tenants/{tenant_id}/incoming/{prefix}{file_format}/{safe_filename}"


class IngestionStorageClient:
    def __init__(self):
        if not settings.AWS_S3_INGEST_BUCKET:
            raise IngestionStorageError(
                "AWS_S3_INGEST_BUCKET não configurado — upload de arquivo de ingestão indisponível."
            )
        self.bucket = settings.AWS_S3_INGEST_BUCKET
        self._session = aioboto3.Session()

    async def upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        """Envia o objeto e retorna o VersionId, se o bucket for versionado
        (senão None) — vira o `s3_version_id` da chave de idempotência em
        core.ingestion_files, exatamente como faria o campo `versionId` de
        um evento S3 real no caminho SQS."""
        async with self._session.client("s3", **s3_client_kwargs()) as s3:
            response = await s3.put_object(Bucket=self.bucket, Key=key, Body=raw_bytes)
            return response.get("VersionId")
