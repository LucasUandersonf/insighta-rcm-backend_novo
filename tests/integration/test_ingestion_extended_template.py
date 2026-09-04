"""
tests/integration/test_ingestion_extended_template.py

Cobre as 5 colunas novas do TEMPLATE ESTENDIDO de Faturamento
(local_atendimento, tipo_paciente, guia_tipo, guia_numero, guia_senha —
ver conversa/PLANO_ADEQUACAO_TISS.md e app/worker/schemas.py) via o
mesmo caminho HTTP síncrono de test_ingestion_upload.py: POST
/ingestion/upload com um CSV real, Postgres real, só a fronteira de
rede do S3 mockada (ver docstring de lá para a justificativa completa
de por que só essa fronteira é mockada).

Estas colunas fecham o elo entre o pipeline de ingestão (que já existia)
e o modelo de dado TISS (Guia/Local/tipo_paciente) construído nas Fases
1 e 4 do plano de adequação — antes desta extensão, Guia e Local nunca
eram populados por um upload de arquivo real, só por criação manual via
API (incompatível com "o SaaS opera exclusivamente sobre dados
consolidados do ERP").
"""
import io
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """Mesma técnica de test_ingestion_upload.py — ver docstring de lá."""
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao-estendido")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


_HEADER = (
    "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento;"
    "local_atendimento;tipo_paciente;guia_tipo;guia_numero;guia_senha"
)


def _csv_bytes(*rows: str) -> bytes:
    return (_HEADER + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _fetch_one(admin_engine, sql: str, **params) -> dict | None:
    async with admin_engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        row = result.mappings().first()
        return dict(row) if row is not None else None


async def _fetch_all(admin_engine, sql: str, **params) -> list[dict]:
    async with admin_engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]


async def test_upload_with_extended_columns_creates_local_tipo_paciente_and_guia(
    client, auth_headers_a, admin_engine, tenant_a
):
    await _create_insurance_plan(admin_engine, tenant_a)
    row = (
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026;"
        "Unidade Centro;Ambulatorial;SP/SADT;GUIA-001;SENHA-001"
    )
    files = {"file": ("faturamento_estendido.csv", io.BytesIO(_csv_bytes(row)), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["row_count"] == 1
    assert body["error_row_count"] == 0

    # Local foi criado (get-or-create por nome) e o appointment aponta pra ele.
    local = await _fetch_one(admin_engine, "SELECT * FROM core.locais WHERE tenant_id = :t AND nome = :n", t=tenant_a, n="Unidade Centro")
    assert local is not None

    appointment = await _fetch_one(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointment is not None
    assert appointment["local_id"] == local["id"]
    assert appointment["tipo_paciente"] == "ambulatorial"  # normalizado a partir de "Ambulatorial"

    # Guia foi criada (SP/SADT -> "sadt") e a billing aponta pra ela.
    guia = await _fetch_one(admin_engine, "SELECT * FROM core.guias WHERE tenant_id = :t AND numero = :n", t=tenant_a, n="GUIA-001")
    assert guia is not None
    assert guia["tipo"] == "sadt"
    assert guia["senha"] == "SENHA-001"

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert billing is not None
    assert billing["guia_id"] == guia["id"]


async def test_multiple_rows_with_same_guia_numero_are_grouped_into_one_guia(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Caso real de mercado: uma SADT com vários procedimentos na mesma
    guia — várias linhas do arquivo, uma única Guia (ver DECISÃO em
    app/sql/015_billing_guia.sql e _get_or_create_guia)."""
    await _create_insurance_plan(admin_engine, tenant_a)
    row_1 = (
        "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026;"
        ";;SADT;GUIA-COMPARTILHADA;SENHA-XYZ"
    )
    row_2 = (
        "12345678900;Paciente Teste;Unimed Nacional;20202020;J06;80,00;20/08/2026;"
        ";;SADT;GUIA-COMPARTILHADA;SENHA-XYZ"
    )
    files = {"file": ("faturamento_guia_compartilhada.csv", io.BytesIO(_csv_bytes(row_1, row_2)), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["row_count"] == 2
    assert response.json()["error_row_count"] == 0

    guias = await _fetch_all(admin_engine, "SELECT * FROM core.guias WHERE tenant_id = :t AND numero = :n", t=tenant_a, n="GUIA-COMPARTILHADA")
    assert len(guias) == 1  # agrupado numa única guia, não duas

    billings = await _fetch_all(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert len(billings) == 2
    assert {b["guia_id"] for b in billings} == {guias[0]["id"]}


async def test_pronto_socorro_alias_is_normalized(client, auth_headers_a, admin_engine, tenant_a):
    """'PS' é um alias comum de export de ERP para tipo_paciente
    pronto_socorro (ver _TIPO_PACIENTE_ALIASES)."""
    await _create_insurance_plan(admin_engine, tenant_a)
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026;;PS;;;"
    files = {"file": ("faturamento_ps.csv", io.BytesIO(_csv_bytes(row)), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    appointment = await _fetch_one(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointment["tipo_paciente"] == "pronto_socorro"


async def test_guia_numero_without_guia_tipo_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    """guia_numero/guia_senha sem guia_tipo não tem como virar uma Guia de
    verdade (Guia.tipo é obrigatório) — a linha é rejeitada estruturalmente
    (ver check_guia_fields_consistency em app/worker/schemas.py), não
    ignorada em silêncio."""
    await _create_insurance_plan(admin_engine, tenant_a)
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026;;;;GUIA-SEM-TIPO;"
    files = {"file": ("faturamento_guia_invalida.csv", io.BytesIO(_csv_bytes(row)), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["row_count"] == 1
    assert body["error_row_count"] == 1  # rejeitada, não normalizada

    guias = await _fetch_all(admin_engine, "SELECT * FROM core.guias WHERE tenant_id = :t", t=tenant_a)
    assert guias == []


async def test_unrecognized_tipo_paciente_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026;;valor-nao-existe;;;"
    files = {"file": ("faturamento_tipo_invalido.csv", io.BytesIO(_csv_bytes(row)), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 1


async def test_upload_without_extended_columns_still_works_as_before(
    client, auth_headers_a, admin_engine, tenant_a
):
    """Compatibilidade retroativa: um export SEM as colunas novas continua
    funcionando exatamente como antes — nada de Local/Guia é criado."""
    await _create_insurance_plan(admin_engine, tenant_a)
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026"
    files = {"file": ("faturamento_legado.csv", io.BytesIO((header + "\r\n" + row + "\r\n").encode("utf-8-sig")), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    appointment = await _fetch_one(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointment["local_id"] is None
    assert appointment["tipo_paciente"] is None

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert billing["guia_id"] is None
