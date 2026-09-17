"""
tests/integration/test_weekday_cancellation_rate.py

Onda 1 item 6 (Plano de Ação) — dias com mais cancelamento, ponta a
ponta via GET /analytics/agenda-metrics. Prova que
AnalyticsRepository.weekday_cancellation_rate_breakdown só conta
desfecho TERMINAL (completed/no_show/cancelled), nunca 'scheduled', e
que AnalyticsService.get_agenda_metrics nunca inventa 0.0% sobre zero
amostra. Mesmo padrão determinístico de
test_agenda_metrics_reports_no_show_rate_per_weekday (test_analytics.py):
ancora numa segunda-feira fixa (Python weekday()==0 -> Postgres DOW==1),
nunca "hoje menos N dias" (evita depender do dia da semana em que o
teste roda).
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


def _target_monday() -> datetime:
    days_until_monday = (0 - date.today().weekday()) % 7 or 7
    target_date = date.today() + timedelta(days=days_until_monday)
    return datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)


async def _create_patient(client, auth_headers) -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Cancelamento"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_appointment(admin_engine, tenant_id, patient_id, *, scheduled_at: datetime, status: str) -> str:
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status) "
                "VALUES (:id, :t, :p, :dt, :status)"
            ),
            {"id": appointment_id, "t": tenant_id, "p": patient_id, "dt": scheduled_at, "status": status},
        )
    return appointment_id


async def test_no_terminal_appointments_returns_empty_breakdown(client, auth_headers_a):
    # Mesmo comportamento de weekday_no_show_rates: só dias com pelo
    # menos 1 atendimento de desfecho terminal aparecem na lista — nunca
    # os 7 dias preenchidos artificialmente com "0 amostra".
    monday = _target_monday()
    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    assert response.json()["weekday_cancellation_rates"] == []


async def test_computes_cancellation_rate_for_the_weekday_with_terminal_appointments(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()

    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, status="cancelled")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, status="completed")
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, status="no_show")

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    bucket = next(b for b in response.json()["weekday_cancellation_rates"] if b["weekday"] == 1)  # segunda
    assert bucket["cancellation_count"] == 1
    assert bucket["total_appointments"] == 3
    assert round(bucket["cancellation_rate"], 4) == round(1 / 3, 4)


async def test_scheduled_appointments_are_never_counted(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()

    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, status="scheduled")

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    # 'scheduled' não tem desfecho terminal -> nem entra na lista (mesmo
    # comportamento de zero amostra do teste acima).
    assert response.json()["weekday_cancellation_rates"] == []


async def test_weekday_cancellation_rate_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, status="cancelled")

    response_b = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_b,
    )
    assert response_b.status_code == 200
    assert response_b.json()["weekday_cancellation_rates"] == []
