"""
tests/integration/test_coparticipation_delayed_payment_insight.py

"Equilíbrio Insighta" (Balanced Scorecard, perna Cliente, mecanismo 5) —
ponta a ponta via GET /analytics/smart-insights: coparticipação já
confirmada como recebida, mas via forma de pagamento que ainda pode não
fechar (boleto/cartão de crédito parcelado). Usa o fluxo real de
POST /billing/{id}/confirm-coparticipation (mesmo endpoint de
test_coparticipation_confirmation.py) — payment_method só existe
DEPOIS de confirmado como recebido, nunca antes.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text


def _window() -> tuple[str, str]:
    today = date.today()
    return today.isoformat(), (today + timedelta(days=2)).isoformat()


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_and_confirm_billing(
    client, auth_headers, plan_id: str, *, coparticipation_value: float, received: bool, payment_method: str | None = None
) -> str:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Coparticipação"}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers,
    )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 150.0,
            "coparticipation_value": coparticipation_value,
        },
        headers=auth_headers,
    )
    billing_id = billing_resp.json()["id"]
    confirm_resp = await client.post(
        f"/api/v1/billing/{billing_id}/confirm-coparticipation",
        json={"received": received, "payment_method": payment_method},
        headers=auth_headers,
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    return billing_id


async def test_delayed_payment_insight_fires_when_boleto_share_is_material(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for _ in range(5):
        await _create_and_confirm_billing(
            client, auth_headers_a, plan_id, coparticipation_value=50.0, received=True, payment_method="boleto"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    insights = response.json()["insights"]
    delayed = next((i for i in insights if "ainda pode não fechar" in i["title"]), None)
    assert delayed is not None
    assert delayed["financial_impact"] == 250.0
    assert "100%" in delayed["message"]


async def test_delayed_payment_insight_absent_when_all_payments_are_immediate(
    client, auth_headers_a, admin_engine, tenant_a
):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for _ in range(5):
        await _create_and_confirm_billing(
            client, auth_headers_a, plan_id, coparticipation_value=50.0, received=True, payment_method="pix"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert not any("ainda pode não fechar" in t for t in titles)


async def test_delayed_payment_insight_absent_below_min_sample(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for _ in range(2):
        await _create_and_confirm_billing(
            client, auth_headers_a, plan_id, coparticipation_value=50.0, received=True, payment_method="boleto"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert not any("ainda pode não fechar" in t for t in titles)


async def test_delayed_payment_insight_ignores_unconfirmed_coparticipation(
    client, auth_headers_a, admin_engine, tenant_a
):
    """coparticipation_received=False -> payment_method nunca é gravado
    (nada foi pago pra registrar "como") — não deveria contar como
    "confirmado via forma arriscada"."""
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    for _ in range(5):
        await _create_and_confirm_billing(
            client, auth_headers_a, plan_id, coparticipation_value=50.0, received=False, payment_method="boleto"
        )

    date_from, date_to = _window()
    response = await client.get(
        f"/api/v1/analytics/smart-insights?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert response.status_code == 200
    titles = [i["title"] for i in response.json()["insights"]]
    assert not any("ainda pode não fechar" in t for t in titles)
