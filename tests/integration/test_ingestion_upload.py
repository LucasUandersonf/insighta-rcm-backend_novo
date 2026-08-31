"""
tests/integration/test_ingestion_upload.py

Cobre o caminho HTTP síncrono de ingestão (POST /ingestion/upload e
GET /ingestion/files) — ver app/api/v1/endpoints/ingestion.py e
app/services/ingestion_processing_service.py.

DECISÃO — como este teste evita depender de um S3 real
-------------------------------------------------------------------------
O projeto não tem, em nenhum outro teste (test_contracts.py e
test_denial_appeals.py incluídos — ambos têm endpoints de upload que
falam com S3 via app/services/contract_storage_client.py, mas nenhum dos
dois é exercitado por um teste de integração hoje), uma convenção
estabelecida de S3 fake/mockado (nem moto, nem qualquer outra lib —
confirmado via grep no repositório). Como este ambiente de sandbox não
tem acesso a instalar dependências novas (moto incluso) nem a uma AWS de
teste, o caminho mais fiel ao "Zero Mocks" que dá para seguir aqui é:
mockar SÓ a fronteira de rede externa (o método `upload_bytes` do
IngestionStorageClient, que faz uma chamada de rede real a um serviço de
terceiro) via monkeypatch, deixando TUDO que é lógica própria do produto
— autenticação, RBAC, RLS, claim_file/idempotência, parsing real dos
parsers de verdade, normalização real, Postgres real — rodando sem
nenhum mock. Isso é equivalente, em espírito, ao próprio
`get_db_no_tenant`/`get_db_with_tenant` do projeto não mockar o Postgres:
a única coisa substituída aqui é a chamada de rede para um serviço de
nuvem de terceiro que este sandbox não consegue alcançar de verdade.
"""
import io
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """
    Mesma técnica de tests/integration/test_reports.py (ver docstring de
    lá): `settings = get_settings()` é lido UMA VEZ no import do módulo,
    então setar `AWS_S3_INGEST_BUCKET` via env dentro de um teste não
    teria efeito nenhum a essa altura — mutamos o atributo diretamente no
    objeto Settings já instanciado (`storage_module.settings`). E, para
    não fazer uma chamada de rede real ao S3, trocamos `upload_bytes` da
    classe por uma função falsa — a ÚNICA fronteira de rede externa deste
    caminho; parsing, normalização e banco continuam 100% reais.
    """
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None  # bucket não-versionado, mesmo default do S3

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


async def test_upload_csv_creates_ingestion_file_and_processes_rows(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)

    files = {"file": ("faturamento_agosto.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["file_format"] == "csv"
    assert body["status"] == "processed"
    assert body["row_count"] == 1
    assert body["error_row_count"] == 0
    assert body["already_processed"] is False

    # A linha foi de fato normalizada — deve ter virado um billing real,
    # visível por outro endpoint já existente (nenhuma normalização
    # duplicada foi inventada por este caminho HTTP).
    billing_resp = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert billing_resp.status_code == 200

    # E aparece no histórico.
    files_resp = await client.get("/api/v1/ingestion/files", headers=auth_headers_a)
    assert files_resp.status_code == 200
    listed = files_resp.json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == body["id"]
    assert listed["items"][0]["original_filename"] == "faturamento_agosto.csv"


async def test_duplicate_upload_is_handled_gracefully(client, auth_headers_a, admin_engine, tenant_a):
    """
    Reenviar exatamente o MESMO arquivo (mesmo nome -> mesma chave S3,
    já que a chave é determinística a partir de tenant+formato+filename)
    deve ser tratado como idempotência, não como erro genérico.
    """
    await _create_insurance_plan(admin_engine, tenant_a)
    files = {"file": ("faturamento_agosto.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}

    first = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert first.status_code == 201, first.text

    files_again = {"file": ("faturamento_agosto.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    second = await client.post("/api/v1/ingestion/upload", files=files_again, headers=auth_headers_a)
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["already_processed"] is True
    assert body["id"] == first.json()["id"]

    # Nenhum arquivo NOVO no histórico — continua só 1.
    files_resp = await client.get("/api/v1/ingestion/files", headers=auth_headers_a)
    assert files_resp.json()["total"] == 1


async def test_malformed_csv_returns_422(client, auth_headers_a):
    # csv_parser espera um cabeçalho reconhecível; um conteúdo que faz o
    # parser inteiro estourar (não um mero erro de validação por linha) é
    # bem mais fácil de simular com um XML malformado — força uma exceção
    # de parsing estrutural no defusedxml, não um erro por linha.
    malformed_xml = b"<atendimentos><atendimento><cpfPaciente>123</cpfPaciente" # tag nunca fechada corretamente
    files = {"file": ("lote.xml", io.BytesIO(malformed_xml), "application/xml")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 422, response.text
    assert "parsear" in response.json()["detail"].lower()


async def test_atendimento_role_cannot_upload(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao@ingestion-upload-test.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    response = await client.post("/api/v1/ingestion/upload", files=files, headers=headers)
    assert response.status_code == 403


async def test_unrecognized_extension_returns_400(client, auth_headers_a):
    files = {"file": ("relatorio.pdf", io.BytesIO(b"%PDF-1.4 nao eh um lote valido"), "application/pdf")}
    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 400


async def _seed_ingestion_file(admin_engine, tenant_id, *, filename: str, received_offset_seconds: int) -> str:
    file_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.ingestion_files "
                "(id, tenant_id, s3_bucket, s3_key, file_format, status, row_count, error_row_count, "
                " original_filename, received_at) "
                "VALUES (:id, :t, 'bucket-teste', :key, 'csv', 'processed', 1, 0, :filename, "
                " now() - make_interval(secs => :offset))"
            ),
            {"id": file_id, "t": tenant_id, "key": f"tenants/{tenant_id}/incoming/csv/{filename}", "filename": filename, "offset": received_offset_seconds},
        )
    return file_id


async def test_list_ingestion_files_is_paginated_and_ordered_desc(client, auth_headers_a, admin_engine, tenant_a):
    oldest = await _seed_ingestion_file(admin_engine, tenant_a, filename="arquivo_1.csv", received_offset_seconds=120)
    middle = await _seed_ingestion_file(admin_engine, tenant_a, filename="arquivo_2.csv", received_offset_seconds=60)
    newest = await _seed_ingestion_file(admin_engine, tenant_a, filename="arquivo_3.csv", received_offset_seconds=0)

    response = await client.get("/api/v1/ingestion/files", params={"limit": 2, "offset": 0}, headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert [item["id"] for item in body["items"]] == [newest, middle]

    second_page = await client.get("/api/v1/ingestion/files", params={"limit": 2, "offset": 2}, headers=auth_headers_a)
    assert [item["id"] for item in second_page.json()["items"]] == [oldest]


async def test_auditor_can_list_but_not_upload(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="auditor@ingestion-upload-test.com", role="auditor")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    list_resp = await client.get("/api/v1/ingestion/files", headers=headers)
    assert list_resp.status_code == 200

    files = {"file": ("faturamento.csv", io.BytesIO(_valid_csv_bytes()), "text/csv")}
    upload_resp = await client.post("/api/v1/ingestion/upload", files=files, headers=headers)
    assert upload_resp.status_code == 403
