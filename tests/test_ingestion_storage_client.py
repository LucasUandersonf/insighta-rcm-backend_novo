"""
tests/test_ingestion_storage_client.py

`build_upload_key` é função pura (sem rede/S3 de verdade) — cobre a
convenção de chave compartilhada com app/worker/s3_key_resolver.py,
inclusive o segmento opcional "agenda/" (ver DECISÃO nos dois módulos).
"""
from app.services.ingestion_storage_client import build_upload_key

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


def test_faturamento_key_has_no_data_type_segment():
    """Retrocompatibilidade: a chave de um upload de Faturamento
    continua IDÊNTICA a antes de `data_type` existir como parâmetro —
    nenhum arquivo já enviado muda de chave de idempotência."""
    key = build_upload_key(_TENANT_ID, "faturamento", "csv", "faturamento_ago.csv")
    assert key == f"tenants/{_TENANT_ID}/incoming/csv/faturamento_ago.csv"


def test_agenda_key_has_data_type_segment():
    key = build_upload_key(_TENANT_ID, "agenda", "csv", "agenda_ago.csv")
    assert key == f"tenants/{_TENANT_ID}/incoming/agenda/csv/agenda_ago.csv"


def test_agenda_key_matches_the_s3_key_resolver_convention():
    """A chave que o upload HTTP produz precisa ser exatamente a que
    s3_key_resolver.resolve() sabe interpretar — é essa consistência que
    permite o mesmo objeto ser roteado pelo caminho SQS sem mudança."""
    from app.worker.s3_key_resolver import resolve

    key = build_upload_key(_TENANT_ID, "agenda", "json", "agenda.json")
    resolved = resolve(key)
    assert resolved.data_type == "agenda"
    assert resolved.file_format == "json"
