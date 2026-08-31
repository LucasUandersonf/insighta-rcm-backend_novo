"""
app/worker/s3_key_resolver.py

Resolve tenant_id e formato de arquivo a partir da chave do objeto S3,
seguindo a convenção documentada em 003_ingestion_tables.sql:

    tenants/{tenant_id}/incoming/{csv|xml|json}/{arquivo}

Tratamos qualquer valor extraído daqui como NÃO CONFIÁVEL até validar
contra core.tenants (feito em ingestion_worker.py, não aqui) — este
módulo só faz parsing sintático da string, nunca decide sozinho se o
tenant é válido.
"""
import re
import uuid
from dataclasses import dataclass

_KEY_PATTERN = re.compile(
    r"^tenants/(?P<tenant_id>[0-9a-fA-F-]{36})/incoming/(?P<file_format>csv|xml|json)/.+$"
)


@dataclass
class ResolvedKey:
    tenant_id: uuid.UUID
    file_format: str


class InvalidIngestionKeyError(Exception):
    """A chave S3 não segue a convenção esperada — arquivo é ignorado e logado, não processado."""


def resolve(s3_key: str) -> ResolvedKey:
    match = _KEY_PATTERN.match(s3_key)
    if not match:
        raise InvalidIngestionKeyError(f"Chave S3 fora do padrão esperado: {s3_key!r}")
    try:
        tenant_id = uuid.UUID(match.group("tenant_id"))
    except ValueError as exc:
        raise InvalidIngestionKeyError(f"tenant_id inválido na chave S3: {s3_key!r}") from exc
    return ResolvedKey(tenant_id=tenant_id, file_format=match.group("file_format"))
