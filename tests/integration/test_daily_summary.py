"""
tests/integration/test_daily_summary.py

Onda 6 do Plano de Ação, item 18 ("resumo diário narrado") — prova que
GET /analytics/daily-summary compõe, em texto corrido, dado real de hoje
(agenda, faturamento) e nunca inventa uma frase sobre um dado ausente.
Ver DECISÃO completa em AnalyticsService.get_daily_summary.
"""
from datetime import datetime, timezone


async def test_daily_summary_with_no_data_is_honest(client, auth_headers_a):
    response = await client.get("/api/v1/analytics/daily-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "Nenhum atendimento agendado pra hoje ainda."
    assert body["sentences"] == ["Nenhum atendimento agendado pra hoje ainda."]


async def test_daily_summary_reports_appointment_count_and_billing_today(client, auth_headers_a, admin_engine, tenant_a):
    from tests.integration.test_analytics import _create_contract, _create_insurance_plan

    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=200.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Resumo"}, headers=auth_headers_a)
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201, appointment_resp.text
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 200.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201, billing_resp.text

    response = await client.get("/api/v1/analytics/daily-summary", headers=auth_headers_a)
    assert response.status_code == 200
    body = response.json()
    assert body["headline"] == "A agenda de hoje tem 1 atendimento previsto."
    assert any(s.startswith("Faturado hoje: R$ 200.00") for s in body["sentences"])


async def test_daily_summary_isolates_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Isolamento"}, headers=auth_headers_a)
    await client.post(
        "/api/v1/appointments",
        json={"patient_id": patient_resp.json()["id"], "scheduled_at": datetime.now(timezone.utc).isoformat()},
        headers=auth_headers_a,
    )

    response_b = await client.get("/api/v1/analytics/daily-summary", headers=auth_headers_b)
    assert response_b.status_code == 200
    assert response_b.json()["headline"] == "Nenhum atendimento agendado pra hoje ainda."


async def test_atendimento_cannot_access_daily_summary(client, admin_engine, tenant_a):
    from tests.conftest import _insert_user, _login

    user = await _insert_user(admin_engine, tenant_id=tenant_a, email="recepcao-resumo@clinica-a.com", role="atendimento")
    token = await _login(client, user["email"], user["password"])
    headers = {"Authorization": f"Bearer {token}"}

    response = await client.get("/api/v1/analytics/daily-summary", headers=headers)
    assert response.status_code == 403
