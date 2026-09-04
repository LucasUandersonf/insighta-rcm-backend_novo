"""
tests/integration/test_glosas.py

Fase 3 do plano de adequação ao fluxo real de mercado (Agendamento ->
Atendimento -> Faturamento — ver conversa/PLANO_ADEQUACAO_TISS.md):
Glosa registra o FATO de uma negativa/redução real vinda da operadora,
e GET /glosas/reconciliacao cruza isso contra o que o motor de risco
(denial_risk_engine.py) previu na criação de cada billing — a métrica
que prova (ou não) que o motor funciona de verdade.
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


async def _create_contract(admin_engine, tenant_id, plan_id, procedure_code, agreed_value):
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


async def _create_billing(client, headers, *, plan_id, procedure_code, cid_code, charged_value, patient_name) -> dict:
    patient_resp = await client.post("/api/v1/patients", json={"full_name": patient_name}, headers=headers)
    patient_id = patient_resp.json()["id"]

    appt_payload = {
        "patient_id": patient_id,
        "insurance_plan_id": plan_id,
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "procedure_code": procedure_code,
    }
    if cid_code is not None:
        appt_payload["cid_code"] = cid_code
    appt_resp = await client.post("/api/v1/appointments", json=appt_payload, headers=headers)
    appointment_id = appt_resp.json()["id"]

    billing_resp = await client.post(
        "/api/v1/billing",
        json={"appointment_id": appointment_id, "insurance_plan_id": plan_id, "charged_value": charged_value},
        headers=headers,
    )
    assert billing_resp.status_code == 201, billing_resp.text
    return billing_resp.json()


def _window() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return (today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()


async def test_create_and_get_glosa(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, "10101012", 150.0)
    billing = await _create_billing(
        client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code="J06", charged_value=150.0, patient_name="Paciente Glosa"
    )

    create_resp = await client.post(
        "/api/v1/glosas",
        json={"billing_id": billing["id"], "codigo_motivo": "51", "descricao_motivo": "Glosa técnica", "valor_glosado": 50.0},
        headers=auth_headers_a,
    )
    assert create_resp.status_code == 201, create_resp.text
    glosa = create_resp.json()
    assert glosa["billing_id"] == billing["id"]
    assert glosa["codigo_motivo"] == "51"
    assert glosa["valor_glosado"] == 50.0

    get_resp = await client.get(f"/api/v1/glosas/{glosa['id']}", headers=auth_headers_a)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == glosa["id"]


async def test_create_glosa_rejects_unknown_billing(client, auth_headers_a):
    resp = await client.post(
        "/api/v1/glosas",
        json={"billing_id": "00000000-0000-0000-0000-000000000000", "valor_glosado": 10.0},
        headers=auth_headers_a,
    )
    assert resp.status_code == 404


async def test_create_glosa_rejects_billing_from_other_tenant(client, auth_headers_a, auth_headers_b, admin_engine, tenant_a, tenant_b):
    plan_id_b = await _create_insurance_plan(admin_engine, tenant_b)
    await _create_contract(admin_engine, tenant_b, plan_id_b, "10101012", 150.0)
    billing_b = await _create_billing(
        client, auth_headers_b, plan_id=plan_id_b, procedure_code="10101012", cid_code="J06", charged_value=150.0, patient_name="Paciente B"
    )

    resp = await client.post(
        "/api/v1/glosas", json={"billing_id": billing_b["id"], "valor_glosado": 10.0}, headers=auth_headers_a
    )
    assert resp.status_code == 404


async def test_reconciliation_classifies_all_four_quadrants_correctly(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, "10101012", 150.0)

    # A) previsto ALTO (CID ausente) + glosa real registrada -> true_positive
    billing_a = await _create_billing(
        client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code=None, charged_value=150.0, patient_name="Paciente A"
    )
    assert billing_a["denial_risk_level"] == "high"
    await client.post("/api/v1/glosas", json={"billing_id": billing_a["id"], "valor_glosado": 150.0}, headers=auth_headers_a)

    # B) previsto ALTO (CID ausente) + SEM glosa real -> false_positive
    billing_b = await _create_billing(
        client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code=None, charged_value=150.0, patient_name="Paciente B"
    )
    assert billing_b["denial_risk_level"] == "high"

    # C) previsto BAIXO (exato) + glosa real mesmo assim -> false_negative (ponto cego)
    billing_c = await _create_billing(
        client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code="J06", charged_value=150.0, patient_name="Paciente C"
    )
    assert billing_c["denial_risk_level"] == "low"
    await client.post("/api/v1/glosas", json={"billing_id": billing_c["id"], "valor_glosado": 30.0}, headers=auth_headers_a)

    # D) previsto BAIXO (exato) + SEM glosa -> true_negative
    billing_d = await _create_billing(
        client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code="J06", charged_value=150.0, patient_name="Paciente D"
    )
    assert billing_d["denial_risk_level"] == "low"

    date_from, date_to = _window()
    recon_resp = await client.get(
        f"/api/v1/glosas/reconciliacao?date_from={date_from}&date_to={date_to}", headers=auth_headers_a
    )
    assert recon_resp.status_code == 200, recon_resp.text
    body = recon_resp.json()

    assert body["true_positive_count"] == 1
    assert body["false_positive_count"] == 1
    assert body["false_negative_count"] == 1
    assert body["true_negative_count"] == 1
    assert body["precision_pct"] == 50.0
    assert body["recall_pct"] == 50.0
    assert body["valor_glosado_previsto"] == 150.0
    assert body["valor_glosado_nao_previsto"] == 30.0


async def test_reconciliation_with_no_billings_returns_none_percentages(client, auth_headers_a):
    """Sem base nenhuma (nenhum billing no período), precision/recall
    não têm denominador para significar nada — None, mesmo princípio de
    _delta_pct/_denial_risk_pct em analytics_service.py."""
    resp = await client.get(
        "/api/v1/glosas/reconciliacao?date_from=2020-01-01&date_to=2020-01-02", headers=auth_headers_a
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["precision_pct"] is None
    assert body["recall_pct"] is None
    assert body["true_positive_count"] == 0


async def test_reconciliation_default_period_does_not_crash(client, auth_headers_a):
    """Sem date_from/date_to: usa o default de 7 dias (mesmo padrão de
    analytics.py) sem quebrar."""
    resp = await client.get("/api/v1/glosas/reconciliacao", headers=auth_headers_a)
    assert resp.status_code == 200


async def test_list_glosas_paginated(client, auth_headers_a, admin_engine, tenant_a):
    plan_id = await _create_insurance_plan(admin_engine, tenant_a)
    await _create_contract(admin_engine, tenant_a, plan_id, "10101012", 150.0)
    for i in range(3):
        billing = await _create_billing(
            client, auth_headers_a, plan_id=plan_id, procedure_code="10101012", cid_code="J06", charged_value=150.0, patient_name=f"Paciente {i}"
        )
        await client.post("/api/v1/glosas", json={"billing_id": billing["id"], "valor_glosado": 10.0}, headers=auth_headers_a)

    list_resp = await client.get("/api/v1/glosas?limit=2&offset=0", headers=auth_headers_a)
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
