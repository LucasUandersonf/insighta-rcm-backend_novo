"""
app/services/appeal_storage_client.py

Mesmo padrão de app/services/contract_storage_client.py, bucket
separado (AWS_S3_APPEALS_BUCKET) — ver DECISÃO em
app/sql/008_denial_appeals.sql sobre por que os três buckets (ingestão,
contratos, recursos) não se misturam.
"""
import uuid

import aioboto3

from app.core.aws_s3 import s3_client_kwargs
from app.core.config import get_settings

settings = get_settings()


class AppealStorageError(Exception):
    pass


def build_attachment_key(tenant_id: str, appeal_id: uuid.UUID, filename: str) -> str:
    safe_filename = filename.replace("/", "_")
    return f"tenants/{tenant_id}/denial-appeals/{appeal_id}/{safe_filename}"


class AppealStorageClient:
    def __init__(self):
        if not settings.AWS_S3_APPEALS_BUCKET:
            raise AppealStorageError("AWS_S3_APPEALS_BUCKET não configurado.")
        self._bucket = settings.AWS_S3_APPEALS_BUCKET
        self._session = aioboto3.Session()

    async def upload_file(self, *, key: str, content: bytes, content_type: str) -> None:
        async with self._session.client("s3", **s3_client_kwargs()) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=content, ContentType=content_type)

    async def download_file(self, *, key: str) -> bytes:
        async with self._session.client("s3", **s3_client_kwargs()) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as stream:
                return await stream.read()
