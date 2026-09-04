"""
tests/integration/test_billing_denial_engine.py

Ponta a ponta via HTTP: cria paciente -> convênio -> contrato -> consulta
-> fatura, e verifica que o denial_risk_engine (testado isoladamente em
tests/test_denial_risk_engine.py) também funciona quando chamado através
da pilha real (endpoint -> service -> repository -> banco).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


async def _create_insurance_plan(admin_engine, tenant_id, display_name="Unimed Nacional", normalized_key="unimed_nacional") -> str:
    import uuid

    plan_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.insurance_plans (id, tenant_id, display_name, normalized_key) "
                "VALUES (:id, :t, :name, :key)"
            ),
            {"id": plan_id, "t": tenant_id, "name": display_name, "key": normalized_key},
        )
    return plan_id


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code="10101012", agreed_value=150.0):
    """Cria o cabeçalho (contracts, já HOMOLOGADO) + um item de preço
    (contract_items) — ver DECISÃO em app/sql/007_contract_intelligence.sql
    sobre por que contracts deixou de carregar procedure_code/agreed_value
    direto na linha."""
    import uuid

    contract_id = str(uuid.uuid4())
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.contracts (id, tenant_id, insurance_plan_id, valid_from, status) "
                "VALUES (:id, :t, :plan, '2026-01-01', 'homologado')"
            ),
            {"id": contract_id, "t": tenant_id, "plan": plan_id},
        )
        await conn.execute(
            text(
                "INSERT INTO core.contract_items (tenant_id, contract_id, tuss_code, agreed_price) "
                "VALUES (:t, :contract, :code, :value)"
            ),
            {"t": tenant_id, "contract": contract_id, "code": procedure_code, "value": agreed_value},
        )
    return contract_id


async def test_billing_with_missing_cid_is_flagged_high_risk_and_held(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="10101012", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Glosa"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "10101012",
            # cid_code OMITIDO de propósito — gatilho clássico de alto risco
        },
        headers=auth_headers_a,
    )
    assert appointment_resp.status_code == 201
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    body = billing_resp.json()
    assert body["denial_risk_level"] == "high"
    assert "missing_cid" in body["denial_reasons"]
    assert body["status"] == "held_for_review"


async def test_billing_above_contract_value_reports_value_saved(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="20202020", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Overcharge"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "20202020",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 180.0},
        headers=auth_headers_a,
    )
    assert billing_resp.status_code == 201
    body = billing_resp.json()
    assert body["denial_risk_level"] == "high"
    assert "value_above_contract" in body["denial_reasons"]
    assert body["value_saved_by_correction"] == pytest.approx(30.0)


async def test_clean_billing_is_low_risk_and_not_held(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="30303030", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Limpo"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    appointment_resp = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "30303030",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )
    appointment_id = appointment_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    body = billing_resp.json()
    assert body["denial_risk_level"] == "low"
    assert body["status"] == "pending"


async def test_list_high_risk_billing_returns_only_held_for_review(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a, display_name="Bradesco", normalized_key="bradesco")
    await _create_contract(admin_engine, tenant_a, plan_id, procedure_code="70707070", agreed_value=150.0)

    patient_resp = await client.post("/api/v1/patients", json={"full_name": "Paciente Alto Risco"}, headers=auth_headers_a)
    patient_id = patient_resp.json()["id"]

    # Um appointment SEM cid_code (vira alto risco) e outro limpo (vira baixo risco)
    high_risk_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "procedure_code": "70707070",
        },
        headers=auth_headers_a,
    )
    clean_appt = await client.post(
        "/api/v1/appointments",
        json={
            "patient_id": patient_id,
            "insurance_plan_id": plan_id,
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "procedure_code": "70707070",
            "cid_code": "J06",
        },
        headers=auth_headers_a,
    )

    await client.post(
        "/api/v1/billing",
        json={"appointment_id": high_risk_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )
    await client.post(
        "/api/v1/billing",
        json={"appointment_id": clean_appt.json()["id"], "insurance_plan_id": plan_id, "charged_value": 150.0},
        headers=auth_headers_a,
    )

    high_risk_list = await client.get("/api/v1/billing/high-risk", headers=auth_headers_a)
    assert high_risk_list.status_code == 200
    # BUG DE TESTE CORRIGIDO: /billing/high-risk devolve o envelope
    # paginado {items, total, limit, offset} (ver PaginatedResponse em
    # app/schemas/pagination.py), não uma lista crua — este teste nunca
    # foi atualizado quando a paginação chegou a este endpoint.
    items = high_risk_list.json()["items"]
    reasons = [item["denial_reasons"] for item in items]
    assert len(items) == 1
    assert "missing_cid" in reasons[0]
