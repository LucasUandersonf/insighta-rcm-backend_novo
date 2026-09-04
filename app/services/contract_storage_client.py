"""
app/services/contract_storage_client.py

Upload SÍNCRONO (do ponto de vista do request HTTP) de UM PDF de
contrato, disparado pela tela de Convênios — pipeline DIFERENTE da
Etapa 1 de ingestão em massa (SFTP -> S3 Event -> SQS -> worker, ver
app/worker/ingestion_worker.py). Aqui não há fila: o usuário sobe um
arquivo, espera a resposta, e o próximo passo (extração por IA) já pode
ser disparado em seguida — por isso um client fininho e direto em vez de
reaproveitar o pipeline assíncrono de lote.

DECISÃO — bucket separado (AWS_S3_CONTRACTS_BUCKET) do bucket de
ingestão (AWS_S3_INGEST_BUCKET)
-------------------------------------------------------------------------
São dados de natureza diferente (um PDF de contrato assinado vs. um CSV/
XML de lote de faturamento) com política de retenção e de acesso
potencialmente diferentes (o PDF original pode precisar ser retido por
mais tempo por motivo contratual/auditoria) — misturar os dois no mesmo
bucket/prefixo acopla decisões de infraestrutura que deviam ser
independentes.
"""
import uuid

import aioboto3

from app.core.aws_s3 import s3_client_kwargs
from app.core.config import get_settings

settings = get_settings()


class ContractStorageError(Exception):
    pass


def build_pdf_key(tenant_id: str, contract_id: uuid.UUID, filename: str) -> str:
    safe_filename = filename.replace("/", "_")
    return f"tenants/{tenant_id}/contracts/{contract_id}/{safe_filename}"


class ContractStorageClient:
    def __init__(self):
        if not settings.AWS_S3_CONTRACTS_BUCKET:
            raise ContractStorageError("AWS_S3_CONTRACTS_BUCKET não configurado.")
        self._bucket = settings.AWS_S3_CONTRACTS_BUCKET
        self._session = aioboto3.Session()

    async def upload_pdf(self, *, key: str, pdf_bytes: bytes) -> None:
        async with self._session.client("s3", **s3_client_kwargs()) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=pdf_bytes, ContentType="application/pdf")

    async def download_pdf(self, *, key: str) -> bytes:
        async with self._session.client("s3", **s3_client_kwargs()) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()
