"""
tests/integration/test_return_rate.py

Achado do Dossiê Insighta RCM — taxa de retorno de pacientes, ponta a
ponta via GET /analytics/return-rate. Prova que
AnalyticsService.get_return_rate usa Appointment.visit_type (só
status='completed'), nunca conta "untagged" no denominador, e compara
contra o período anterior de mesma duração.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


def _window() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


async def _create_patient(client, auth_headers, full_name="Paciente Retorno") -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_appointment(admin_engine, tenant_id, patient_id, *, scheduled_at, status="completed", visit_type=None):
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status, visit_type) "
                "VALUES (:id, :t, :p, :dt, :status, :visit_type)"
            ),
            {"id": appointment_id, "t": tenant_id, "p": patient_id, "dt": scheduled_at, "status": status, "visit_type": visit_type},
        )
    return appointment_id


async def test_no_completed_appointments_returns_none_rate(client, auth_headers_a):
    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["return_rate"] is None
    assert body["return_count"] == 0
    assert body["first_visit_count"] == 0
    assert body["untagged_count"] == 0


async def test_computes_rate_from_completed_appointments_in_window(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    for _ in range(3):
        await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="primeira_consulta")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["return_count"] == 3
    assert body["first_visit_count"] == 1
    assert body["return_rate"]["value"] == 75.0  # 3/(3+1)


async def test_untagged_appointments_are_never_counted_in_the_rate(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type=None)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type=None)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["return_count"] == 1
    assert body["untagged_count"] == 2
    assert body["return_rate"]["value"] == 100.0  # denominador é só 1 (retorno) — untagged nunca entra


async def test_non_completed_appointments_are_ignored(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, status="scheduled", visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, status="no_show", visit_type="retorno")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["return_rate"] is None
    assert body["return_count"] == 0


async def test_trend_compares_against_previous_period_of_same_duration(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    date_from, date_to = _window()
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    previous_window = datetime.now(timezone.utc) - timedelta(days=10)  # dentro do período anterior de 7 dias

    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=previous_window, visit_type="retorno")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=previous_window, visit_type="primeira_consulta")

    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    return_rate = response.json()["return_rate"]
    assert return_rate["value"] == 100.0  # 2/(2+0) no período atual
    assert return_rate["previous_value"] == 50.0  # 1/(1+1) no período anterior
    assert return_rate["delta_pct"] is not None
    assert return_rate["delta_pct"] > 0  # melhorou


async def test_no_previous_window_sample_has_no_trend_but_still_has_rate(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    return_rate = response.json()["return_rate"]
    assert return_rate["value"] == 100.0
    assert return_rate["delta_pct"] is None  # sem amostra no período anterior, nunca inventa 0%


async def test_return_rate_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent, visit_type="retorno")

    date_from, date_to = _window()
    response_b = await client.get(
        f"/api/v1/analytics/return-rate?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert response_b.json()["return_count"] == 0
