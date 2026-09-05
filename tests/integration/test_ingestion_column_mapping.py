"""
tests/integration/test_ingestion_column_mapping.py

Mapeador Automático de Coluna (ver app/sql/021_ingestion_column_aliases.sql
e app/services/column_mapping_service.py) — cobre o fluxo completo:
preview de cabeçalho -> confirmação de mapeamento -> upload real
aplicando o alias salvo. Mesma técnica de mock (só a fronteira de rede
do S3) de test_ingestion_upload.py.
"""
import io
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-column-mapping")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


# Cabeçalho NÃO padrão: "Nome do Paciente" em vez de "nome_paciente" — o
# único campo obrigatório que o padrão não reconhece neste arquivo.
_NONSTANDARD_HEADER = "cpf_paciente;Nome do Paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
_NONSTANDARD_ROW = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026"


def _csv_bytes(header: str, *rows: str) -> bytes:
    return (header + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")


async def test_preview_headers_suggests_mapping_for_nonstandard_header(client, auth_headers_a):
    files = {"file": ("faturamento.csv", io.BytesIO(_csv_bytes(_NONSTANDARD_HEADER, _NONSTANDARD_ROW)), "text/csv")}
    response = await client.post("/api/v1/ingestion/preview-headers", files=files, data={"data_type": "faturamento"}, headers=auth_headers_a)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Nome do Paciente" in body["raw_headers"]
    assert body["suggested_mapping"].get("Nome do Paciente") == "patient_name"
    assert "patient_name" not in body["unresolved_required_fields"]


async def test_upload_with_nonstandard_header_is_rejected_before_mapping_confirmed(client, auth_headers_a, admin_engine, tenant_a):
    """Sem o alias confirmado, o comportamento é o de sempre: a linha é
    rejeitada por campo obrigatório ausente (nenhuma mágica automática
    sem confirmação explícita do usuário)."""
    await _create_insurance_plan(admin_engine, tenant_a)
    files = {"file": ("faturamento_sem_mapa.csv", io.BytesIO(_csv_bytes(_NONSTANDARD_HEADER, _NONSTANDARD_ROW)), "text/csv")}
    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 1


async def test_full_flow_preview_confirm_then_upload_succeeds(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)

    preview_files = {"file": ("faturamento.csv", io.BytesIO(_csv_bytes(_NONSTANDARD_HEADER, _NONSTANDARD_ROW)), "text/csv")}
    preview = await client.post(
        "/api/v1/ingestion/preview-headers", files=preview_files, data={"data_type": "faturamento"}, headers=auth_headers_a
    )
    assert preview.status_code == 200
    suggested = preview.json()["suggested_mapping"]
    assert suggested

    save_resp = await client.post(
        "/api/v1/ingestion/column-aliases",
        json={"data_type": "faturamento", "mapping": suggested},
        headers=auth_headers_a,
    )
    assert save_resp.status_code == 201, save_resp.text
    saved = save_resp.json()
    assert any(a["source_header"] == "Nome do Paciente" and a["canonical_field"] == "patient_name" for a in saved)

    upload_files = {"file": ("faturamento_com_mapa.csv", io.BytesIO(_csv_bytes(_NONSTANDARD_HEADER, _NONSTANDARD_ROW)), "text/csv")}
    upload_resp = await client.post("/api/v1/ingestion/upload", files=upload_files, headers=auth_headers_a)
    assert upload_resp.status_code == 201, upload_resp.text
    assert upload_resp.json()["error_row_count"] == 0

    async with admin_engine.begin() as conn:
        result = await conn.execute(text("SELECT * FROM core.patients WHERE tenant_id = :t"), {"t": tenant_a})
        patients = result.mappings().all()
    assert any(p["full_name"] == "Paciente Teste" for p in patients)


async def test_list_and_delete_column_aliases(client, auth_headers_a):
    save_resp = await client.post(
        "/api/v1/ingestion/column-aliases",
        json={"data_type": "faturamento", "mapping": {"Nome do Paciente": "patient_name"}},
        headers=auth_headers_a,
    )
    assert save_resp.status_code == 201
    alias_id = save_resp.json()[0]["id"]

    list_resp = await client.get("/api/v1/ingestion/column-aliases", params={"data_type": "faturamento"}, headers=auth_headers_a)
    assert list_resp.status_code == 200
    assert any(a["id"] == alias_id for a in list_resp.json())

    delete_resp = await client.delete(f"/api/v1/ingestion/column-aliases/{alias_id}", headers=auth_headers_a)
    assert delete_resp.status_code == 204

    list_after = await client.get("/api/v1/ingestion/column-aliases", params={"data_type": "faturamento"}, headers=auth_headers_a)
    assert list_after.json() == []


async def test_save_column_alias_rejects_unknown_canonical_field(client, auth_headers_a):
    response = await client.post(
        "/api/v1/ingestion/column-aliases",
        json={"data_type": "faturamento", "mapping": {"Alguma_Coluna": "campo_que_nao_existe"}},
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_preview_headers_rejects_non_faturamento_data_type(client, auth_headers_a):
    files = {"file": ("agenda.csv", io.BytesIO(b"cpf_paciente;nome_paciente\r\n"), "text/csv")}
    response = await client.post("/api/v1/ingestion/preview-headers", files=files, data={"data_type": "agenda"}, headers=auth_headers_a)
    assert response.status_code == 400


async def test_preview_headers_rejects_xml(client, auth_headers_a):
    files = {"file": ("faturamento.xml", io.BytesIO(b"<atendimentos></atendimentos>"), "application/xml")}
    response = await client.post("/api/v1/ingestion/preview-headers", files=files, data={"data_type": "faturamento"}, headers=auth_headers_a)
    assert response.status_code == 400


async def test_column_aliases_require_manage_role(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="atendimento@column-mapping.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/ingestion/column-aliases", headers=headers)
    assert response.status_code == 403
