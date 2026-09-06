"""tests/integration/test_integrations.py — Central de Integrações &
Webhooks: emissão/revogação de chaves de API por tenant.

A segunda metade deste arquivo (a partir de _fake_ingestion_bucket)
prova que a chave de fato AUTENTICA algo — ver DECISÃO completa em
app/api/api_key_auth.py e app/api/v1/endpoints/integrations.py sobre o
bug corrigido (chaves emitidas, nunca verificadas em lugar nenhum)."""
import io
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


async def test_owner_can_create_list_and_revoke_api_key(client, auth_headers_a):
    create_resp = await client.post("/api/v1/integrations/api-keys", json={"name": "ERP Produção"}, headers=auth_headers_a)
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["api_key"].startswith("iarcm_")
    assert body["key_prefix"] == body["api_key"][:12]
    key_id = body["id"]

    list_resp = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert all("api_key" not in item for item in list_resp.json())  # nunca reexibe o segredo
    assert any(item["id"] == key_id for item in list_resp.json())

    revoke_resp = await client.delete(f"/api/v1/integrations/api-keys/{key_id}", headers=auth_headers_a)
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["revoked_at"] is not None


async def test_atendimento_cannot_manage_api_keys(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao2@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/v1/integrations/api-keys", json={"name": "Tentativa indevida"}, headers=headers)
    assert resp.status_code == 403


async def test_tenant_b_cannot_see_tenant_a_api_keys(client, auth_headers_a, auth_headers_b):
    await client.post("/api/v1/integrations/api-keys", json={"name": "Chave da Clínica A"}, headers=auth_headers_a)

    list_b = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_b)
    assert list_b.status_code == 200
    assert list_b.json() == []


# =====================================================================
# POST /integrations/ingest — a chave de API de fato autenticando algo.
# =====================================================================


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """Mesma técnica de test_ingestion_upload.py: mocka só a fronteira de
    rede externa (S3), tudo o mais (auth por API key, RLS, parsing,
    normalização, Postgres) roda de verdade."""
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


def _valid_csv_bytes() -> bytes:
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026"
    return (header + "\r\n" + row + "\r\n").encode("utf-8-sig")


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _issue_api_key(client, auth_headers, name="ERP de teste") -> str:
    resp = await client.post("/api/v1/integrations/api-keys", json={"name": name}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["api_key"]


async def test_ingest_via_api_key_processes_file(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    api_key = await _issue_api_key(client, auth_headers_a)

    files = {"file": ("faturamento_erp.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/integrations/ingest", files=files, headers={"X-API-Key": api_key})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "processed"
    assert body["row_count"] == 1

    # Foi de fato normalizado NO TENANT CERTO (visível via JWT normal).
    billing_resp = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert billing_resp.status_code == 200


async def test_ingest_without_api_key_header_401(client):
    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/integrations/ingest", files=files)
    assert response.status_code == 401


async def test_ingest_with_bogus_api_key_401(client):
    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post(
        "/api/v1/integrations/ingest", files=files, headers={"X-API-Key": "iarcm_" + "0" * 48}
    )
    assert response.status_code == 401


async def test_ingest_with_revoked_api_key_401(client, auth_headers_a):
    create_resp = await client.post("/api/v1/integrations/api-keys", json={"name": "Vai ser revogada"}, headers=auth_headers_a)
    key_id = create_resp.json()["id"]
    api_key = create_resp.json()["api_key"]

    revoke_resp = await client.delete(f"/api/v1/integrations/api-keys/{key_id}", headers=auth_headers_a)
    assert revoke_resp.status_code == 200

    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/integrations/ingest", files=files, headers={"X-API-Key": api_key})
    assert response.status_code == 401


async def test_ingest_via_api_key_from_tenant_a_never_touches_tenant_b(
    client, auth_headers_a, auth_headers_b, admin_engine, tenant_a
):
    await _create_insurance_plan(admin_engine, tenant_a)
    api_key = await _issue_api_key(client, auth_headers_a)

    files = {"file": ("faturamento_erp.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/integrations/ingest", files=files, headers={"X-API-Key": api_key})
    assert response.status_code == 201

    # A chave é da Clínica A — o arquivo de ingestão aparece só lá.
    files_a = await client.get("/api/v1/ingestion/files", headers=auth_headers_a)
    assert files_a.json()["total"] == 1
    files_b = await client.get("/api/v1/ingestion/files", headers=auth_headers_b)
    assert files_b.json()["total"] == 0


async def test_ingest_updates_last_used_at(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    create_resp = await client.post("/api/v1/integrations/api-keys", json={"name": "Rastreada"}, headers=auth_headers_a)
    key_id = create_resp.json()["id"]
    api_key = create_resp.json()["api_key"]

    before = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_a)
    assert next(k for k in before.json() if k["id"] == key_id)["last_used_at"] is None

    files = {"file": ("faturamento_erp.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    await client.post("/api/v1/integrations/ingest", files=files, headers={"X-API-Key": api_key})

    after = await client.get("/api/v1/integrations/api-keys", headers=auth_headers_a)
    assert next(k for k in after.json() if k["id"] == key_id)["last_used_at"] is not None


async def test_ingest_rejects_unrecognized_data_type(client, auth_headers_a):
    api_key = await _issue_api_key(client, auth_headers_a)
    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post(
        "/api/v1/integrations/ingest", files=files, data={"data_type": "planilha_qualquer"}, headers={"X-API-Key": api_key}
    )
    assert response.status_code == 400
