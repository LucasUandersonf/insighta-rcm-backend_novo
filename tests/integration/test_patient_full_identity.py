"""
tests/integration/test_patient_full_identity.py

Escopo completo de pessoa física (pedido do usuário: "todo sistema tem
dados de pessoa física com nome, telefone, data de nascimento, endereço,
email, CPF, sexo... é gerado uma agenda com os dados de pessoa física").
Ver DECISÃO completa em app/sql/058_patient_full_identity.sql,
RawAppointmentRow/RawBillingRow (app/worker/schemas.py) e
NormalizationService._get_or_create_patient (app/services/normalization_service.py).

Cobre dois caminhos de entrada:
1. Ingestão em massa via Template de Agenda (o ponto de entrada mais
   comum na prática — o agendamento normalmente acontece antes do
   faturamento) — inclui o enriquecimento (fill-forward) de uma
   reimportação mais completa, sem nunca sobrescrever dado já existente.
2. Cadastro/edição manual via POST/PATCH /patients.
"""
import io
import uuid

from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


async def _fake_ingestion_setup(monkeypatch):
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-pessoa-fisica")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)


_AGENDA_HEADER = (
    "cpf_paciente;nome_paciente;nome_profissional;registro_profissional;convenio;local_atendimento;"
    "tipo_paciente;data_agendamento;hora_agendamento;duracao_minutos;status;codigo_procedimento;cid;"
    "codigo_agendamento;telefone_paciente;email_paciente;data_nascimento_paciente;sexo_paciente;"
    "endereco_paciente;cidade_paciente;uf_paciente;cep_paciente"
)


