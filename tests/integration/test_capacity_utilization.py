"""
tests/integration/test_capacity_utilization.py

Ponta a ponta: cadastra profissional com grade semanal, cria consultas
com duração, e verifica que o endpoint de utilização calcula igual ao
que já validamos isoladamente em tests/test_capacity_service.py.
"""
from datetime import date, datetime, timedelta, timezone


async def test_utilization_reflects_booked_minutes_against_weekly_grid(client, auth_headers_a):
    # Grade: só terça-feira (weekday=2), 8h-12h = 240 min/semana no período testado.
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dra. Utilização",
            "availability": [{"weekday": 2, "start_time": "08:00:00", "end_time": "12:00:00"}],
        },
        headers=auth_headers_a,
    )
    assert professional_resp.status_code == 201
    professional_id = professional_resp.json()["id"]

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Capacidade"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # Encontra a próxima terça-feira a partir de hoje, para bater com a grade cadastrada.
    today = date.today()
    days_until_tuesday = (1 - today.weekday()) % 7  # Python: Monday=0; terça=1
    next_tuesday = today + timedelta(days=days_until_tuesday or 7)
    scheduled_at = datetime.combine(next_tuesday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9)

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "professional_id": professional_id,
            "scheduled_at": scheduled_at.isoformat(),
            "duration_minutes": 60,
        },
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201

    period_start = next_tuesday - timedelta(days=next_tuesday.weekday())  # segunda da mesma semana
    period_end = period_start + timedelta(days=6)

    utilization_resp = await client.get(
        f"/api/v1/capacity/utilization/{professional_id}",
        params={"date_from": period_start.isoformat(), "date_to": period_end.isoformat()},
        headers=auth_headers_a,
    )
    assert utilization_resp.status_code == 200
    body = utilization_resp.json()
    assert body["available_minutes"] == 240
    assert body["booked_minutes"] == 60
    assert body["utilization_rate"] == 60 / 240
