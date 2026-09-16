"""
tests/integration/test_patient_revenue_pareto.py

Achado do Dossiê Insighta RCM — Pareto de receita por paciente, ponta a
ponta via GET /analytics/patient-revenue-pareto. Prova que
AnalyticsService.get_patient_revenue_pareto soma Billing.charged_value
por paciente, ordena do maior pro menor, e calcula share_pct/
cumulative_share_pct sem nunca dividir por zero.
"""
import uuid
from datetime import date, timedelta


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


async def _create_insurance_plan(admin_engine, tenant_id) -> str:
    from sqlalchemy import text

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": "Unimed Nacional", "key": "unimed_nacional"},
        )
    return plan_id


async def _create_and_bill(client, auth_headers, plan_id: str, *, full_name: str, charged_value: float, patient_id: str | None = None) -> str:
    if patient_id is None:
        patient_resp = await client.post("/api/v1/patients", json={"full_name": full_name}, headers=auth_headers)
        patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": "2026-01-15T10:00:00Z",
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers,
    )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return patient_id


async def test_no_billing_returns_zero_total_and_empty_items(client, auth_headers_a):
    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-revenue-pareto?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_billed"] == 0.0
    assert body["items"] == []
    assert body["top_n_share_pct"] is None


async def test_ranks_patients_by_revenue_descending_with_cumulative_share(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill(client, auth_headers_a, plan_id, full_name="Paciente Alto Valor", charged_value=700.0)
    await _create_and_bill(client, auth_headers_a, plan_id, full_name="Paciente Baixo Valor", charged_value=300.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-revenue-pareto?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_billed"] == 1000.0
    names = [item["full_name"] for item in body["items"]]
    assert names == ["Paciente Alto Valor", "Paciente Baixo Valor"]
    assert body["items"][0]["share_pct"] == 70.0
    assert body["items"][0]["cumulative_share_pct"] == 70.0
    assert body["items"][1]["share_pct"] == 30.0
    assert body["items"][1]["cumulative_share_pct"] == 100.0
    assert body["top_n_share_pct"] == 100.0


async def test_sums_multiple_billings_for_the_same_patient(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Recorrente"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]
    await _create_and_bill(client, auth_headers_a, plan_id, full_name="Paciente Recorrente", charged_value=100.0, patient_id=patient_id)
    await _create_and_bill(client, auth_headers_a, plan_id, full_name="Paciente Recorrente", charged_value=150.0, patient_id=patient_id)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/patient-revenue-pareto?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["revenue"] == 250.0


async def test_pareto_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill(client, auth_headers_a, plan_id, full_name="Paciente Tenant A", charged_value=500.0)

    date_from, date_to = _window()
    response_b = await client.get(
        f"/api/v1/analytics/patient-revenue-pareto?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert response_b.json()["items"] == []
