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


async def test_appointment_without_duration_falls_back_to_tenant_observed_average(client, auth_headers_a):
    """Achado de auditoria: todo agendamento vindo da ingestão de
    Faturamento nunca tem duration_minutes preenchido (RawBillingRow não
    carrega esse campo) — sem fallback, essas linhas contribuíam 0
    minuto pra booked_minutes, zerando utilization_rate silenciosamente
    pra qualquer tenant que só usa Faturamento. Aqui: uma consulta com
    duração conhecida (60min) estabelece a média observada do tenant; uma
    segunda consulta SEM duration_minutes (simulando dado de Faturamento)
    precisa herdar essa média, não contribuir 0."""
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dra. Sem Duração",
            "availability": [{"weekday": 2, "start_time": "08:00:00", "end_time": "12:00:00"}],
        },
        headers=auth_headers_a,
    )
    professional_id = professional_resp.json()["id"]
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Duração"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    today = date.today()
    days_until_tuesday = (1 - today.weekday()) % 7
    next_tuesday = today + timedelta(days=days_until_tuesday or 7)
    period_start = next_tuesday - timedelta(days=next_tuesday.weekday())
    period_end = period_start + timedelta(days=6)

    # Consulta 1: duração conhecida (60min) — estabelece a média observada do tenant.
    await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "professional_id": professional_id,
            "scheduled_at": datetime.combine(next_tuesday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9).isoformat(),
            "duration_minutes": 60,
        },
        headers=auth_headers_a,
    )
    # Consulta 2: SEM duration_minutes (mesmo formato de dado vindo de Faturamento).
    await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "professional_id": professional_id,
            "scheduled_at": datetime.combine(next_tuesday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=10).isoformat(),
        },
        headers=auth_headers_a,
    )

    utilization_resp = await client.get(
        f"/api/v1/capacity/utilization/{professional_id}",
        params={"date_from": period_start.isoformat(), "date_to": period_end.isoformat()},
        headers=auth_headers_a,
    )
    body = utilization_resp.json()
    # 60 (duração própria) + 60 (fallback = média observada do tenant) = 120,
    # nunca 60 (o que aconteceria se a consulta 2 contribuísse 0).
    assert body["booked_minutes"] == 120


async def test_no_known_duration_anywhere_leaves_booked_minutes_honestly_at_zero(client, auth_headers_a):
    """Sem NENHUM agendamento com duração conhecida em lugar nenhum do
    tenant, não há dado pra estimar — booked_minutes continua 0 (nunca
    um número inventado do nada)."""
    professional_resp = await client.post(
        "/api/v1/professionals",
        json={
            "full_name": "Dr. Nenhuma Duração Conhecida",
            "availability": [{"weekday": 2, "start_time": "08:00:00", "end_time": "12:00:00"}],
        },
        headers=auth_headers_a,
    )
    professional_id = professional_resp.json()["id"]
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Sem Duração 2"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    today = date.today()
    days_until_tuesday = (1 - today.weekday()) % 7
    next_tuesday = today + timedelta(days=days_until_tuesday or 7)
    period_start = next_tuesday - timedelta(days=next_tuesday.weekday())
    period_end = period_start + timedelta(days=6)

    await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "professional_id": professional_id,
            "scheduled_at": datetime.combine(next_tuesday, datetime.min.time(), tzinfo=timezone.utc).replace(hour=9).isoformat(),
        },
        headers=auth_headers_a,
    )

    utilization_resp = await client.get(
        f"/api/v1/capacity/utilization/{professional_id}",
        params={"date_from": period_start.isoformat(), "date_to": period_end.isoformat()},
        headers=auth_headers_a,
    )
    assert utilization_resp.json()["booked_minutes"] == 0
