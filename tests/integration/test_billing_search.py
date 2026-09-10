"""
tests/integration/test_billing_search.py

Cobre GET /billing/search — busca por nome/CPF do paciente que alimenta
o autocomplete das telas de Recurso de Glosa e "registrar pagamento
recebido" (ver DECISÃO em BillingRepository.search). Achado do Raio-X da
Sala de Comando: essas telas exigiam colar o billing_id como UUID cru,
mesmo o backend já tendo todo o dado necessário para buscar por nome.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) VALUES (:id, :t, :n, :k)"),
            {"id": plan_id, "t": tenant_id, "n": display_name, "k": normalized_key},
        )
    return plan_id


async def _create_billing(client, admin_engine, tenant_id, auth_headers, plan_id, patient_name, procedure_code="10101012"):
    patient_resp = await client.post("/api/v1/patients", json={"full_name": patient_name}, headers=auth_headers)
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": procedure_code,
            "cid_code": "J06",
        },
        headers=auth_headers,
    )
    appointment_id = appointment_resp.json()["id"]
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 200.0},
        headers=auth_headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()["id"]


async def test_search_finds_billing_by_patient_name(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    billing_id = await _create_billing(client, admin_engine, tenant_a, auth_headers_a, plan_id, "Maria da Silva Santos")

    response = await client.get("/api/v1/billing/search", params={"q": "Maria da Silva"}, headers=auth_headers_a)
    assert response.status_code == 200, response.text
    results = response.json()
    assert len(results) == 1
    assert results[0]["id"] == billing_id
    assert results[0]["patient_name"] == "Maria da Silva Santos"
    assert results[0]["procedure_code"] == "10101012"
    assert results[0]["insurance_plan_name"] == "Unimed Nacional"
    assert results[0]["charged_value"] == 200.0


async def test_search_finds_billing_by_cpf(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    patient_resp = await client.post(
        "/api/v1/patients", json={"full_name": "Joao Pereira", "cpf": "12345678900"}, headers=auth_headers_a
    )
    patient_id = patient_resp.json()["id"]
    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_resp.json()["id"], "insurance_plan_id": plan_id, "charged_value": 300.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201, billing_resp.text

    response = await client.get("/api/v1/billing/search", params={"q": "123.456.789-00"}, headers=auth_headers_a)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["patient_name"] == "Joao Pereira"


async def test_search_query_too_short_returns_empty(client, auth_headers_a):
    response = await client.get("/api/v1/billing/search", params={"q": "M"}, headers=auth_headers_a)
    assert response.status_code == 422  # min_length=2 no Query


async def test_search_does_not_leak_across_tenants(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a, tenant_b):
    plan_id_a = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_billing(client, admin_engine, tenant_a, auth_headers_a, plan_id_a, "Paciente Exclusivo Tenant A")

    response = await client.get("/api/v1/billing/search", params={"q": "Exclusivo"}, headers=auth_headers_b)
    assert response.status_code == 200, response.text
    assert response.json() == []
