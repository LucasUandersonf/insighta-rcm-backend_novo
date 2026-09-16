"""
tests/integration/test_weekday_squeeze_in.py

Onda 5 do Plano de Ação, item 15 — em quais dias da semana a agenda
mais recebe encaixe, ponta a ponta via GET /analytics/agenda-metrics.
Prova que AnalyticsRepository.weekday_squeeze_in_breakdown só conta
agendamentos com is_squeeze_in INFORMADO (não NULL), e que
AnalyticsService.get_agenda_metrics nunca inventa 0.0% sobre zero
amostra. Mesmo padrão determinístico de
test_weekday_cancellation_rate.py: ancora numa segunda-feira fixa
(Python weekday()==0 -> Postgres DOW==1).
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


def _target_monday() -> datetime:
    days_until_monday = (0 - date.today().weekday()) % 7 or 7
    target_date = date.today() + timedelta(days=days_until_monday)
    return datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)


async def _create_patient(client, auth_headers) -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Encaixe"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_appointment(admin_engine, tenant_id, patient_id, *, scheduled_at: datetime, is_squeeze_in) -> str:
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, scheduled_at, status, is_squeeze_in) "
                "VALUES (:id, :t, :p, :dt, 'completed', :squeeze)"
            ),
            {"id": appointment_id, "t": tenant_id, "p": patient_id, "dt": scheduled_at, "squeeze": is_squeeze_in},
        )
    return appointment_id


async def test_no_informed_appointments_returns_empty_breakdown(client, auth_headers_a):
    monday = _target_monday()
    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    assert response.json()["weekday_squeeze_in_rates"] == []


async def test_computes_squeeze_in_rate_for_the_weekday_with_informed_appointments(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()

    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=True)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=False)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=False)

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    bucket = next(b for b in response.json()["weekday_squeeze_in_rates"] if b["weekday"] == 1)  # segunda
    assert bucket["squeeze_in_count"] == 1
    assert bucket["total_informed"] == 3
    assert round(bucket["squeeze_in_rate"], 4) == round(1 / 3, 4)


async def test_appointments_without_squeeze_in_info_are_never_counted(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()

    # NULL (não informado) — nunca conta nem no numerador nem no
    # denominador, senão a taxa "afunda" por falta de dado, não por
    # falta de encaixe de verdade.
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=None)

    response = await client.get(
        f"/api/v1/analytics/agenda-metrics?date_from={monday.date().isoformat()}&date_to={monday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    assert response.json()["weekday_squeeze_in_rates"] == []


async def test_smart_insights_flags_weekday_with_most_squeeze_in(client, auth_headers_a, admin_engine, tenant_a):
    """Onda 6 do Plano de Ação, item 19 — mesmo cenário acima, agora
    ponta a ponta via GET /analytics/smart-insights (motor de insights,
    não só o breakdown cru de agenda-metrics)."""
    patient_id = await _create_patient(client, auth_headers_a)
    monday = _target_monday()
    wednesday = monday + timedelta(days=2)

    # Segunda: 9 de 10 (90%) são encaixe. Quarta: 1 de 10 (10%). Média
    # geral = 10/20 = 50% -> segunda fica +40pp acima (crítico, >=25pp).
    for _ in range(9):
        await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=True)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=monday, is_squeeze_in=False)
    await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=wednesday, is_squeeze_in=True)
    for _ in range(9):
        await _seed_appointment(admin_engine, tenant_a, patient_id, scheduled_at=wednesday, is_squeeze_in=False)

    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={monday.date().isoformat()}&date_to={wednesday.date().isoformat()}",
        headers=auth_headers_a,
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    squeeze_insight = next((i for i in insights if "mais recebe encaixe" in i["title"]), None)
    assert squeeze_insight is not None
    assert "segunda-feira" in squeeze_insight["title"].lower()
    assert squeeze_insight["severity"] == "critical"
    assert squeeze_insight["category"] == "agenda"
    assert squeeze_insight["action_href"] == "#agenda-resumo"
