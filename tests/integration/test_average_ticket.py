"""
tests/integration/test_average_ticket.py

Achado do Dossiê Insighta RCM — ticket médio, ponta a ponta via
GET /analytics/average-ticket. Prova que AnalyticsService.get_average_ticket
agrega Billing.charged_value (geral/canal/procedimento), nunca inventa
média sobre zero lançamentos, e compara contra o período anterior de
mesma duração.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


def _window() -> tuple[str, str]:
    today = date.today()
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


async def _create_and_bill(
    client, auth_headers, plan_id: str, *, charged_value: float, procedure_code: str = "10101012"
) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Ticket"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": "2026-01-15T10:00:00Z",
            "procedure_code": procedure_code,
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
    return billing_resp.json()["id"]


async def _create_and_bill_with_channel(
    client, admin_engine, auth_headers, tenant_id, plan_id: str, *, charged_value: float, booking_channel: str, procedure_code: str
) -> str:
    # booking_channel só é gravado pela ingestão/normalização (ver
    # normalization_service.py) — POST /appointments não aceita esse
    # campo, então o teste semeia o agendamento direto via SQL, igual ao
    # resto da suíte faz para colunas fora do alcance da API de escrita.
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Ticket Canal"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appointment_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.appointments (id, tenant_id, patient_id, insurance_plan_id, scheduled_at, status, procedure_code, booking_channel) "
                "VALUES (:id, :t, :p, :plan, :dt, 'completed', :proc, :channel)"
            ),
            {
                "id": appointment_id,
                "t": tenant_id,
                "p": patient_id,
                "plan": plan_id,
                "dt": datetime.now(timezone.utc),
                "proc": procedure_code,
                "channel": booking_channel,
            },
        )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()["id"]


async def _create_insurance_plan(admin_engine, tenant_id) -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": "Unimed Nacional", "key": "unimed_nacional"},
        )
    return plan_id


async def test_no_billing_returns_none_overall_and_empty_breakdowns(client, auth_headers_a):
    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/average-ticket?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] is None
    assert body["billing_count"] == 0
    assert body["by_channel"] == []
    assert body["by_procedure"] == []


async def test_computes_overall_average_from_billings_in_window(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill(client, auth_headers_a, plan_id, charged_value=100.0)
    await _create_and_bill(client, auth_headers_a, plan_id, charged_value=200.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/average-ticket?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    assert body["billing_count"] == 2
    assert body["overall"]["value"] == 150.0


async def test_breaks_down_by_channel_and_procedure(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill_with_channel(
        client, admin_engine, auth_headers_a, tenant_a, plan_id, charged_value=100.0, booking_channel="whatsapp", procedure_code="10101012"
    )
    await _create_and_bill_with_channel(
        client, admin_engine, auth_headers_a, tenant_a, plan_id, charged_value=300.0, booking_channel="telefone", procedure_code="10101020"
    )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/average-ticket?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    body = response.json()
    by_channel = {item["channel"]: item["average_ticket"] for item in body["by_channel"]}
    assert by_channel == {"whatsapp": 100.0, "telefone": 300.0}
    by_procedure = {item["procedure_code"]: item["average_ticket"] for item in body["by_procedure"]}
    assert by_procedure == {"10101012": 100.0, "10101020": 300.0}


async def test_no_previous_window_sample_has_no_trend_but_still_has_average(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill(client, auth_headers_a, plan_id, charged_value=150.0)

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/average-ticket?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    overall = response.json()["overall"]
    assert overall["value"] == 150.0
    assert overall["delta_pct"] is None


async def test_average_ticket_is_isolated_between_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_and_bill(client, auth_headers_a, plan_id, charged_value=100.0)

    date_from, date_to = _window()
    response_b = await client.get(
        f"/api/v1/analytics/average-ticket?date_from={date_from}&date_to={date_to}", headers=auth_headers_b
    )
    assert response_b.status_code == 200
    assert response_b.json()["billing_count"] == 0
