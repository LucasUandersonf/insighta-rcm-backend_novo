"""
tests/integration/test_ingestion_agenda.py

Cobre o Template de Integração "Agenda" (ver app/sql/019_agenda_ingestion.sql
e docstring de RawAppointmentRow em app/worker/schemas.py) via
POST /ingestion/upload?data_type=agenda — mesmo caminho HTTP e mesma
técnica de mock (só a fronteira de rede do S3, ver docstring de
test_ingestion_upload.py) do template de Faturamento.

Diferença central testada aqui: uma linha de Agenda NUNCA cria Billing
(é agendamento, não cobrança) e faz UPSERT por `codigo_agendamento`
(Appointment.external_id) em vez de sempre criar um registro novo — o
mesmo agendamento é tipicamente reexportado várias vezes conforme seu
status muda (agendado -> confirmado -> atendido/faltou).
"""
import io
import uuid

import pytest
from sqlalchemy import text

from app.services import ingestion_storage_client as storage_module


@pytest.fixture(autouse=True)
def _fake_ingestion_bucket(monkeypatch):
    """Mesma técnica de test_ingestion_upload.py — ver docstring de lá."""
    monkeypatch.setattr(storage_module.settings, "AWS_S3_INGEST_BUCKET", "bucket-teste-ingestao-agenda")

    async def _fake_upload_bytes(self, *, key: str, raw_bytes: bytes) -> str | None:
        return None

    monkeypatch.setattr(storage_module.IngestionStorageClient, "upload_bytes", _fake_upload_bytes)
    yield


_HEADER = (
    "cpf_paciente;nome_paciente;nome_profissional;registro_profissional;convenio;local_atendimento;"
    "tipo_paciente;data_agendamento;hora_agendamento;duracao_minutos;status;codigo_procedimento;cid;"
    "codigo_agendamento"
)


def _csv_bytes(*rows: str) -> bytes:
    return (_HEADER + "\r\n" + "\r\n".join(rows) + "\r\n").encode("utf-8-sig")


async def _upload_agenda(client, auth_headers, *rows: str, filename: str = "agenda_agosto.csv"):
    files = {"file": (filename, io.BytesIO(_csv_bytes(*rows)), "text/csv")}
    return await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "agenda"}, headers=auth_headers)


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