def _agenda_csv(*rows: str) -> bytes:
    return (_AGENDA_HEADER + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")


async def _upload_agenda(client, auth_headers, *rows: str, filename: str = "agenda.csv"):
    files = {"file": (filename, io.BytesIO(_agenda_csv(*rows)), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "agenda"}, headers=auth_headers)


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _fetch_patient(admin_engine, tenant_id) -> dict:
    async with admin_engine.begin() as conn:
        result = await conn.execute(text("SELECT * FROM core.patients WHERE tenant_id = :t"), {"t": tenant_id})
        row = result.mappings().first()
        assert row is not None, "paciente não foi criado"
        return dict(row)


async def test_agenda_upload_captures_full_patient_identity(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    await _fake_ingestion_setup(monkeypatch)
    await _create_insurance_plan(admin_engine, tenant_a)
    row = (
        "12345678909;Maria Silva;Dra. Ana Souza;CRM12345;Unimed Nacional;Unidade Centro;"
        "Ambulatorial;20/08/2026;14:30;30;Agendado;10101012;J06;AG-001;"
        "(11) 98888-7777;maria@example.com;05/03/1990;Feminino;"
        "Rua das Flores, 123;São Paulo;sp;01310-100"
    )
    response = await _upload_agenda(client, auth_headers_a, row)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    patient = await _fetch_patient(admin_engine, tenant_a)
    assert patient["cpf"] == "12345678909"
    assert patient["phone"] == "11988887777"
    assert patient["email"] == "maria@example.com"
    assert patient["birth_date"].isoformat() == "1990-03-05"
    assert patient["sex"] == "F"
    assert patient["address_street"] == "Rua das Flores, 123"
    assert patient["address_city"] == "São Paulo"
    assert patient["address_state"] == "SP"
    assert patient["zip_code"] == "01310100"


async def test_agenda_reimport_enriches_missing_fields_without_overwriting_existing(
    client, auth_headers_a, admin_engine, tenant_a, monkeypatch
):
    """Uma reimportação mais completa preenche o que ainda está vazio
    (ex: a primeira vinda só tinha telefone, a segunda também trouxe
    e-mail) — mas NUNCA sobrescreve um valor que já existe, mesmo que a
    segunda linha traga um telefone diferente (pode ter sido corrigido
    manualmente depois, mais confiável que um arquivo velho reimportado)."""
    await _fake_ingestion_setup(monkeypatch)
    await _create_insurance_plan(admin_engine, tenant_a)

    first_row = (
        "12345678909;Maria Silva;;;Unimed Nacional;;;20/08/2026;;;Agendado;;;AG-100;"
        "11988887777;;;;;;;"
    )
    first = await _upload_agenda(client, auth_headers_a, first_row)
    assert first.status_code == 201, first.text
    assert first.json()["error_row_count"] == 0

    patient_after_first = await _fetch_patient(admin_engine, tenant_a)
    assert patient_after_first["phone"] == "11988887777"
    assert patient_after_first["email"] is None

    second_row = (
        "12345678909;Maria Silva;;;Unimed Nacional;;;21/08/2026;;;Agendado;;;AG-101;"
        "11900000000;maria@example.com;;;;;;"
    )
    # Nome de arquivo diferente do primeiro upload — a chave de
    # idempotência (tenant_id, s3_bucket, s3_key) usa o nome do arquivo
    # (ver DECISÃO em IngestionRepository.claim_file); o mesmo nome duas
    # vezes seria tratado como reenvio do mesmo arquivo, não uma segunda
    # importação de verdade.
    second = await _upload_agenda(client, auth_headers_a, second_row, filename="agenda_2.csv")
    assert second.status_code == 201, second.text
    assert second.json()["error_row_count"] == 0

    patient_after_second = await _fetch_patient(admin_engine, tenant_a)
    # E-mail era NULL antes -> preenchido pela reimportação.
    assert patient_after_second["email"] == "maria@example.com"
    # Telefone já existia -> a reimportação NUNCA sobrescreve.
    assert patient_after_second["phone"] == "11988887777"


async def test_agenda_upload_rejects_row_with_invalid_cpf_checksum(client, auth_headers_a, admin_engine, tenant_a, monkeypatch):
    await _fake_ingestion_setup(monkeypatch)
    await _create_insurance_plan(admin_engine, tenant_a)
    row = "12345678900;Maria Silva;;;Unimed Nacional;;;20/08/2026;;;Agendado;;;AG-200;;;;;;;;"
    response = await _upload_agenda(client, auth_headers_a, row)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 1


# ---------------------------------------------------------------------
# Cadastro/edição manual (POST/PATCH /patients)
# ---------------------------------------------------------------------


async def _create_patient(client, auth_headers, **overrides) -> dict:
    payload = {"full_name": "Paciente Completo", **overrides}
    resp = await client.post("/api/v1/patients", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_patient_with_full_identity_fields(client, auth_headers_a):
    body = await _create_patient(
        client,
        auth_headers_a,
        cpf="123.456.789-09",
        birth_date="1990-03-05",
        phone="(11) 98888-7777",
        email="Maria@Example.com",
        sex="feminino",
        address_street="Rua das Flores, 123",
        address_city="São Paulo",
        address_state="sp",
    )
    assert body["cpf"] == "12345678909"
    assert body["phone"] == "11988887777"
    assert body["email"] == "maria@example.com"
    assert body["sex"] == "F"
    assert body["address_state"] == "SP"


async def test_create_patient_rejects_cpf_with_invalid_check_digit(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients", json={"full_name": "X", "cpf": "12345678900"}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_create_patient_rejects_malformed_email(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients", json={"full_name": "X", "email": "nao-e-email"}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_create_patient_rejects_phone_with_wrong_digit_count(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients", json={"full_name": "X", "phone": "123"}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_create_patient_rejects_unrecognized_sex_value(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/patients", json={"full_name": "X", "sex": "indefinido"}, headers=auth_headers_a
    )
    assert resp.status_code == 422


async def test_update_patient_fills_in_identity_fields_later(client, auth_headers_a):
    body = await _create_patient(client, auth_headers_a)
    assert body["phone"] is None
    assert body["birth_date"] is None

    resp = await client.patch(
        f"/api/v1/patients/{body['id']}",
        json={
            "birth_date": "1985-07-20",
            "phone": "11988887777",
            "email": "paciente@example.com",
            "sex": "M",
            "address_street": "Av. Paulista, 1000",
            "address_city": "São Paulo",
            "address_state": "SP",
        },
        headers=auth_headers_a,
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["birth_date"] == "1985-07-20"
    assert updated["phone"] == "11988887777"
    assert updated["email"] == "paciente@example.com"
    assert updated["sex"] == "M"
    assert updated["address_street"] == "Av. Paulista, 1000"


async def test_anonymize_patient_clears_full_identity_fields(client, auth_headers_a):
    body = await _create_patient(
        client,
        auth_headers_a,
        cpf="12345678909",
        birth_date="1990-03-05",
        phone="11988887777",
        email="maria@example.com",
        sex="F",
        address_street="Rua das Flores, 123",
        address_city="São Paulo",
        address_state="SP",
    )

    resp = await client.post(f"/api/v1/patients/{body['id']}/anonymize", headers=auth_headers_a)
    assert resp.status_code == 200
    anonymized = resp.json()
    assert anonymized["cpf"] is None
    assert anonymized["birth_date"] is None
    assert anonymized["phone"] is None
    assert anonymized["email"] is None
    assert anonymized["sex"] is None
    assert anonymized["address_street"] is None
    assert anonymized["address_city"] is None
    assert anonymized["address_state"] is None
