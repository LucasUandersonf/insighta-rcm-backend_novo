"""
tests/integration/test_inactive_patients.py

Carteira de pacientes inativos (Sala de Comando) — a lista real por
trás da recomendação "reativar quem não voltou" do insight de meta
anual. Prova ponta a ponta que só entra quem JÁ teve atendimento (nunca
um cadastro sem histórico), só quando o último atendimento passou de 1
ano, e que o RLS isola entre tenants.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_appointment(admin_engine, tenant_id: str, patient_id: str, scheduled_at: datetime) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:t, :p, :dt, 'completed')"
            ),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at},
        )


async def test_patient_inactive_for_over_a_year_appears_in_the_list(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sumido"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    old_appointment = datetime.now(timezone.utc) - timedelta(days=400)
    await _create_appointment(admin_engine, tenant_a, patient_id, old_appointment)

    response = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["inactive_after_days"] == 365
    item = body["items"][0]
    assert item["full_name"] == "Paciente Sumido"
    assert item["days_since_last_appointment"] >= 400


async def test_patient_with_recent_appointment_does_not_appear(client, auth_headers_a, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Fiel"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    recent_appointment = datetime.now(timezone.utc) - timedelta(days=10)
    await _create_appointment(admin_engine, tenant_a, patient_id, recent_appointment)

    response = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["total_count"] == 0


async def test_patient_with_no_appointment_history_never_counts_as_inactive(client, auth_headers_a):
    """Um cadastro sem NENHUM atendimento não é 'inativo' — é um
    cadastro que talvez nunca tenha virado paciente de fato (ver
    DECISÃO em AnalyticsRepository.inactive_patients_count)."""
    await client.post("/api/v1/patients", json={"full_name": "Cadastro Sem Histórico"}, headers=auth_headers_a)

    response = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["total_count"] == 0


async def test_inactive_patients_orders_longest_inactive_first(client, auth_headers_a, admin_engine, tenant_a):
    patient_a_resp = await client.post("/api/v1/patients", json={"full_name": "Inativo há 400 dias"}, headers=auth_headers_a)
    patient_b_resp = await client.post("/api/v1/patients", json={"full_name": "Inativo há 800 dias"}, headers=auth_headers_a)
    now = datetime.now(timezone.utc)
    await _create_appointment(admin_engine, tenant_a, patient_a_resp.json()["id"], now - timedelta(days=400))
    await _create_appointment(admin_engine, tenant_a, patient_b_resp.json()["id"], now - timedelta(days=800))

    response = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_a)
    body = response.json()
    assert body["total_count"] == 2
    assert [i["full_name"] for i in body["items"]] == ["Inativo há 800 dias", "Inativo há 400 dias"]


async def test_inactive_patients_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    await _create_appointment(
        admin_engine, tenant_a, patient_resp.json()["id"], datetime.now(timezone.utc) - timedelta(days=400)
    )

    response_b = await client.get("/api/v1/analytics/inactive-patients", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["total_count"] == 0
