"""
tests/integration/test_billing_payment_method.py

"Mapa de Dados Insighta" — Domínio Financeiro particular (Onda 1):
forma de pagamento + parcelas da parte que o PRÓPRIO paciente paga —
complementa coparticipation_value (quanto foi cobrado) com COMO foi
pago, alimentando um futuro insight de inadimplência de particular.
"""
import uuid
from datetime import datetime, timedelta, timezone


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Particular", normalized_key="unimed_particular") -> str:
    from sqlalchemy import text

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :name, :key)"),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_billing(client, auth_headers, plan_id: str, **extra) -> dict:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Particular"}, headers=auth_headers)
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
    payload = {"appointment_id": appt_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 300.0, **extra}
    billing_resp = await client.post("/api/v1/billing", json=payload, headers=auth_headers)
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()


async def test_new_billing_has_no_payment_method_by_default(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing = await _create_billing(client, auth_headers_a, plan_id)
    assert billing["payment_method"] is None
    assert billing["installments"] is None


async def test_create_billing_with_payment_method_and_installments(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing = await _create_billing(client, auth_headers_a, plan_id, payment_method="cartao_credito", installments=3)
    assert billing["payment_method"] == "cartao_credito"
    assert billing["installments"] == 3


async def test_create_billing_rejects_unknown_payment_method(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente X"}, headers=auth_headers_a)
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
        headers=auth_headers_a,
    )
    response = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 300.0,
            "payment_method": "criptomoeda",
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_create_billing_rejects_zero_installments(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Y"}, headers=auth_headers_a)
    appt_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
        headers=auth_headers_a,
    )
    response = await client.post(
        "/api/v1/billing",
        json={
            "appointment_id": appt_resp.json()["id"],
            "insurance_plan_id": plan_id,
            "charged_value": 300.0,
            "installments": 0,
        },
        headers=auth_headers_a,
    )
    assert response.status_code == 422


async def test_payment_method_appears_in_billing_search(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_billing(client, auth_headers_a, plan_id, payment_method="pix")

    resp = await client.get("/api/v1/billing/search?q=Paciente Particular", headers=auth_headers_a)
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) >= 1
    assert results[0]["payment_method"] == "pix"
