"""
tests/integration/test_data_freshness.py

Achado do Dossiê Insighta RCM ("Como o dado entra no sistema") — ponta a
ponta via GET /analytics/data-freshness. Prova que
AnalyticsService.get_data_freshness só conta IngestionFile com
status='processed' (nunca 'failed'/'received'), agrupa por data_type
(MAX(processed_at)), e que stalest_at é o pior caso entre os tipos já
importados com sucesso.
"""
import uuid
from datetime import datetime

from sqlalchemy import text


async def _seed_ingestion_file(
    admin_engine, tenant_id, *, data_type: str, status: str, processed_offset_seconds: int | None
) -> str:
    file_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.ingestion_files "
                "(id, tenant_id, s3_bucket, s3_key, file_format, status, data_type, row_count, error_row_count, processed_at) "
                "VALUES (:id, :t, 'bucket-teste', :key, 'csv', :status, :data_type, 1, 0, "
                " CASE WHEN CAST(:offset AS INTEGER) IS NULL THEN NULL ELSE now() - make_interval(secs => CAST(:offset AS INTEGER)) END)"
            ),
            {
                "id": file_id,
                "t": tenant_id,
                "key": f"tenants/{tenant_id}/incoming/csv/{file_id}.csv",
                "status": status,
                "data_type": data_type,
                "offset": processed_offset_seconds,
            },
        )
    return file_id


async def test_no_ingestion_yet_returns_empty_items_and_no_stalest(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["stalest_at"] is None


async def test_returns_most_recent_processed_at_per_data_type(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="processed", processed_offset_seconds=3600)
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="processed", processed_offset_seconds=60)
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="agenda", status="processed", processed_offset_seconds=120)

    response = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    by_type = {item["data_type"]: datetime.fromisoformat(item["last_ingested_at"]) for item in body["items"]}
    assert set(by_type.keys()) == {"faturamento", "agenda"}
    # MAX(processed_at) por tipo: o mais recente ("60s atrás") deve vencer o mais antigo ("3600s atrás")
    assert by_type["faturamento"] > by_type["agenda"]


async def test_failed_and_received_files_are_not_counted(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="failed", processed_offset_seconds=10)
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="agenda", status="processing", processed_offset_seconds=None)

    response = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_never_imported_data_type_never_appears(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="processed", processed_offset_seconds=30)

    response = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_a)
    assert response.status_code == 200
    by_type = {item["data_type"] for item in response.json()["items"]}
    assert by_type == {"faturamento"}


async def test_stalest_at_is_the_oldest_of_the_freshest_per_type(client, auth_headers_a, admin_engine, tenant_a):
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="processed", processed_offset_seconds=60)
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="glosa", status="processed", processed_offset_seconds=7200)

    response = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    glosa_item = next(i for i in body["items"] if i["data_type"] == "glosa")
    assert body["stalest_at"] == glosa_item["last_ingested_at"]


async def test_data_freshness_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    await _seed_ingestion_file(admin_engine, tenant_a, data_type="faturamento", status="processed", processed_offset_seconds=30)

    response_b = await client.get("/api/v1/analytics/data-freshness", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["items"] == []
