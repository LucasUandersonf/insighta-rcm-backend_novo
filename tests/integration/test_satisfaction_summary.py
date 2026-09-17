"""
tests/integration/test_satisfaction_summary.py

"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 2) —
resumo de NPS/satisfação pós-atendimento, ponta a ponta via
GET /analytics/satisfaction-summary. Prova que
AnalyticsService.get_satisfaction_summary agrega o dado real do banco
(janela fixa de 90 dias, distribuição por nota, tendência contra a
janela anterior, RLS isola entre tenants).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_patient(client, auth_headers, full_name="Paciente Satisfação") -> str:
    resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_appointment_with_score(admin_engine, tenant_id, patient_id, *, scheduled_at, score):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (tenant_id, patient_id, scheduled_at, status, visit_satisfaction_score) "
                "VALUES (:t, :p, :dt, 'completed', :score)"
            ),
            {"t": tenant_id, "p": patient_id, "dt": scheduled_at, "score": score},
        )


async def test_no_evaluations_yet_returns_none_average(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["average_score"] is None
    assert body["response_count"] == 0
    assert body["distribution"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert body["window_days"] == 90


async def test_computes_average_and_distribution_from_current_window(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    for score in (5, 5, 4, 3, 1):
        await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=recent, score=score)

    response = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["response_count"] == 5
    assert body["distribution"] == {"1": 1, "2": 0, "3": 1, "4": 1, "5": 2}
    assert body["average_score"]["value"] == 3.6  # (5+5+4+3+1)/5


async def test_evaluation_outside_90_day_window_is_not_counted(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    too_old = datetime.now(timezone.utc) - timedelta(days=200)
    await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=too_old, score=5)

    response = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_a)
    assert response.status_code == 200
    assert response.json()["response_count"] == 0


async def test_trend_compares_against_previous_90_day_window(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    previous_window = datetime.now(timezone.utc) - timedelta(days=120)  # dentro da janela anterior de 90-180 dias

    await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=recent, score=5)
    await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=previous_window, score=3)

    response = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_a)
    assert response.status_code == 200
    average_score = response.json()["average_score"]
    assert average_score["value"] == 5.0
    assert average_score["previous_value"] == 3.0
    assert average_score["delta_pct"] is not None
    assert average_score["delta_pct"] > 0  # melhorou


async def test_no_previous_window_data_has_no_trend_but_still_has_average(client, auth_headers_a, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=recent, score=4)

    response = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_a)
    assert response.status_code == 200
    average_score = response.json()["average_score"]
    assert average_score["value"] == 4.0
    assert average_score["delta_pct"] is None  # sem base de comparação, nunca inventa 0%


async def test_satisfaction_summary_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_id = await _create_patient(client, auth_headers_a)
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    await _seed_appointment_with_score(admin_engine, tenant_a, patient_id, scheduled_at=recent, score=5)

    response_b = await client.get("/api/v1/analytics/satisfaction-summary", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["response_count"] == 0
