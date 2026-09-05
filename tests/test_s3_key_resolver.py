"""
tests/test_s3_key_resolver.py

Função pura (sem banco/rede) — cobre a extensão da convenção de chave S3
pra reconhecer o segmento opcional "agenda/" (ver DECISÃO em
app/worker/s3_key_resolver.py) sem quebrar a convenção de Faturamento
que já existia.
"""
import uuid

import pytest

from app.worker.s3_key_resolver import InvalidIngestionKeyError, resolve

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def test_faturamento_key_without_data_type_segment():
    resolved = resolve(f"tenants/{_TENANT_ID}/incoming/csv/faturamento_ago.csv")
    assert resolved.tenant_id == uuid.UUID(_TENANT_ID)
    assert resolved.file_format == "csv"
    assert resolved.data_type == "faturamento"


@pytest.mark.parametrize("file_format", ["csv", "xml", "json"])
def test_agenda_key_with_data_type_segment(file_format):
    resolved = resolve(f"tenants/{_TENANT_ID}/incoming/agenda/{file_format}/agenda_ago.{file_format}")
    assert resolved.tenant_id == uuid.UUID(_TENANT_ID)
    assert resolved.file_format == file_format
    assert resolved.data_type == "agenda"


def test_unrecognized_data_type_segment_is_invalid():
    """Só "agenda/" é reconhecido — qualquer outro segmento no meio da
    chave não bate no padrão e é tratado como chave inválida (ignorada e
    logada), nunca adivinhado."""
    with pytest.raises(InvalidIngestionKeyError):
        resolve(f"tenants/{_TENANT_ID}/incoming/repasse/csv/arquivo.csv")


def test_invalid_uuid_still_rejected():
    with pytest.raises(InvalidIngestionKeyError):
        resolve("tenants/nao-e-um-uuid/incoming/csv/arquivo.csv")


def test_unrecognized_file_format_still_rejected():
    with pytest.raises(InvalidIngestionKeyError):
        resolve(f"tenants/{_TENANT_ID}/incoming/agenda/pdf/arquivo.pdf")
