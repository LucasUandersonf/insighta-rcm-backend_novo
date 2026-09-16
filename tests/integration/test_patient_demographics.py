"""
tests/integration/test_patient_demographics.py

Achado do Dossiê Insighta RCM — faixa etária/demografia, ponta a ponta
via GET /analytics/patient-demographics. Prova que
AnalyticsService.get_patient_demographics conta pacientes DISTINTOS com
atendimento concluído no período, calcula idade a partir de
Patient.birth_date na data de HOJE, e nunca joga paciente sem
birth_date numa faixa por padrão.
"""
from datetime import date, datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta
from sqlalchemy import text


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


async def _create_patient(client, auth_headers, *, full_name: str, birth_date: str | None = None) -> str:
    payload = {"full_name": full_name}
    if birth_date is not None:
        payload["birth_date"] = birth_date
    resp = await client.post("/api/v1/patients", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_completed_appointment(admin_engine, tenant_id, patient_id, *, scheduled_at):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:t, :p, :dt, 'completed')"
            ),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at},
        )


async def test_no_completed_appointments_returns_zeroed_buckets(client, auth_headers_a):
    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-demographics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert [b["label"] for b in body["buckets"]] == ["0-17", "18-30", "31-45", "46-60", "60+"]
    assert all(b["patient_count"] == 0 for b in body["buckets"])
    assert body["unknown_age_count"] == 0


async def test_buckets_patients_by_current_age(client, auth_headers_a, admin_engine, tenant_a):
    today = date.today()
    recent = datetime.now(timezone.utc) - timedelta(days=2)

    child_id = await _create_patient(client, auth_headers_a, full_name="Criança", birth_date=(today - relativedelta(years=10)).isoformat())
    adult_id = await _create_patient(client, auth_headers_a, full_name="Jovem Adulto", birth_date=(today - relativedelta(years=25)).isoformat())
    senior_id = await _create_patient(client, auth_headers_a, full_name="Idoso", birth_date=(today - relativedelta(years=70)).isoformat())

    for patient_id in (child_id, adult_id, senior_id):
        await _seed_completed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-demographics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    counts = {b["label"]: b["patient_count"] for b in response.json()["buckets"]}
    assert counts["0-17"] == 1
    assert counts["18-30"] == 1
    assert counts["60+"] == 1
    assert counts["31-45"] == 0
    assert counts["46-60"] == 0


async def test_patient_without_birth_date_counts_as_unknown_never_a_bucket(client, auth_headers_a, admin_engine, tenant_a):
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    patient_id = await _create_patient(client, auth_headers_a, full_name="Sem Data de Nascimento")
    await _seed_completed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-demographics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["unknown_age_count"] == 1
    assert all(b["patient_count"] == 0 for b in body["buckets"])


async def test_counts_each_patient_once_even_with_multiple_appointments(client, auth_headers_a, admin_engine, tenant_a):
    today = date.today()
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    patient_id = await _create_patient(client, auth_headers_a, full_name="Recorrente", birth_date=(today - relativedelta(years=30)).isoformat())
    await _seed_completed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent)
    await _seed_completed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-demographics?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    counts = {b["label"]: b["patient_count"] for b in response.json()["buckets"]}
    assert counts["18-30"] == 1


async def test_patient_demographics_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    today = date.today()
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    patient_id = await _create_patient(client, auth_headers_a, full_name="Paciente A", birth_date=(today - relativedelta(years=30)).isoformat())
    await _seed_completed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=recent)

    date_from, date_to = _window()
    response_b = await client.get(
        f"/api/v1/analytics/patient-demographics?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert all(b["patient_count"] == 0 for b in response_b.json()["buckets"])