async def test_upload_agenda_creates_appointment_without_billing(client, auth_headers_a, admin_engine, tenant_a):
    await _create_insurance_plan(admin_engine, tenant_a)
    row = (
        "12345678900;Paciente Teste;Dra. Ana Souza;CRM12345;Unimed Nacional;Unidade Centro;"
        "Ambulatorial;20/08/2026;14:30;30;Agendado;10101012;J06;AG-001"
    )
    response = await _upload_agenda(client, auth_headers_a, row)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["data_type"] == "agenda"
    assert body["row_count"] == 1
    assert body["error_row_count"] == 0

    appointment = await _fetch_one(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointment is not None
    assert appointment["status"] == "scheduled"
    assert appointment["external_id"] == "AG-001"
    assert appointment["duration_minutes"] == 30
    assert appointment["tipo_paciente"] == "ambulatorial"
    assert appointment["procedure_code"] == "10101012"
    assert appointment["scheduled_at"].hour == 14
    assert appointment["scheduled_at"].minute == 30

    # NENHUM billing foi criado — Agenda é agendamento, não cobrança.
    billings = await _fetch_all(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert billings == []

    # Local e profissional foram resolvidos pelo mesmo caminho get-or-create
    # do template de Faturamento.
    local = await _fetch_one(admin_engine, "SELECT * FROM core.locais WHERE tenant_id = :t", t=tenant_a)
    assert local is not None
    assert appointment["local_id"] == local["id"]

    professional = await _fetch_one(admin_engine, "SELECT * FROM core.professionals WHERE tenant_id = :t", t=tenant_a)
    assert professional is not None
    assert professional["professional_registry"] == "CRM12345"


async def test_reupload_with_same_external_id_updates_instead_of_duplicating(client, auth_headers_a, admin_engine, tenant_a):
    """Caso real: o mesmo relatório de Agenda é reexportado depois que o
    paciente FALTOU — a linha muda de status, mas é o MESMO agendamento
    (mesmo codigo_agendamento). Deve fazer UPSERT, nunca duplicar."""
    await _create_insurance_plan(admin_engine, tenant_a)
    first_row = (
        "12345678900;Paciente Teste;;;Unimed Nacional;;;20/08/2026;09:00;;Agendado;;;AG-777"
    )
    first = await _upload_agenda(client, auth_headers_a, first_row, filename="agenda_dia1.csv")
    assert first.status_code == 201, first.text

    second_row = (
        "12345678900;Paciente Teste;;;Unimed Nacional;;;20/08/2026;09:00;;Faltou;;;AG-777"
    )
    second = await _upload_agenda(client, auth_headers_a, second_row, filename="agenda_dia2.csv")
    assert second.status_code == 201, second.text

    appointments = await _fetch_all(
        admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t AND external_id = :e", t=tenant_a, e="AG-777"
    )
    assert len(appointments) == 1  # UPSERT, não duplicata
    assert appointments[0]["status"] == "no_show"


async def test_agenda_row_without_external_id_always_creates_new(client, auth_headers_a, admin_engine, tenant_a):
    """Limitação aceita e documentada: sem codigo_agendamento, não há
    chave de upsert — cada linha vira um agendamento novo."""
    row = "12345678900;Paciente Teste;;;;;;20/08/2026;10:00;;Agendado;;;"
    first = await _upload_agenda(client, auth_headers_a, row, filename="agenda_sem_id_1.csv")
    second = await _upload_agenda(client, auth_headers_a, row, filename="agenda_sem_id_2.csv")
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    appointments = await _fetch_all(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert len(appointments) == 2


async def test_agenda_status_aliases_are_normalized(client, auth_headers_a, admin_engine, tenant_a):
    rows = [
        "11111111111;Paciente Um;;;;;;20/08/2026;08:00;;confirmado;;;AG-C1",
        "22222222222;Paciente Dois;;;;;;20/08/2026;09:00;;atendido;;;AG-C2",
        "33333333333;Paciente Tres;;;;;;20/08/2026;10:00;;cancelado;;;AG-C3",
    ]
    response = await _upload_agenda(client, auth_headers_a, *rows)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    statuses = {
        r["external_id"]: r["status"]
        for r in await _fetch_all(admin_engine, "SELECT external_id, status FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    }
    assert statuses == {"AG-C1": "confirmed", "AG-C2": "completed", "AG-C3": "cancelled"}


async def test_agenda_row_with_unknown_insurance_plan_is_rejected(client, auth_headers_a, admin_engine, tenant_a):
    row = "12345678900;Paciente Teste;;;Convenio Desconhecido;;;20/08/2026;10:00;;Agendado;;;"
    response = await _upload_agenda(client, auth_headers_a, row)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 1

    appointments = await _fetch_all(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointments == []


async def test_agenda_without_insurance_plan_column_is_accepted(client, auth_headers_a, admin_engine, tenant_a):
    """Diferente de Faturamento: convênio é OPCIONAL em Agenda (agendamento
    pode existir antes da confirmação de cobertura)."""
    row = "12345678900;Paciente Teste;;;;;;20/08/2026;10:00;;Agendado;;;"
    response = await _upload_agenda(client, auth_headers_a, row)
    assert response.status_code == 201, response.text
    assert response.json()["error_row_count"] == 0

    appointment = await _fetch_one(admin_engine, "SELECT * FROM core.appointments WHERE tenant_id = :t", t=tenant_a)
    assert appointment["insurance_plan_id"] is None


async def test_agenda_upload_rejects_xml(client, auth_headers_a):
    files = {"file": ("agenda.xml", io.BytesIO(b"<agenda></agenda>"), "application/xml")}
    response = await client.post("/api/v1/ingestion/upload", files=files, data={"data_type": "agenda"}, headers=auth_headers_a)
    assert response.status_code == 400


async def test_default_data_type_is_faturamento_when_omitted(client, auth_headers_a, admin_engine, tenant_a):
    """Retrocompatibilidade: upload sem `data_type` continua se comportando
    como Faturamento (cria Billing), exatamente como antes desta mudança."""
    await _create_insurance_plan(admin_engine, tenant_a)
    header = "cpf_paciente;nome_paciente;convenio;codigo_procedimento;cid;valor_cobrado;data_atendimento"
    row = "12345678900;Paciente Teste;Unimed Nacional;10101012;J06;150,00;20/08/2026"
    files = {"file": ("faturamento_padrao.csv", io.BytesIO((header + "\r\n" + row + "\r\n").encode("utf-8-sig")), "text/csv")}

    response = await client.post("/api/v1/ingestion/upload", files=files, headers=auth_headers_a)
    assert response.status_code == 201, response.text
    assert response.json()["data_type"] == "faturamento"

    billing = await _fetch_one(admin_engine, "SELECT * FROM core.billing WHERE tenant_id = :t", t=tenant_a)
    assert billing is not None
